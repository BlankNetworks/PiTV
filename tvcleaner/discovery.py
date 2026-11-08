"""Read-only, bounded LAN hints for ADB-capable TVs.

An open port or mDNS record does not establish that a device is a TV. The user
must select a target, approve ADB on that TV, and pass the transport snapshot.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import re
import socket
import subprocess

from .transport import (
    PRIVATE_NETWORKS, TransportError, adb_environment, private_ipv4,
    resolve_adb, split_endpoint, validate_endpoint,
)

_PROBE_TIMEOUT = 0.35
_MAX_WORKERS = 32
_MDNS_TYPES = {
    "_adb-tls-pairing._tcp": ("adb_tls_pairing", "pairing_required"),
    "_adb-tls-connect._tcp": ("adb_tls", "approval_required"),
}
_MAX_AUTO_NETWORKS = 4
_VIRTUAL_KINDS = {
    "bridge", "veth", "tun", "tap", "wireguard", "dummy", "vxlan", "geneve",
    "gre", "gretap", "ipip", "sit", "vrf", "bond", "team", "macvlan", "ipvlan", "vlan",
}
_PHYSICAL_NAME = re.compile(r"(?:eth[0-9]+|en[A-Za-z0-9_.:-]+|wlan[0-9]+|wl[A-Za-z0-9_.:-]+)\Z")


def validate_cidr(cidr: str) -> ipaddress.IPv4Network:
    if (not isinstance(cidr, str) or not cidr or len(cidr) > 32
            or cidr != cidr.strip() or "/" not in cidr):
        raise TransportError("Choose a private IPv4 subnet with a /24 or smaller scan range.")
    try:
        network = ipaddress.IPv4Network(cidr, strict=False)
    except (ValueError, ipaddress.AddressValueError):
        raise TransportError("The scan range must be an IPv4 CIDR such as 192.168.1.0/24.") from None
    if network.prefixlen < 24 or network.num_addresses > 256:
        raise TransportError("Scan at most one /24 subnet (256 addresses) at a time.")
    if not any(network.subnet_of(allowed) for allowed in PRIVATE_NETWORKS):
        raise TransportError("Only RFC1918 or link-local IPv4 subnets can be scanned.")
    return network


def validate_network(cidr: str) -> ipaddress.IPv4Network:
    """Controller compatibility entry point; invalid requests raise ValueError."""
    try:
        return validate_cidr(cidr)
    except TransportError as error:
        raise ValueError(str(error)) from None


def local_networks() -> list[dict]:
    """List active Ethernet/Wi-Fi networks with actual addresses and bounded CIDRs.

    Roaming discovery never changes interface settings. It skips conventional
    virtual, bridge and VPN interfaces and caps broader LANs to the Pi's own /24.
    An unrecognized interface can still be used with an explicit manual CIDR.
    """
    try:
        result = subprocess.run(
            ["ip", "-j", "-d", "-4", "addr", "show", "up"], capture_output=True,
            text=True, timeout=5, check=False,
        )
        if result.returncode or len(result.stdout) > 1024 * 1024:
            return []
        interfaces = json.loads(result.stdout)
        if not isinstance(interfaces, list):
            return []
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        return []
    found: dict[tuple[str, str, str], dict] = {}
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        name = interface.get("ifname")
        if not isinstance(name, str) or len(name) > 64 or not _PHYSICAL_NAME.fullmatch(name):
            continue
        flags = interface.get("flags", [])
        linkinfo = interface.get("linkinfo", {})
        if (interface.get("operstate") in ("DOWN", "DORMANT", "LOWERLAYERDOWN", "NOTPRESENT")
                or interface.get("master") or interface.get("link_type") in ("loopback", "ppp", "none")
                or (isinstance(flags, list) and "LOOPBACK" in flags)
                or (isinstance(linkinfo, dict) and linkinfo.get("info_kind") in _VIRTUAL_KINDS)):
            continue
        addresses = interface.get("addr_info", [])
        if not isinstance(addresses, list):
            continue
        for info in addresses:
            if not isinstance(info, dict) or info.get("family") != "inet":
                continue
            prefix = info.get("prefixlen")
            if isinstance(prefix, bool) or not isinstance(prefix, int) or not 0 <= prefix <= 32:
                continue
            try:
                address = private_ipv4(info.get("local", ""))
                network = validate_cidr(f"{address}/{max(prefix, 24)}")
            except TransportError:
                continue
            original = ipaddress.IPv4Network(f"{address}/{prefix}", strict=False)
            found[(str(network), name, str(address))] = {
                "cidr": str(network), "interface": name, "address": str(address),
                "network": str(original), "prefixlen": prefix, "bounded": prefix < 24,
            }
    return [found[key] for key in sorted(found)]


def _tcp_5555(address: str) -> dict | None:
    try:
        # Numeric AF_INET avoids DNS and never sends an ADB command.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(_PROBE_TIMEOUT)
            if connection.connect_ex((address, 5555)) != 0:
                return None
    except OSError:
        return None
    return {
        "endpoint": validate_endpoint(f"{address}:5555"), "address": address,
        "port": 5555, "transport": "adb_tcp", "status": "unverified",
    }


def _mdns(network: ipaddress.IPv4Network, adb_binary=None, home=None) -> list[dict]:
    try:
        executable = resolve_adb(adb_binary)
        result = subprocess.run(
            [executable, "mdns", "services"], capture_output=True, text=True,
            timeout=5, check=False, env=adb_environment(home),
        )
        if result.returncode or len(result.stdout) > 1024 * 1024:
            return []
    except (TransportError, OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        return []
    found: dict[str, dict] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        service = _MDNS_TYPES.get(fields[1].rstrip("."))
        if service is None:
            continue
        try:
            endpoint = validate_endpoint(fields[2])
            address, port = split_endpoint(endpoint)
        except TransportError:
            continue
        if ipaddress.IPv4Address(address) not in network:
            continue
        found[endpoint] = {
            "endpoint": endpoint, "address": address, "port": port,
            "transport": service[0], "status": service[1],
        }
        if len(found) >= 256:
            break
    return list(found.values())


def discover(cidr: str, adb_binary=None, home=None) -> list[dict]:
    """Probe port 5555 and list local TLS ADB advertisements, without connecting.

    Nothing here accepts debugging prompts, calls ``adb connect``/``pair`` or
    sends commands to a device. Port probes and advertisements are only hints.
    """
    network = validate_cidr(cidr)
    found: dict[str, dict] = {}
    addresses = [str(address) for address in network.hosts()]
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, max(1, len(addresses)))) as pool:
        futures = [pool.submit(_tcp_5555, address) for address in addresses]
        for future in as_completed(futures):
            record = future.result()
            if record:
                found[record["endpoint"]] = record
    # Prefer the more specific transport advertisement when an endpoint overlaps.
    for record in _mdns(network, adb_binary, home):
        found[record["endpoint"]] = record
    return sorted(found.values(), key=lambda item: (int(ipaddress.IPv4Address(item["address"])), item["port"]))


def discover_networks(cidr="auto", adb_binary=None, home=None) -> dict:
    """Discover a manual subnet or at most four currently attached LAN subnets.

    Returns ``devices``, ``networks`` and ``warnings``. Auto mode re-reads network
    addresses on every call so moving the Pi does not retain the previous home's
    scan range. USB enumeration is separate and needs no network configuration.
    """
    warnings = []
    own_addresses = set()
    if cidr == "auto":
        interfaces = local_networks()
        own_addresses = {item.get("address") for item in interfaces}
        unique = {}
        for item in interfaces:
            unique.setdefault(item["cidr"], item)
        networks = list(unique.values())[:_MAX_AUTO_NETWORKS]
        if len(unique) > _MAX_AUTO_NETWORKS:
            warnings.append("Only the first four attached Ethernet/Wi-Fi subnets were scanned. Select a subnet manually for others.")
        if any(item.get("bounded") for item in networks):
            warnings.append("A LAN is larger than /24. Only the Pi's containing /24 is scanned automatically; select other ranges manually.")
        if not networks:
            warnings.append("No active private IPv4 Ethernet/Wi-Fi network was found. Connect the Pi to the TV's local network.")
    else:
        network = str(validate_cidr(cidr))
        networks = [{"cidr": network, "interface": "manual", "address": None,
                     "network": network, "prefixlen": validate_cidr(cidr).prefixlen, "bounded": False}]
    found = {}
    for network in networks:
        for device in discover(network["cidr"], adb_binary=adb_binary, home=home):
            # Do not promote a listener on the Pi itself to a TV candidate.
            if device["address"] not in own_addresses:
                found[device["endpoint"]] = device
    devices = sorted(found.values(), key=lambda item: (int(ipaddress.IPv4Address(item["address"])), item["port"]))
    return {"devices": devices, "networks": networks, "warnings": warnings}

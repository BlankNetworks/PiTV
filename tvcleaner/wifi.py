"""PiTV access point and USB Wi-Fi uplink, operated by a narrow root worker.

NetworkManager's shared IPv4 mode supplies DHCP, DNS and NAT on the built-in
radio. The web account only writes one validated request in its private state;
it cannot run nmcli or read saved Wi-Fi passwords.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .storage import atomic_json, read_json


STATE = Path("/var/lib/tvcleaner/state")
NM_DIR = Path("/etc/NetworkManager/system-connections")
AP_FILE = NM_DIR / "pitv-access-point.nmconnection"
UPLINK_FILE = NM_DIR / "pitv-uplink.nmconnection"
AP_INTERFACE = "wlan0"
AP_ID = "PiTV Access Point"
UPLINK_ID = "PiTV Uplink"
AP_UUID = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/BlankNetworks/PiTV/ap"))
UPLINK_UUID = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/BlankNetworks/PiTV/uplink"))
DEFAULT_SSID = "PITV"
DEFAULT_PASSWORD = "pitv12345"
MANAGED_MARKER = "# Managed by PiTV. Configure this network in the PiTV web interface.\n"
REQUEST = "wifi-request.json"
RESULT = "wifi-result.json"
PUBLIC = "wifi-public.json"
_INTERFACE = re.compile(r"[A-Za-z0-9_.-]{1,32}\Z")


class WifiError(RuntimeError):
    pass


def _nm(*args, timeout=20):
    if not shutil.which("nmcli"):
        raise WifiError("NetworkManager is not available. Use current Raspberry Pi OS with NetworkManager.")
    try:
        result = subprocess.run(["nmcli", "--colors", "no", *args], capture_output=True,
                                text=True, timeout=timeout, check=False,
                                env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WifiError("NetworkManager did not respond in time.") from error
    if result.returncode:
        # nmcli can echo connection details. Never return it to the tokenless UI.
        raise WifiError("NetworkManager could not complete this Wi-Fi operation.")
    return result.stdout


def _devices():
    try:
        lines = _nm("-t", "-e", "no", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status").splitlines()
    except WifiError:
        return {}
    devices = {}
    for line in lines:
        parts = line.split(":", 3)
        if len(parts) == 4 and _INTERFACE.fullmatch(parts[0]):
            devices[parts[0]] = {"type": parts[1], "state": parts[2], "connection": parts[3]}
    return devices


def usb_adapters(sysfs=Path("/sys/class/net")):
    adapters = []
    for path in sysfs.glob("*") if sysfs.exists() else []:
        if not _INTERFACE.fullmatch(path.name) or path.name == AP_INTERFACE:
            continue
        try:
            target = (path / "device").resolve(strict=True)
            wireless = (path / "wireless").exists() or (path / "phy80211").exists()
        except OSError:
            continue
        if wireless and "/usb" in str(target):
            adapters.append(path.name)
    return sorted(adapters)


def _interface(value):
    if not isinstance(value, str) or not _INTERFACE.fullmatch(value) or value not in usb_adapters():
        raise WifiError("Select a connected USB Wi-Fi adapter.")
    devices = _devices()
    if devices.get(value, {}).get("type") != "wifi":
        raise WifiError("The USB adapter is not managed as a Wi-Fi device.")
    return value


def _ssid(value):
    if (not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 32
            or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise WifiError("Enter a Wi-Fi name of 1–32 bytes without control characters or edge spaces.")
    return value


def _password(value, *, optional=False):
    if optional and value == "":
        return value
    if (not isinstance(value, str) or not 8 <= len(value) <= 63
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise WifiError("A Wi-Fi password must have 8–63 printable characters.")
    return value


def _escape(value):
    # GLib keyfiles escape backslash, and leading spaces as \s.
    value = value.replace("\\", "\\\\")
    return ("\\s" + value[1:]) if value.startswith(" ") else value


def _read_managed(path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise WifiError("A PiTV Wi-Fi profile path is not a regular file.")
    value = path.read_bytes()
    if len(value) > 65536 or not value.startswith(MANAGED_MARKER.encode()):
        raise WifiError("A PiTV Wi-Fi profile path is already used by another configuration.")
    return value


def _write_managed(path, value):
    _read_managed(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pitv-wifi-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _profile(identifier, unique_id, interface, ssid, password, *, hotspot=False, sae=False, hidden=False):
    text = (MANAGED_MARKER + "[connection]\n" +
            f"id={identifier}\nuuid={unique_id}\ntype=wifi\ninterface-name={interface}\n" +
            "autoconnect=true\n" + ("autoconnect-priority=100\n" if hotspot else "autoconnect-priority=50\n") +
            "\n[wifi]\n" + f"ssid={_escape(ssid)}\nmode={'ap' if hotspot else 'infrastructure'}\n")
    if hotspot:
        text += "band=bg\n"
    elif hidden:
        text += "hidden=true\n"
    if password:
        text += "\n[wifi-security]\n" + f"key-mgmt={'sae' if sae else 'wpa-psk'}\npsk={_escape(password)}\n"
    text += "\n[ipv4]\nmethod=" + ("shared" if hotspot else "auto") + "\n"
    if not hotspot:
        text += "route-metric=1\n"
    text += "\n[ipv6]\nmethod=" + ("disabled" if hotspot else "auto") + "\n"
    return text.encode("utf-8")


def _profile_value(source, key):
    if source is None:
        return ""
    for line in source.decode("utf-8").splitlines():
        if line.startswith(key + "="):
            value = line.split("=", 1)[1]
            if value.startswith("\\s"):
                value = " " + value[2:]
            return value.replace("\\\\", "\\")
    return ""


def _write_public(state, **values):
    path = Path(state) / PUBLIC
    current = read_json(path, {}) or {}
    atomic_json(path, {**current, **values})
    account = pwd.getpwnam("tvcleaner")
    os.chown(path, account.pw_uid, account.pw_gid)


def _apply_profile(path, content, unique_id, interface, *, previous_connection=""):
    old = _read_managed(path)
    _write_managed(path, content)
    try:
        _nm("connection", "load", str(path))
        _nm("--wait", "45", "connection", "up", "uuid", unique_id, "ifname", interface, timeout=50)
    except WifiError:
        if old is None:
            path.unlink(missing_ok=True)
        else:
            _write_managed(path, old)
        try:
            _nm("connection", "reload")
            if previous_connection:
                _nm("--wait", "20", "connection", "up", "id", previous_connection,
                    "ifname", interface, timeout=25)
        except WifiError:
            pass
        raise


def _split_escaped(line):
    fields = [""]
    escaped = False
    for char in line:
        if escaped:
            fields[-1] += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("")
        else:
            fields[-1] += char
    return fields


def _scan_networks(output, current_ap):
    entries = 0
    unreadable = 0
    networks = {}
    for line in output.splitlines():
        if not line:
            continue
        entries += 1
        fields = _split_escaped(line)
        if len(fields) != 3:
            unreadable += 1
            continue
        ssid, signal, security = fields
        try:
            _ssid(ssid)
            strength = max(0, min(100, int(signal)))
        except (WifiError, ValueError):
            unreadable += 1
            continue
        if ssid == current_ap:
            continue
        if ssid not in networks or strength > networks[ssid]["signal"]:
            networks[ssid] = {"ssid": ssid, "signal": strength, "security": security or "Open"}
    return sorted(networks.values(), key=lambda n: -n["signal"])[:100], entries, unreadable


def scan(interface):
    interface = _interface(interface)
    if _nm("radio", "wifi").strip() == "disabled":
        _nm("radio", "wifi", "on")
        time.sleep(2)
    current_ap = _profile_value(_read_managed(AP_FILE), "ssid") if AP_FILE.exists() else DEFAULT_SSID
    output = _nm("-t", "-e", "yes", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi",
                 "list", "--rescan", "yes", "ifname", interface, timeout=35)
    networks, entries, unreadable = _scan_networks(output, current_ap)
    if not networks:
        # Some drivers return the cached list before an active scan has settled.
        time.sleep(2)
        output = _nm("-t", "-e", "yes", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi",
                     "list", "--rescan", "no", "ifname", interface)
        networks, entries, unreadable = _scan_networks(output, current_ap)
    result = {"interface": interface, "networks": networks}
    if not networks:
        device_state = _devices().get(interface, {}).get("state", "unknown")
        if device_state in ("unavailable", "unmanaged"):
            message = "The USB Wi-Fi adapter is " + device_state + " in NetworkManager. Check its driver and radio switch."
        elif entries and unreadable == entries:
            message = "The adapter returned Wi-Fi entries, but PiTV could not read them. You can enter the network name below."
        else:
            message = "The USB adapter saw no broadcast Wi-Fi networks. Check its antenna and supported Wi-Fi band, or enter the network name below."
        result["message"] = message
        result["device_state"] = device_state
    return result


def connect(state, interface, ssid, password):
    interface = _interface(interface)
    ssid = _ssid(ssid)
    if ssid == _profile_value(_read_managed(AP_FILE), "ssid") or ssid == DEFAULT_SSID:
        raise WifiError("Choose the TV network, not the PiTV hotspot itself.")
    try:
        visible = {item["ssid"]: item for item in scan(interface)["networks"]}
    except WifiError:
        visible = {}
    # A typed SSID must work even when the AP hides its name or scanning fails
    # to return it. NetworkManager probes that SSID on connection activation.
    security = visible[ssid]["security"].upper() if ssid in visible else ("WPA2" if password else "OPEN")
    if "802.1X" in security or "WEP" in security or "WPA1" in security and "WPA2" not in security:
        raise WifiError("This Wi-Fi security type is not supported by PiTV relay mode.")
    open_network = security in ("OPEN", "--", "")
    if open_network:
        if password:
            raise WifiError("This network is open; leave its password blank.")
    else:
        password = _password(password)
    previous = _devices().get(interface, {}).get("connection", "")
    content = _profile(UPLINK_ID, UPLINK_UUID, interface, ssid, password,
                       sae="WPA3" in security and "WPA2" not in security,
                       hidden=ssid not in visible)
    _apply_profile(UPLINK_FILE, content, UPLINK_UUID, interface,
                   previous_connection=previous if previous != UPLINK_ID else "")
    _write_public(state, uplink_ssid=ssid, uplink_interface=interface)
    return status(state)


def disconnect(state, interface):
    """Disconnect only the PiTV-managed USB uplink and disable its autoconnect."""
    interface = _interface(interface)
    device = _devices().get(interface, {})
    if device.get("connection") != UPLINK_ID or device.get("state") != "connected":
        raise WifiError("The selected USB adapter is not connected through PiTV.")
    old = _read_managed(UPLINK_FILE)
    if old is None:
        raise WifiError("The PiTV USB Wi-Fi profile is missing. No connection was changed.")
    changed = old.replace(b"autoconnect=true\n", b"autoconnect=false\n", 1)
    if changed == old:
        raise WifiError("The PiTV USB Wi-Fi profile needs review before disconnecting.")
    _write_managed(UPLINK_FILE, changed)
    try:
        _nm("connection", "load", str(UPLINK_FILE))
        _nm("connection", "down", "uuid", UPLINK_UUID)
    except WifiError:
        _write_managed(UPLINK_FILE, old)
        try:
            _nm("connection", "load", str(UPLINK_FILE))
        except WifiError:
            pass
        raise
    _write_public(state, uplink_ssid="", uplink_interface=interface)
    return status(state)


def configure_ap(state, ssid, password):
    ssid = _ssid(ssid)
    old = _read_managed(AP_FILE)
    password = _password(password, optional=True)
    if not password:
        password = _profile_value(old, "psk") if old else DEFAULT_PASSWORD
    _password(password)
    devices = _devices()
    radio = devices.get(AP_INTERFACE, {})
    if radio.get("type") != "wifi":
        raise WifiError("The Pi's built-in wlan0 Wi-Fi radio is not available.")
    previous = radio.get("connection", "") if radio.get("state") == "connected" else ""
    if previous and previous != AP_ID and not any(
            item.get("state") == "connected" for name, item in devices.items() if name != AP_INTERFACE):
        raise WifiError("Connect Ethernet or the USB Wi-Fi adapter before switching the Pi's only network to PITV.")
    content = _profile(AP_ID, AP_UUID, AP_INTERFACE, ssid, password, hotspot=True)
    _apply_profile(AP_FILE, content, AP_UUID, AP_INTERFACE,
                   previous_connection=previous if previous != AP_ID else "")
    _write_public(state, ap_ssid=ssid, ap_configured=True, ap_default_password=password == DEFAULT_PASSWORD)
    return status(state)


def provision(state=STATE):
    """Start the default AP only when the built-in radio has no active connection."""
    if os.geteuid() != 0:
        raise WifiError("The Wi-Fi provisioner must run as root.")
    devices = _devices()
    radio = devices.get(AP_INTERFACE, {})
    if radio.get("type") != "wifi":
        return {"state": "unavailable", "message": "Built-in Wi-Fi or NetworkManager is unavailable."}
    if _nm("radio", "wifi").strip() == "disabled":
        _nm("radio", "wifi", "on")
        time.sleep(2)
    if _read_managed(AP_FILE) is not None:
        return {"state": "existing", "message": "The existing PiTV hotspot configuration was preserved."}
    if radio.get("state") == "connected":
        return {"state": "preserved", "message": "Built-in Wi-Fi is already connected; it was not changed."}
    return configure_ap(state, DEFAULT_SSID, DEFAULT_PASSWORD)


def status(state=STATE, *, demo=False):
    if demo:
        settings = read_json(Path(state) / "wifi-demo.json", {}) or {}
        return {"supported": True, "demo": True,
                "ap": {"ssid": settings.get("ap_ssid", DEFAULT_SSID), "active": True,
                       "address": "10.42.0.1", "default_password": settings.get("ap_default_password", True)},
                "uplink": {"interface": "wlan1", "connected": bool(settings.get("uplink_ssid")),
                           "ssid": settings.get("uplink_ssid", ""),
                           "internet_via_usb": bool(settings.get("uplink_ssid"))}, "adapters": ["wlan1"]}
    devices = _devices()
    public = read_json(Path(state) / PUBLIC, {}) or {}
    ap = devices.get(AP_INTERFACE, {})
    adapters = [item for item in usb_adapters() if devices.get(item, {}).get("type") == "wifi"]
    try:
        radio_enabled = _nm("radio", "wifi").strip() == "enabled"
    except WifiError:
        radio_enabled = False
    uplink_iface = public.get("uplink_interface") if public.get("uplink_interface") in adapters else (adapters[0] if adapters else "")
    uplink = devices.get(uplink_iface, {})
    ssid = public.get("ap_ssid") or DEFAULT_SSID
    active = ap.get("state") == "connected" and ap.get("connection") == AP_ID
    connected = uplink.get("state") == "connected" and uplink.get("connection") == UPLINK_ID
    ap_address = ""
    route_interface = ""
    if active:
        try:
            ap_address = _nm("-g", "IP4.ADDRESS", "device", "show", AP_INTERFACE).splitlines()[0].split("/", 1)[0]
        except (WifiError, IndexError):
            pass
    if connected:
        try:
            route = subprocess.run(["ip", "-j", "-4", "route", "get", "1.1.1.1"],
                                   capture_output=True, text=True, timeout=3, check=False)
            routes = json.loads(route.stdout) if route.returncode == 0 else []
            route_interface = routes[0].get("dev", "") if isinstance(routes, list) and routes else ""
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return {"supported": ap.get("type") == "wifi", "demo": False,
            "radio_enabled": radio_enabled,
            "adapter_states": {item: devices[item].get("state", "unknown") for item in adapters},
            "ap": {"ssid": ssid, "active": active, "configured": bool(public.get("ap_configured")),
                   "interface": AP_INTERFACE, "address": ap_address,
                   "default_password": bool(public.get("ap_default_password", True))},
            "uplink": {"interface": uplink_iface, "connected": connected,
                       "ssid": public.get("uplink_ssid", "") if connected else "",
                       "internet_via_usb": connected and route_interface == uplink_iface},
            "adapters": adapters}


def _result(state, value):
    path = Path(state) / RESULT
    atomic_json(path, value)
    account = pwd.getpwnam("tvcleaner")
    os.chown(path, account.pw_uid, account.pw_gid)


def request(state, action, payload=None, *, demo=False):
    payload = payload or {}
    if demo:
        if action == "scan":
            return {"interface": "wlan1", "networks": [{"ssid": "Demo TV Wi-Fi", "signal": 82, "security": "WPA2"}]}
        if action == "connect":
            _ssid(payload.get("ssid"))
            _password(payload.get("password"))
            current = read_json(Path(state) / "wifi-demo.json", {}) or {}
            atomic_json(Path(state) / "wifi-demo.json", {**current, "uplink_ssid": payload["ssid"]})
            return status(state, demo=True)
        if action == "disconnect":
            current = read_json(Path(state) / "wifi-demo.json", {}) or {}
            atomic_json(Path(state) / "wifi-demo.json", {**current, "uplink_ssid": ""})
            return status(state, demo=True)
        if action == "ap":
            _ssid(payload.get("ssid"))
            _password(payload.get("password", ""), optional=True)
            current = read_json(Path(state) / "wifi-demo.json", {}) or {}
            atomic_json(Path(state) / "wifi-demo.json", {**current, "ap_ssid": payload["ssid"],
                        "ap_default_password": (payload["password"] == DEFAULT_PASSWORD if payload.get("password")
                                                else current.get("ap_default_password", True))})
            return status(state, demo=True)
    if action not in ("scan", "connect", "disconnect", "ap"):
        raise WifiError("Unknown Wi-Fi operation.")
    if action in ("scan", "disconnect"):
        _interface(payload.get("interface", ""))
    elif action == "connect":
        _interface(payload.get("interface", ""))
        _ssid(payload.get("ssid"))
        if not isinstance(payload.get("password"), str) or len(payload["password"]) > 63:
            raise WifiError("Enter a valid Wi-Fi password.")
    else:
        _ssid(payload.get("ssid"))
        _password(payload.get("password", ""), optional=True)
    state = Path(state)
    lock = state / "wifi-request.lock"
    with lock.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        prior = read_json(state / RESULT, {}) or {}
        if ((state / REQUEST).exists() or
                (prior.get("state") == "pending" and (state / RESULT).stat().st_mtime > time.time() - 120)):
            raise WifiError("A Wi-Fi operation is already in progress.")
        identifier = uuid.uuid4().hex
        atomic_json(state / RESULT, {"id": identifier, "state": "pending"})
        atomic_json(state / REQUEST, {**payload, "id": identifier, "action": action})
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        value = read_json(state / RESULT, {}) or {}
        if value.get("id") == identifier and value.get("state") in ("done", "error"):
            if value["state"] == "error":
                raise WifiError(value.get("error", "Wi-Fi operation failed."))
            return value.get("result", {})
        time.sleep(0.25)
    raise WifiError("The Wi-Fi operation timed out. Check its status before retrying.")


def worker(state=STATE):
    if os.geteuid() != 0:
        raise WifiError("The Wi-Fi worker must run as root.")
    state = Path(state)
    path = state / REQUEST
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    finally:
        path.unlink(missing_ok=True)
    identifier = value.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        return
    try:
        action = value.get("action")
        if action == "scan":
            result = scan(value.get("interface"))
        elif action == "connect":
            result = connect(state, value.get("interface"), value.get("ssid"), value.get("password"))
        elif action == "disconnect":
            result = disconnect(state, value.get("interface"))
        elif action == "ap":
            result = configure_ap(state, value.get("ssid"), value.get("password", ""))
        else:
            raise WifiError("Unknown Wi-Fi operation.")
        _result(state, {"id": identifier, "state": "done", "result": result})
    except Exception as error:
        _result(state, {"id": identifier, "state": "error", "error": str(error)[:250]})


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("worker", "provision"):
        raise SystemExit("Usage: python3 -m tvcleaner.wifi worker|provision")
    if sys.argv[1] == "worker":
        worker()
    else:
        print(json.dumps(provision()))


if __name__ == "__main__":
    main()

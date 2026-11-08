from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from tvcleaner.discovery import _mdns, _tcp_5555, discover, discover_networks, local_networks, validate_cidr, validate_network
from tvcleaner.transport import TransportError


class DiscoveryTests(unittest.TestCase):
    def test_large_public_and_hostname_networks_refused(self):
        for value in ("192.168.0.0/16", "10.0.0.0/8", "192.0.2.0/24", "8.8.8.0/24",
                      "100.64.0.0/24", "127.0.0.0/24", "::1/128", "tv.local/24",
                      "192.168.1.1", "192.168.1.0/24\n", None):
            with self.subTest(value=value), self.assertRaises(TransportError):
                validate_cidr(value)

    def test_controller_validation_uses_value_error(self):
        with self.assertRaises(ValueError):
            validate_network("8.8.8.0/24")

    def test_private_subnet_normalized_and_bounded(self):
        self.assertEqual(str(validate_cidr("192.168.1.50/24")), "192.168.1.0/24")
        self.assertEqual(validate_cidr("10.1.1.0/24").num_addresses, 256)
        self.assertEqual(validate_cidr("169.254.2.1/32").num_addresses, 1)

    @patch("tvcleaner.discovery.subprocess.run")
    def test_local_networks_caps_broad_interfaces_and_ignores_public(self, run):
        data = [
            {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "10.22.33.44", "prefixlen": 8}]},
            {"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "192.168.4.1", "prefixlen": 24}]},
            {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
            {"ifname": "public0", "addr_info": [{"family": "inet", "local": "8.8.8.8", "prefixlen": 24}]},
        ]
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(data), "")
        self.assertEqual(local_networks(), [
            {"cidr": "10.22.33.0/24", "interface": "eth0", "address": "10.22.33.44",
             "network": "10.0.0.0/8", "prefixlen": 8, "bounded": True},
            {"cidr": "192.168.4.0/24", "interface": "wlan0", "address": "192.168.4.1",
             "network": "192.168.4.0/24", "prefixlen": 24, "bounded": False},
        ])
        self.assertLessEqual(run.call_args.kwargs["timeout"], 5)

    @patch("tvcleaner.discovery.subprocess.run")
    def test_no_ip_tool_or_malformed_output_is_nonfatal(self, run):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("ip", 5)):
            run.side_effect = failure
            self.assertEqual(local_networks(), [])
        run.side_effect = None
        for output in ("not json", "{}", "[null,{},123]"):
            run.return_value = subprocess.CompletedProcess([], 0, output, "")
            self.assertEqual(local_networks(), [])

    @patch("tvcleaner.discovery.socket.socket")
    def test_tcp_probe_is_bounded_and_does_not_send_adb_payload(self, socket_constructor):
        connection = MagicMock()
        socket_constructor.return_value.__enter__.return_value = connection
        connection.connect_ex.return_value = 0
        record = _tcp_5555("192.168.1.50")
        self.assertEqual(record["endpoint"], "192.168.1.50:5555")
        self.assertEqual(record["status"], "unverified")
        socket_constructor.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout.assert_called_once_with(0.35)
        connection.connect_ex.assert_called_once_with(("192.168.1.50", 5555))
        connection.send.assert_not_called()
        connection.sendall.assert_not_called()
        socket_constructor.return_value.__exit__.assert_called_once()

    @patch("tvcleaner.discovery.socket.socket")
    def test_connection_errors_are_closed_port(self, socket_constructor):
        socket_constructor.side_effect = OSError("unreachable")
        self.assertIsNone(_tcp_5555("192.168.1.50"))

    @patch("tvcleaner.discovery.subprocess.run")
    def test_mdns_filters_to_selected_private_subnet_and_tls_services(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, """List of discovered mdns services
adb-test-pair _adb-tls-pairing._tcp. 192.168.1.50:37123
adb-test-connect _adb-tls-connect._tcp. 192.168.1.50:40235
outside _adb-tls-connect._tcp. 192.168.2.1:40235
public _adb-tls-connect._tcp. 8.8.8.8:5555
hostname _adb-tls-connect._tcp. rebinding.example:5555
v6 _adb-tls-connect._tcp. [fe80::1]:5555
printer _ipp._tcp. 192.168.1.1:631
bad _adb-tls-connect._tcp. 192.168.1.1:999999
""", "")
        records = _mdns(ipaddress.IPv4Network("192.168.1.0/24"), "adb")
        self.assertEqual(len(records), 2)
        self.assertEqual({record["status"] for record in records}, {"pairing_required", "approval_required"})
        self.assertEqual(run.call_args.args[0], ["adb", "mdns", "services"])
        self.assertEqual(run.call_args.kwargs["env"]["ADB_MDNS_AUTO_CONNECT"], "0")
        self.assertNotIn("ADB_SERVER_SOCKET", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["ANDROID_ADB_SERVER_PORT"], "5038")
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("tvcleaner.discovery._mdns", return_value=[])
    @patch("tvcleaner.discovery._tcp_5555", return_value=None)
    def test_scan_probes_only_requested_hosts_and_port(self, tcp, mdns):
        self.assertEqual(discover("192.168.1.0/24", adb_binary="adb"), [])
        self.assertEqual(tcp.call_count, 254)
        self.assertTrue(all(ipaddress.IPv4Address(call.args[0]) in ipaddress.IPv4Network("192.168.1.0/24") for call in tcp.call_args_list))
        mdns.assert_called_once_with(ipaddress.IPv4Network("192.168.1.0/24"), "adb", None)

    @patch("tvcleaner.discovery._mdns")
    @patch("tvcleaner.discovery._tcp_5555")
    def test_invalid_scan_never_starts_network_or_adb(self, tcp, mdns):
        with self.assertRaises(TransportError):
            discover("8.8.8.0/24")
        tcp.assert_not_called()
        mdns.assert_not_called()

    @patch("tvcleaner.discovery._mdns")
    @patch("tvcleaner.discovery._tcp_5555")
    def test_discovery_keeps_tls_pairing_and_connect_ports(self, tcp, mdns):
        tcp.return_value = {"endpoint": "192.168.1.50:5555", "address": "192.168.1.50", "port": 5555, "transport": "adb_tcp", "status": "unverified"}
        mdns.return_value = [{"endpoint": "192.168.1.50:37123", "address": "192.168.1.50", "port": 37123, "transport": "adb_tls_pairing", "status": "pairing_required"}]
        records = discover("192.168.1.50/32", adb_binary="adb")
        self.assertEqual([record["port"] for record in records], [5555, 37123])

    @patch("tvcleaner.discovery.subprocess.run")
    def test_roaming_skips_virtual_vpn_bridge_down_and_slave_interfaces(self, run):
        def interface(name, **extra):
            return dict({"ifname": name, "addr_info": [{"family": "inet", "local": "192.168.1.2", "prefixlen": 24}]}, **extra)
        data = [interface(name) for name in ("docker0", "br-123", "virbr0", "veth12", "tun0", "tap0", "wg0", "tailscale0", "lo", "eth0@if10")]
        data.extend([interface("eth1", linkinfo={"info_kind": "bridge"}),
                     interface("eth2", operstate="DOWN"), interface("eth3", master="br0"),
                     interface("eth4", linkinfo={"info_kind": "veth"}),
                     interface("enx123456", operstate="UP", link_type="ether"),
                     interface("wlp2s0", operstate="UP", link_type="ether")])
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(data), "")
        self.assertEqual([item["interface"] for item in local_networks()], ["enx123456", "wlp2s0"])

    @patch("tvcleaner.discovery.discover", return_value=[])
    @patch("tvcleaner.discovery.local_networks")
    def test_roaming_redetects_subnets_after_moving_the_pi(self, networks, discover_mock):
        networks.side_effect = [
            [{"cidr": "192.168.1.0/24", "interface": "eth0", "address": "192.168.1.2"}],
            [{"cidr": "192.168.50.0/24", "interface": "eth0", "address": "192.168.50.9"}],
        ]
        first = discover_networks("auto", adb_binary="adb", home="/tmp/private")
        second = discover_networks("auto", adb_binary="adb", home="/tmp/private")
        self.assertEqual(first["networks"][0]["cidr"], "192.168.1.0/24")
        self.assertEqual(second["networks"][0]["cidr"], "192.168.50.0/24")
        self.assertEqual([call.args[0] for call in discover_mock.call_args_list], ["192.168.1.0/24", "192.168.50.0/24"])
        self.assertEqual(networks.call_count, 2)

    @patch("tvcleaner.discovery.discover", return_value=[])
    @patch("tvcleaner.discovery.local_networks")
    def test_roaming_deduplicates_caps_networks_and_reports_bounded_ranges(self, networks, discover_mock):
        data = [{"cidr": f"192.168.{i}.0/24", "interface": f"eth{i}", "address": f"192.168.{i}.2", "bounded": i == 0} for i in range(6)]
        data.insert(1, dict(data[0], interface="wlan0"))
        networks.return_value = data
        result = discover_networks()
        self.assertEqual(discover_mock.call_count, 4)
        self.assertEqual(len(result["networks"]), 4)
        self.assertEqual(len(result["warnings"]), 2)

    @patch("tvcleaner.discovery.discover")
    @patch("tvcleaner.discovery.local_networks")
    def test_roaming_excludes_all_of_the_pi_addresses(self, networks, discover_mock):
        networks.return_value = [
            {"cidr": "192.168.1.0/24", "interface": "eth0", "address": "192.168.1.2"},
            {"cidr": "192.168.1.0/24", "interface": "wlan0", "address": "192.168.1.3"},
        ]
        discover_mock.return_value = [{"endpoint": f"192.168.1.{number}:5555", "address": f"192.168.1.{number}", "port": 5555} for number in (2, 3, 50)]
        self.assertEqual([item["address"] for item in discover_networks()["devices"]], ["192.168.1.50"])

    @patch("tvcleaner.discovery.discover", return_value=[])
    @patch("tvcleaner.discovery.local_networks", return_value=[])
    def test_no_network_is_visible_without_scanning_or_reconfiguring(self, networks, discover_mock):
        result = discover_networks()
        self.assertEqual(result["devices"], [])
        self.assertEqual(result["networks"], [])
        self.assertTrue(result["warnings"])
        discover_mock.assert_not_called()

    @patch("tvcleaner.discovery.discover", return_value=[])
    @patch("tvcleaner.discovery.local_networks")
    def test_manual_range_stays_explicit_and_does_not_roam(self, networks, discover_mock):
        result = discover_networks("192.168.20.5/24", adb_binary="adb")
        self.assertEqual(result["networks"][0]["cidr"], "192.168.20.0/24")
        discover_mock.assert_called_once_with("192.168.20.0/24", adb_binary="adb", home=None)
        networks.assert_not_called()
        with self.assertRaises(TransportError):
            discover_networks("0.0.0.0/0")


if __name__ == "__main__":
    unittest.main()

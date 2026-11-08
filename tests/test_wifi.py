"""Wi-Fi relay validation, interface selection and request isolation."""
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tvcleaner import wifi
from tvcleaner.storage import atomic_json


class WifiTests(unittest.TestCase):
    def test_only_usb_wireless_interfaces_are_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usb1/radio").mkdir(parents=True)
            (root / "platform/radio").mkdir(parents=True)
            for name, target in (("wlan0", root / "platform/radio"),
                                 ("wlan1", root / "usb1/radio"),
                                 ("eth0", root / "usb1/radio")):
                device = root / "net" / name
                device.mkdir(parents=True)
                (device / "device").symlink_to(target)
            (root / "net/wlan0/wireless").mkdir()
            (root / "net/wlan1/wireless").mkdir()
            self.assertEqual(wifi.usb_adapters(root / "net"), ["wlan1"])

    def test_scan_keeps_network_names_and_skips_own_hotspot(self):
        output = "PITV:100:WPA2\nCafe\\: TV:78:WPA2 WPA3\nCafe\\: TV:60:WPA2\n:25:WPA2\n"
        with mock.patch.object(wifi, "_interface", return_value="wlan1"), \
                mock.patch.object(wifi, "_nm", return_value=output), \
                mock.patch.object(wifi, "_read_managed", return_value=None):
            result = wifi.scan("wlan1")
        self.assertEqual(result["networks"], [{"ssid": "Cafe: TV", "signal": 78, "security": "WPA2 WPA3"}])

    def test_ap_does_not_replace_only_active_management_wifi(self):
        devices = {"wlan0": {"type": "wifi", "state": "connected", "connection": "Home Wi-Fi"}}
        with mock.patch.object(wifi, "_devices", return_value=devices), \
                mock.patch.object(wifi, "_read_managed", return_value=None), \
                mock.patch.object(wifi, "_apply_profile") as apply:
            with self.assertRaisesRegex(wifi.WifiError, "only network"):
                wifi.configure_ap(Path("/unused"), "PITV", "pitv12345")
            apply.assert_not_called()

    def test_ap_profile_shares_ipv4_and_validates_password(self):
        profile = wifi._profile(wifi.AP_ID, wifi.AP_UUID, "wlan0", "PITV", "pitv12345", hotspot=True)
        self.assertIn(b"method=shared", profile)
        self.assertIn(b"psk=pitv12345", profile)
        self.assertIn(b"mode=ap", profile)
        with self.assertRaises(wifi.WifiError):
            wifi._password("PIT1234")

    def test_usb_connection_keeps_password_out_of_commands_and_in_root_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "pitv-uplink.nmconnection"
            target.parent.chmod(0o700)
            network = {"networks": [{"ssid": "Home TV", "signal": 90, "security": "WPA2"}]}
            with mock.patch.object(wifi, "UPLINK_FILE", target), \
                    mock.patch.object(wifi, "AP_FILE", Path(directory) / "absent"), \
                    mock.patch.object(wifi, "_interface", return_value="wlan1"), \
                    mock.patch.object(wifi, "scan", return_value=network), \
                    mock.patch.object(wifi, "_devices", return_value={"wlan1": {"state": "disconnected"}}), \
                    mock.patch.object(wifi, "_nm", return_value="") as nm, \
                    mock.patch.object(wifi, "_write_public"), \
                    mock.patch.object(wifi, "status", return_value={"uplink": {"connected": True}}):
                self.assertTrue(wifi.connect(Path(directory), "wlan1", "Home TV", "private123")["uplink"]["connected"])
            self.assertIn("psk=private123", target.read_text())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertNotIn("private123", repr(nm.call_args_list))

    def test_failed_activation_restores_prior_uplink_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "pitv-uplink.nmconnection"
            old = wifi._profile(wifi.UPLINK_ID, wifi.UPLINK_UUID, "wlan1", "Prior TV", "priorpass8")
            target.write_bytes(old)
            with mock.patch.object(wifi, "_nm", side_effect=["", wifi.WifiError("activation failed"), "", ""]):
                with self.assertRaises(wifi.WifiError):
                    wifi._apply_profile(target, b"# Managed by PiTV. Configure this network in the PiTV web interface.\nnew", wifi.UPLINK_UUID, "wlan1", previous_connection="Prior TV")
            self.assertEqual(target.read_bytes(), old)

    def test_request_route_cannot_be_overridden_by_json_fields(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(wifi, "_interface", return_value="wlan1"):
            root = Path(directory)
            written = {}
            def capture(path, value):
                atomic_json(path, value)
                if path.name == wifi.REQUEST:
                    written.update(value)
                    atomic_json(root / wifi.RESULT, {"id": value["id"], "state": "done", "result": {"networks": []}})
            with mock.patch.object(wifi, "atomic_json", side_effect=capture):
                self.assertEqual(wifi.request(root, "scan", {"interface": "wlan1", "action": "ap"}), {"networks": []})
            self.assertEqual(written["action"], "scan")


if __name__ == "__main__":
    unittest.main()

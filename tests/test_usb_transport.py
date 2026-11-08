"""USB selection and real-subprocess bridge tests; no physical ADB is contacted."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tvcleaner.transport import (
    AdbTransport, TransportError, adb_environment, parse_usb_devices,
    upstream_endpoint, upstream_environment, upstream_record_tag, validate_endpoint,
)

ROOT = Path(__file__).resolve().parents[1]
USB = "usb:TV-TEST-1234"
SERIAL = USB[4:]
LIST = f"List of devices attached\n{SERIAL} device usb:1-1.3 product:tv model:Example_TV device:tv transport_id:7\n"


def result(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(["adb"], returncode, stdout, stderr)


class UsbEndpointTests(unittest.TestCase):
    def test_usb_endpoint_is_explicit_and_strict(self):
        for serial in ("ABC123", "TV-TEST-1234", "vendor.serial_2"):
            self.assertEqual(validate_endpoint("usb:" + serial), "usb:" + serial)
        for endpoint in ("usb:", "USB:ABC123", "usb:-s", "usb:a:b", "usb:a/b", "usb:a b",
                         "usb:ABC\n", "usb:a;id", "usb:$(id)", "usb:é", "usb:emulator-5554",
                         "usb:" + "a" * 129, "usb:\x00"):
            with self.subTest(endpoint=endpoint), self.assertRaises(TransportError):
                validate_endpoint(endpoint)

    def test_upstream_mapping_is_stable_unique_and_never_a_real_address(self):
        self.assertRegex(upstream_endpoint(USB), r"^usb-[a-f0-9]{32}\.invalid:5555$")
        self.assertEqual(upstream_endpoint(USB), upstream_endpoint(USB))
        self.assertNotEqual(upstream_endpoint(USB), upstream_endpoint("usb:OTHER"))
        self.assertEqual(upstream_endpoint("192.168.1.2"), "192.168.1.2:5555")
        self.assertEqual(upstream_record_tag("192.168.1.2:43210"), "192.168.1.2")
        self.assertEqual(upstream_record_tag(USB), upstream_endpoint(USB).replace(":", "_"))

    def test_record_tag_agrees_with_actual_upstream_naming(self):
        spec = importlib.util.spec_from_file_location("usb_test_recovery", ROOT / "vendor/cleanyourtv/tools/recovery.py")
        recovery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recovery)
        list_path = Path("/tmp/usb-test/batch.txt")
        for endpoint in (USB, "192.168.1.50:5555", "10.1.2.3:40123"):
            actual = recovery.record_path(list_path, upstream_endpoint(endpoint))
            expected = f"recovery-{upstream_record_tag(endpoint)}-{recovery.list_digest(list_path)}.json"
            self.assertEqual(actual.name, expected)

    def test_upstream_wrapper_requires_system_adb_and_trusted_script(self):
        environment = upstream_environment(USB, "/usr/bin/adb", ROOT)
        self.assertEqual(environment["TVCLEANER_USB_TARGET"], USB)
        self.assertEqual(environment["TV_HOST"], upstream_endpoint(USB))
        self.assertEqual(environment["TV_ADB_BINARY"], str(ROOT / "scripts/adb-usb-wrapper.py"))
        for binary in ("/bin/sh", "/tmp/arbitrary-adb", "adb --evil"):
            with self.subTest(binary=binary), self.assertRaises(TransportError):
                upstream_environment(USB, binary, ROOT)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TransportError, "wrapper"):
                upstream_environment(USB, "/usr/bin/adb", Path(directory))

    def test_network_environment_cannot_inherit_usb_or_remote_server_settings(self):
        with patch.dict(os.environ, {
            "ADB_SERVER_SOCKET": "tcp:evil.example:5037", "ANDROID_ADB_SERVER_ADDRESS": "evil.example",
            "ANDROID_ADB_SERVER_PORT": "9999", "ANDROID_SERIAL": "OTHER",
            "TVCLEANER_USB_TARGET": USB, "TVCLEANER_USB_BINARY": "/tmp/evil",
            "ADB_VENDOR_KEYS": "/tmp/other-key",
        }):
            environment = adb_environment("/tmp/private-tv-home")
            environment.update(upstream_environment("192.168.1.50", "/usr/bin/adb", ROOT))
        self.assertNotIn("ADB_SERVER_SOCKET", environment)
        self.assertEqual(environment["ANDROID_ADB_SERVER_PORT"], "5038")
        self.assertEqual(environment["TV_HOST"], "192.168.1.50:5555")
        self.assertFalse(any(key.startswith("TVCLEANER_USB_") for key in environment))
        for key in ("ANDROID_ADB_SERVER_ADDRESS", "ANDROID_SERIAL", "ADB_VENDOR_KEYS"):
            self.assertNotIn(key, environment)


class UsbEnumerationTests(unittest.TestCase):
    def test_only_usb_bus_backed_devices_are_returned(self):
        devices = parse_usb_devices(LIST + """192.168.1.50:5555 device product:tv model:TV transport_id:8
emulator-5554 device product:sdk model:sdk transport_id:9
no-bus device product:tv model:TV transport_id:10
10.1.2.3:5555 device usb:1-2 product:fake
bad;id device usb:1-4
wrong-path device usb:../../devices
double-path device usb:1-2 usb:1-3
""")
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["endpoint"], USB)
        self.assertEqual(devices[0]["transport"], "usb")
        self.assertEqual(devices[0]["state"], "device")
        self.assertEqual(devices[0]["status"], "authorized")

    def test_authorization_offline_and_permissions_remain_visible(self):
        devices = parse_usb_devices("""List of devices attached
TV1 unauthorized usb:1-1 transport_id:1
TV2 offline usb:1-2 transport_id:2
???????????? no permissions (missing udev rules) usb:1-3 transport_id:3
TV4 recovery usb:1-4 transport_id:4
""")
        self.assertEqual([item["status"] for item in devices], ["approval_required", "offline", "permission_required", "unavailable"])
        self.assertIsNone(devices[2]["endpoint"])
        self.assertEqual(devices[2]["state"], "no_permissions")

    def test_duplicate_serials_cannot_select_one_of_two_devices(self):
        devices = parse_usb_devices("TV1 device usb:1-1\nTV1 device usb:1-2\n")
        self.assertEqual(len(devices), 2)
        self.assertTrue(all(item["endpoint"] is None and item["state"] == "ambiguous" for item in devices))

    def test_enumeration_output_is_bounded(self):
        for text in (None, "x" * (1024 * 1024 + 1), "\n".join(f"TV{i} device usb:1-{i+1}" for i in range(65))):
            with self.assertRaises(TransportError):
                parse_usb_devices(text)


class UsbAdbCallTests(unittest.TestCase):
    def setUp(self):
        self.transport = AdbTransport(adb_binary="adb")

    @patch("tvcleaner.transport.subprocess.run")
    def test_usb_connect_checks_usb_state_without_network_connect(self, run):
        run.side_effect = [result(LIST), result("device\n")]
        self.assertIn("authorized", self.transport.connect(USB))
        self.assertEqual([call.args[0] for call in run.call_args_list], [["adb", "devices", "-l"], ["adb", "-s", SERIAL, "get-state"]])

    @patch("tvcleaner.transport.subprocess.run")
    def test_usb_shell_always_explicitly_selects_enumerated_serial(self, run):
        run.side_effect = [result(LIST), result("TV model\n")]
        self.assertEqual(self.transport.shell(USB, "getprop ro.product.model"), "TV model\n")
        self.assertEqual(run.call_args.args[0], ["adb", "-s", SERIAL, "shell", "getprop ro.product.model"])
        self.assertNotIn("ADB_SERVER_SOCKET", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["ANDROID_ADB_SERVER_PORT"], "5038")
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("tvcleaner.transport.subprocess.run")
    def test_usb_rechecks_every_operation_and_blocks_disconnect(self, run):
        run.side_effect = [result(LIST), result("ok"), result("List of devices attached\n")]
        self.transport.shell(USB, "getprop")
        with self.assertRaisesRegex(TransportError, "missing"):
            self.transport.shell(USB, "getprop")
        self.assertEqual(len(run.call_args_list), 3)

    @patch("tvcleaner.transport.subprocess.run")
    def test_unready_and_network_only_serials_never_receive_shell(self, run):
        for listing in (LIST.replace(" device ", " unauthorized "), LIST.replace(" device ", " offline "),
                        LIST.replace(" device ", " recovery "), LIST.replace("usb:1-1.3 ", "")):
            run.reset_mock()
            run.return_value = result(listing)
            with self.subTest(listing=listing), self.assertRaises(TransportError):
                self.transport.shell(USB, "pm disable-user --user 0 com.example.ads")
            self.assertEqual(run.call_args.args[0], ["adb", "devices", "-l"])
            self.assertEqual(run.call_count, 1)

    @patch("tvcleaner.transport.subprocess.run")
    def test_usb_pair_and_malformed_shell_never_start_adb(self, run):
        with self.assertRaises(TransportError):
            self.transport.pair(USB, "123456")
        with self.assertRaises(TransportError):
            self.transport.shell(USB, "getprop\nrm")
        run.assert_not_called()

    @patch("tvcleaner.transport.subprocess.run")
    def test_usb_screenshot_uses_same_serial(self, run):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRuoAAAAASUVORK5CYII=")
        run.side_effect = [result(LIST), result(png, b"")]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "shot.png"
            self.transport.screenshot(USB, destination)
            self.assertEqual(destination.read_bytes(), png)
            self.assertEqual(run.call_args.args[0], ["adb", "-s", SERIAL, "exec-out", "screencap", "-p"])


class UsbBridgeSubprocessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.log = self.folder / "adb-calls.jsonl"
        self.fake = self.folder / "adb"
        self.fake.write_text("""#!/usr/bin/python3
import json,os,sys
with open(os.environ['TEST_ADB_LOG'],'a') as handle:
    handle.write(json.dumps({'args':sys.argv[1:],'socket':os.environ.get('ADB_SERVER_SOCKET'),
                             'port':os.environ.get('ANDROID_ADB_SERVER_PORT'),
                             'usb_override':os.environ.get('TVCLEANER_USB_TARGET')})+'\\n')
if sys.argv[1:]==['devices','-l']:
    print(os.environ['TEST_USB_LIST'])
elif sys.argv[1:]==['-s','TV-TEST-1234','get-state']:
    print('device')
elif sys.argv[1:]==['-s','TV-TEST-1234','shell','-T','-x','getprop ro.product.model']:
    print('Fixture TV')
else:
    raise SystemExit(23)
""", encoding="utf-8")
        self.fake.chmod(0o700)
        self.environment = os.environ.copy()
        self.environment.update({"HOME": str(self.folder), "TEST_ADB_LOG": str(self.log),
                                 "TEST_USB_LIST": LIST, "TVCLEANER_USB_TARGET": USB,
                                 "TV_HOST": upstream_endpoint(USB),
                                 "ADB_SERVER_SOCKET": "tcp:evil.example:5037",
                                 "TVCLEANER_USB_BINARY": "/tmp/not-allowed", "TV_ADB_BINARY": "/tmp/not-allowed"})

    def invoke(self, args, **environment):
        env = dict(self.environment, **environment)
        # Only the Python fixture entry point injects the fake executable; the
        # shipped script never accepts such an argument or environment override.
        harness = "import sys;sys.path.insert(0,sys.argv.pop(1));from tvcleaner.usb_bridge import main;raise SystemExit(main(sys.argv[2:],_native_adb=sys.argv[1]))"
        return subprocess.run([sys.executable, "-I", "-c", harness, str(ROOT), str(self.fake), *args],
                              env=env, capture_output=True, text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_real_bridge_subprocess_forwards_only_mapped_usb_serial(self):
        response = self.invoke(["-s", upstream_endpoint(USB), "shell", "-T", "-x", "getprop ro.product.model"])
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(response.stdout.strip(), "Fixture TV")
        calls = self.calls()
        self.assertEqual([call["args"] for call in calls], [["devices", "-l"], ["-s", SERIAL, "shell", "-T", "-x", "getprop ro.product.model"]])
        self.assertTrue(all(call["socket"] is None and call["port"] == "5038" and call["usb_override"] is None for call in calls))

    def test_get_state_is_supported(self):
        response = self.invoke(["-s", upstream_endpoint(USB), "get-state"])
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(response.stdout.strip(), "device")

    def test_other_target_and_noncleanup_adb_commands_are_rejected_before_adb(self):
        for args in (["-s", "192.168.1.50:5555", "get-state"],
                     ["-s", upstream_endpoint(USB), "root"],
                     ["-s", upstream_endpoint(USB), "reboot"],
                     ["-s", upstream_endpoint(USB), "forward", "tcp:1234", "tcp:5555"],
                     ["connect", "192.168.1.50:5555"],
                     ["-s", upstream_endpoint(USB), "shell", "getprop\nreboot"]):
            with self.subTest(args=args):
                self.assertEqual(self.invoke(args).returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_unapproved_usb_cannot_receive_guarded_upstream_shell(self):
        response = self.invoke(["-s", upstream_endpoint(USB), "shell", "-T", "-x", "getprop ro.product.model"],
                               TEST_USB_LIST=LIST.replace(" device ", " unauthorized "))
        self.assertEqual(response.returncode, 2)
        self.assertEqual([call["args"] for call in self.calls()], [["devices", "-l"]])

    def test_missing_usb_evidence_never_falls_back_to_network(self):
        response = self.invoke(["-s", upstream_endpoint(USB), "get-state"],
                               TEST_USB_LIST=LIST.replace("usb:1-1.3 ", ""))
        self.assertEqual(response.returncode, 2)
        self.assertEqual(len(self.calls()), 1)

    def test_missing_mapping_and_home_are_rejected(self):
        args = ["-s", upstream_endpoint(USB), "get-state"]
        for changes in ({"TVCLEANER_USB_TARGET": ""}, {"TVCLEANER_USB_TARGET": "usb:bad;id"},
                        {"TV_HOST": "192.168.1.50:5555"}, {"HOME": ""}, {"HOME": "relative"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.invoke(args, **changes).returncode, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()

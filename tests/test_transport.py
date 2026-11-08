from __future__ import annotations

import base64
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tvcleaner.transport import AdbTransport, TransportError, adb_environment, validate_endpoint

ENDPOINT = "192.168.1.50:5555"
PROPERTIES = {
    "ro.product.manufacturer": "Example TV", "ro.product.brand": "Example",
    "ro.product.model": "Living Room TV", "ro.product.device": "example_tv",
    "ro.product.name": "example_product", "ro.build.version.release": "12",
    "ro.build.version.sdk": "31", "ro.build.display.id": "EXAMPLE.2026.1",
    "ro.build.fingerprint": "example/tv/tv:12/EXAMPLE/123:user/release-keys",
    "ro.serialno": "TEST-SERIAL-NOT-A-DEVICE",
}
PACKAGES = {
    "android", "com.android.settings", "com.android.systemui", "com.vendor.home",
    "com.vendor.otherhome", "com.vendor.input", "com.vendor.ime", "com.vendor.otherime",
    "com.vendor.ads", "org.videolan.vlc", "com.vendor.disabled",
}
HOME_QUERY = "resolve-activity --brief --user 0 -a android.intent.action.MAIN -c android.intent.category.HOME"
HOME_CANDIDATES = "cmd package query-activities --brief --user 0 -a android.intent.action.MAIN -c android.intent.category.HOME"
INPUT_QUERY = "cmd package query-services --brief --user 0 -a android.media.tv.TvInputService"


def outputs():
    return {
        "getprop": "\n".join(f"[{key}]: [{value}]" for key, value in PROPERTIES.items()),
        "pm list packages --user 0": "\n".join("package:" + name for name in sorted(PACKAGES)),
        "pm list packages -d --user 0": "package:com.vendor.disabled\n",
        "pm list packages -3 --user 0": "package:org.videolan.vlc\n",
        "settings list global": "airplane_mode_on=0\n",
        "settings list secure": "android_id=0123456789abcdef\ndefault_input_method=com.vendor.ime/.Keyboard\nenabled_input_methods=com.vendor.ime/.Keyboard:com.vendor.otherime/.Keyboard;123\n",
        "settings list system": "screen_brightness=100\n",
        "pm list features": "feature:android.software.leanback\nfeature:reqGlEsVersion=0x20000\n",
        "cmd package " + HOME_QUERY: "priority=0 preferredOrder=0 match=0x108000\ncom.vendor.home/.MainActivity\n",
        "pm " + HOME_QUERY: "com.vendor.home/.MainActivity\n",
        HOME_CANDIDATES: "com.vendor.home/.MainActivity\ncom.vendor.otherhome/.MainActivity\n",
        "ime list -a -s": "com.vendor.ime/.Keyboard\ncom.vendor.otherime/.Keyboard\n",
        INPUT_QUERY: "com.vendor.input/.HdmiInputService\n",
        "dumpsys tv_input": "TV INPUT MANAGER (dumpsys tv_input)\nTvInputInfo{id=com.vendor.input/.HdmiInputService}\n",
    }


def result(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(["adb"], returncode, stdout, stderr)


class EndpointTests(unittest.TestCase):
    def test_canonical_private_endpoints(self):
        self.assertEqual(validate_endpoint("192.168.1.50"), ENDPOINT)
        self.assertEqual(validate_endpoint("10.1.2.3:37123"), "10.1.2.3:37123")
        self.assertEqual(validate_endpoint("172.31.0.4:5555"), "172.31.0.4:5555")
        self.assertEqual(validate_endpoint("169.254.4.1:5555"), "169.254.4.1:5555")

    def test_public_loopback_hostname_url_and_injection_rejected(self):
        invalid = ["127.0.0.1", "8.8.8.8", "192.0.2.1", "172.32.1.1", "100.64.1.1",
                   "0.0.0.0", "224.0.0.1", "tv.local", "https://192.168.1.1", "::1",
                   "192.168.1.50:0", "192.168.1.50:65536", "192.168.1.50:5555;id",
                   "192.168.1.50\n", "192.168.1.50:5555:6666", "192.168.001.1", None]
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(TransportError):
                validate_endpoint(endpoint)

    def test_environment_does_not_redirect_adb_or_auto_connect(self):
        with patch.dict("os.environ", {"ADB_SERVER_SOCKET": "tcp:example.com:5037", "ADB_VENDOR_KEYS": "/wrong"}):
            environment = adb_environment("/tmp/example-private-home")
        self.assertEqual(environment["HOME"], str(Path("/tmp/example-private-home").resolve()))
        self.assertEqual(environment["ANDROID_USER_HOME"], str(Path("/tmp/example-private-home/.android").resolve()))
        self.assertEqual(environment["ADB_MDNS_AUTO_CONNECT"], "0")
        self.assertNotIn("ADB_SERVER_SOCKET", environment)
        self.assertEqual(environment["ANDROID_ADB_SERVER_PORT"], "5038")
        self.assertNotIn("ADB_VENDOR_KEYS", environment)


class AdbCallTests(unittest.TestCase):
    def setUp(self):
        self.transport = AdbTransport(adb_binary="adb")

    @patch("tvcleaner.transport.subprocess.run")
    def test_pairing_secret_is_stdin_only(self, run):
        run.return_value = result("Enter pairing code: Successfully paired to " + ENDPOINT + "\n")
        response = self.transport.pair(ENDPOINT, "654321")
        self.assertNotIn("654321", response)
        self.assertEqual(run.call_args.args[0], ["adb", "pair", ENDPOINT])
        self.assertEqual(run.call_args.kwargs["input"], "654321\n")
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("tvcleaner.transport.subprocess.run")
    def test_pairing_failure_never_leaks_echoed_code(self, run):
        run.return_value = result("Entered secret: 654321", returncode=1)
        with self.assertRaises(TransportError) as error:
            self.transport.pair(ENDPOINT, "654321")
        self.assertNotIn("654321", str(error.exception))

    @patch("tvcleaner.transport.subprocess.run")
    def test_invalid_pairing_code_never_starts_process(self, run):
        for code in ("12345", "1234567", "123456\n", "12;pwd", 123456, "１２３４５６"):
            with self.subTest(code=code), self.assertRaises(TransportError):
                self.transport.pair(ENDPOINT, code)
        run.assert_not_called()

    @patch("tvcleaner.transport.subprocess.run")
    def test_connect_requires_device_authorization(self, run):
        run.side_effect = [result("connected to " + ENDPOINT), result("unauthorized")]
        with self.assertRaisesRegex(TransportError, "not authorized"):
            self.transport.connect(ENDPOINT)

    @patch("tvcleaner.transport.subprocess.run")
    def test_connect_handles_adb_zero_exit_failure(self, run):
        run.return_value = result("failed to connect to " + ENDPOINT)
        with self.assertRaises(TransportError):
            self.transport.connect(ENDPOINT)
        self.assertEqual(run.call_count, 1)

    @patch("tvcleaner.transport.subprocess.run")
    def test_connect_success_verifies_state(self, run):
        run.side_effect = [result("already connected to " + ENDPOINT), result("device\n")]
        self.assertIn("authorized", self.transport.connect(ENDPOINT))
        self.assertEqual(run.call_args.args[0], ["adb", "-s", ENDPOINT, "get-state"])

    @patch("tvcleaner.transport.subprocess.run")
    def test_timeouts_and_command_errors_normalized(self, run):
        for failure in (subprocess.TimeoutExpired("adb", 20, output="private device data"), FileNotFoundError()):
            run.side_effect = failure
            with self.assertRaises(TransportError) as error:
                self.transport.shell(ENDPOINT, "getprop")
            self.assertNotIn("private device data", str(error.exception))

    @patch("tvcleaner.transport.subprocess.run")
    def test_invalid_endpoint_cannot_start_adb(self, run):
        with self.assertRaises(TransportError):
            self.transport.shell("example.com:5555", "getprop")
        run.assert_not_called()

    @patch("tvcleaner.transport.subprocess.run")
    def test_remote_error_on_zero_exit_is_rejected(self, run):
        run.return_value = result("Error: permission denied\n")
        with self.assertRaises(TransportError):
            self.transport.shell(ENDPOINT, "settings list secure")

    @patch("tvcleaner.transport.subprocess.run")
    def test_screenshot_saved_private_and_validated(self, run):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRuoAAAAASUVORK5CYII=")
        run.return_value = result(png, b"")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shots" / "after.png"
            self.transport.screenshot(ENDPOINT, path)
            self.assertEqual(path.read_bytes(), png)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            run.return_value = result(b"error: screenshot failed", b"")
            with self.assertRaises(TransportError):
                self.transport.screenshot(ENDPOINT, path)
            self.assertEqual(path.read_bytes(), png)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.transport = AdbTransport(adb_binary="adb")
        self.output = outputs()
        self.calls = []

    def shell(self, endpoint, command):
        self.assertEqual(endpoint, ENDPOINT)
        self.calls.append(command)
        value = self.output[command]
        if isinstance(value, Exception):
            raise value
        return value

    def snapshot(self):
        with patch.object(self.transport, "shell", side_effect=self.shell):
            return self.transport.snapshot(ENDPOINT)

    def test_snapshot_preserves_backups_and_dynamic_protection(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["identity"]["family"], "android_tv")
        self.assertEqual(snapshot["identity"]["fingerprint"], PROPERTIES["ro.build.fingerprint"])
        self.assertEqual(set(snapshot["packages"]), PACKAGES)
        self.assertEqual(set(snapshot["protected"]), {"com.vendor.home", "com.vendor.otherhome", "com.vendor.ime", "com.vendor.otherime", "com.vendor.input"})
        for name in ("getprop", "packages", "disabled", "settings_global", "settings_secure", "settings_system", "features", "home"):
            self.assertIn(name, snapshot["raw"])
        self.assertFalse(any(word in command for command in self.calls for word in ("disable", "uninstall", "start-activity", "tcpip", "root")))

    def test_recovery_identity_ignores_broken_home_keyboard_and_input(self):
        self.output["cmd package " + HOME_QUERY] = "No activity found"
        self.output["pm " + HOME_QUERY] = "No activity found"
        self.output["ime list -a -s"] = TransportError("keyboard unavailable")
        self.output[INPUT_QUERY] = TransportError("TV input unavailable")
        with patch.object(self.transport, "shell", side_effect=self.shell):
            result = self.transport.identity_snapshot(ENDPOINT)
        self.assertEqual(result["identity"]["fingerprint"], PROPERTIES["ro.build.fingerprint"])
        self.assertEqual(self.calls, ["getprop", "settings list secure", "pm list features"])
        with self.assertRaises(TransportError):
            self.snapshot()

    def test_recovery_identity_requires_stable_device_id(self):
        self.output["getprop"] = self.output["getprop"].replace("[TEST-SERIAL-NOT-A-DEVICE]", "[unknown]")
        self.output["settings list secure"] = "android_id=0000000000000000\n"
        with patch.object(self.transport, "shell", side_effect=self.shell):
            with self.assertRaisesRegex(TransportError, "stable device ID"):
                self.transport.identity_snapshot(ENDPOINT)

    def test_recovery_identity_still_requires_tv_features(self):
        self.output["pm list features"] = "feature:android.hardware.telephony\n"
        with patch.object(self.transport, "shell", side_effect=self.shell):
            with self.assertRaisesRegex(TransportError, "did not identify"):
                self.transport.identity_snapshot(ENDPOINT)

    def test_recovery_identity_still_requires_firmware(self):
        self.output["getprop"] = "\n".join(line for line in self.output["getprop"].splitlines() if "fingerprint" not in line)
        with patch.object(self.transport, "shell", side_effect=self.shell):
            with self.assertRaisesRegex(TransportError, "identity or firmware"):
                self.transport.identity_snapshot(ENDPOINT)

    def test_recovery_and_cleanup_use_identical_fire_os_identity(self):
        self.output["getprop"] = self.output["getprop"].replace("[Example TV]", "[Amazon]")
        before = self.snapshot()
        with patch.object(self.transport, "shell", side_effect=self.shell):
            recovery = self.transport.identity_snapshot(ENDPOINT)
        self.assertEqual(before["identity"], recovery["identity"])
        self.assertEqual(recovery["identity"]["family"], "fire_os")

    def test_android_phone_is_refused(self):
        self.output["pm list features"] = "feature:android.hardware.telephony\n"
        with self.assertRaisesRegex(TransportError, "did not identify"):
            self.snapshot()

    def test_usb_phone_still_requires_television_features(self):
        self.output["pm list features"] = "feature:android.hardware.telephony\n"
        with patch.object(self.transport, "shell", side_effect=lambda endpoint, command: self.output[command]):
            with self.assertRaisesRegex(TransportError, "did not identify"):
                self.transport.snapshot("usb:TEST-PHONE")

    def test_fire_tv_requires_tv_evidence(self):
        self.output["getprop"] = self.output["getprop"].replace("[Example TV]", "[Amazon]")
        self.output["pm list features"] = "feature:amazon.hardware.fire_tv\n"
        self.assertEqual(self.snapshot()["identity"]["family"], "fire_os")
        self.output["pm list features"] = "feature:android.hardware.touchscreen\n"
        with self.assertRaises(TransportError):
            self.snapshot()

    def test_partial_or_failed_installed_inventory_is_refused(self):
        for output in ("", "package:com.vendor.home\nError: package manager failed\n", "package:com.vendor.home\ntruncated"):
            self.output["pm list packages --user 0"] = output
            with self.subTest(output=output), self.assertRaises(TransportError):
                self.snapshot()

    def test_inventory_disagreement_is_refused(self):
        self.output["pm list packages -d --user 0"] = "package:com.absent.package\n"
        with self.assertRaisesRegex(TransportError, "inventories disagree"):
            self.snapshot()

    def test_empty_disabled_and_third_party_inventories_are_valid(self):
        self.output["pm list packages -d --user 0"] = ""
        self.output["pm list packages -3 --user 0"] = ""
        snapshot = self.snapshot()
        self.assertEqual(snapshot["disabled"], [])
        self.assertEqual(snapshot["third_party"], [])

    def test_missing_firmware_fingerprint_is_refused(self):
        self.output["getprop"] = "\n".join(line for line in self.output["getprop"].splitlines() if "fingerprint" not in line)
        with self.assertRaisesRegex(TransportError, "identity or firmware"):
            self.snapshot()

    def test_incomplete_settings_backup_is_refused(self):
        for output in ("", "Permission denied\n", "some_key=1\ntruncated value"):
            self.output["settings list secure"] = output
            with self.subTest(output=output), self.assertRaises(TransportError):
                self.snapshot()

    def test_unresolved_home_is_refused(self):
        self.output["cmd package " + HOME_QUERY] = "android/com.android.internal.app.ResolverActivity\n"
        with self.assertRaisesRegex(TransportError, "Home app"):
            self.snapshot()

    def test_disabled_home_is_refused(self):
        self.output["pm list packages -d --user 0"] = "package:com.vendor.home\n"
        with self.assertRaisesRegex(TransportError, "Home app"):
            self.snapshot()

    def test_old_android_home_query_falls_back_without_mutation(self):
        self.output["cmd package " + HOME_QUERY] = TransportError("command not available")
        self.assertEqual(self.snapshot()["home"], "com.vendor.home/.MainActivity")
        self.assertIn("pm " + HOME_QUERY, self.calls)

    def test_keyboard_settings_protect_keyboard_if_ime_command_unavailable(self):
        self.output["ime list -a -s"] = TransportError("command not available")
        self.assertIn("com.vendor.ime", self.snapshot()["protected"])

    def test_missing_keyboard_identity_is_refused(self):
        self.output["ime list -a -s"] = ""
        self.output["settings list secure"] = "android_id=0123456789abcdef\n"
        with self.assertRaisesRegex(TransportError, "keyboard"):
            self.snapshot()

    def test_tv_input_registry_fallback_protects_input(self):
        self.output[INPUT_QUERY] = TransportError("command not available")
        self.assertIn("com.vendor.input", self.snapshot()["protected"])

    def test_streaming_stick_can_have_no_input_services(self):
        self.output[INPUT_QUERY] = "No services found\n"
        self.output["dumpsys tv_input"] = "TV INPUT MANAGER (dumpsys tv_input)\n mInputMap: {}\n"
        self.assertEqual(self.snapshot()["identity"]["family"], "android_tv")

    def test_missing_tv_input_registry_is_refused(self):
        self.output[INPUT_QUERY] = "No services found\n"
        self.output["dumpsys tv_input"] = "Can't find service: tv_input\n"
        with self.assertRaises(TransportError):
            self.snapshot()


if __name__ == "__main__":
    unittest.main()

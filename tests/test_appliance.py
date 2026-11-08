"""Headless decisions and owner-approved firmware recipes, with no hardware access."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tvcleaner.demo import DemoTransport
from tvcleaner.recipes import CHECK_FIELDS
from tvcleaner.service import CleanerService, ServiceError, device_key
from tvcleaner.storage import atomic_json, read_json

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "192.168.1.50:5555"
PROFILE = "xiaomi-mi-tv-4a/debloat.txt"
PACKAGE = "com.miui.tv.analytics"
CHECKS = {key: True for key in CHECK_FIELDS}


class PortableTV(DemoTransport):
    def __init__(self):
        super().__init__()
        self.serial = "OWNER-TEST-001"
        self.firmware = None
        self.user_apps = []

    def snapshot(self, endpoint):
        snapshot = super().snapshot(endpoint)
        snapshot["identity"]["serial"] = self.serial
        if self.firmware:
            snapshot["identity"]["fingerprint"] = self.firmware
        snapshot["packages"] = sorted(snapshot["packages"] + self.user_apps)
        snapshot["third_party"] = sorted(self.user_apps)
        return snapshot

    def usb_devices(self):
        return [{"endpoint": "usb:OWNER-TEST-001", "transport": "usb", "state": "device", "status": "authorized"}]


class ApplianceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tv = PortableTV()
        self.service = CleanerService(ROOT, self.tmp.name, demo=True, transport=self.tv)

    def cleanup(self):
        plan = self.service.plan(ENDPOINT, PROFILE)
        return self.service.apply(plan["id"], [PACKAGE])

    def recipe(self):
        run = self.cleanup()
        self.service.restore(run["id"], ENDPOINT)
        public = self.service.create_recipe(run["id"], CHECKS, "Simulation of the owner's functional checks.")
        return public, run

    def arm(self, mode="auto"):
        return self.service.set_automatic(True, "", mode, True, "recipes")

    def test_recipe_requires_restore_test(self):
        run = self.cleanup()
        with self.assertRaisesRegex(ServiceError, "restore test"):
            self.service.create_recipe(run["id"], CHECKS, "")

    def test_recipe_requires_all_actual_checks(self):
        run = self.cleanup()
        self.service.restore(run["id"], ENDPOINT)
        checks = dict(CHECKS, reboot=False)
        with self.assertRaises(ValueError):
            self.service.create_recipe(run["id"], checks, "Not rebooted yet")

    def test_tampered_cleanup_evidence_cannot_approve_recipe(self):
        run = self.cleanup()
        self.service.restore(run["id"], ENDPOINT)
        path = self.service.runs_dir / run["id"] / "after.json"
        snapshot = read_json(path)
        snapshot["home"] = "com.example.changed/.Home"
        atomic_json(path, snapshot)
        with self.assertRaisesRegex(ServiceError, "evidence"):
            self.service.create_recipe(run["id"], CHECKS, "")

    def test_restoration_with_unrelated_state_changes_cannot_approve_recipe(self):
        run = self.cleanup()
        self.tv.disabled.add("com.xiaomi.mimusic2")
        self.service.restore(run["id"], ENDPOINT)
        with self.assertRaisesRegex(ServiceError, "exact original state"):
            self.service.create_recipe(run["id"], CHECKS, "")

    def test_new_matching_device_cleans_without_individual_enrollment(self):
        recipe, _ = self.recipe()
        self.tv.serial = "NEW-OWNED-TV-002"
        self.arm()
        result = self.service.automatic_pass()
        self.assertEqual(result["results"][0]["status"], "completed")
        self.assertEqual(self.tv.disabled, {PACKAGE})
        self.assertEqual(self.service.config["enrollments"], {})
        self.assertEqual(self.service.status()["appliance"]["phase"], "complete")

    def test_same_recipe_is_idempotent(self):
        self.recipe()
        self.arm()
        self.service.automatic_pass()
        count = len(self.service.list_runs()["runs"])
        result = self.service.automatic_pass()
        self.assertEqual(result["results"][0]["status"], "already_clean")
        self.assertEqual(len(self.service.list_runs()["runs"]), count)

    def test_new_firmware_waits_for_review(self):
        self.recipe()
        self.tv.firmware = "DEMO/xiaomi/tarzan:11/new-build:user/release-keys"
        self.arm()
        result = self.service.automatic_pass()
        self.assertEqual(result["results"][0]["status"], "review_required")
        self.assertFalse(self.tv.disabled)

    def test_user_installed_app_does_not_expand_cleanup(self):
        self.recipe()
        self.tv.user_apps = ["com.example.familyapp"]
        self.arm()
        result = self.service.automatic_pass()
        self.assertEqual(result["results"][0]["status"], "completed")
        self.assertEqual(self.tv.disabled, {PACKAGE})

    def test_disabled_recipe_cannot_apply(self):
        recipe, _ = self.recipe()
        self.service.set_recipe_enabled(recipe["id"], False)
        self.arm()
        result = self.service.automatic_pass()
        self.assertEqual(result["results"][0]["status"], "review_required")
        self.assertFalse(self.tv.disabled)

    def test_recipe_revoked_after_plan_stops_mutation(self):
        recipe, _ = self.recipe()
        self.arm()
        plan = self.service.plan
        def revoke(*args):
            result = plan(*args)
            self.service.set_recipe_enabled(recipe["id"], False)
            return result
        with patch.object(self.service, "plan", side_effect=revoke):
            result = self.service.automatic_pass()
        self.assertEqual(result["results"][0]["status"], "stopped")
        self.assertFalse(self.tv.disabled)

    def test_duplicate_matching_recipes_require_review(self):
        self.recipe()
        self.recipe()
        self.arm()
        result = self.service.automatic_pass()
        self.assertEqual(result["results"][0]["status"], "ambiguous_recipes")
        self.assertFalse(self.tv.disabled)

    def test_different_eligible_tvs_do_not_receive_arbitrary_selection(self):
        self.recipe()
        self.arm()
        snapshot = self.tv.snapshot
        def distinct(endpoint):
            result = snapshot(endpoint)
            result["identity"]["serial"] = endpoint
            return result
        devices = [{"endpoint": ENDPOINT}, {"endpoint": "192.168.1.51:5555"}]
        with patch.object(self.tv, "snapshot", side_effect=distinct), patch.object(self.service, "scan", return_value={"devices": devices}):
            result = self.service.automatic_pass()
        self.assertEqual([value["status"] for value in result["results"]], ["multiple_tvs", "multiple_tvs"])
        self.assertFalse(self.tv.disabled)

    def test_same_tv_over_two_transports_only_runs_once(self):
        self.recipe()
        self.arm()
        devices = [{"endpoint": "usb:OWNER-TEST-001", "transport": "usb"}, {"endpoint": ENDPOINT}]
        with patch.object(self.service, "scan", return_value={"devices": devices}):
            result = self.service.automatic_pass()
        self.assertEqual([value["status"] for value in result["results"]], ["completed"])

    def test_usb_unauthorized_is_reported_without_commands(self):
        self.arm()
        devices = [{"endpoint": "usb:OWNER-TEST-001", "transport": "usb", "state": "unauthorized"}]
        with patch.object(self.service, "scan", return_value={"devices": devices}), patch.object(self.tv, "connect") as connect:
            result = self.service.automatic_pass()
        connect.assert_not_called()
        self.assertEqual(result["results"][0]["status"], "waiting_for_authorization")

    def test_scan_limit_prevents_partial_selection(self):
        self.recipe()
        self.arm()
        devices = [{"endpoint": ENDPOINT}] * 33
        with patch.object(self.service, "scan", return_value={"devices": devices}):
            result = self.service.automatic_pass()
        self.assertEqual(result["results"][-1]["status"], "discovery_limit")
        self.assertFalse(self.tv.disabled)

    def test_failed_mutation_is_not_retried_on_next_pass(self):
        self.recipe()
        self.arm()
        failure = {"id": "a" * 32, "status": "rolled_back", "summary": "Simulated TV refusal"}
        with patch.object(self.service, "apply", return_value=failure) as apply:
            self.service.automatic_pass()
            result = self.service.automatic_pass()
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(result["results"][0]["status"], "paused_after_failure")
        self.service.clear_automatic_block(ENDPOINT)
        self.assertFalse(self.service.config["automatic_blocks"])

    def test_auto_network_selection_is_recomputed_each_pass(self):
        self.arm()
        with patch.object(self.service, "scan", return_value={"devices": []}) as scan:
            self.service.automatic_pass()
            self.service.automatic_pass()
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(scan.call_args.args, ("",))

    def test_configuration_validation(self):
        with self.assertRaises(ServiceError):
            self.service.set_automatic(True, "", "usb", False, "recipes")
        with self.assertRaises(ServiceError):
            self.service.set_automatic(True, "", "everywhere", True, "recipes")

    def test_another_worker_is_excluded(self):
        self.service.automatic_lock.acquire()
        try:
            self.assertEqual(self.service.automatic_pass()["status"], "busy")
        finally:
            self.service.automatic_lock.release()

    def test_export_contains_no_per_device_identity(self):
        recipe, _ = self.recipe()
        data = self.service.export_recipe(recipe["id"])
        content = json.dumps(data)
        for secret in ("OWNER-TEST-001", "0123456789abcdef", ENDPOINT):
            self.assertNotIn(secret, content)

    def test_support_report_excludes_user_apps_and_identifiers(self):
        self.tv.user_apps = ["com.example.privateapp"]
        content = json.dumps(self.service.support_report(ENDPOINT))
        for secret in ("OWNER-TEST-001", "0123456789abcdef", ENDPOINT, "com.example.privateapp", "settings_secure"):
            self.assertNotIn(secret, content)

    def test_recipe_import_rejects_different_mode(self):
        recipe, _ = self.recipe()
        exported = self.service.export_recipe(recipe["id"])
        target = CleanerService(ROOT, Path(self.tmp.name) / "other", demo=False, transport=PortableTV())
        with self.assertRaisesRegex(ServiceError, "simulation mode"):
            target.import_recipe(exported)

    def test_recipe_import_is_explicit_and_preserves_digest(self):
        recipe, _ = self.recipe()
        exported = self.service.export_recipe(recipe["id"])
        target = CleanerService(ROOT, Path(self.tmp.name) / "other", demo=True, transport=PortableTV())
        result = target.import_recipe(exported)
        self.assertEqual(result["digest"], exported["digest"])
        with self.assertRaisesRegex(ServiceError, "already exists"):
            target.import_recipe(exported)


if __name__ == "__main__":
    unittest.main()

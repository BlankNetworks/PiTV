"""Regression tests for state writers and revoking unattended operations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tvcleaner.service as module
from tvcleaner.service import CleanerService, ServiceError
from tvcleaner.storage import read_json

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "192.168.1.50:5555"
PROFILE = "xiaomi-mi-tv-4a/debloat.txt"


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = CleanerService(ROOT, self.temp.name, demo=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_another_service_instance_cannot_mutate_while_locked(self):
        other = CleanerService(ROOT, self.temp.name, demo=True)
        lock = self.service._acquire_mutation()
        try:
            with self.assertRaisesRegex(ServiceError, "Another process"):
                other.apply("0" * 32, [])
        finally:
            self.service._release_mutation(lock)

    def test_read_refresh_during_write_does_not_lose_configuration(self):
        self.service.set_automatic(False, "")
        original = module.atomic_json
        def interleave(path, value):
            if Path(path).name == "config.json":
                self.service._refresh_config()
            original(path, value)
        with patch.object(module, "atomic_json", side_effect=interleave):
            self.service.set_automatic(True, "192.168.1.0/24")
        self.assertTrue(read_json(self.service.state / "config.json")["automatic"]["enabled"])

    def test_turning_automatic_off_after_plan_prevents_mutation(self):
        plan = self.service.plan(ENDPOINT, PROFILE)
        selected = [row["name"] for row in plan["packages"] if row["selected"]]
        self.service.apply(plan["id"], selected, enroll=True)
        self.service.transport.disabled.clear()
        self.service.set_automatic(True, "192.168.1.0/24")
        original = self.service.plan
        def revoke(endpoint, profile_id):
            result = original(endpoint, profile_id)
            self.service.set_automatic(False, "192.168.1.0/24")
            return result
        with patch.object(self.service, "plan", side_effect=revoke):
            result = self.service.automatic_pass()
        self.assertFalse(self.service.transport.disabled)
        self.assertEqual(result["results"][0]["status"], "stopped")

    def test_changed_baseline_disabled_packages_disqualify_automation(self):
        plan = self.service.plan(ENDPOINT, PROFILE)
        selected = [row["name"] for row in plan["packages"] if row["selected"]]
        self.service.apply(plan["id"], selected, enroll=True)
        self.service.transport.disabled.add("com.xiaomi.mimusic2")
        self.assertIsNone(self.service.enrollment_for(self.service.transport.snapshot(ENDPOINT)))


if __name__ == "__main__":
    unittest.main()

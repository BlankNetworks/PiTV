"""Lifecycle and recovery tests using the pinned catalog, never a real TV.

The non-demo runner uses the actual upstream recovery writer and command planner
against a simulated ADB device. Only subprocess/network execution is replaced;
controller planning, backups, hashes, recovery validation and rollback remain real.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tvcleaner.demo import DemoTransport
from tvcleaner.service import CleanerService, ServiceError, binding
from tvcleaner.storage import atomic_json, read_json
from tvcleaner.transport import TransportError
from tvcleaner.web import Jobs

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "192.168.1.50:5555"
NEW_ENDPOINT = "192.168.1.60:37123"
PROFILE_ID = "xiaomi-mi-tv-4a/debloat.txt"
ANALYTICS = "com.miui.tv.analytics"
STATISTICS = "com.xiaomi.statistic"
FEEDBACK = "com.google.android.feedback"


class MutableDemoTransport(DemoTransport):
    """Stateful fixture with independent identity and strict health probes."""
    binary = "never-execute-adb-in-tests"

    def __init__(self):
        super().__init__()
        initial = super().snapshot(ENDPOINT)
        self.identity = initial["identity"]
        self.installed = set(initial["packages"])
        self.home = initial["home"]
        self.protected = set(initial["protected"])
        self.third_party = set()
        self.global_settings = {"window_animation_scale": "0.5", "transition_animation_scale": "1.5"}
        self.connect_calls = []
        self.screenshots = []
        self.fail_screenshot = False
        self.require_healthy_home = False

    def connect(self, endpoint):
        self.connect_calls.append(endpoint)
        return "Simulated connection; no TV was contacted."

    def identity_snapshot(self, endpoint):
        return {"identity": copy.deepcopy(self.identity)}

    def snapshot(self, endpoint):
        if self.require_healthy_home and not self.home:
            raise TransportError("The active Home app could not be verified; cleanup is blocked.")
        value = super().snapshot(endpoint)
        value.update(identity=copy.deepcopy(self.identity), packages=sorted(self.installed),
                     disabled=sorted(self.disabled), home=self.home, protected=sorted(self.protected),
                     third_party=sorted(self.third_party))
        value["raw"]["settings_global"] = "\n".join(f"{key}={item}" for key, item in sorted(self.global_settings.items()))
        return value

    def screenshot(self, endpoint, destination):
        self.screenshots.append((endpoint, Path(destination)))
        if self.fail_screenshot:
            raise TransportError("Simulated protected video screenshot refusal")


class FakeRecoveryDevice:
    """Only the fixed read commands used by upstream recovery.save are allowed."""
    def __init__(self, transport):
        self.transport = transport

    def shell(self, command, **kwargs):
        if command == "pm list packages --user 0":
            return "\n".join("package:" + name for name in sorted(self.transport.installed))
        if command == "pm list packages -d --user 0":
            return "\n".join("package:" + name for name in sorted(self.transport.disabled))
        if command == "cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME":
            return self.transport.home
        if command.startswith("settings get global "):
            return self.transport.global_settings.get(command.removeprefix("settings get global "), "null")
        raise AssertionError(f"Unexpected recovery read command: {command}")

    def close(self):
        pass


class FakeUpstreamRunner:
    """Replace process execution while retaining real upstream record semantics."""
    def __init__(self, service):
        self.service = service
        self.transport = service.transport
        self.calls = []
        self.disable_batches = []
        self.restore_batches = []
        self.restore_commands = []
        self.fail_disable_batch = None
        self.partial_count = 1
        self.fail_save = False
        self.fail_restore = False
        self.refuse_without_error = False
        self.record_hook = None
        self.after_disable = None

    def __call__(self, relative, arguments, endpoint, timeout=240):
        self.calls.append((relative, [str(value) for value in arguments], endpoint))
        output = io.StringIO()
        returncode = 0
        recovery = self.service.recovery
        with patch.dict(os.environ, {"HOME": str(self.service.home), "TV_HOST": endpoint}, clear=False), \
                patch.object(recovery, "reach", return_value=FakeRecoveryDevice(self.transport)), \
                redirect_stdout(output), redirect_stderr(output):
            try:
                if relative == "tools/recovery.py":
                    if len(arguments) != 2 or arguments[0] != "--save":
                        raise AssertionError("Unexpected recovery writer arguments")
                    list_path = Path(arguments[1])
                    if self.fail_save:
                        returncode = 1
                    else:
                        recovery.save(list_path, endpoint)
                        if self.record_hook:
                            self.record_hook(self.service._record_path(list_path, endpoint))
                elif relative == "tools/disable.sh":
                    list_path = Path(arguments[0])
                    # The actual disable wrapper also saves again; first record wins.
                    recovery.save(list_path, endpoint)
                    names = recovery.read_list(list_path)
                    if recovery.core_packages(names):
                        raise AssertionError("A core package reached the mutation runner")
                    self.disable_batches.append(names)
                    if self.fail_disable_batch == len(self.disable_batches):
                        self.transport.disabled.update(names[:self.partial_count])
                        returncode = 1
                    elif not self.refuse_without_error:
                        self.transport.disabled.update(names)
                    if self.after_disable:
                        self.after_disable(len(self.disable_batches))
                elif relative == "tools/restore.sh":
                    list_path = Path(arguments[0])
                    stock = arguments[1] if len(arguments) > 1 else "com.google.android.tvlauncher"
                    commands = recovery.restore_commands(list_path, stock, endpoint)
                    self.restore_batches.append(recovery.read_list(list_path))
                    self.restore_commands.extend(commands)
                    if self.fail_restore:
                        returncode = 1
                    else:
                        for command in commands:
                            self._restore_command(command)
                else:
                    raise AssertionError(f"Unexpected upstream command: {relative}")
            except SystemExit as error:
                # The actual child process turns this into a nonzero exit status.
                returncode = 1
                print(str(error))
        return subprocess.CompletedProcess([relative, *map(str, arguments)], returncode, output.getvalue(), "")

    def _restore_command(self, command):
        if command.startswith("pm enable "):
            self.transport.disabled.discard(command.removeprefix("pm enable "))
        elif command.startswith("cmd package set-home-activity --user 0 "):
            home = command.removeprefix("cmd package set-home-activity --user 0 ")
            if "$" in home:
                raise AssertionError("A fallback Home command reached restoration")
            self.transport.home = home
        elif command.startswith("settings put global "):
            key, value = command.removeprefix("settings put global ").split(" ", 1)
            self.transport.global_settings[key] = value
        elif command.startswith("settings delete global "):
            self.transport.global_settings.pop(command.removeprefix("settings delete global "), None)
        elif command != "pm list packages -d":
            raise AssertionError(f"Unexpected restore command: {command}")


class ServiceFixture(unittest.TestCase):
    demo = True

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tvcleaner-service-test-")
        self.addCleanup(self.temporary.cleanup)
        self.transport = MutableDemoTransport()
        # A misplaced production call must fail the test before executing anything.
        self.process_guard = patch("subprocess.Popen", side_effect=AssertionError("Tests must not launch ADB or upstream processes"))
        self.process_guard.start()
        self.addCleanup(self.process_guard.stop)
        self.service = CleanerService(ROOT, Path(self.temporary.name) / "state", demo=self.demo, transport=self.transport)
        if not self.demo:
            self.runner = FakeUpstreamRunner(self.service)
            self.service._tool = self.runner

    def plan(self):
        return self.service.plan(ENDPOINT, PROFILE_ID)

    def cleanup(self, packages=None, *, enroll=False):
        plan = self.plan()
        return self.service.apply(plan["id"], packages or [ANALYTICS, STATISTICS], enroll=enroll)

    def private_run(self, result):
        return read_json(self.service.runs_dir / result["id"] / "run.json")

    def install_profile(self):
        profile = self.service.catalog.get(PROFILE_ID)
        self.transport.installed.update(entry["name"] for entry in profile["packages"])
        plan = self.plan()
        names = [row["name"] for row in plan["packages"] if row["installed"] and not row["protected"] and not row["disabled"]]
        self.assertGreaterEqual(len(names), 7)
        return plan, names[:7]


class DemoLifecycleTests(ServiceFixture):
    def test_apply_restore_round_trip_and_durable_private_backup(self):
        before = self.transport.snapshot(ENDPOINT)
        result = self.cleanup()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(set(result["changed"]), {ANALYTICS, STATISTICS})
        self.assertTrue(result["needs_manual_check"])
        folder = self.service.runs_dir / result["id"]
        self.assertEqual(read_json(folder / "before.json"), before)
        self.assertEqual((folder / "before.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        restored = self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(self.transport.snapshot(ENDPOINT), before)
        self.assertEqual(read_json(folder / "run.json")["status"], "restored")
        self.assertEqual(read_json(folder / "restored.json"), before)

    def test_pre_disabled_packages_stay_disabled(self):
        self.transport.disabled.add(STATISTICS)
        result = self.cleanup([ANALYTICS, FEEDBACK])
        self.assertEqual(set(result["changed"]), {ANALYTICS, FEEDBACK})
        self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(self.transport.disabled, {STATISTICS})

    def test_expired_and_used_plans_never_repeat_mutations(self):
        plan = self.plan()
        path = self.service.plans_dir / (plan["id"] + ".json")
        stored = read_json(path)
        stored["expires_at"] = 0
        atomic_json(path, stored)
        with self.assertRaisesRegex(ServiceError, "expired"):
            self.service.apply(plan["id"], [ANALYTICS])
        self.assertFalse(self.transport.disabled)
        self.assertEqual(self.service.list_runs()["runs"], [])
        plan = self.plan()
        self.service.apply(plan["id"], [ANALYTICS])
        with self.assertRaisesRegex(ServiceError, "already been used"):
            self.service.apply(plan["id"], [ANALYTICS])
        self.assertEqual(len(self.service.list_runs()["runs"]), 1)

    def test_changed_identity_firmware_home_or_inventory_invalidates_plan(self):
        mutations = {
            "serial": lambda: self.transport.identity.update(serial="ANOTHER-SIMULATED-TV"),
            "firmware": lambda: self.transport.identity.update(fingerprint="DEMO/new-firmware"),
            "build": lambda: self.transport.identity.update(build="different-build"),
            "home": lambda: setattr(self.transport, "home", "com.example.home/.Main"),
            "installed": lambda: self.transport.installed.add("com.example.newapp"),
            "disabled": lambda: self.transport.disabled.add(FEEDBACK),
            "protection": lambda: self.transport.protected.add(ANALYTICS),
            "third_party": lambda: self.transport.third_party.add(ANALYTICS),
        }
        for name, mutate in mutations.items():
            with self.subTest(change=name):
                self.transport = MutableDemoTransport()
                self.service.transport = self.transport
                plan = self.plan()
                mutate()
                state = self.transport.snapshot(ENDPOINT)
                with self.assertRaisesRegex(ServiceError, "changed"):
                    self.service.apply(plan["id"], [ANALYTICS])
                self.assertEqual(self.transport.snapshot(ENDPOINT), state)
        self.assertEqual(self.service.list_runs()["runs"], [])

    def test_forbidden_unavailable_and_unreviewed_packages_are_refused(self):
        self.transport.disabled.add(STATISTICS)
        plan = self.plan()
        for packages in (["android"], ["com.google.android.tvlauncher"], ["com.xiaomi.mitv.updateservice"],
                         ["com.mitv.milinkservice"], ["com.example.not.in.plan"], [STATISTICS],
                         ["com.xm.webcontent"], [ANALYTICS + ";id"], [], "not a list", [123]):
            with self.subTest(packages=packages), self.assertRaises(ServiceError):
                self.service.apply(plan["id"], packages)
        self.assertEqual(self.transport.disabled, {STATISTICS})
        self.assertEqual(self.service.list_runs()["runs"], [])

    def test_dynamic_and_user_installed_apps_remain_preserved(self):
        self.transport.protected.add(ANALYTICS)
        self.transport.third_party.add(STATISTICS)
        plan = self.plan()
        rows = {row["name"]: row for row in plan["packages"]}
        for name in (ANALYTICS, STATISTICS):
            self.assertTrue(rows[name]["protected"])
            self.assertFalse(rows[name]["selected"])
            with self.assertRaises(ServiceError):
                self.service.apply(plan["id"], [name])

    def test_inspection_does_not_expose_raw_identifiers(self):
        result = self.service.inspect(ENDPOINT)
        encoded = json.dumps(result)
        for private in (self.transport.identity["serial"], self.transport.identity["android_id"], self.transport.identity["fingerprint"]):
            self.assertNotIn(private, encoded)
        self.assertNotIn("raw", result)
        self.assertFalse(result["automatic_eligible"])

    def test_no_stable_identity_allows_inspection_but_blocks_cleanup(self):
        self.transport.identity.update(serial="unknown", android_id="0000000000000000")
        inspection = self.service.inspect(ENDPOINT)
        self.assertFalse(inspection["automatic_eligible"])
        self.assertTrue(inspection["warnings"])
        with self.assertRaisesRegex(ServiceError, "stable device ID"):
            self.plan()

    def test_unsupported_family_is_refused_even_with_matching_packages(self):
        self.transport.identity["family"] = "phone"
        with self.assertRaisesRegex(ServiceError, "not a supported"):
            self.plan()

    def test_restore_refuses_changed_device_or_firmware(self):
        result = self.cleanup()
        original = copy.deepcopy(self.transport.identity)
        for key, value in (("serial", "DIFFERENT-TV"), ("fingerprint", "different-firmware")):
            with self.subTest(key=key):
                self.transport.identity = {**original, key: value}
                with self.assertRaisesRegex(ServiceError, "different TV or firmware"):
                    self.service.restore(result["id"], ENDPOINT)
                self.assertEqual(self.transport.disabled, {ANALYTICS, STATISTICS})
        self.transport.identity = original
        self.assertEqual(self.service.restore(result["id"], ENDPOINT)["status"], "restored")

    def test_screenshot_refusal_does_not_undo_verified_cleanup(self):
        self.transport.fail_screenshot = True
        result = self.cleanup()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(any("screenshot" in warning for warning in result["warnings"]))
        self.assertEqual(self.transport.disabled, {ANALYTICS, STATISTICS})

    def test_enrollment_requires_explicit_opt_in_and_restore_revokes_it(self):
        result = self.cleanup(enroll=True)
        snapshot = self.transport.snapshot(ENDPOINT)
        enrollment = self.service.enrollment_for(snapshot)
        self.assertEqual(enrollment["packages"], sorted([ANALYTICS, STATISTICS]))
        self.assertTrue(self.service.inspect(ENDPOINT)["automatic_eligible"])
        restarted = CleanerService(ROOT, self.service.state, demo=True, transport=self.transport)
        self.assertIsNotNone(restarted.enrollment_for(snapshot))
        self.service.restore(result["id"], ENDPOINT)
        self.assertIsNone(self.service.enrollment_for(self.transport.snapshot(ENDPOINT)))
        self.assertEqual(read_json(self.service.state / "config.json")["enrollments"], {})

    def test_enrollment_invalidates_when_firmware_home_or_installed_apps_change(self):
        self.cleanup(enroll=True)
        original = self.transport.snapshot(ENDPOINT)
        for field, value in (("fingerprint", "new-firmware"), ("build", "new-build"), ("serial", "new-device")):
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed["identity"][field] = value
                self.assertIsNone(self.service.enrollment_for(changed))
        for field, value in (("home", "com.example.home/.Main"), ("packages", original["packages"] + ["com.example.new"]),
                             ("protected", original["protected"] + [ANALYTICS]), ("third_party", [ANALYTICS])):
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed[field] = value
                self.assertIsNone(self.service.enrollment_for(changed))
        self.assertIsNotNone(self.service.enrollment_for(original))

    def test_unknown_tv_only_gets_review_required_in_automatic_mode(self):
        self.service.set_automatic(True, "192.168.1.0/24")
        result = self.service.automatic_pass()
        self.assertEqual([item["status"] for item in result["results"]], ["review_required"])
        self.assertFalse(self.transport.disabled)
        self.assertEqual(self.service.list_runs()["runs"], [])

    def test_automatic_reapplies_only_the_previously_enrolled_selection(self):
        self.cleanup([ANALYTICS], enroll=True)
        self.service.set_automatic(True, "192.168.1.0/24")
        self.transport.disabled.clear()
        result = self.service.automatic_pass()
        self.assertEqual([item["status"] for item in result["results"]], ["completed"])
        self.assertEqual(self.transport.disabled, {ANALYTICS})
        already = self.service.automatic_pass()
        self.assertEqual([item["status"] for item in already["results"]], ["already_clean"])
        self.assertEqual(len(self.service.list_runs()["runs"]), 2)

    def test_automatic_does_not_connect_to_tls_pairing_advertisements(self):
        self.service.set_automatic(True, "192.168.1.0/24")
        with patch.object(self.service, "scan", return_value={"devices": [
                {"endpoint": "192.168.1.50:37123", "transport": "adb_tls_pairing"}]}):
            result = self.service.automatic_pass()
        self.assertEqual(result["results"], [{"endpoint": "192.168.1.50:37123", "status": "pairing_required"}])
        self.assertEqual(self.transport.connect_calls, [])

    def test_interrupted_run_blocks_new_cleanup_then_can_be_restored(self):
        result = self.cleanup(enroll=True)
        run = self.private_run(result)
        run["status"] = "applying"
        path = self.service.runs_dir / result["id"] / "run.json"
        atomic_json(path, run)
        restarted = CleanerService(ROOT, self.service.state, demo=True, transport=self.transport)
        self.assertIsNone(restarted.enrollment_for(self.transport.snapshot(ENDPOINT)))
        plan = restarted.plan(ENDPOINT, PROFILE_ID)
        with self.assertRaisesRegex(ServiceError, "unfinished operation"):
            restarted.apply(plan["id"], [FEEDBACK])
        self.assertEqual(read_json(path)["status"], "interrupted")
        self.assertEqual(self.transport.disabled, {ANALYTICS, STATISTICS})
        self.assertEqual(restarted.restore(result["id"], ENDPOINT)["status"], "restored")

    def test_async_restore_job_reports_and_persists_restored_state(self):
        result = self.cleanup()
        jobs = Jobs()
        job_id = jobs.submit(self.service.restore, result["id"], ENDPOINT)["job_id"]
        jobs.pool.shutdown(wait=True)
        job = jobs.get(job_id)
        self.assertEqual(job["state"], "done")
        self.assertIsNone(job["error"])
        self.assertEqual(job["result"]["status"], "restored")
        self.assertEqual(self.private_run(result)["status"], "restored")
        self.assertFalse(self.transport.disabled)

    def test_non_boolean_enrollment_and_invalid_run_ids_are_refused(self):
        plan = self.plan()
        with self.assertRaises(ServiceError):
            self.service.apply(plan["id"], [ANALYTICS], enroll="yes")
        for run_id in ("../run", "not-a-run", "0" * 32):
            with self.subTest(run_id=run_id), self.assertRaises(ServiceError):
                self.service.restore(run_id, ENDPOINT)
        self.assertFalse(self.transport.disabled)


class RecoveryLifecycleTests(ServiceFixture):
    demo = False

    def test_real_upstream_record_and_restore_keep_pre_disabled_launcher(self):
        self.transport.installed.add("com.mitv.tvhome")
        self.transport.disabled.update({"com.mitv.tvhome", STATISTICS})
        before = self.transport.snapshot(ENDPOINT)
        result = self.cleanup([ANALYTICS, FEEDBACK])
        self.assertEqual(result["status"], "completed")
        run = self.private_run(result)
        record_path = self.service.home / ".cleanyourtv" / run["batches"][0]["record_file"]
        record = self.service.recovery.load_record(record_path)
        self.assertEqual(record["packages"]["com.mitv.tvhome"], "disabled")
        self.assertEqual(record["packages"][ANALYTICS], "enabled")
        self.assertEqual(record["home"], before["home"])
        self.assertEqual(self.runner.calls[0][0], "tools/recovery.py")
        self.assertEqual(self.runner.calls[1][0], "tools/disable.sh")
        restored = self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(self.transport.snapshot(ENDPOINT), before)
        self.assertNotIn("pm enable com.mitv.tvhome", self.runner.restore_commands)
        self.assertNotIn("pm enable " + STATISTICS, self.runner.restore_commands)

    def test_multiple_small_batches_restore_in_reverse_order(self):
        plan, names = self.install_profile()
        result = self.service.apply(plan["id"], names)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([len(batch) for batch in self.runner.disable_batches], [5, 2])
        self.assertEqual(set(result["changed"]), set(names))
        self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(self.runner.restore_batches, list(reversed(self.runner.disable_batches)))
        self.assertFalse(self.transport.disabled)

    def test_partial_first_batch_refusal_rolls_back_and_stops_future_batches(self):
        self.transport.disabled.add(FEEDBACK)
        before = self.transport.snapshot(ENDPOINT)
        plan, names = self.install_profile()
        self.runner.fail_disable_batch = 1
        result = self.service.apply(plan["id"], names)
        self.assertEqual(result["status"], "rolled_back")
        self.assertTrue(result["errors"])
        self.assertEqual(len(self.runner.disable_batches), 1)
        self.assertEqual(len(self.runner.restore_batches), 1)
        self.assertEqual(self.transport.disabled, set(before["disabled"]))

    def test_partial_second_batch_refusal_restores_both_batches(self):
        plan, names = self.install_profile()
        self.runner.fail_disable_batch = 2
        result = self.service.apply(plan["id"], names)
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(len(self.runner.disable_batches), 2)
        self.assertEqual(self.runner.restore_batches, list(reversed(self.runner.disable_batches)))
        self.assertFalse(self.transport.disabled)

    def test_successful_process_exit_without_changes_is_not_successful_cleanup(self):
        self.runner.refuse_without_error = True
        result = self.cleanup()
        self.assertEqual(result["status"], "rolled_back")
        self.assertTrue(any("Verification" in error for error in result["errors"]))
        self.assertFalse(self.transport.disabled)

    def test_backup_failure_never_starts_a_disable_or_fallback_restore(self):
        self.runner.fail_save = True
        result = self.cleanup()
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(self.runner.disable_batches, [])
        self.assertEqual(self.runner.restore_batches, [])
        self.assertFalse(self.transport.disabled)

    def test_incomplete_or_mismatched_generated_records_block_disable(self):
        mutations = {
            "host": lambda record: record.update(host=NEW_ENDPOINT),
            "list": lambda record: record.update(list="/different/list.txt"),
            "home": lambda record: record.update(home="com.example.home/.Main"),
            "selected_state": lambda record: record["packages"].update({ANALYTICS: "disabled"}),
            "invalid_state": lambda record: record["packages"].update({ANALYTICS: "not-a-state"}),
            "missing_package": lambda record: record["packages"].pop(ANALYTICS),
            "animation": lambda record: record["settings"].update(window_animation_scale="999"),
        }
        for label, mutate in mutations.items():
            with self.subTest(record=label):
                def hook(path, change=mutate):
                    record = read_json(path)
                    change(record)
                    atomic_json(path, record)
                self.runner.record_hook = hook
                result = self.cleanup()
                self.assertEqual(result["status"], "rolled_back")
                self.assertEqual(self.runner.disable_batches, [])
                self.assertEqual(self.runner.restore_batches, [])
                self.assertFalse(self.transport.disabled)

    def test_modified_or_missing_recovery_record_stops_restore_before_commands(self):
        result = self.cleanup()
        run = self.private_run(result)
        path = self.service.runs_dir / result["id"] / run["batches"][0]["record_backup"]
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ServiceError, "missing or changed"):
            self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(self.runner.restore_batches, [])
        self.assertEqual(self.private_run(result)["status"], "needs_restore")
        path.unlink()
        with self.assertRaisesRegex(ServiceError, "missing or changed"):
            self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(self.runner.restore_batches, [])
        self.assertEqual(self.transport.disabled, {ANALYTICS, STATISTICS})
        path.write_bytes(original)
        self.assertEqual(self.service.restore(result["id"], ENDPOINT)["status"], "restored")

    def test_missing_live_record_is_rebuilt_from_verified_archive(self):
        result = self.cleanup()
        run = self.private_run(result)
        batch = run["batches"][0]
        live = self.service.home / ".cleanyourtv" / batch["record_file"]
        archive = self.service.runs_dir / result["id"] / batch["record_backup"]
        expected = archive.read_bytes()
        live.unlink()
        self.assertEqual(self.service.restore(result["id"], ENDPOINT)["status"], "restored")
        self.assertEqual(live.read_bytes(), expected)
        self.assertEqual(archive.read_bytes(), expected)
        self.assertFalse(self.transport.disabled)

    def test_changed_saved_batch_stops_restore_before_commands(self):
        result = self.cleanup()
        run = self.private_run(result)
        path = self.service.runs_dir / result["id"] / run["batches"][0]["file"]
        path.write_text("com.example.other\n", encoding="utf-8")
        with self.assertRaisesRegex(ServiceError, "cleanup list changed"):
            self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(self.runner.restore_batches, [])
        self.assertEqual(self.transport.disabled, {ANALYTICS, STATISTICS})

    def test_restore_follows_same_device_to_new_private_ip_and_tls_port(self):
        result = self.cleanup()
        run = self.private_run(result)
        folder = self.service.runs_dir / result["id"]
        batch = run["batches"][0]
        old_record = self.service.home / ".cleanyourtv" / batch["record_file"]
        new_record = self.service._record_path(folder / batch["file"], NEW_ENDPOINT)
        self.assertFalse(new_record.exists())
        restored = self.service.restore(result["id"], NEW_ENDPOINT)
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(new_record.read_bytes(), old_record.read_bytes())
        self.assertEqual(new_record.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.transport.disabled)
        self.assertEqual(self.runner.calls[-1][2], NEW_ENDPOINT)

    def test_conflicting_record_at_new_ip_cannot_be_overwritten(self):
        result = self.cleanup()
        run = self.private_run(result)
        batch = run["batches"][0]
        destination = self.service._record_path(self.service.runs_dir / result["id"] / batch["file"], NEW_ENDPOINT)
        atomic_json(destination, {"different": "record"})
        before = destination.read_bytes()
        with self.assertRaisesRegex(ServiceError, "conflicting restoration record"):
            self.service.restore(result["id"], NEW_ENDPOINT)
        self.assertEqual(destination.read_bytes(), before)
        self.assertEqual(self.runner.restore_batches, [])

    def test_identity_change_after_disable_stops_rollback_to_other_device(self):
        original = copy.deepcopy(self.transport.identity)
        self.runner.after_disable = lambda count: self.transport.identity.update(serial="ANOTHER-TV")
        result = self.cleanup()
        self.assertEqual(result["status"], "needs_restore")
        self.assertEqual(self.runner.restore_batches, [])
        self.assertTrue(any("different" in error.lower() for error in result["errors"]))
        self.transport.identity = original
        self.runner.after_disable = None
        self.assertEqual(self.service.restore(result["id"], ENDPOINT)["status"], "restored")

    def test_recovery_can_repair_home_when_strict_snapshot_is_unavailable(self):
        result = self.cleanup()
        self.transport.require_healthy_home = True
        self.transport.home = ""
        with self.assertRaises(TransportError):
            self.transport.snapshot(ENDPOINT)
        restored = self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(self.transport.home, "com.google.android.tvlauncher/.MainActivity")
        self.assertFalse(self.transport.disabled)

    def test_failed_rollback_retains_backups_and_can_be_retried(self):
        self.runner.fail_disable_batch = 1
        self.runner.fail_restore = True
        result = self.cleanup()
        self.assertEqual(result["status"], "needs_restore")
        self.assertTrue(self.transport.disabled)
        folder = self.service.runs_dir / result["id"]
        original_backup = (folder / "before.json").read_bytes()
        self.assertIsNone(self.service.enrollment_for(self.transport.snapshot(ENDPOINT)))
        self.runner.fail_restore = False
        retried = self.service.restore(result["id"], ENDPOINT)
        self.assertEqual(retried["status"], "restored")
        self.assertFalse(self.transport.disabled)
        self.assertEqual((folder / "before.json").read_bytes(), original_backup)

    def test_unrelated_external_disable_is_preserved_during_rollback(self):
        self.runner.after_disable = lambda count: self.transport.disabled.add("com.netflix.ninja")
        result = self.cleanup()
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(self.transport.disabled, {"com.netflix.ninja"})
        self.assertTrue(any("Other package states differ" in warning for warning in result["warnings"]))

    def test_restore_job_failure_reports_error_and_durable_needs_restore(self):
        result = self.cleanup()
        self.runner.fail_restore = True
        jobs = Jobs()
        job_id = jobs.submit(self.service.restore, result["id"], ENDPOINT)["job_id"]
        jobs.pool.shutdown(wait=True)
        job = jobs.get(job_id)
        self.assertEqual(job["state"], "error")
        self.assertIsNone(job["result"])
        self.assertIn("did not complete restoration", job["error"])
        self.assertEqual(self.private_run(result)["status"], "needs_restore")
        self.assertEqual(self.transport.disabled, {ANALYTICS, STATISTICS})

    def test_upstream_integrity_failure_blocks_before_mutation(self):
        # Tamper the expected digest in memory, never modify the pinned checkout.
        self.service.manifest["files"]["tools/disable.sh"] = "0" * 64
        with self.assertRaisesRegex(ServiceError, "Pinned source changed"):
            self.plan()
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(self.transport.disabled)


if __name__ == "__main__":
    unittest.main()

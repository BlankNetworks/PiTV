"""Release validation and update transactions in temporary directories only."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from tvcleaner import releases
from tvcleaner.releases import (ReleaseError, ReleaseUpdater, UpdatePaths,
                               inspect_release, release_version, stage_release)


def fixture_files(version="0.2.0"):
    files = {name: b"fixture content\n" for name in releases.REQUIRED_FILES}
    files["tvcleaner/__init__.py"] = f'__version__ = "{version}"\n'.encode()
    pinned = {}
    for name in releases.REQUIRED_PINNED:
        content = ("fixture upstream " + name + "\n").encode()
        files["vendor/cleanyourtv/" + name] = content
        pinned[name] = hashlib.sha256(content).hexdigest()
    for name in ("examples/example-tv/tv.json", "examples/example-tv/debloat.txt"):
        content = b"{}\n" if name.endswith(".json") else b"com.example.analytics\n"
        files["vendor/cleanyourtv/" + name] = content
        pinned[name] = hashlib.sha256(content).hexdigest()
    files["UPSTREAM.json"] = json.dumps({"repository": "https://github.com/Gathin23/cleanyourtv",
                                         "commit": "a" * 40, "files": pinned}).encode()
    return files


def write_archive(path, files=None, extra=(), compression=zipfile.ZIP_DEFLATED):
    if files is None:
        files = fixture_files()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=compression) as archive:
            for relative, content in files.items():
                info = zipfile.ZipInfo("universal-tv-debloater/" + relative)
                info.compress_type = compression
                info.external_attr = (stat.S_IFREG | (0o755 if relative.endswith(".sh") else 0o644)) << 16
                archive.writestr(info, content)
            for name, content, mode in extra:
                info = zipfile.ZipInfo(name)
                info.compress_type = compression
                info.external_attr = mode << 16
                archive.writestr(info, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReleaseArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "release.zip"
        self.sha256 = write_archive(self.archive)

    def test_inspects_all_manifest_files_and_literal_version(self):
        data = inspect_release(self.archive, self.sha256)
        self.assertEqual(data["version"], "0.2.0")
        self.assertEqual(data["upstream_commit"], "a" * 40)
        self.assertEqual(data["pinned_files"], len(releases.REQUIRED_PINNED) + 2)
        self.assertEqual(data["sha256"], self.sha256)
        self.assertFalse((self.root / "universal-tv-debloater").exists())

    def test_preflight_never_imports_release_code(self):
        files = fixture_files()
        files["tvcleaner/__init__.py"] = b'raise RuntimeError("MUST NOT EXECUTE")\n__version__ = "0.2.0"\n'
        digest = write_archive(self.archive, files)
        with patch("subprocess.run", side_effect=AssertionError("preflight executed a command")):
            self.assertEqual(inspect_release(self.archive, digest)["version"], "0.2.0")

    def test_stage_strips_one_root_and_preserves_executable_scripts(self):
        target = self.root / "staged"
        result = stage_release(self.archive, self.sha256, target)
        self.assertEqual(result["directory"], str(target))
        self.assertEqual((target / "README.md").read_bytes(), fixture_files()["README.md"])
        self.assertEqual(stat.S_IMODE((target / "scripts/install-pi.sh").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((target / "README.md").stat().st_mode), 0o644)
        self.assertFalse((target / "universal-tv-debloater").exists())

    def test_wrong_digest_does_not_create_destination(self):
        target = self.root / "staged"
        with self.assertRaisesRegex(ReleaseError, "SHA256 mismatch"):
            stage_release(self.archive, "0" * 64, target)
        self.assertFalse(target.exists())

    def test_hash_format_is_required(self):
        for value in ("", "latest", "f" * 63, "z" * 64):
            with self.subTest(value=value), self.assertRaises(ReleaseError):
                inspect_release(self.archive, value)

    def test_case_insensitive_digest_is_supported(self):
        self.assertEqual(inspect_release(self.archive, self.sha256.upper())["version"], "0.2.0")

    def test_cannot_downgrade_or_reinstall(self):
        for installed in ("0.2.0", "0.3.0", "1.0.0"):
            with self.subTest(installed=installed), self.assertRaisesRegex(ReleaseError, "newer version"):
                inspect_release(self.archive, self.sha256, installed)
        self.assertEqual(inspect_release(self.archive, self.sha256, "0.1.9")["version"], "0.2.0")

    def test_version_must_be_one_literal_assignment(self):
        for value in (b'__version__ = get_version()\n', b'__version__ = "0.2.0"\n__version__ = "9.0.0"\n',
                      b'__version__ = "latest"\n', b'__version__ = "01.2.3"\n', b'x = "0.2.0"\n'):
            with self.subTest(value=value), self.assertRaises(ReleaseError):
                release_version(value)
        self.assertEqual(release_version(b'__version__: str = "0.2.0"\n'), "0.2.0")

    def test_rejects_unsafe_and_outside_paths(self):
        for path in ("/etc/passwd", "other/file.txt", "universal-tv-debloater/../escape",
                     "universal-tv-debloater/a/../../escape", "universal-tv-debloater/a//b",
                     "universal-tv-debloater/./file", "universal-tv-debloater/C:/file",
                     "universal-tv-debloater/a\\b", "universal-tv-debloater/a\nb"):
            with self.subTest(path=path):
                digest = write_archive(self.archive, extra=[(path, b"bad", stat.S_IFREG | 0o644)])
                with self.assertRaises(ReleaseError):
                    inspect_release(self.archive, digest)

    def test_rejects_symlinks_special_files_and_privileged_modes(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o600, stat.S_IFIFO | 0o600,
                     stat.S_IFSOCK | 0o600, stat.S_IFREG | 0o4755, stat.S_IFREG | 0o2644):
            with self.subTest(mode=mode):
                digest = write_archive(self.archive, extra=[("universal-tv-debloater/extra", b"target", mode)])
                with self.assertRaisesRegex(ReleaseError, "special files"):
                    inspect_release(self.archive, digest)

    def test_rejects_duplicate_and_case_colliding_members(self):
        for path in ("universal-tv-debloater/README.md", "universal-tv-debloater/readme.MD"):
            with self.subTest(path=path):
                digest = write_archive(self.archive, extra=[(path, b"second", stat.S_IFREG | 0o644)])
                with self.assertRaisesRegex(ReleaseError, "duplicate"):
                    inspect_release(self.archive, digest)

    def test_rejects_file_as_parent_directory(self):
        digest = write_archive(self.archive, extra=[
            ("universal-tv-debloater/conflict", b"file", stat.S_IFREG | 0o644),
            ("universal-tv-debloater/conflict/child", b"child", stat.S_IFREG | 0o644)])
        with self.assertRaisesRegex(ReleaseError, "parent directory"):
            inspect_release(self.archive, digest)

    def test_rejects_directory_with_data_or_wrong_type(self):
        for content, mode in ((b"data", stat.S_IFDIR | 0o755), (b"", stat.S_IFREG | 0o644)):
            digest = write_archive(self.archive, extra=[("universal-tv-debloater/dir/", content, mode)])
            with self.assertRaises(ReleaseError):
                inspect_release(self.archive, digest)

    def test_rejects_runtime_data_or_credentials(self):
        for name in ("state/config.json", ".venv/bin/python", "vendor/cleanyourtv/tools/adbkey/key",
                     "nested/auth.json", "nested/adbkey", "vendor/cleanyourtv/backup/before.json"):
            with self.subTest(name=name):
                digest = write_archive(self.archive, extra=[("universal-tv-debloater/" + name, b"private", stat.S_IFREG | 0o600)])
                with self.assertRaisesRegex(ReleaseError, "credentials or runtime"):
                    inspect_release(self.archive, digest)

    def test_rejects_missing_application_file(self):
        files = fixture_files()
        del files["scripts/install-pi.sh"]
        digest = write_archive(self.archive, files)
        with self.assertRaisesRegex(ReleaseError, "missing required"):
            inspect_release(self.archive, digest)

    def test_rejects_missing_or_changed_pinned_source(self):
        for remove in (True, False):
            files = fixture_files()
            if remove:
                del files["vendor/cleanyourtv/tools/disable.sh"]
            else:
                files["vendor/cleanyourtv/tools/disable.sh"] = b"changed"
            digest = write_archive(self.archive, files)
            with self.subTest(remove=remove), self.assertRaisesRegex(ReleaseError, "pinned upstream"):
                inspect_release(self.archive, digest)

    def test_rejects_unpinned_new_tools_or_catalog(self):
        for name in ("tools/new.py", "tools/new.sh", "examples/new/tv.json", "examples/new/debloat.txt"):
            files = fixture_files()
            files["vendor/cleanyourtv/" + name] = b"new"
            digest = write_archive(self.archive, files)
            with self.subTest(name=name), self.assertRaisesRegex(ReleaseError, "not pinned"):
                inspect_release(self.archive, digest)

    def test_rejects_malformed_manifest(self):
        for value in (b'[]', b'{"files":{},"files":{}}', b'not JSON',
                      json.dumps({"repository": "unreviewed", "commit": "a" * 40, "files": {}}).encode()):
            files = fixture_files()
            files["UPSTREAM.json"] = value
            digest = write_archive(self.archive, files)
            with self.subTest(value=value), self.assertRaises(ReleaseError):
                inspect_release(self.archive, digest)

    def test_respects_archive_entry_member_and_total_limits(self):
        for variable in ("MAX_ARCHIVE_BYTES", "MAX_ENTRIES", "MAX_MEMBER_BYTES", "MAX_EXPANDED_BYTES"):
            with self.subTest(variable=variable), patch.object(releases, variable, 1), self.assertRaises(ReleaseError):
                inspect_release(self.archive, self.sha256)

    def test_rejects_invalid_crc_even_when_outer_hash_matches(self):
        write_archive(self.archive, compression=zipfile.ZIP_STORED)
        data = self.archive.read_bytes()
        data = data.replace(b"fixture content\n", b"broken! content\n", 1)
        self.archive.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ReleaseError, "completely validated"):
            inspect_release(self.archive, digest)

    def test_rejects_nul_filename(self):
        digest = write_archive(self.archive, extra=[("universal-tv-debloater/nulXsuffix", b"x", stat.S_IFREG | 0o644)])
        data = self.archive.read_bytes().replace(b"nulXsuffix", b"nul\x00suffix")
        self.archive.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ReleaseError, "terminator"):
            inspect_release(self.archive, digest)

    def test_rejects_encrypted_flag(self):
        data = bytearray(self.archive.read_bytes())
        first = data.index(b"PK\x01\x02")
        data[first + 8] |= 1
        self.archive.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ReleaseError, "Encrypted"):
            inspect_release(self.archive, digest)

    def test_existing_destination_is_not_overwritten(self):
        destination = self.root / "existing"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(ReleaseError):
            stage_release(self.archive, self.sha256, destination)
        self.assertEqual(marker.read_text(), "keep")

    def test_archive_and_destination_symlinks_are_refused(self):
        symlink = self.root / "alias.zip"
        symlink.symlink_to(self.archive)
        with self.assertRaises(ReleaseError):
            inspect_release(symlink, self.sha256)
        destination_parent = self.root / "parent-alias"
        destination_parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ReleaseError):
            stage_release(self.archive, self.sha256, destination_parent / "staged")

    def test_source_change_during_extraction_removes_stage(self):
        destination = self.root / "staged"
        original_sync = releases._fsync_directory
        def change_source(path):
            original_sync(path)
            if path == destination:
                with self.archive.open("ab") as stream:
                    stream.write(b"changed")
        with patch.object(releases, "_fsync_directory", side_effect=change_source):
            with self.assertRaisesRegex(ReleaseError, "ZIP changed"):
                stage_release(self.archive, self.sha256, destination)
        self.assertFalse(destination.exists())


class FixtureUpdater(ReleaseUpdater):
    """All systemctl/installer/HTTP operations are in-memory fixture behavior."""
    def __init__(self, paths):
        super().__init__(paths)
        self.commands = []
        self.active = True
        self.enabled = True
        self.fail_install = False
        self.fail_health = False
        self.installer_seen = False

    def _require_installation(self):
        # Fixture paths have already been created under TemporaryDirectory.
        self.paths.updates.mkdir(mode=0o700, exist_ok=True)

    def _require_recovery(self):
        if not self.paths.state.is_dir() or not self.paths.updates.is_dir():
            raise AssertionError("Missing temporary fixture directories")

    def _command(self, arguments, *, check=True, timeout=30):
        self.commands.append(list(arguments))
        code = 0
        if arguments[:2] == ["systemctl", "is-active"]:
            code = 0 if self.active else 3
        elif arguments[:2] == ["systemctl", "is-enabled"]:
            code = 0 if self.enabled else 1
        elif arguments[:2] == ["systemctl", "stop"]:
            self.active = False
        elif arguments[:2] == ["systemctl", "start"]:
            self.active = True
        elif arguments[:2] == ["systemctl", "enable"]:
            self.enabled = True
        elif arguments[:2] == ["systemctl", "disable"]:
            self.enabled = False
        if check and code:
            raise ReleaseError("fixture command refused")
        return subprocess.CompletedProcess(arguments, code, "", "")

    def _installer(self, folder):
        self.installer_seen = True
        config = json.loads((self.paths.state / "config.json").read_text())
        if config["automatic"]["enabled"] is not False:
            raise AssertionError("automatic cleanup was not disabled before installer")
        with (self.paths.state / "mutation.lock").open("a+") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError("mutation lock was released while installer ran")
        self.paths.service_file.write_text("new service")
        self.paths.wrapper.write_text("new wrapper")
        self.paths.usb_rule.write_text("new usb rule")
        self.active = True
        self.enabled = True
        if self.fail_install:
            raise ReleaseError("fixture installer failed")

    def _health_check(self, version):
        if self.fail_health:
            raise ReleaseError("fixture web health failed")
        return {"port": 8081, "version": version, "automatic_enabled": False}


class ReleaseUpdateTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("opt", "state", "units", "bin", "rules"):
            (self.root / directory).mkdir()
        app = self.root / "opt/universal-tv-debloater"
        (app / "tvcleaner").mkdir(parents=True)
        (app / "UPSTREAM.json").write_text("{}")
        (app / "tvcleaner/__init__.py").write_text('__version__ = "0.1.0"\n')
        (app / "old-marker").write_text("old installation")
        self.paths = UpdatePaths(application=app, state=self.root / "state", updates=self.root / "history",
                                 service_file=self.root / "units/tvcleaner.service",
                                 wrapper=self.root / "bin/tvcleaner", usb_rule=self.root / "rules/70-tvcleaner-adb.rules",
                                 update_service=self.root / "units/pitv-update.service",
                                 update_path=self.root / "units/pitv-update.path")
        self.paths.service_file.write_text("old service")
        self.paths.wrapper.write_text("old wrapper")
        self.config = {"automatic": {"enabled": True, "network_mode": "auto", "usb": True, "policy": "recipes", "cidr": ""},
                       "enrollments": {"old-tv": {"profile_id": "example"}},
                       "recipes": {"old-recipe": {"verified": True}}, "unrelated_setting": 42}
        (self.paths.state / "config.json").write_text(json.dumps(self.config))
        (self.paths.state / "access.json").write_text('{"token":"private-test-token"}')
        (self.paths.state / "home/.android").mkdir(parents=True)
        (self.paths.state / "home/.android/adbkey").write_text("private test key")
        (self.paths.state / "runs/old-run").mkdir(parents=True)
        (self.paths.state / "runs/old-run/run.json").write_text('{"status":"completed","changed":["com.example.analytics"]}')
        (self.paths.state / "runs/old-run/recovery.json").write_text("original recovery bytes")
        self.archive = self.root / "release.zip"
        self.sha256 = write_archive(self.archive)
        self.updater = FixtureUpdater(self.paths)

    def assert_runtime_preserved(self):
        self.assertEqual((self.paths.state / "access.json").read_text(), '{"token":"private-test-token"}')
        self.assertEqual((self.paths.state / "home/.android/adbkey").read_text(), "private test key")
        self.assertEqual((self.paths.state / "runs/old-run/recovery.json").read_text(), "original recovery bytes")
        config = json.loads((self.paths.state / "config.json").read_text())
        expected = json.loads(json.dumps(self.config))
        expected["automatic"]["enabled"] = False
        self.assertEqual(config, expected)

    def test_success_keeps_old_app_and_runtime_and_disables_automatic(self):
        dropin = self.paths.service_file.parent / "tvcleaner.service.d"
        dropin.mkdir()
        override = dropin / "port.conf"
        override.write_text("keep port override")
        result = self.updater.update(self.archive, self.sha256)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(result["port"], 8081)
        self.assertEqual(release_version((self.paths.application / "tvcleaner/__init__.py").read_bytes()), "0.2.0")
        self.assertFalse((self.paths.application / "old-marker").exists())
        previous = self.paths.application.with_name(".tvcleaner-previous-" + result["id"])
        self.assertEqual((previous / "old-marker").read_text(), "old installation")
        self.assertEqual(override.read_text(), "keep port override")
        self.assert_runtime_preserved()
        self.assertTrue(self.updater.active)
        self.assertTrue(self.updater.enabled)
        self.assertTrue(self.updater.installer_seen)
        self.assertFalse(list(self.paths.application.parent.glob(".tvcleaner-stage-*")))

    def test_installer_failure_restores_old_app_and_managed_files(self):
        self.updater.fail_install = True
        with self.assertRaisesRegex(ReleaseError, "previous application was restored"):
            self.updater.update(self.archive, self.sha256)
        self.assertEqual((self.paths.application / "old-marker").read_text(), "old installation")
        self.assertEqual(self.paths.service_file.read_text(), "old service")
        self.assertEqual(self.paths.wrapper.read_text(), "old wrapper")
        self.assertFalse(self.paths.usb_rule.exists())
        self.assert_runtime_preserved()
        self.assertTrue(self.updater.active)
        history = list(self.paths.updates.glob("*/update.json"))
        self.assertEqual(len(history), 1)
        self.assertEqual(json.loads(history[0].read_text())["status"], "rolled_back")

    def test_failed_health_check_also_rolls_back(self):
        self.updater.fail_health = True
        with self.assertRaisesRegex(ReleaseError, "previous application was restored"):
            self.updater.update(self.archive, self.sha256)
        self.assertEqual((self.paths.application / "old-marker").read_text(), "old installation")
        self.assert_runtime_preserved()

    def test_update_refuses_active_cleanup_before_state_or_service_changes(self):
        with (self.paths.state / "mutation.lock").open("a+") as other:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ReleaseError, "running"):
                self.updater.update(self.archive, self.sha256)
        self.assertFalse(self.updater.commands)
        self.assertFalse(self.updater.installer_seen)
        self.assertTrue(json.loads((self.paths.state / "config.json").read_text())["automatic"]["enabled"])

    def test_unfinished_or_corrupt_run_blocks_update(self):
        path = self.paths.state / "runs/old-run/run.json"
        for value in ('{"status":"needs_restore"}', '{"status":"interrupted"}', '{"status":"applying"}', 'not-json'):
            path.write_text(value)
            with self.subTest(value=value), self.assertRaises(ReleaseError):
                self.updater.update(self.archive, self.sha256)
        self.assertFalse(self.updater.commands)
        self.assertFalse(self.updater.installer_seen)

    def test_invalid_release_has_no_service_side_effects(self):
        with self.assertRaisesRegex(ReleaseError, "SHA256 mismatch"):
            self.updater.update(self.archive, "0" * 64)
        self.assertFalse(self.updater.commands)
        self.assertTrue((self.paths.application / "old-marker").exists())

    def test_low_disk_space_stops_before_installation(self):
        usage = type("Usage", (), {"free": 1})()
        with patch.object(releases.shutil, "disk_usage", return_value=usage), self.assertRaisesRegex(ReleaseError, "disk space"):
            self.updater.update(self.archive, self.sha256)
        self.assertFalse(self.updater.commands)

    def test_existing_unfinished_update_requires_recovery(self):
        pending = self.paths.updates / ("f" * 32)
        pending.mkdir(parents=True)
        (pending / "update.json").write_text('{"status":"installing"}')
        with self.assertRaisesRegex(ReleaseError, "earlier update"):
            self.updater.update(self.archive, self.sha256)
        self.assertFalse(self.updater.commands)

    def test_corrupted_config_is_not_replaced_with_defaults(self):
        (self.paths.state / "config.json").write_text("corrupt")
        with self.assertRaises(ReleaseError):
            self.updater.update(self.archive, self.sha256)
        self.assertEqual((self.paths.state / "config.json").read_text(), "corrupt")
        self.assertFalse(self.updater.commands)

    def test_recovery_restores_backup_after_failed_rollback(self):
        self.updater.fail_install = True
        with patch.object(self.updater, "_rollback", side_effect=ReleaseError("fixture interruption")):
            with self.assertRaisesRegex(ReleaseError, "needs local recovery"):
                self.updater.update(self.archive, self.sha256)
        record_path = next(self.paths.updates.glob("*/update.json"))
        identifier = record_path.parent.name
        self.assertEqual(release_version((self.paths.application / "tvcleaner/__init__.py").read_bytes()), "0.2.0")
        with patch.object(releases.os, "geteuid", return_value=0):
            result = self.updater.recover(identifier)
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual((self.paths.application / "old-marker").read_text(), "old installation")
        self.assert_runtime_preserved()

    def test_recovery_handles_missing_current_application_after_power_loss(self):
        self.updater.fail_install = True
        with patch.object(self.updater, "_rollback", side_effect=ReleaseError("fixture interruption")):
            with self.assertRaises(ReleaseError):
                self.updater.update(self.archive, self.sha256)
        record_path = next(self.paths.updates.glob("*/update.json"))
        identifier = record_path.parent.name
        import shutil
        shutil.rmtree(self.paths.application)
        with patch.object(releases.os, "geteuid", return_value=0):
            result = self.updater.recover(identifier)
        self.assertEqual(result["status"], "rolled_back")
        self.assertTrue((self.paths.application / "old-marker").exists())

    def test_recovery_does_not_downgrade_a_completed_update(self):
        result = self.updater.update(self.archive, self.sha256)
        with patch.object(releases.os, "geteuid", return_value=0), self.assertRaisesRegex(ReleaseError, "already finished"):
            self.updater.recover(result["id"])
        self.assertEqual(release_version((self.paths.application / "tvcleaner/__init__.py").read_bytes()), "0.2.0")

    def test_rollback_restores_previously_disabled_and_stopped_service(self):
        self.updater.active = False
        self.updater.enabled = False
        self.updater.fail_install = True
        with self.assertRaises(ReleaseError):
            self.updater.update(self.archive, self.sha256)
        self.assertFalse(self.updater.active)
        self.assertFalse(self.updater.enabled)
        self.assert_runtime_preserved()


if __name__ == "__main__":
    unittest.main()

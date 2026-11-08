"""Reviewed, local release updates. No network updater or downloaded code in preflight.

The expected SHA256 is an operator-supplied trust decision, not a publisher
signature. Run this installed module (or a reviewed downloaded copy) with the
system Python. Archive inspection never imports any code from the archive.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener
import uuid
import zipfile


ARCHIVE_ROOT = "universal-tv-debloater"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 16000
MAX_JSON_BYTES = 4 * 1024 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}")
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
TRANSACTION_RE = re.compile(r"[0-9a-f]{32}")
SERVICE = "tvcleaner.service"
UNFINISHED_RUNS = {"applying", "restoring", "interrupted", "needs_restore"}
FINAL_UPDATES = {"completed", "rolled_back", "aborted"}
REQUIRED_FILES = {
    "UPSTREAM.json", "LICENSE", "README.md", "scripts/install-pi.sh",
    "scripts/pitv-update.service", "scripts/pitv-update.path",
    "scripts/pitv-auto-update.service", "scripts/pitv-auto-update.timer",
    "scripts/pitv-wifi.service", "scripts/pitv-wifi.path",
    "scripts/pitv-logind.conf", "scripts/pitv-sleep.conf",
    "scripts/cli.py", "tvcleaner/__init__.py", "tvcleaner/__main__.py",
    "tvcleaner/service.py", "tvcleaner/transport.py", "tvcleaner/web.py",
    "tvcleaner/catalog.py", "tvcleaner/policy.py", "tvcleaner/storage.py", "tvcleaner/updates.py",
    "tvcleaner/wifi.py",
}
REQUIRED_PINNED = {
    "LICENSE", "tools/tv.py", "tools/recovery.py", "tools/disable.sh",
    "tools/restore.sh", "tools/requirements.txt",
}
PRIVATE_PREFIXES = (
    ".git", ".venv", ".state", "state", "vendor/cleanyourtv/tools/venv",
    "vendor/cleanyourtv/tools/adbkey", "vendor/cleanyourtv/backup",
    "vendor/cleanyourtv/shots", "vendor/cleanyourtv/launcher/.gradle",
)


class ReleaseError(RuntimeError):
    """An update failed a prerequisite or needs local recovery."""


def _hash_file(stream) -> str:
    stream.seek(0)
    value = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        value.update(chunk)
    stream.seek(0)
    return value.hexdigest()


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseError("JSON contains a duplicate key.")
        result[key] = value
    return result


def _decode_json(value: bytes) -> dict[str, Any]:
    if len(value) > MAX_JSON_BYTES:
        raise ReleaseError("A release or state manifest is too large.")
    try:
        result = json.loads(value.decode("utf-8"), object_pairs_hook=_json_pairs)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ReleaseError("A release or state manifest is not valid JSON.") from error
    if not isinstance(result, dict):
        raise ReleaseError("A release or state manifest must be a JSON object.")
    return result


def release_version(source: bytes) -> str:
    """Read a literal __version__ assignment without executing Python."""
    if len(source) > 65536:
        raise ReleaseError("The release version file is too large.")
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeError, ValueError, RecursionError) as error:
        raise ReleaseError("The release version file is malformed.") from error
    values = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                raise ReleaseError("The release version must be a literal major.minor.patch value.")
            values.append(value.value)
    if len(values) != 1 or not VERSION_RE.fullmatch(values[0]):
        raise ReleaseError("The release must declare exactly one major.minor.patch version.")
    return values[0]


def _version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise ReleaseError("The installed release version is invalid.")
    return tuple(int(part) for part in value.split("."))


def _relative_name(value: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096:
        raise ReleaseError("The archive contains an invalid path.")
    if "\\" in value or ":" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ReleaseError("The archive contains a nonportable or unsafe path.")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or PurePosixPath(value).is_absolute():
        raise ReleaseError("The archive contains an absolute, empty or parent path.")
    return value


def _private_name(relative: str) -> bool:
    if any(relative == prefix or relative.startswith(prefix + "/") for prefix in PRIVATE_PREFIXES):
        return True
    return any(part in ("__pycache__", "adbkey", "adbkey.pub", "auth.json") for part in relative.split("/"))


def _archive_members(archive: zipfile.ZipFile):
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ENTRIES:
        raise ReleaseError("The archive has no entries or exceeds the entry limit.")
    files = {}
    names = {}
    directories = set()
    total = 0
    for info in infos:
        raw = info.orig_filename
        if raw != info.filename:
            raise ReleaseError("An archive filename contains a hidden terminator.")
        directory = info.is_dir()
        name = _relative_name(raw[:-1] if directory else raw)
        if name == ARCHIVE_ROOT:
            if not directory:
                raise ReleaseError("The release root must be a directory.")
            relative = ""
        elif name.startswith(ARCHIVE_ROOT + "/"):
            relative = name[len(ARCHIVE_ROOT) + 1:]
        else:
            raise ReleaseError("Every entry must be under universal-tv-debloater/.")
        folded = name.casefold()
        if folded in names:
            raise ReleaseError("The archive contains duplicate or case-colliding paths.")
        names[folded] = directory
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or mode & 0o7000:
            raise ReleaseError("Symlinks, special files and privileged file modes are not allowed.")
        if (kind == stat.S_IFDIR and not directory) or (kind == stat.S_IFREG and directory):
            raise ReleaseError("An archive entry has inconsistent file and directory types.")
        if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ReleaseError("Encrypted or unsupported compressed entries are not allowed.")
        if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
            raise ReleaseError("An archive entry exceeds the size limit.")
        total += info.file_size
        if total > MAX_EXPANDED_BYTES:
            raise ReleaseError("The expanded archive exceeds the size limit.")
        if directory:
            if info.file_size:
                raise ReleaseError("Archive directories must not contain file data.")
            directories.add(relative)
        else:
            if _private_name(relative):
                raise ReleaseError("A release archive must not contain credentials or runtime state.")
            files[relative] = info
    for name, directory in names.items():
        parts = name.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if names.get(parent) is False:
                raise ReleaseError("An archive file is also used as a parent directory.")
    if not REQUIRED_FILES <= files.keys():
        raise ReleaseError("The archive is missing required application files.")
    return files, directories, total


@contextmanager
def _verified_archive(path: Path, expected_sha256: str, installed_version: str | None = None):
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256.lower()):
        raise ReleaseError("Provide the expected 64-character SHA256 from the release handoff.")
    expected_sha256 = expected_sha256.lower()
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ReleaseError("The release ZIP could not be opened as a regular local file.") from error
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_ARCHIVE_BYTES:
            raise ReleaseError("The release must be a regular ZIP file within the size limit.")
        actual = _hash_file(stream)
        if actual != expected_sha256:
            raise ReleaseError("Release SHA256 mismatch. Nothing was installed.")
        try:
            with zipfile.ZipFile(stream) as archive:
                files, directories, total = _archive_members(archive)
                version = release_version(archive.read(files["tvcleaner/__init__.py"]))
                if installed_version is not None and _version_tuple(version) <= _version_tuple(installed_version):
                    raise ReleaseError("The update must have a newer version. Reinstalls and downgrades are refused.")
                manifest = _decode_json(archive.read(files["UPSTREAM.json"]))
                if (manifest.get("repository") != "https://github.com/Gathin23/cleanyourtv"
                        or not isinstance(manifest.get("commit"), str)
                        or not re.fullmatch(r"[0-9a-f]{40}", manifest["commit"])
                        or not isinstance(manifest.get("files"), dict)
                        or not REQUIRED_PINNED <= manifest["files"].keys()):
                    raise ReleaseError("The upstream source manifest is incomplete or malformed.")
                pinned = {}
                for relative, expected in manifest["files"].items():
                    _relative_name(relative)
                    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
                        raise ReleaseError("An upstream source digest is malformed.")
                    member = "vendor/cleanyourtv/" + relative
                    if member not in files:
                        raise ReleaseError("A pinned upstream file is missing from the archive.")
                    pinned[member] = expected
                for relative in files:
                    if relative.startswith("vendor/cleanyourtv/"):
                        vendor = relative[len("vendor/cleanyourtv/"):]
                        used_source = (vendor.startswith("tools/") and vendor.endswith((".py", ".sh", ".txt")))
                        used_catalog = (vendor.startswith("examples/") and vendor.endswith((".json", ".txt")))
                        if (used_source or used_catalog) and relative not in pinned:
                            raise ReleaseError("A runnable upstream tool or catalog file is not pinned.")
                # Reading every entry validates CRCs before extraction. Retain the
                # hashes so extraction also detects a source changing in place.
                hashes = {}
                for relative, info in files.items():
                    value = hashlib.sha256()
                    count = 0
                    with archive.open(info) as member:
                        while chunk := member.read(1024 * 1024):
                            count += len(chunk)
                            if count > info.file_size or count > MAX_MEMBER_BYTES:
                                raise ReleaseError("An archive entry expanded beyond its declared size.")
                            value.update(chunk)
                    if count != info.file_size:
                        raise ReleaseError("An archive entry has incomplete data.")
                    hashes[relative] = value.hexdigest()
                    if relative in pinned and hashes[relative] != pinned[relative]:
                        raise ReleaseError("A pinned upstream file does not match its release manifest.")
                result = {"version": version, "sha256": actual, "upstream_commit": manifest["commit"],
                          "file_count": len(files), "pinned_files": len(pinned),
                          "archive_bytes": before.st_size, "expanded_bytes": total}
                yield archive, files, directories, hashes, result
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError, EOFError) as error:
            if isinstance(error, ReleaseError):
                raise
            raise ReleaseError("The ZIP could not be completely validated or extracted.") from error
        after = os.fstat(stream.fileno())
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or _hash_file(stream) != expected_sha256):
            raise ReleaseError("The release ZIP changed while it was being verified. Nothing will be installed.")


def inspect_release(archive: Path, expected_sha256: str, installed_version: str | None = None) -> dict:
    """Validate a ZIP and report metadata, without executing or extracting it."""
    with _verified_archive(archive, expected_sha256, installed_version) as (_, _, _, _, metadata):
        result = dict(metadata)
    return result


def stage_release(archive: Path, expected_sha256: str, destination: Path,
                  installed_version: str | None = None) -> dict:
    """Extract a fully checked release into a new, previously absent directory."""
    destination = Path(destination)
    created = False
    try:
        with _verified_archive(archive, expected_sha256, installed_version) as (source, files, dirs, hashes, metadata):
            _no_symlink_parents(destination.parent)
            destination.mkdir(mode=0o700)
            created = True
            for directory in sorted(dirs, key=lambda item: (item.count("/"), item)):
                if directory:
                    (destination / directory).mkdir(mode=0o755, parents=True, exist_ok=True)
            for relative, info in files.items():
                path = destination / relative
                path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                value = hashlib.sha256()
                count = 0
                with source.open(info) as reader, path.open("xb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        count += len(chunk)
                        if count > info.file_size:
                            raise ReleaseError("A release entry changed during extraction.")
                        value.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if count != info.file_size or value.hexdigest() != hashes[relative]:
                    raise ReleaseError("A release entry changed during extraction.")
                path.chmod(0o755 if (info.external_attr >> 16) & 0o111 else 0o644)
            destination.chmod(0o755)
            _fsync_directory(destination)
            result = dict(metadata, directory=str(destination))
        return result
    except Exception:
        if created:
            shutil.rmtree(destination)
        raise


def _no_symlink_parents(path: Path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ReleaseError("An installation or state path is a symlink. Review it locally before updating.")


def _regular_bytes(path: Path, limit=MAX_JSON_BYTES) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ReleaseError(f"Required local file could not be read: {path.name}") from error
    with os.fdopen(descriptor, "rb") as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode) or status.st_size > limit:
            raise ReleaseError(f"Required local file is not a bounded regular file: {path.name}")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ReleaseError("A local state file exceeded its size limit.")
        return data


def _fsync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, value: bytes, mode=0o600, owner=None):
    _no_symlink_parents(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".update-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            if owner is not None:
                os.fchown(stream.fileno(), *owner)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path: Path, value, owner=None):
    _atomic_bytes(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), owner=owner)


@contextmanager
def _exclusive_lock(path: Path, owner=None):
    _no_symlink_parents(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReleaseError("An update lock is not a regular file.")
        if owner is not None:
            os.fchown(descriptor, *owner)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReleaseError("A cleanup, restore, configuration change or another update is running. Try again after it finishes.") from None
        yield
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class UpdatePaths:
    application: Path = Path("/opt/universal-tv-debloater")
    state: Path = Path("/var/lib/tvcleaner/state")
    updates: Path = Path("/var/lib/tvcleaner-updates")
    service_file: Path = Path("/etc/systemd/system/tvcleaner.service")
    wrapper: Path = Path("/usr/local/bin/tvcleaner")
    usb_rule: Path = Path("/etc/udev/rules.d/70-tvcleaner-adb.rules")
    update_service: Path = Path("/etc/systemd/system/pitv-update.service")
    update_path: Path = Path("/etc/systemd/system/pitv-update.path")
    auto_update_service: Path = Path("/etc/systemd/system/pitv-auto-update.service")
    auto_update_timer: Path = Path("/etc/systemd/system/pitv-auto-update.timer")
    wifi_service: Path = Path("/etc/systemd/system/pitv-wifi.service")
    wifi_path: Path = Path("/etc/systemd/system/pitv-wifi.path")

    def managed_files(self):
        return {"service": self.service_file, "wrapper": self.wrapper, "usb_rule": self.usb_rule,
                "update_service": self.update_service, "update_path": self.update_path,
                "auto_update_service": self.auto_update_service, "auto_update_timer": self.auto_update_timer,
                "wifi_service": self.wifi_service, "wifi_path": self.wifi_path}


def _disabled_config(state: Path) -> tuple[dict, bytes | None]:
    path = state / "config.json"
    original = _regular_bytes(path) if path.exists() else None
    value = _decode_json(original) if original is not None else {"automatic": {"enabled": False, "cidr": ""}, "enrollments": {}}
    automatic = value.setdefault("automatic", {"enabled": False, "cidr": ""})
    if not isinstance(automatic, dict):
        raise ReleaseError("The appliance automatic-mode configuration is malformed.")
    automatic["enabled"] = False
    return value, original


def _check_unfinished_runs(state: Path):
    runs = state / "runs"
    if runs.is_symlink():
        raise ReleaseError("The runs directory must not be a symlink.")
    for path in runs.glob("*/run.json"):
        _no_symlink_parents(path.parent)
        run = _decode_json(_regular_bytes(path))
        status = run.get("status")
        if not isinstance(status, str) or status in UNFINISHED_RUNS:
            raise ReleaseError("A TV has an unfinished cleanup or restore. Resolve that run before updating the appliance.")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class ReleaseUpdater:
    """Root-only installed-app updater; path injection exists for isolated tests."""

    def __init__(self, paths: UpdatePaths | None = None, health_port: int | None = None):
        self.paths = paths or UpdatePaths()
        if health_port is not None and (isinstance(health_port, bool) or not isinstance(health_port, int) or not 1 <= health_port <= 65535):
            raise ReleaseError("The health-check port is invalid.")
        self.health_port = health_port

    def _command(self, arguments, *, check=True, timeout=30):
        try:
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False,
                                    env=self._environment())
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReleaseError(f"The local command did not complete: {Path(arguments[0]).name}") from error
        if check and result.returncode:
            raise ReleaseError(f"The local command failed: {Path(arguments[0]).name} {arguments[1] if len(arguments) > 1 else ''}")
        return result

    @staticmethod
    def _environment():
        environment = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME", "BASH_ENV", "ENV", "TVCLEANER_STATE"):
            environment.pop(name, None)
        environment.update(PATH="/usr/sbin:/usr/bin:/sbin:/bin", PYTHONDONTWRITEBYTECODE="1")
        return environment

    def _require_installation(self):
        if os.geteuid() != 0 or not sys.platform.startswith("linux"):
            raise ReleaseError("Run this update with sudo on the Linux Pi, not on the Mac.")
        for path in (self.paths.application, self.paths.state, self.paths.updates):
            _no_symlink_parents(path)
        if not self.paths.application.is_dir() or not self.paths.state.is_dir():
            raise ReleaseError("An existing installed appliance is required. Use install-pi.sh for the first installation.")
        if not (self.paths.application / "UPSTREAM.json").is_file():
            raise ReleaseError("The installation directory is not this application.")
        if self.paths.application.stat().st_uid != 0:
            raise ReleaseError("The application directory must be root-owned before it can be updated.")
        self.paths.updates.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.paths.updates.stat().st_uid != 0:
            raise ReleaseError("The update-history directory must be root-owned.")
        self.paths.updates.chmod(0o700)

    def _state_owner(self):
        status = self.paths.state.stat()
        return status.st_uid, status.st_gid

    @contextmanager
    def _locks(self):
        with ExitStack() as stack:
            stack.enter_context(_exclusive_lock(self.paths.updates / "update.lock"))
            owner = self._state_owner()
            stack.enter_context(_exclusive_lock(self.paths.state / "mutation.lock", owner))
            stack.enter_context(_exclusive_lock(self.paths.state / "configuration.lock", owner))
            yield

    def _journal(self, folder, record):
        _atomic_json(folder / "update.json", record)

    def _backup_managed(self, folder):
        result = {}
        for name, path in self.paths.managed_files().items():
            _no_symlink_parents(path)
            if path.exists():
                data = _regular_bytes(path)
                mode = stat.S_IMODE(path.stat().st_mode)
                _atomic_bytes(folder / f"{name}.before", data)
                result[name] = {"exists": True, "mode": mode, "sha256": hashlib.sha256(data).hexdigest()}
            else:
                result[name] = {"exists": False}
        return result

    def _restore_managed(self, folder, record):
        for name, path in self.paths.managed_files().items():
            # An older interrupted update may have recorded fewer managed units.
            if name not in record["managed_files"]:
                continue
            item = record["managed_files"][name]
            _no_symlink_parents(path)
            if item["exists"]:
                data = _regular_bytes(folder / f"{name}.before")
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ReleaseError("A saved service or wrapper backup changed; recovery stopped.")
                _atomic_bytes(path, data, item["mode"])
            elif path.exists():
                if not path.is_file():
                    raise ReleaseError("A managed installation file changed type; recovery stopped.")
                path.unlink()
                _fsync_directory(path.parent)

    def _installer(self, folder):
        log = folder / "install.log"
        with log.open("xb") as output:
            os.fchmod(output.fileno(), 0o600)
            child = subprocess.Popen(["bash", str(self.paths.application / "scripts/install-pi.sh")],
                                     cwd=self.paths.application, env={**self._environment(), "PITV_RELEASE_UPDATE": "1"},
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            try:
                code = child.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                raise ReleaseError("The installer exceeded its 30-minute limit.") from None
            finally:
                output.flush()
                os.fsync(output.fileno())
        # The v0.1 installer prints the private control token. Keep it out of
        # retained update logs, even though those logs are root-only.
        try:
            token = _decode_json(_regular_bytes(self.paths.state / "access.json")).get("token")
            if isinstance(token, str) and token:
                data = _regular_bytes(log, 32 * 1024 * 1024)
                _atomic_bytes(log, data.replace(token.encode("utf-8"), b"[access token redacted]"))
        except ReleaseError:
            pass
        if code:
            raise ReleaseError("The release installer failed. See the private installation log.")

    def _health_check(self, version):
        self._command(["systemctl", "is-active", "--quiet", SERVICE])
        self._command(["systemctl", "is-enabled", "--quiet", SERVICE])
        status = _decode_json(self._command([str(self.paths.wrapper), "status"], timeout=60).stdout.encode("utf-8"))
        if status.get("version") != version or status.get("automatic", {}).get("enabled") is not False:
            raise ReleaseError("The installed CLI did not confirm the new version with automatic cleanup off.")
        self._command(["runuser", "-u", "tvcleaner", "--", "adb", "version"])
        port = self.health_port
        if port is None:
            command = self._command(["systemctl", "show", SERVICE, "--property=ExecStart", "--value"]).stdout
            ports = set(re.findall(r"--port(?:=|\s+)([0-9]{1,5})(?=\s|;|$)", command))
            if len(ports) != 1 or not 1 <= int(next(iter(ports))) <= 65535:
                raise ReleaseError("The service's HTTP port could not be determined. Supply --port explicitly.")
            port = int(next(iter(ports)))
        token = _decode_json(_regular_bytes(self.paths.state / "access.json")).get("token")
        if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
            raise ReleaseError("The local web access token is unavailable.")
        # Never send this header through an ambient proxy or follow a redirect.
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        request = Request(f"http://127.0.0.1:{port}/api/status", headers={"Authorization": "Bearer " + token})
        deadline = time.monotonic() + 10
        while True:
            try:
                with opener.open(request, timeout=2) as response:
                    data = _decode_json(response.read(MAX_JSON_BYTES + 1))
                if data.get("version") != version or data.get("automatic", {}).get("enabled") is not False:
                    raise ReleaseError("The running web service did not confirm the expected version with automatic cleanup off.")
                return {"port": port, "version": version, "automatic_enabled": False}
            except (URLError, OSError) as error:
                if time.monotonic() >= deadline:
                    raise ReleaseError("The authenticated local control page did not become healthy.") from error
                time.sleep(0.25)

    def _rollback(self, folder, record):
        # Never replace code underneath a new service that could not be stopped.
        self._command(["systemctl", "stop", SERVICE])
        self._command(["systemctl", "stop", "pitv-update.path"], check=False)
        self._command(["systemctl", "stop", "pitv-auto-update.timer"], check=False)
        self._command(["systemctl", "stop", "pitv-wifi.path"], check=False)
        previous = self.paths.application.with_name(".tvcleaner-previous-" + record["id"])
        failed = self.paths.application.with_name(".tvcleaner-failed-" + record["id"])
        if previous.exists():
            _no_symlink_parents(previous)
            if self.paths.application.exists():
                _no_symlink_parents(self.paths.application)
                if failed.exists():
                    raise ReleaseError("An earlier failed release directory already exists; inspect it before recovery.")
                os.replace(self.paths.application, failed)
            os.replace(previous, self.paths.application)
            _fsync_directory(self.paths.application.parent)
        elif release_version(_regular_bytes(self.paths.application / "tvcleaner/__init__.py", 65536)) != record["old_version"]:
            raise ReleaseError("The previous application backup is missing. Keep the current files for local recovery.")
        self._restore_managed(folder, record)
        configuration = _decode_json(_regular_bytes(folder / "config.disabled.json"))
        configuration["automatic"]["enabled"] = False
        _atomic_json(self.paths.state / "config.json", configuration, self._state_owner())
        self._command(["systemctl", "daemon-reload"])
        self._command(["udevadm", "control", "--reload-rules"], check=False)
        self._command(["systemctl", "enable" if record.get("was_update_path_enabled") else "disable", "pitv-update.path"], check=False)
        if record.get("was_update_path_active"):
            self._command(["systemctl", "start", "pitv-update.path"])
        self._command(["systemctl", "enable" if record.get("was_auto_update_timer_enabled") else "disable", "pitv-auto-update.timer"], check=False)
        if record.get("was_auto_update_timer_active"):
            self._command(["systemctl", "start", "pitv-auto-update.timer"])
        self._command(["systemctl", "enable" if record.get("was_wifi_path_enabled") else "disable", "pitv-wifi.path"], check=False)
        if record.get("was_wifi_path_active"):
            self._command(["systemctl", "start", "pitv-wifi.path"])
        self._command(["systemctl", "enable" if record["was_enabled"] else "disable", SERVICE])
        if record["was_active"]:
            self._command(["systemctl", "start", SERVICE])
            self._command(["systemctl", "is-active", "--quiet", SERVICE])
        record.update(status="rolled_back", finished_at=time.time(), automatic_enabled=False)
        self._journal(folder, record)

    def update(self, archive: Path, expected_sha256: str) -> dict:
        self._require_installation()
        old_version = release_version(_regular_bytes(self.paths.application / "tvcleaner/__init__.py", 65536))
        metadata = inspect_release(archive, expected_sha256, old_version)
        # Reserve disk for source staging, the archive and dependencies. The old
        # installation is renamed on the same filesystem, not copied.
        if shutil.disk_usage(self.paths.application.parent).free < metadata["expanded_bytes"] * 2 + 256 * 1024 * 1024:
            raise ReleaseError("There is not enough free disk space to stage and verify the update.")
        with self._locks():
            _check_unfinished_runs(self.paths.state)
            for pending in self.paths.updates.glob("*/update.json"):
                previous_update = _decode_json(_regular_bytes(pending))
                if previous_update.get("status") not in FINAL_UPDATES:
                    raise ReleaseError("An earlier update needs recovery. Run update-pi.sh recover with its update ID first.")
            current = release_version(_regular_bytes(self.paths.application / "tvcleaner/__init__.py", 65536))
            if current != old_version:
                raise ReleaseError("The installed release changed during preflight. Start the update again.")
            identifier = uuid.uuid4().hex
            folder = self.paths.updates / identifier
            folder.mkdir(mode=0o700)
            configuration, original = _disabled_config(self.paths.state)
            if original is not None:
                _atomic_bytes(folder / "config.before.json", original)
            _atomic_json(folder / "config.disabled.json", configuration)
            record = {"id": identifier, "status": "prepared", "created_at": time.time(),
                      "old_version": old_version, "release": metadata,
                      "was_active": self._command(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0,
                      "was_enabled": self._command(["systemctl", "is-enabled", "--quiet", SERVICE], check=False).returncode == 0,
                      "was_update_path_active": self._command(["systemctl", "is-active", "--quiet", "pitv-update.path"], check=False).returncode == 0,
                      "was_update_path_enabled": self._command(["systemctl", "is-enabled", "--quiet", "pitv-update.path"], check=False).returncode == 0,
                      "was_auto_update_timer_active": self._command(["systemctl", "is-active", "--quiet", "pitv-auto-update.timer"], check=False).returncode == 0,
                      "was_auto_update_timer_enabled": self._command(["systemctl", "is-enabled", "--quiet", "pitv-auto-update.timer"], check=False).returncode == 0,
                      "was_wifi_path_active": self._command(["systemctl", "is-active", "--quiet", "pitv-wifi.path"], check=False).returncode == 0,
                      "was_wifi_path_enabled": self._command(["systemctl", "is-enabled", "--quiet", "pitv-wifi.path"], check=False).returncode == 0,
                      "managed_files": self._backup_managed(folder), "automatic_enabled": False}
            self._journal(folder, record)
            staging_parent = Path(tempfile.mkdtemp(prefix=".tvcleaner-stage-", dir=self.paths.application.parent))
            staging = staging_parent / "application"
            service_touched = False
            try:
                stage_release(archive, expected_sha256, staging, old_version)
                _atomic_json(self.paths.state / "config.json", configuration, self._state_owner())
                record["status"] = "stopping"
                self._journal(folder, record)
                service_touched = True
                self._command(["systemctl", "stop", SERVICE])
                self._command(["systemctl", "stop", "pitv-wifi.path"], check=False)
                record["status"] = "switching"
                self._journal(folder, record)
                previous = self.paths.application.with_name(".tvcleaner-previous-" + identifier)
                if previous.exists():
                    raise ReleaseError("The previous-release backup destination already exists.")
                os.replace(self.paths.application, previous)
                os.replace(staging, self.paths.application)
                _fsync_directory(self.paths.application.parent)
                record["status"] = "installing"
                self._journal(folder, record)
                self._installer(folder)
                record["status"] = "verifying"
                self._journal(folder, record)
                health = self._health_check(metadata["version"])
                record.update(status="completed", finished_at=time.time(), health=health,
                              previous_application=str(previous))
                self._journal(folder, record)
                return {"id": identifier, "status": "completed", "version": metadata["version"],
                        "previous_version": old_version, "automatic_enabled": False,
                        "port": health["port"], "history": str(folder),
                        "message": "Release installed and checked. Review its changes, then explicitly re-enable automatic cleanup."}
            except BaseException as error:
                record["error"] = str(error)
                if service_touched:
                    try:
                        self._rollback(folder, record)
                    except Exception as recovery_error:
                        record.update(status="rollback_failed", recovery_error=str(recovery_error))
                        self._journal(folder, record)
                        raise ReleaseError(f"Update stopped and rollback needs local recovery. Update ID: {identifier}") from error
                    raise ReleaseError(f"Update stopped; the previous application was restored and automatic cleanup remains off. Update ID: {identifier}") from error
                record.update(status="aborted", finished_at=time.time())
                self._journal(folder, record)
                raise
            finally:
                shutil.rmtree(staging_parent, ignore_errors=True)

    def _require_recovery(self):
        # A power failure between the two renames can leave the application path
        # absent. Recovery still uses this reviewed updater from its source ZIP.
        if os.geteuid() != 0 or not sys.platform.startswith("linux"):
            raise ReleaseError("Run recovery with sudo on the Linux Pi.")
        for path in (self.paths.application, self.paths.state, self.paths.updates):
            _no_symlink_parents(path)
        if not self.paths.state.is_dir() or not self.paths.updates.is_dir() or self.paths.updates.stat().st_uid != 0:
            raise ReleaseError("The appliance state or private update history is unavailable.")

    def recover(self, identifier: str) -> dict:
        """Recover an interrupted update, never perform an arbitrary downgrade."""
        if not isinstance(identifier, str) or not TRANSACTION_RE.fullmatch(identifier):
            raise ReleaseError("The update ID must be the 32-character ID in the private update history.")
        self._require_recovery()
        with self._locks():
            _check_unfinished_runs(self.paths.state)
            folder = self.paths.updates / identifier
            _no_symlink_parents(folder)
            record = _decode_json(_regular_bytes(folder / "update.json"))
            if record.get("id") != identifier or record.get("status") in FINAL_UPDATES:
                raise ReleaseError("This update is already finished; recovery is only for interrupted updates.")
            try:
                self._rollback(folder, record)
            except Exception as error:
                record.update(status="rollback_failed", recovery_error=str(error))
                self._journal(folder, record)
                raise ReleaseError("Update recovery did not complete. Keep the private history and application backups.") from error
            return {"id": identifier, "status": "rolled_back", "version": record["old_version"],
                    "automatic_enabled": False, "history": str(folder)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and install a reviewed local TV-cleaner release ZIP.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "stage", "update"):
        command = commands.add_parser(name)
        command.add_argument("archive", type=Path)
        command.add_argument("--sha256", required=True)
        if name == "stage":
            command.add_argument("--destination", type=Path, required=True)
        if name in ("inspect", "stage"):
            command.add_argument("--installed-version")
        if name == "update":
            command.add_argument("--port", type=int, help="Local HTTP port if systemd ExecStart cannot be inspected")
    recovery = commands.add_parser("recover", help="Restore the previous app after an interrupted update")
    recovery.add_argument("update_id")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_release(args.archive, args.sha256, args.installed_version)
        elif args.command == "stage":
            result = stage_release(args.archive, args.sha256, args.destination, args.installed_version)
        elif args.command == "update":
            print("Verifying the release and preparing the update. Dependency installation can take several minutes.", file=sys.stderr, flush=True)
            result = ReleaseUpdater(health_port=args.port).update(args.archive, args.sha256)
        else:
            result = ReleaseUpdater().recover(args.update_id)
        print(json.dumps(result, indent=2))
        return 0
    except (ReleaseError, OSError, ValueError) as error:
        print(f"TV-cleaner release: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

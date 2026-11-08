"""Pinned PiTV release checks and root-owned update requests.

The web service never accepts a download URL or a local release path. A root
systemd path unit handles a request only after independently checking the
release in the fixed PiTV repository.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import __version__
from .storage import atomic_json, read_json


REPOSITORY = "BlankNetworks/PiTV"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
STATE = Path("/var/lib/tvcleaner/state")
DOWNLOADS = Path("/var/lib/tvcleaner-updates/downloads")
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
SHA256 = re.compile(r"^sha256:([0-9a-f]{64})$")


class UpdateError(RuntimeError):
    pass


class _TrustedRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in (
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"
        ):
            raise UpdateError("The release download redirected outside GitHub.")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _opener():
    return build_opener(ProxyHandler({}), _TrustedRedirect())


def _version(value):
    match = VERSION.fullmatch(str(value))
    if not match:
        raise UpdateError("The PiTV release has an invalid version.")
    return tuple(map(int, match.groups()))


def _release(data):
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise UpdateError("No stable PiTV release is available.")
    tag = data.get("tag_name")
    version = _version(tag)
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("The PiTV release tag is invalid.")
    name = f"pitv-{tag}.zip"
    matches = [item for item in data.get("assets", []) if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise UpdateError("The PiTV release must have exactly one matching ZIP asset.")
    asset = matches[0]
    digest = asset.get("digest", "")
    match = SHA256.fullmatch(digest) if isinstance(digest, str) else None
    url = asset.get("browser_download_url", "")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
    size = asset.get("size")
    if (not match or url != expected_url or asset.get("state") != "uploaded"
            or not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE_BYTES):
        raise UpdateError("The PiTV release asset is incomplete or untrusted.")
    return {"version": tag[1:], "version_tuple": version, "tag": tag, "name": name,
            "sha256": match.group(1), "url": url, "size_bytes": size,
            "release_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
            "published_at": str(data.get("published_at") or ""),
            "notes": str(data.get("body") or "")[:2000]}


def fetch_latest():
    request = Request(LATEST_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "PiTV-updater"})
    try:
        with _opener().open(request, timeout=10) as response:
            body = response.read(MAX_METADATA_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise UpdateError(f"GitHub could not be reached (HTTP {error.code}).") from error
    except (URLError, TimeoutError, OSError) as error:
        raise UpdateError("The Pi could not check PiTV releases on GitHub.") from error
    if len(body) > MAX_METADATA_BYTES:
        raise UpdateError("The release information is too large.")
    try:
        return _release(json.loads(body))
    except (ValueError, UnicodeDecodeError, TypeError) as error:
        raise UpdateError("GitHub returned unreadable PiTV release information.") from error


def check(current_version=__version__, *, latest=None):
    current = _version(current_version)
    release = fetch_latest() if latest is None else latest
    if release is None:
        return {"available": False, "current_version": current_version,
                "message": "No PiTV release has been published yet."}
    return {"available": release["version_tuple"] > current,
            "current_version": current_version, "latest_version": release["version"],
            "sha256": release["sha256"], "size_bytes": release["size_bytes"],
            "release_url": release["release_url"], "published_at": release["published_at"],
            "notes": release["notes"]}


def status(state):
    value = read_json(Path(state) / "update-status.json", default={"state": "idle"})
    return value if isinstance(value, dict) else {"state": "idle"}


def request_install(state, version, sha256, *, demo=False):
    if demo:
        raise UpdateError("A demo cannot install PiTV releases.")
    if not isinstance(version, str) or not isinstance(sha256, str):
        raise UpdateError("Check for updates again before installing.")
    if status(state).get("state") in ("requested", "downloading", "validating", "installing"):
        raise UpdateError("A PiTV update is already in progress.")
    if (Path(state) / "update-request.json").exists():
        raise UpdateError("A PiTV update request is already pending.")
    release = fetch_latest()
    if (release is None or release["version"] != version or release["sha256"] != sha256
            or release["version_tuple"] <= _version(__version__)):
        raise UpdateError("The checked release changed. Check for updates again.")
    atomic_json(Path(state) / "update-status.json", {"state": "requested", "version": version,
                                                    "updated_at": time.time()})
    atomic_json(Path(state) / "update-request.json", {"version": version, "sha256": sha256})
    return {"state": "requested", "version": version}


def _write_status(state, value):
    path = Path(state) / "update-status.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".update-status-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({**value, "updated_at": time.time()}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _download(release, directory=DOWNLOADS):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.geteuid():
        raise UpdateError("The update download directory has the wrong owner.")
    directory.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pitv-download-", dir=directory)
    digest = hashlib.sha256()
    size = 0
    try:
        request = Request(release["url"], headers={"Accept": "application/octet-stream", "User-Agent": "PiTV-updater"})
        with _opener().open(request, timeout=30) as response:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES or size > release["size_bytes"]:
                        raise UpdateError("The PiTV release download exceeded its stated size.")
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        if size != release["size_bytes"] or digest.hexdigest() != release["sha256"]:
            raise UpdateError("The PiTV release download did not match its published SHA-256.")
        target = directory / release["name"]
        os.replace(temporary, target)
        return target
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_request(state=STATE):
    if os.geteuid() != 0 or sys.platform != "linux":
        raise UpdateError("The PiTV update worker must run as root on the Pi.")
    request_path = Path(state) / "update-request.json"
    request = read_json(request_path)
    request_path.unlink(missing_ok=True)
    if not isinstance(request, dict):
        raise UpdateError("No PiTV update request is pending.")
    try:
        release = fetch_latest()
        if (release is None or request.get("version") != release["version"]
                or request.get("sha256") != release["sha256"]
                or release["version_tuple"] <= _version(__version__)):
            raise UpdateError("The requested release changed. Check for updates again.")
        _write_status(state, {"state": "downloading", "version": release["version"]})
        archive = _download(release)
        _write_status(state, {"state": "validating", "version": release["version"]})
        from .releases import inspect_release, ReleaseUpdater
        inspect_release(archive, release["sha256"], __version__)
        _write_status(state, {"state": "installing", "version": release["version"]})
        result = ReleaseUpdater().update(archive, release["sha256"])
        _write_status(state, {"state": "completed", "version": release["version"],
                              "previous_version": result.get("previous_version")})
        return result
    except Exception as error:
        _write_status(state, {"state": "failed", "version": request.get("version"),
                              "error": str(error)[:500]})
        raise


if __name__ == "__main__":
    if sys.argv[1:] != ["install-request"]:
        raise SystemExit("Usage: python3 -m tvcleaner.updates install-request")
    install_request()

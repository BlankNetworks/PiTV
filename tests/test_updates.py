import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tvcleaner import updates
from tvcleaner import __version__

PARTS = list(map(int, __version__.split(".")))
NEXT = ".".join(map(str, (PARTS[0], PARTS[1], PARTS[2] + 1)))


def release(version=NEXT):
    tag = "v" + version
    name = f"pitv-{tag}.zip"
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "published_at": "2026-11-07T00:00:00Z", "body": "Reviewed PiTV release",
            "assets": [{"name": name, "state": "uploaded", "size": 1000,
                        "digest": "sha256:" + "a" * 64,
                        "browser_download_url": f"https://github.com/BlankNetworks/PiTV/releases/download/{tag}/{name}"}]}


class UpdateTests(unittest.TestCase):
    def test_release_is_pinned_to_exact_repository_asset_and_digest(self):
        parsed = updates._release(release())
        self.assertEqual(parsed["version"], NEXT)
        self.assertEqual(parsed["sha256"], "a" * 64)
        self.assertTrue(updates.check(__version__, latest=parsed)["available"])
        self.assertFalse(updates.check(NEXT, latest=parsed)["available"])
        for change in ("digest", "browser_download_url", "state"):
            bad = release()
            bad["assets"][0][change] = "untrusted"
            with self.subTest(change=change), self.assertRaises(updates.UpdateError):
                updates._release(bad)

    def test_request_is_written_only_for_current_reviewed_release(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(updates, "fetch_latest", return_value=updates._release(release())):
            state = Path(directory)
            with self.assertRaises(updates.UpdateError):
                updates.request_install(state, NEXT, "b" * 64)
            self.assertFalse((state / "update-request.json").exists())
            value = updates.request_install(state, NEXT, "a" * 64)
            self.assertEqual(value["state"], "requested")
            self.assertEqual(updates.status(state)["state"], "requested")
            with self.assertRaises(updates.UpdateError):
                updates.request_install(state, NEXT, "a" * 64)

    def test_demo_cannot_request_install(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(updates.UpdateError):
                updates.request_install(directory, NEXT, "a" * 64, demo=True)

    def test_download_checks_published_digest_and_size(self):
        payload = b"checked PiTV release" * 50
        item = updates._release(release())
        item["size_bytes"] = len(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()
        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(payload)
        with tempfile.TemporaryDirectory() as directory, patch.object(updates, "_opener", return_value=Opener()):
            folder = Path(directory) / "downloads"
            archive = updates._download(item, folder)
            self.assertEqual(archive.read_bytes(), payload)
            item["sha256"] = "b" * 64
            with self.assertRaises(updates.UpdateError):
                updates._download(item, folder)


if __name__ == "__main__":
    unittest.main()
import hashlib
import io

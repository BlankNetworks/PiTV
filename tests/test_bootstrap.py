"""The one-command first installer checks its release archive before extracting."""
from pathlib import Path
import hashlib
import io
import runpy
import stat
import tempfile
import unittest
from unittest import mock
import zipfile


BOOTSTRAP = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/bootstrap-pi.py.in"))
ROOT = BOOTSTRAP["ROOT"]


def archive(path, extra=()):
    with zipfile.ZipFile(path, "w") as result:
        for name in BOOTSTRAP["REQUIRED"]:
            content = ('__version__ = "@PITV_VERSION@"\n' if name == "tvcleaner/__init__.py" else "reviewed\n")
            result.writestr(f"{ROOT}/{name}", content)
        for name, content in extra:
            result.writestr(name, content)


class BootstrapTests(unittest.TestCase):
    def test_download_requires_embedded_release_digest(self):
        payload = b"example release bytes"
        digest = hashlib.sha256(payload).hexdigest()
        globals_ = BOOTSTRAP["download"].__globals__
        opener = mock.Mock()
        opener.open.side_effect = lambda *_args, **_kwargs: io.BytesIO(payload)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(globals_, {"SHA256": digest, "build_opener": lambda *_args: opener}):
            file = Path(directory) / "release.zip"
            BOOTSTRAP["download"](file)
            self.assertEqual(file.read_bytes(), payload)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(globals_, {"SHA256": "0" * 64, "build_opener": lambda *_args: opener}):
            with self.assertRaises(BOOTSTRAP["InstallError"]):
                BOOTSTRAP["download"](Path(directory) / "release.zip")

    def test_valid_archive_extracts_to_temporary_app_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "release.zip"
            archive(file)
            source = BOOTSTRAP["inspect_and_extract"](file, directory)
            self.assertEqual(source.name, ROOT)
            self.assertTrue((source / "scripts/install-pi.sh").is_file())

    def test_parent_path_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "release.zip"
            archive(file, [(f"{ROOT}/../outside.txt", "outside")])
            with self.assertRaises(BOOTSTRAP["InstallError"]):
                BOOTSTRAP["inspect_and_extract"](file, directory)
            self.assertFalse((Path(directory) / "outside.txt").exists())

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "release.zip"
            link = zipfile.ZipInfo(f"{ROOT}/link")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(file, "w") as result:
                for name in BOOTSTRAP["REQUIRED"]:
                    content = ('__version__ = "@PITV_VERSION@"\n' if name == "tvcleaner/__init__.py" else "reviewed\n")
                    result.writestr(f"{ROOT}/{name}", content)
                result.writestr(link, "../outside")
            with self.assertRaises(BOOTSTRAP["InstallError"]):
                BOOTSTRAP["inspect_and_extract"](file, directory)


if __name__ == "__main__":
    unittest.main()

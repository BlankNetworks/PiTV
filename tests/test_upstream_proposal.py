"""The scheduled upstream proposal prepares a releasable review branch."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare-upstream-pr.py"


class UpstreamProposalTests(unittest.TestCase):
    def test_bumps_pitv_version_and_describes_review_without_approving_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tvcleaner").mkdir()
            (root / "tvcleaner/__init__.py").write_text('__version__ = "0.2.4"\n')
            (root / "RELEASE_NOTES.md").write_text("# 0.2.4 PiTV release\n\nExisting notes.\n")
            result = subprocess.run([sys.executable, str(SCRIPT), "--tag", "v3.5.0",
                                     "--commit", "a" * 40, "--root", str(root)],
                                    capture_output=True, text=True, check=True)
            self.assertIn("v0.2.5", result.stdout)
            self.assertIn('"0.2.5"', (root / "tvcleaner/__init__.py").read_text())
            notes = (root / "RELEASE_NOTES.md").read_text()
            self.assertTrue(notes.startswith("# 0.2.5 PiTV release"))
            self.assertIn("CleanYourTV v3.5.0", notes)
            self.assertIn("No new automatic TV cleanup recipe", notes)
            self.assertIn("Existing notes.", notes)


if __name__ == "__main__":
    unittest.main()

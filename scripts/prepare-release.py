#!/usr/bin/env python3
"""Build and inspect the versioned PiTV release from a committed checkout."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tvcleaner import __version__
from tvcleaner.releases import inspect_release


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    notes = (root / "RELEASE_NOTES.md").read_text(encoding="utf-8").split("\n---\n", 1)[0].strip()
    if not notes.startswith(f"# {__version__} PiTV release\n"):
        raise SystemExit("The first release-notes heading must match tvcleaner.__version__.")
    tag = "v" + __version__
    args.out_dir.mkdir(parents=True, exist_ok=True)
    archive = args.out_dir / f"pitv-{tag}.zip"
    subprocess.run(["git", "-C", str(root), "archive", "--format=zip",
                    "--prefix=universal-tv-debloater/", "-o", str(archive.resolve()), "HEAD"], check=True)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    metadata = inspect_release(archive, digest)
    if metadata["version"] != __version__:
        raise SystemExit("The committed release version differs from the checkout.")
    (args.out_dir / "release-notes.md").write_text(notes + "\n", encoding="utf-8")
    (args.out_dir / "sha256.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"{tag} {archive} sha256:{digest}")


if __name__ == "__main__":
    main()

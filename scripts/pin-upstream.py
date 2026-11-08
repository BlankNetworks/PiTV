#!/usr/bin/env python3
"""Pin a clean CleanYourTV checkout already copied into vendor/cleanyourtv."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout", type=Path, help="Clean checkout of a reviewed upstream tag")
    parser.add_argument("--commit", required=True, help="Expected full commit SHA for that tag")
    parser.add_argument("--tag", help="Reviewed upstream release tag, such as v3.4.0")
    args = parser.parse_args()
    source = args.checkout.resolve()
    root = Path(__file__).resolve().parents[1]
    snapshot = root / "vendor/cleanyourtv"
    commit = git(source, "rev-parse", "HEAD").decode().strip()
    if commit != args.commit or git(source, "status", "--porcelain").strip():
        raise SystemExit("Upstream checkout is changed or is not the expected commit.")
    if args.tag:
        tagged = git(source, "rev-parse", f"{args.tag}^{{commit}}").decode().strip()
        if tagged != commit:
            raise SystemExit("The upstream tag does not resolve to the reviewed commit.")
    names = [item.decode() for item in git(source, "ls-files", "-z").split(b"\0") if item]
    expected = set(names)
    actual = {str(path.relative_to(snapshot)) for path in snapshot.rglob("*")
              if path.is_file() and "__pycache__" not in path.relative_to(snapshot).parts and path.suffix != ".pyc"}
    if expected != actual:
        raise SystemExit("Vendored snapshot differs from the reviewed upstream checkout.")
    files = {}
    for name in sorted(names):
        source_file = source / name
        snapshot_file = snapshot / name
        if source_file.is_symlink() or snapshot_file.is_symlink():
            raise SystemExit("Upstream symlinks are not accepted in a release.")
        digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
        if hashlib.sha256(snapshot_file.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Vendored file differs from upstream: {name}")
        files[name] = digest
    manifest = {"repository": "https://github.com/Gathin23/cleanyourtv", "commit": commit}
    if args.tag:
        manifest["tag"] = args.tag
    manifest["files"] = files
    (root / "UPSTREAM.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Pinned {len(files)} CleanYourTV files at {commit}.")


if __name__ == "__main__":
    main()

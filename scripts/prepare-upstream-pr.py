#!/usr/bin/env python3
"""Bump a PiTV review branch for a newly pinned CleanYourTV release."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", args.tag) or not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise SystemExit("The upstream release identifier is invalid.")
    root = args.root
    version_file = root / "tvcleaner/__init__.py"
    source = version_file.read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', source, re.MULTILINE)
    if not match:
        raise SystemExit("PiTV has no literal patch version to increase.")
    old = match.group(0)
    next_version = f"{match.group(1)}.{match.group(2)}.{int(match.group(3)) + 1}"
    version_file.write_text(source.replace(old, f'__version__ = "{next_version}"', 1), encoding="utf-8")
    notes_file = root / "RELEASE_NOTES.md"
    notes = notes_file.read_text(encoding="utf-8")
    today = datetime.now(timezone.utc)
    heading = f"# {next_version} PiTV release\n\nPrepared {today:%B} {today.day}, {today.year}.\n\n"
    addition = (f"- Proposes the reviewed CleanYourTV {args.tag} snapshot at `{args.commit}`. "
                "Review the catalog, tool changes, profile notes and TV safety before merging.\n"
                "- No new automatic TV cleanup recipe is approved by this proposal.\n\n---\n\n")
    notes_file.write_text(heading + addition + notes, encoding="utf-8")
    print(f"Prepared PiTV v{next_version} review proposal for CleanYourTV {args.tag}.")


if __name__ == "__main__":
    main()

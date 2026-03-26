#!/usr/bin/env python3
"""Extract a single release entry from CHANGELOG.md."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"


def main(version: str) -> None:
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    heading = f"## [{version}]"

    try:
        start = lines.index(heading) + 1
    except ValueError as exc:
        raise SystemExit(f"Version {version} not found in {CHANGELOG}") from exc

    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].startswith("## ["):
            end = idx
            break

    body = lines[start:end]

    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()

    if not body:
        raise SystemExit(f"No release notes found for version {version}")

    sys.stdout.write("\n".join(body))
    sys.stdout.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: extract_release_notes.py <version>")

    main(sys.argv[1])

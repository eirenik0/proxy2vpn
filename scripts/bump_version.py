#!/usr/bin/env python3
"""Simple version bump utility for proxy2vpn."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
VERSION_FILE = ROOT / "src" / "proxy2vpn" / "_version.py"


def update_file(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text = re.sub(pattern, replacement, text)
    path.write_text(new_text, encoding="utf-8")


def main(version: str) -> None:
    update_file(PYPROJECT, r'version = "[^"]+"', f'version = "{version}"')
    update_file(VERSION_FILE, r'__version__ = "[^"]+"', f'__version__ = "{version}"')
    print(f"Bumped version to {version}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: bump_version.py <version>")
        sys.exit(1)
    main(sys.argv[1])

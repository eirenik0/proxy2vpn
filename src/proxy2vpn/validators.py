from __future__ import annotations

from pathlib import Path
import re

import typer

# Allowed characters for user provided names (profiles, services, etc.)
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def sanitize_name(value: str) -> str:
    """Trim and validate NAME-like parameters."""
    cleaned = value.strip()
    if not _NAME_RE.match(cleaned):
        raise typer.BadParameter("Use alphanumeric characters, '-' or '_' only")
    return cleaned


def validate_port(port: int) -> int:
    """Ensure PORT is within valid bounds."""
    if not 0 <= port <= 65535:
        raise typer.BadParameter("Port must be between 0 and 65535")
    return port


def sanitize_path(path: Path) -> Path:
    """Resolve and return PATH with user expansion."""
    return path.expanduser().resolve()

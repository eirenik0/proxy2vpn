from __future__ import annotations

import typing
from pathlib import Path
import re

import typer

from proxy2vpn.common import abort

if typing.TYPE_CHECKING:
    from .compose_manager import ComposeManager
    from proxy2vpn.core.models import VPNService

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


def validate_all_name_args(all_flag: bool, name: str | None) -> None:
    """Validate mutually exclusive --all and NAME arguments."""
    if all_flag and name is not None:
        abort("Cannot specify NAME when using --all")
    if not all_flag and name is None:
        abort("Specify a service NAME or use --all")


def validate_service_exists(manager: "ComposeManager", name: str) -> "VPNService":
    """Validate service exists and return it, abort if not found."""
    try:
        return manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

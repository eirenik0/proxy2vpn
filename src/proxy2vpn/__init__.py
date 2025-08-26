"""proxy2vpn Python package."""

try:
    from importlib.metadata import version

    __version__ = version("proxy2vpn")
except Exception:
    # Fallback when package is not installed (development mode)
    __version__ = "dev"

from proxy2vpn.adapters import (
    server_manager,
    docker_ops,
    compose_utils,
    fleet_commands,
    ip_utils,
    monitoring,
    server_monitor,
    compose_validator,
    fleet_manager,
)
from proxy2vpn.cli import typer_ext
from proxy2vpn.core.services import diagnostics

__all__ = [
    "server_manager",
    "docker_ops",
    "compose_utils",
    "fleet_commands",
    "ip_utils",
    "monitoring",
    "server_monitor",
    "diagnostics",
    "compose_validator",
    "typer_ext",
    "fleet_manager",
]

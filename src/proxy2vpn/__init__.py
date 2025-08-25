"""proxy2vpn Python package."""

try:
    from importlib.metadata import version

    __version__ = version("proxy2vpn")
except Exception:
    # Fallback when package is not installed (development mode)
    __version__ = "dev"

# Maintain backward compatibility by exposing the old flat structure
# Import from new locations but expose at root level

# Core models and config
from proxy2vpn.core.models import VPNService, Profile
from proxy2vpn.core import config
from proxy2vpn.core.services import diagnostics

from proxy2vpn.cli.main import app

# Adapters - main interfaces
from proxy2vpn.adapters import compose_manager
from proxy2vpn.adapters import docker_ops
from proxy2vpn.adapters import compose_validator
from proxy2vpn.adapters import ip_utils
from proxy2vpn.adapters import server_manager
from proxy2vpn.adapters import http_client
from proxy2vpn.adapters import compose_utils
from proxy2vpn.adapters import validators
from proxy2vpn.adapters import logging_utils
from proxy2vpn.adapters import utils
from proxy2vpn.adapters import display_utils
from proxy2vpn.adapters import fleet_manager
from proxy2vpn.adapters import fleet_commands
from proxy2vpn.adapters import monitoring
from proxy2vpn.adapters import profile_allocator
from proxy2vpn.adapters import server_monitor

# CLI components
from proxy2vpn.cli import typer_ext


# For backward compatibility - create cli namespace with app attribute
class CLI:
    def __init__(self, app):
        self.app = app

    def __getattr__(self, name: str):  # pragma: no cover - simple delegation
        # Provide convenient access for tests to monkeypatch core classes/functions
        # Resolve known symbols from submodules
        from .adapters.compose_manager import ComposeManager
        from .adapters.server_manager import ServerManager
        from .adapters.http_client import GluetunControlClient
        from .cli.commands.profile import apply as profile_apply

        mapping = {
            "ComposeManager": ComposeManager,
            "ServerManager": ServerManager,
            "GluetunControlClient": GluetunControlClient,
            "profile_apply": profile_apply,
        }
        if name in mapping:
            return mapping[name]
        # As a fallback, try to return attribute from the top-level package
        try:
            return globals()[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


cli = CLI(app)

__all__ = [
    "cli",
    "VPNService",
    "Profile",
    "config",
    "compose_manager",
    "server_manager",
    "http_client",
    "__version__",
    "diagnostics",
    "docker_ops",
    "compose_validator",
    "ip_utils",
    "validators",
    "logging_utils",
    "utils",
    "display_utils",
    "fleet_manager",
    "fleet_commands",
    "monitoring",
    "profile_allocator",
    "server_monitor",
    "typer_ext",
    "compose_utils",
    "compose_validator",
]

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
from .core.models import VPNService, Profile
from .core import config
from .core.services import diagnostics

from .cli.main import app

# Adapters - main interfaces
from .adapters.compose_manager import ComposeManager
from .adapters import docker_ops
from .adapters import compose_validator
from .adapters import ip_utils
from .adapters.server_manager import ServerManager
from .adapters.http_client import GluetunControlClient
from .adapters.compose_utils import load_compose, save_compose, set_service_image
from .adapters import validators
from .adapters import logging_utils
from .adapters import utils
from .adapters import display_utils
from .adapters import fleet_manager
from .adapters import fleet_commands
from .adapters import monitoring
from .adapters import profile_allocator
from .adapters import server_monitor

# CLI components
from .cli import typer_ext

# For backward compatibility - expose modules by their old names
models = type("models", (), {"VPNService": VPNService, "Profile": Profile})()
compose_manager = ComposeManager
server_manager = type("server_manager", (), {})()
for attr in dir(ServerManager):
    if not attr.startswith("_"):
        setattr(server_manager, attr, getattr(ServerManager, attr))

# Create module-like objects for backward compatibility
compose_utils = type("compose_utils", (), {})()
control_client = GluetunControlClient


# For backward compatibility - create cli namespace with app attribute
class CLI:
    def __init__(self, app):
        self.app = app


cli = CLI(app)

__all__ = [
    "cli",
    "VPNService",
    "Profile",
    "config",
    "ComposeManager",
    "ServerManager",
    "GluetunControlClient",
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
    "set_service_image",
    "save_compose",
    "load_compose",
]

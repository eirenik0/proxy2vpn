"""Common CLI utilities and validation functions."""

from .compose_manager import ComposeManager
from ..core.models import VPNService
from .utils import abort


def validate_all_name_args(all_flag: bool, name: str | None) -> None:
    """Validate mutually exclusive --all and NAME arguments."""
    if all_flag and name is not None:
        abort("Cannot specify NAME when using --all")
    if not all_flag and name is None:
        abort("Specify a service NAME or use --all")


def validate_service_exists(manager: ComposeManager, name: str) -> VPNService:
    """Validate service exists and return it, abort if not found."""
    try:
        return manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

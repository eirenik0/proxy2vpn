"""Preset utilities built on top of YAML anchors."""

from __future__ import annotations

from pathlib import Path
from typing import List

import click

from . import config
from .compose_manager import ComposeManager
from .models import VPNService


def _get_compose_file() -> Path:
    """Get compose file path from Typer context or default."""

    try:
        ctx = click.get_current_context()
        return ctx.obj.get("compose_file", config.COMPOSE_FILE)
    except RuntimeError:
        return config.COMPOSE_FILE


def list_available_presets() -> List[str]:
    """Return names of available presets (profile anchors)."""

    mgr = ComposeManager(_get_compose_file())
    return [profile.name for profile in mgr.list_profiles()]


def apply_preset(
    preset_name: str,
    service_name: str,
    port: int,
) -> None:
    """Create a VPN service using a preset anchor."""

    mgr = ComposeManager(_get_compose_file())
    # Ensure preset exists
    mgr.get_profile(preset_name)
    env = {"VPN_SERVICE_PROVIDER": config.DEFAULT_PROVIDER}
    labels = {
        "vpn.type": "vpn",
        "vpn.port": str(port),
        "vpn.provider": config.DEFAULT_PROVIDER,
        "vpn.profile": preset_name,
        "vpn.location": "",
    }
    service = VPNService(
        name=service_name,
        port=port,
        provider=config.DEFAULT_PROVIDER,
        profile=preset_name,
        location="",
        environment=env,
        labels=labels,
    )
    mgr.add_service(service)

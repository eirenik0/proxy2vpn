"""Compatibility wrappers around :class:`GluetunControlClient`."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .http_client import (
    GluetunControlClient,
    HTTPClientError as ControlClientError,
)

__all__ = [
    "ControlClientError",
    "get_status",
    "set_openvpn_status",
    "get_public_ip",
    "restart_tunnel",
    "get_dns_status",
    "set_dns_status",
    "get_updater_status",
    "set_updater_status",
    "get_port_forwarded",
    "get_openvpn_settings",
]


async def get_status(base_url: str) -> dict[str, Any]:
    """Return the current control server status."""
    async with GluetunControlClient(base_url) as client:
        return asdict(await client.status())


async def set_openvpn_status(base_url: str, status: bool) -> dict[str, Any]:
    """Enable or disable OpenVPN through the control API."""
    async with GluetunControlClient(base_url) as client:
        return asdict(await client.set_openvpn(status))


async def get_public_ip(base_url: str) -> str:
    """Return the public IP reported by the control API."""
    async with GluetunControlClient(base_url) as client:
        return (await client.public_ip()).ip


async def restart_tunnel(base_url: str) -> dict[str, Any]:
    """Restart the VPN tunnel through the control API."""
    async with GluetunControlClient(base_url) as client:
        return asdict(await client.restart_tunnel())


async def get_dns_status(base_url: str) -> dict[str, Any]:
    """Return the DNS server status."""
    async with GluetunControlClient(base_url) as client:
        return asdict(await client.dns_status())


async def set_dns_status(base_url: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable DNS server through the control API."""
    async with GluetunControlClient(base_url) as client:
        return asdict(await client.set_dns_status(enabled))


async def get_updater_status(base_url: str) -> dict[str, Any]:
    """Return the updater service status."""
    async with GluetunControlClient(base_url) as client:
        return asdict(await client.updater_status())


async def set_updater_status(base_url: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable updater service through the control API."""
    async with GluetunControlClient(base_url) as client:
        return asdict(await client.set_updater_status(enabled))


async def get_port_forwarded(base_url: str) -> int:
    """Return the port forwarded by the VPN."""
    async with GluetunControlClient(base_url) as client:
        return (await client.port_forwarded()).port


async def get_openvpn_settings(base_url: str) -> dict[str, str]:
    """Return the OpenVPN settings."""
    async with GluetunControlClient(base_url) as client:
        return (await client.openvpn_settings()).settings

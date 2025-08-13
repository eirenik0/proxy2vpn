"""Client helpers for interacting with the gluetun control API."""

from __future__ import annotations

from typing import Any

import asyncio
import aiohttp

from .config import (
    CONTROL_API_ENDPOINTS,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    VERIFY_SSL,
)


class ControlClientError(RuntimeError):
    """Raised when the control API returns an error."""


def _build_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


async def _request(method: str, url: str, action: str, **kwargs: Any) -> Any:
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
    connector = aiohttp.TCPConnector(ssl=VERIFY_SSL)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                async with session.request(method, url, **kwargs) as response:
                    response.raise_for_status()
                    return await response.json(content_type=None)
            except (
                aiohttp.ClientResponseError
            ) as exc:  # pragma: no cover - handled for clarity
                raise ControlClientError(
                    f"{action}: {exc.status} {exc.message}"
                ) from exc
            except aiohttp.ClientError as exc:  # pragma: no cover - network issues
                if attempt > MAX_RETRIES:
                    raise ControlClientError(f"{action}: {exc}") from exc
                await asyncio.sleep(0.5 * attempt)


async def get_status(base_url: str) -> dict[str, Any]:
    """Return the current control server status."""
    url = _build_url(base_url, CONTROL_API_ENDPOINTS["status"])
    return await _request("get", url, "Failed to get status")


async def set_openvpn_status(base_url: str, status: bool) -> dict[str, Any]:
    """Enable or disable OpenVPN through the control API."""
    url = _build_url(base_url, CONTROL_API_ENDPOINTS["openvpn"])
    payload = {"status": status}
    return await _request("post", url, "Failed to set OpenVPN status", json=payload)


async def get_public_ip(base_url: str) -> str:
    """Return the public IP reported by the control API."""
    url = _build_url(base_url, CONTROL_API_ENDPOINTS["ip"])
    data = await _request("get", url, "Failed to get public IP")
    if isinstance(data, dict):
        return data.get("ip", "")
    return str(data)


async def restart_tunnel(base_url: str) -> dict[str, Any]:
    """Restart the VPN tunnel through the control API."""
    url = _build_url(base_url, CONTROL_API_ENDPOINTS["openvpn_status"])
    payload = {"status": "restarted"}
    return await _request("put", url, "Failed to restart tunnel", json=payload)

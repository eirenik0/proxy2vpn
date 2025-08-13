"""Client helpers for interacting with the gluetun control API."""

from __future__ import annotations

from typing import Any

import aiohttp


class ControlClientError(RuntimeError):
    """Raised when the control API returns an error."""


def _build_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


async def _request(method: str, url: str, action: str, **kwargs: Any) -> Any:
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, **kwargs) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
    except aiohttp.ClientResponseError as exc:  # pragma: no cover - handled for clarity
        raise ControlClientError(f"{action}: {exc.status} {exc.message}") from exc
    except aiohttp.ClientError as exc:  # pragma: no cover - network issues
        raise ControlClientError(f"{action}: {exc}") from exc


async def get_status(base_url: str) -> dict[str, Any]:
    """Return the current control server status."""
    url = _build_url(base_url, "status")
    return await _request("get", url, "Failed to get status")


async def set_openvpn_status(base_url: str, status: bool) -> dict[str, Any]:
    """Enable or disable OpenVPN through the control API."""
    url = _build_url(base_url, "openvpn")
    payload = {"status": status}
    return await _request("post", url, "Failed to set OpenVPN status", json=payload)


async def get_public_ip(base_url: str) -> str:
    """Return the public IP reported by the control API."""
    url = _build_url(base_url, "ip")
    data = await _request("get", url, "Failed to get public IP")
    if isinstance(data, dict):
        return data.get("ip", "")
    return str(data)

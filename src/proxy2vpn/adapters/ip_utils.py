"""Utilities for retrieving the public IP address."""

import asyncio
from typing import Mapping

import ipaddress
import re

from .http_client import HTTPClient, HTTPClientConfig, HTTPClientError

IP_SERVICES = ("https://ipinfo.io/ip", "https://ifconfig.me/ip")

IP_REGEX = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}")


def _parse_ip(text: str) -> str:
    """Extract a valid IP address from arbitrary text."""
    candidate = text.strip()
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        match = IP_REGEX.search(text)
        if match:
            try:
                ipaddress.ip_address(match.group())
                return match.group()
            except ValueError:
                return ""
    return ""


async def _fetch_ip(client: HTTPClient, url: str, proxy: str | None) -> str:
    """Fetch IP address from a single service."""
    try:
        text = await client.get_text(url, proxy=proxy)
        ip = _parse_ip(text)
        if ip:
            return ip
    except HTTPClientError:
        return ""
    return ""


async def fetch_ip_async(
    proxies: Mapping[str, str] | None = None, timeout: int = 3
) -> str:
    """Return the public IP address using external services concurrently."""
    proxy = None
    if proxies:
        proxy = proxies.get("http") or proxies.get("https")

    cfg = HTTPClientConfig(base_url="http://0.0.0.0", timeout=timeout)
    async with HTTPClient(cfg) as client:
        tasks = [
            asyncio.create_task(_fetch_ip(client, url, proxy)) for url in IP_SERVICES
        ]
        try:
            for task in asyncio.as_completed(tasks):
                ip = await task
                if ip:
                    return ip
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return ""


def fetch_ip(proxies: Mapping[str, str] | None = None, timeout: int = 3) -> str:
    """Return the public IP address in synchronous contexts.

    This helper runs the asynchronous :func:`fetch_ip_async` function using
    ``asyncio.run``. It must only be used from synchronous code; callers running
    inside an existing event loop should use :func:`fetch_ip_async` directly to
    avoid ``RuntimeError`` from nested event loops.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fetch_ip_async(proxies=proxies, timeout=timeout))
    raise RuntimeError(
        "fetch_ip() cannot be called from an async context; use fetch_ip_async()."
    )

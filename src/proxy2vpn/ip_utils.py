"""Utilities for retrieving the public IP address."""

from __future__ import annotations

import asyncio
from typing import Mapping

import aiohttp

IP_SERVICES = ("https://ipinfo.io/ip", "https://ifconfig.me")


async def _fetch_ip(session: aiohttp.ClientSession, url: str, proxy: str | None) -> str:
    """Fetch IP address from a single service."""
    try:
        async with session.get(url, proxy=proxy) as resp:
            text = await resp.text()
            ip = text.strip()
            if ip:
                return ip
    except aiohttp.ClientError:
        return ""
    return ""


async def fetch_ip_async(
    proxies: Mapping[str, str] | None = None, timeout: int = 3
) -> str:
    """Return the public IP address using external services concurrently."""
    proxy = None
    if proxies:
        proxy = proxies.get("http") or proxies.get("https")

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(
        timeout=timeout_cfg, connector=connector
    ) as session:
        tasks = [
            asyncio.create_task(_fetch_ip(session, url, proxy)) for url in IP_SERVICES
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

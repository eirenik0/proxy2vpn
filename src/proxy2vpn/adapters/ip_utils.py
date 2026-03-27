"""Utilities for retrieving the public IP address."""

import asyncio
import shutil
import subprocess
from typing import Mapping

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

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
    except (HTTPClientError, asyncio.TimeoutError):
        # Don't hide proxy connection failures - let caller handle them
        return ""
    return ""


def _curl_proxy_args(proxy: str) -> list[str]:
    """Return curl proxy arguments with auth separated from the URL."""

    parsed = urlsplit(proxy)
    if not parsed.scheme or not parsed.hostname:
        return []

    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"

    args = ["-x", urlunsplit((parsed.scheme, netloc, "", "", ""))]
    if parsed.username is not None or parsed.password is not None:
        username = parsed.username or ""
        password = parsed.password or ""
        args.extend(["--proxy-user", f"{username}:{password}"])
    return args


def _fetch_ip_with_curl(url: str, proxy: str, timeout: int) -> str:
    """Fetch an IP through a proxy with curl when Python HTTP stacks cannot."""

    if shutil.which("curl") is None:
        return ""

    proxy_args = _curl_proxy_args(proxy)
    if not proxy_args:
        return ""

    command = [
        "curl",
        "-fsSL",
        "--connect-timeout",
        str(max(1, timeout)),
        "--max-time",
        str(max(1, timeout)),
        *proxy_args,
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(2, timeout + 1),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""

    if completed.returncode != 0:
        return ""
    return _parse_ip(completed.stdout)


async def fetch_ip_async(
    proxies: Mapping[str, str] | None = None, timeout: int = 3
) -> str:
    """Return the public IP address using external services concurrently."""
    proxy = None
    if proxies:
        proxy = proxies.get("http") or proxies.get("https")

    # Gluetun's authenticated HTTP proxy works reliably with curl on hosts where
    # multiple Python HTTP clients fail CONNECT/forward-proxy requests.
    if proxy:
        for url in IP_SERVICES:
            ip = await asyncio.to_thread(_fetch_ip_with_curl, url, proxy, timeout)
            if ip:
                return ip

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

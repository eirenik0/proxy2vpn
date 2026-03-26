"""Proxy helpers shared by connectivity checks and diagnostics."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
import re
from urllib.parse import urlsplit, urlunsplit


_PROXY_AUTH_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<password>[^@\s/]+)@(?P<host>[^/\s]+)"
)


def extract_proxy_credentials_from_env(
    env_vars: Iterable[str] | dict[str, str],
) -> tuple[str | None, str | None]:
    """Extract ``HTTPPROXY_USER`` and ``HTTPPROXY_PASSWORD`` from env entries."""
    if isinstance(env_vars, dict):
        return env_vars.get("HTTPPROXY_USER"), env_vars.get("HTTPPROXY_PASSWORD")

    username = None
    password = None
    for item in env_vars:
        if not isinstance(item, str):
            continue
        if item.startswith("HTTPPROXY_USER="):
            username = item.split("=", 1)[1]
        elif item.startswith("HTTPPROXY_PASSWORD="):
            password = item.split("=", 1)[1]
    return username, password


def extract_proxy_credentials_from_container(
    container: Any,
) -> tuple[str | None, str | None]:
    """Extract proxy credentials from a container environment list."""
    env_vars = container.attrs.get("Config", {}).get("Env", []) if container else []
    return extract_proxy_credentials_from_env(env_vars)


def build_proxy_url(
    *,
    port: str | int,
    host: str = "localhost",
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Build an HTTP proxy URL, including credentials when both are present."""
    if username and password:
        credentials = f"{username}:{password}@"
        return f"http://{credentials}{host}:{int(port)}"
    return f"http://{host}:{int(port)}"


def build_proxy_urls(
    port: str | int, username: str | None = None, password: str | None = None
) -> dict[str, str]:
    """Build both http and https proxy URLs."""
    url = build_proxy_url(port=port, username=username, password=password)
    return {"http": url, "https": url}


def build_proxy_urls_from_container(container: Any, port: str | int) -> dict[str, str]:
    """Build proxy URLs from container environment credentials."""
    username, password = extract_proxy_credentials_from_container(container)
    return build_proxy_urls(port, username=username, password=password)


def redact_proxy_url(url: str) -> str:
    """Return a proxy URL with credentials replaced by stars."""
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc and parts.scheme:
        # Drop existing credentials from the host:port portion
        host_port = netloc.rsplit("@", 1)[1]
        return urlunsplit(
            (
                parts.scheme,
                f"***:***@{host_port}",
                parts.path,
                parts.query,
                parts.fragment,
            )
        )

    return _PROXY_AUTH_RE.sub(r"\g<scheme>***:***@\g<host>", url)

"""Asynchronous HTTP client utilities for proxy2vpn."""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import aiohttp

from proxy2vpn.core.config import (
    CONTROL_API_ENDPOINTS,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    VERIFY_SSL,
)
from .logging_utils import get_logger
from typing import Self

logger = get_logger(__name__)


class HTTPClientError(RuntimeError):
    """Raised when an HTTP request fails."""


@dataclass(slots=True)
class RetryPolicy:
    """Configuration for request retries."""

    attempts: int = MAX_RETRIES
    backoff: float = 0.5


@dataclass(slots=True)
class HTTPClientConfig:
    """Settings for :class:`HTTPClient`."""

    base_url: str
    timeout: float = DEFAULT_TIMEOUT
    verify_ssl: bool = VERIFY_SSL
    auth: tuple[str, str] | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)


class HTTPClient:
    """Simple wrapper around :class:`aiohttp.ClientSession` with retries."""

    def __init__(self, config: HTTPClientConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> Self:
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - trivial
        await self.close()

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        timeout = aiohttp.ClientTimeout(total=self._config.timeout)
        connector = aiohttp.TCPConnector(ssl=self._config.verify_ssl)
        auth = None
        if self._config.auth:
            username, password = self._config.auth
            auth = aiohttp.BasicAuth(username, password)
        self._session = aiohttp.ClientSession(
            base_url=self._config.base_url,
            timeout=timeout,
            connector=connector,
            auth=auth,
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        await self._ensure_session()
        if not self._session:
            raise HTTPClientError("session not initialized")

        for attempt in range(1, self._config.retry.attempts + 2):
            start = time.perf_counter()
            try:
                async with self._session.request(method, path, **kwargs) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
                    elapsed = time.perf_counter() - start
                    logger.info(
                        "http_request",
                        extra={
                            "method": method.upper(),
                            "path": path,
                            "status": resp.status,
                            "elapsed": elapsed,
                        },
                    )
                    return data
            except aiohttp.ClientError as exc:
                elapsed = time.perf_counter() - start
                logger.warning(
                    "http_request_error",
                    extra={
                        "method": method.upper(),
                        "path": path,
                        "elapsed": elapsed,
                        "error": str(exc),
                        "attempt": attempt,
                    },
                )
                if attempt > self._config.retry.attempts:
                    raise HTTPClientError(str(exc)) from exc
                await asyncio.sleep(self._config.retry.backoff * attempt)

    async def request_text(self, method: str, path: str, **kwargs: Any) -> str | None:
        await self._ensure_session()
        if not self._session:
            raise HTTPClientError("session not initialized")

        for attempt in range(1, self._config.retry.attempts + 2):
            start = time.perf_counter()
            try:
                async with self._session.request(method, path, **kwargs) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
                    elapsed = time.perf_counter() - start
                    logger.info(
                        "http_request",
                        extra={
                            "method": method.upper(),
                            "path": path,
                            "status": resp.status,
                            "elapsed": elapsed,
                        },
                    )
                    return text
            except aiohttp.ClientError as exc:
                elapsed = time.perf_counter() - start
                logger.warning(
                    "http_request_error",
                    extra={
                        "method": method.upper(),
                        "path": path,
                        "elapsed": elapsed,
                        "error": str(exc),
                        "attempt": attempt,
                    },
                )
                if attempt > self._config.retry.attempts:
                    raise HTTPClientError(str(exc)) from exc
                await asyncio.sleep(self._config.retry.backoff * attempt)

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def get_text(self, path: str, **kwargs: Any) -> str | None:
        return await self.request_text("GET", path, **kwargs)


@dataclass(slots=True)
class StatusResponse:
    """Response payload for the ``/status`` endpoint."""

    status: str


@dataclass(slots=True)
class OpenVPNResponse:
    """Response payload for the ``/openvpn`` endpoint."""

    status: bool


@dataclass(slots=True)
class IPResponse:
    """Response payload for the ``/ip`` endpoint."""

    ip: str


@dataclass(slots=True)
class OpenVPNStatusResponse:
    """Response payload for the ``/openvpn/status`` endpoint."""

    status: str


class GluetunControlClient(HTTPClient):
    """Client for interacting with Gluetun's control API."""

    ENDPOINTS = CONTROL_API_ENDPOINTS

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        verify_ssl: bool = VERIFY_SSL,
    ):
        parsed = urlparse(base_url)
        if not (parsed.scheme and parsed.netloc):
            raise ValueError(f"invalid base URL: {base_url}")
        auth: tuple[str, str] | None = None
        auth_env = os.getenv("GLUETUN_CONTROL_AUTH")
        if auth_env:
            username, sep, password = auth_env.partition(":")
            if not sep:
                raise ValueError("GLUETUN_CONTROL_AUTH must be in 'user:pass' format")
            auth = (username, password)
        config = HTTPClientConfig(
            base_url=f"{parsed.scheme}://{parsed.netloc}",
            timeout=timeout,
            verify_ssl=verify_ssl,
            auth=auth,
        )
        super().__init__(config)

    async def status(self) -> StatusResponse:
        data = await self.get(self.ENDPOINTS["status"])
        return StatusResponse(**data)

    async def set_openvpn(self, enabled: bool) -> OpenVPNResponse:
        payload = {"status": enabled}
        data = await self.post(self.ENDPOINTS["openvpn"], json=payload)
        return OpenVPNResponse(**data)

    async def public_ip(self) -> IPResponse:
        data = await self.get(self.ENDPOINTS["ip"])
        return IPResponse(**data)

    async def restart_tunnel(self) -> OpenVPNStatusResponse:
        payload = {"status": "restarted"}
        data = await self.request("PUT", self.ENDPOINTS["openvpn_status"], json=payload)
        return OpenVPNStatusResponse(**data)

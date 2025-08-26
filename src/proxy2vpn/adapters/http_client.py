"""Asynchronous HTTP client utilities for proxy2vpn."""

import asyncio
import os
import time
from typing import Any, Self
from urllib.parse import urlparse

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, field_validator, AliasChoices

from proxy2vpn.core.config import (
    CONTROL_API_ENDPOINTS,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    VERIFY_SSL,
)
from .logging_utils import get_logger

logger = get_logger(__name__)


class HTTPClientError(RuntimeError):
    """Raised when an HTTP request fails."""


class RetryPolicy(BaseModel):
    """Configuration for request retries."""

    attempts: int = MAX_RETRIES
    backoff: float = 0.5

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    @field_validator("attempts")
    @classmethod
    def _attempts_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("attempts must be >= 0")
        return v

    @field_validator("backoff")
    @classmethod
    def _backoff_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("backoff must be >= 0")
        return v


class HTTPClientConfig(BaseModel):
    """Settings for :class:`HTTPClient`."""

    base_url: str
    timeout: float = DEFAULT_TIMEOUT
    verify_ssl: bool = VERIFY_SSL
    auth: tuple[str, str] | None = None
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    @field_validator("timeout")
    @classmethod
    def _timeout_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("timeout must be > 0")
        return v


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
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
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
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
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


class StatusResponse(BaseModel):
    """Response payload for the ``/status`` endpoint."""

    status: str
    model_config = ConfigDict(extra="ignore")


class OpenVPNResponse(BaseModel):
    """Response payload for the ``/openvpn`` endpoint."""

    status: bool
    model_config = ConfigDict(extra="ignore")


class IPResponse(BaseModel):
    """Response payload for the ``/ip`` endpoint.

    Accepts either 'ip' or 'public_ip' keys from server responses.
    """

    ip: str = Field(validation_alias=AliasChoices("ip", "public_ip"))
    model_config = ConfigDict(extra="ignore")


class OpenVPNStatusResponse(BaseModel):
    """Response payload for the ``/openvpn/status`` endpoint.

    Accepts either 'status' or legacy 'outcome' key from server responses.
    """

    status: str = Field(validation_alias=AliasChoices("status", "outcome"))
    model_config = ConfigDict(extra="ignore")


class DNSStatusResponse(BaseModel):
    """Response payload for the ``/dns/status`` endpoint."""

    status: str
    model_config = ConfigDict(extra="ignore")


class UpdaterStatusResponse(BaseModel):
    """Response payload for the ``/updater/status`` endpoint."""

    status: str
    model_config = ConfigDict(extra="ignore")


class PortForwardResponse(BaseModel):
    """Response payload for the ``/openvpn/portforwarded`` endpoint."""

    port: int
    model_config = ConfigDict(extra="ignore")


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
        """Request a VPN tunnel restart.

        Tries the newer Gluetun convention first (PUT status="restarted").
        If the server responds with an error (e.g., older versions not supporting
        "restarted"), falls back to a stop/start sequence using the same endpoint
        with status="stopped" then status="running".
        """
        try:
            payload = {"status": "restarted"}
            data = await self.request(
                "PUT", self.ENDPOINTS["openvpn_status"], json=payload
            )
            return OpenVPNStatusResponse(**data)
        except HTTPClientError:
            # Fallback for older servers: stop then start
            await self.request(
                "PUT", self.ENDPOINTS["openvpn_status"], json={"status": "stopped"}
            )
            data = await self.request(
                "PUT", self.ENDPOINTS["openvpn_status"], json={"status": "running"}
            )
            return OpenVPNStatusResponse(**data)

    async def dns_status(self) -> DNSStatusResponse:
        data = await self.get(self.ENDPOINTS["dns_status"])
        return DNSStatusResponse(**data)

    async def updater_status(self) -> UpdaterStatusResponse:
        data = await self.get(self.ENDPOINTS["updater_status"])
        return UpdaterStatusResponse(**data)

    async def port_forwarded(self) -> PortForwardResponse:
        data = await self.get(self.ENDPOINTS["port_forward"])
        return PortForwardResponse(**data)

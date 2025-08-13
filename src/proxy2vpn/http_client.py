"""Asynchronous HTTP client utilities for proxy2vpn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import asyncio
import time

import aiohttp

from .logging_utils import get_logger


logger = get_logger(__name__)


class HTTPClientError(RuntimeError):
    """Raised when an HTTP request fails."""


@dataclass(slots=True)
class RetryPolicy:
    """Configuration for request retries."""

    attempts: int = 3
    backoff: float = 0.5


@dataclass(slots=True)
class HTTPClientConfig:
    """Settings for :class:`HTTPClient`."""

    base_url: str
    timeout: float = 10
    verify_ssl: bool = True
    auth: tuple[str, str] | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)


class HTTPClient:
    """Simple wrapper around :class:`aiohttp.ClientSession` with retries."""

    def __init__(self, config: HTTPClientConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "HTTPClient":
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

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

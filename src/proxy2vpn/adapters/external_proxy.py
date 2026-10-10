"""HTTP CONNECT probes: explicit proxy, verified TLS, and no direct fallback."""

from __future__ import annotations

import asyncio
import base64
import os
import ssl
from pathlib import Path
from time import monotonic

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from proxy2vpn.core.egress import (
    EgressCapabilities,
    EgressObservation,
    EgressOperation,
    require_supported_operation,
)
from proxy2vpn.core.external_proxy import ExternalProxyEndpoint
from proxy2vpn.core.services.diagnostics import DiagnosticResult
from proxy2vpn.adapters.ip_utils import parse_ip_literal


class ExternalProxyConfig(BaseModel):
    version: int = Field(default=1, ge=1, le=1)
    endpoints: list[ExternalProxyEndpoint]
    model_config = ConfigDict(extra="forbid")


def load_external_endpoints(path: Path) -> list[ExternalProxyEndpoint]:
    """Missing optional configuration preserves existing compose-only behavior."""
    if not path.exists():
        return []
    try:
        config = ExternalProxyConfig.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (ValidationError, ValueError):
        # ValidationError includes input values: never expose them in CLI/logs.
        raise ValueError(
            "Invalid external proxy configuration; check the documented schema"
        ) from None
    names = [endpoint.name for endpoint in config.endpoints]
    if len(names) != len(set(names)):
        raise ValueError("External proxy endpoint ids must be unique")
    return config.endpoints


def external_config_path(compose_file: Path, configured_path: str) -> Path:
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else compose_file.parent / path


def _check(
    name: str, passed: bool | None, message: str, *, persistent: bool = False
) -> DiagnosticResult:
    return DiagnosticResult(
        check=name,
        passed=passed,
        message=message,
        recommendation="Verify the endpoint configuration or contact its operator."
        if passed is not True
        else "",
        persistent=persistent,
    )


# @lat: [[lat.md/egress#External Proxy Adapter]]
class ExternalProxyAdapter:
    """One configured identity; requests provide no sticky-session guarantees."""

    capabilities = EgressCapabilities()

    def __init__(
        self,
        endpoint: ExternalProxyEndpoint,
        probe_timeout: int = 5,
        *,
        tls_context: ssl.SSLContext | None = None,
        credential_values: tuple[str, str] | None = None,
    ):
        self.endpoint = endpoint
        self.identity = endpoint.identity
        self.probe_timeout = probe_timeout
        self._tls_context = tls_context
        self._credential_values = credential_values

    async def execute(self, operation: EgressOperation) -> None:
        require_supported_operation(self.capabilities, operation)

    async def observe(
        self, *, lines: int = 20, timeout: int | None = None
    ) -> EgressObservation:
        auth = None
        if self.endpoint.credentials is not None:
            if self._credential_values is None:
                username = os.environ.get(self.endpoint.credentials.username_env)
                password = os.environ.get(self.endpoint.credentials.password_env)
            else:
                username, password = self._credential_values
            if not username or not password or ":" in username:
                return EgressObservation(
                    0,
                    "auth_config",
                    [
                        _check(
                            "config_error",
                            False,
                            "Proxy credential references are missing or invalid.",
                            persistent=True,
                        )
                    ],
                )
            try:
                auth = "Basic " + base64.b64encode(
                    f"{username}:{password}".encode("latin1")
                ).decode("ascii")
            except UnicodeEncodeError:
                return EgressObservation(
                    0,
                    "auth_config",
                    [
                        _check(
                            "config_error",
                            False,
                            "Proxy credentials are not valid HTTP Basic credentials.",
                            persistent=True,
                        )
                    ],
                )
        results: list[DiagnosticResult] = []
        connected = False
        authentication: bool | None = None
        latency: float | None = None
        # Ambient HTTP(S)_PROXY/netrc must never select a different connection.
        async with aiohttp.ClientSession(
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=timeout or self.probe_timeout),
        ) as session:
            for url in self.endpoint.probe_urls:
                started = monotonic()
                try:
                    async with session.get(
                        url,
                        proxy=self.endpoint.connection.proxy_url,
                        proxy_headers={"Proxy-Authorization": auth}
                        if auth is not None
                        else None,
                        allow_redirects=False,
                        ssl=self._tls_context or True,
                    ) as response:
                        authentication = True if auth is not None else None
                        if response.status != 200:
                            continue
                        body = bytearray()
                        while len(body) <= 4096:
                            chunk = await response.content.read(4097 - len(body))
                            if not chunk:
                                break
                            body.extend(chunk)
                        connected = True
                        latency = (monotonic() - started) * 1000
                        try:
                            ip = (
                                parse_ip_literal(body.decode("ascii"))
                                if len(body) <= 4096
                                else None
                            )
                        except (ValueError, UnicodeDecodeError):
                            ip = None
                        if ip is None:
                            continue
                        expected = self.endpoint.expected_egress_ips
                        matches = not expected or ip in expected
                        results = [
                            _check(
                                "connectivity",
                                True,
                                "HTTPS request completed through the configured proxy.",
                            ),
                            _check("egress_ip", True, "A valid exit IP was observed."),
                        ]
                        if expected:
                            results.append(
                                _check(
                                    "egress_identity",
                                    matches,
                                    "Observed exit IP matches the configured allowlist."
                                    if matches
                                    else "Observed exit IP is outside the configured allowlist.",
                                )
                            )
                        return EgressObservation(
                            100 if matches else 0,
                            "healthy" if matches else "egress_mismatch",
                            results,
                            available=True,
                            authentication=authentication,
                            connectivity=True,
                            latency_ms=latency,
                            current_egress_ip=ip,
                        )
                except aiohttp.ClientHttpProxyError as exc:
                    if exc.status == 407:
                        return self._authentication_failure()
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                    # Neither exception text nor response bodies are diagnostics:
                    # either may echo endpoint credentials supplied by the operator.
                    continue
        return EgressObservation(
            0,
            "unknown" if connected else "connectivity",
            [
                _check(
                    "connectivity",
                    connected,
                    "Proxy request completed."
                    if connected
                    else "No proxy request completed successfully.",
                ),
                _check("egress_ip", None, "No valid exit-IP evidence is available."),
            ],
            available=True if connected else None,
            authentication=authentication,
            connectivity=connected,
            latency_ms=latency,
        )

    @staticmethod
    def _authentication_failure() -> EgressObservation:
        return EgressObservation(
            0,
            "auth_config",
            [
                _check(
                    "auth_failure",
                    False,
                    "Proxy authentication was rejected (HTTP 407).",
                    persistent=True,
                ),
                _check(
                    "connectivity",
                    False,
                    "The proxy did not establish the requested tunnel.",
                ),
                _check("egress_ip", None, "Exit IP is unknown."),
            ],
            available=True,
            authentication=False,
            connectivity=False,
        )

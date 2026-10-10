"""Dedicated mobile control API: fixed HTTPS origin, no retry or raw diagnostics."""

import asyncio
import json
from urllib.parse import quote

import aiohttp

from proxy2vpn.adapters.external_proxy import ExternalProxyAdapter
from proxy2vpn.core.egress import (
    EgressCapabilities,
    EgressOperation,
    UnsupportedEgressOperation,
)
from proxy2vpn.core.iproyal import ControlResult, valid_rotation_key


class IPRoyalMobileAdapter(ExternalProxyAdapter):
    """Observation shares the configured proxy; control never falls back to Docker."""

    capabilities = EgressCapabilities(request_different_exit_ip=True)

    async def execute(self, operation: EgressOperation) -> None:
        raise UnsupportedEgressOperation(
            "Provider operations require the explicit durable manual coordinator"
        )

    async def request_exit_ip(self, token: str, rotation_key: str) -> ControlResult:
        if not valid_rotation_key(rotation_key):
            return ControlResult(
                request_outcome="not_dispatched", reason_code="credentials_missing"
            )
        url = "https://apid.iproyal.com/v1/reseller/orders/4g/rotate-ip/" + quote(
            rotation_key, safe=""
        )
        try:
            async with aiohttp.ClientSession(
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as session:
                async with session.post(
                    url,
                    headers={"X-Access-Token": token},
                    allow_redirects=False,
                    ssl=True,
                ) as response:
                    status = response.status
                    retry = response.headers.get("Retry-After", "")
                    retry_after = (
                        min(int(retry), 86400)
                        if retry.isdecimal() and len(retry) <= 8
                        else None
                    )
                    if status in {401, 403}:
                        return ControlResult(
                            request_outcome="rejected", reason_code="auth_rejected"
                        )
                    if status == 429:
                        return ControlResult(
                            request_outcome="rejected",
                            reason_code="rate_limited",
                            retry_after_seconds=retry_after,
                        )
                    if 300 <= status < 400:
                        return ControlResult(
                            request_outcome="unknown", reason_code="redirect"
                        )
                    if 400 <= status < 500:
                        return ControlResult(
                            request_outcome="rejected", reason_code="http_rejected"
                        )
                    if not 200 <= status < 300:
                        return ControlResult(
                            request_outcome="unknown", reason_code="http_rejected"
                        )
                    # No documented response schema. Reject malformed framing and
                    # explicit negative acknowledgments; never infer IP replacement.
                    body = bytearray()
                    while len(body) <= 65536:
                        chunk = await response.content.read(65537 - len(body))
                        if not chunk:
                            break
                        body.extend(chunk)
                    if len(body) > 65536 or not body:
                        return ControlResult(
                            request_outcome="unknown", reason_code="malformed_response"
                        )
                    try:
                        value = json.loads(body)
                    except (ValueError, UnicodeDecodeError, RecursionError):
                        return ControlResult(
                            request_outcome="unknown", reason_code="malformed_response"
                        )
                    if isinstance(value, dict) and (
                        value.get("success") is False
                        or value.get("error") not in (None, False, "")
                    ):
                        return ControlResult(
                            request_outcome="unknown", reason_code="malformed_response"
                        )
                    return ControlResult(
                        request_outcome="acknowledged", reason_code="acknowledged"
                    )
        except asyncio.TimeoutError:
            return ControlResult(request_outcome="unknown", reason_code="timeout")
        except (aiohttp.ClientError, OSError, ValueError):
            return ControlResult(
                request_outcome="unknown", reason_code="transport_error"
            )


def external_adapter(endpoint, probe_timeout=5):
    """Select only an explicitly configured provider; generic endpoints stay generic."""
    cls = (
        IPRoyalMobileAdapter
        if endpoint.mobile_provider is not None
        else ExternalProxyAdapter
    )
    return cls(endpoint, probe_timeout)

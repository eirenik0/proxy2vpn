"""Actual control client and CONNECT probes, isolated entirely to local listeners."""

import asyncio
from contextlib import asynccontextmanager
import json

import aiohttp
import pytest

from proxy2vpn.adapters.iproyal import IPRoyalMobileAdapter
from test_external_proxy import controlled_proxy, tls_material as _tls_material
from test_mobile_operations import mobile as _mobile

tls_material = _tls_material
mobile = _mobile


@asynccontextmanager
async def api(status, chunks, requests, *, change=None, retry=None):
    async def handler(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            requests.append(header)
            if change:
                change()
            payload = b"".join(chunks)
            writer.write(
                f"HTTP/1.1 {status} Sandbox\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n".encode()
                + (f"Retry-After: {retry}\r\n".encode() if retry else b"")
                + (
                    b"Location: https://other.invalid/secret\r\n"
                    if status == 302
                    else b""
                )
                + b"\r\n"
            )
            await writer.drain()
            for chunk in chunks:
                writer.write(chunk)
                await writer.drain()
                await asyncio.sleep(0.01)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()


def local_transport(monkeypatch, origin, calls):
    original = aiohttp.ClientSession.post

    def post(self, url, **kwargs):
        calls.append((url, kwargs))
        assert url.startswith(
            "https://apid.iproyal.com/v1/reseller/orders/4g/rotate-ip/"
        )
        assert kwargs["allow_redirects"] is False
        assert kwargs["ssl"] is True
        assert self.trust_env is False
        path = url.split("apid.iproyal.com", 1)[1]
        return original(self, origin + path, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "post", post)


@pytest.mark.parametrize(
    "status,chunks,outcome,reason",
    [
        (200, [b'{"synt', b'hetic":true}'], "acknowledged", "acknowledged"),
        (200, [b'{"synthetic":true}', b"junk"], "unknown", "malformed_response"),
        (200, [b'{"synthetic":true}', b" " * 65536], "unknown", "malformed_response"),
        (200, [b'{"success":false}'], "unknown", "malformed_response"),
        (200, [b"[" * 2000 + b"0" + b"]" * 2000], "unknown", "malformed_response"),
        (401, [], "rejected", "auth_rejected"),
        (403, [], "rejected", "auth_rejected"),
        (404, [], "rejected", "http_rejected"),
        (429, [], "rejected", "rate_limited"),
        (500, [], "unknown", "http_rejected"),
        (302, [], "unknown", "redirect"),
    ],
)
# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Actual HTTP Client]]
def test_real_control_client_classifies_bounded_responses(
    mobile, monkeypatch, status, chunks, outcome, reason
):
    operations, _ = mobile
    adapter = operations._snapshot("mobile")[-1]
    requests, calls = [], []

    async def scenario():
        async with api(status, chunks, requests, retry="900") as origin:
            local_transport(monkeypatch, origin, calls)
            return await adapter.request_exit_ip(
                "mobile-token-secret", "mobile-key-secret"
            )

    result = asyncio.run(scenario())
    assert result.request_outcome == outcome
    assert result.reason_code == reason
    assert len(calls) == len(requests) == 1
    assert b"X-Access-Token: mobile-token-secret\r\n" in requests[0]
    if status == 429:
        assert result.retry_after_seconds == 900
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("change", [False, True])
# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Actual Proxy Verification]]
def test_manual_real_adapter_uses_same_authenticated_proxy(
    mobile, monkeypatch, tls_material, change
):
    operations, document = mobile
    requests, calls = [], []

    async def scenario():
        body = bytearray(b"198.51.100.8")
        async with controlled_proxy(
            tls_material,
            body=body,
            credentials="mobile-user-secret:mobile-password-secret",
        ) as (endpoint, tls, proxy, _):
            document["endpoints"][0]["connection"] = endpoint.connection.model_dump()
            document["endpoints"][0]["probe_urls"] = endpoint.probe_urls
            (operations.store.compose_root / "external-proxies.json").write_text(
                json.dumps(document)
            )
            initial = IPRoyalMobileAdapter.__init__

            def initialize(self, *args, **kwargs):
                initial(self, *args, tls_context=tls, **kwargs)

            monkeypatch.setattr(IPRoyalMobileAdapter, "__init__", initialize)
            mutate = (
                (lambda: body.__setitem__(slice(None), b"198.51.100.9"))
                if change
                else None
            )
            async with api(
                200, [b'{"synthetic":true}'], requests, change=mutate
            ) as origin:
                local_transport(monkeypatch, origin, calls)
                result = await operations.request("mobile")
            assert len(proxy["proxy_auth"]) == 2
            assert proxy["proxy_auth"][0] == proxy["proxy_auth"][1]
            return result

    result = asyncio.run(scenario())
    assert result.request_outcome == "acknowledged"
    assert result.exit_change == ("changed" if change else "unchanged")
    assert result.authentication is True
    assert result.connectivity is True
    assert result.session_replaced is None
    assert len(requests) == 1

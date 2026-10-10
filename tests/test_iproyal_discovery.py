"""Local contract discovery: control acknowledgements never establish exit changes."""

import asyncio
from contextlib import asynccontextmanager
import json

import aiohttp
import pytest

from proxy2vpn.adapters.external_proxy import ExternalProxyAdapter
from test_external_proxy import (
    controlled_proxy,
    endpoint,
    tls_material as _tls_material,
)

tls_material = _tls_material


@asynccontextmanager
async def control_api(body, *, delay=0, change=False):
    """Synthetic protocol fixture, not a claimed provider response schema."""
    requests = []

    async def handler(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            requests.append(header)
            if b"X-Access-Token: sandbox-token\r\n" not in header:
                writer.write(
                    b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                return
            if change:
                body[:] = b"198.51.100.9"
            await asyncio.sleep(delay)
            response = b'{"synthetic":true}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(response)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + response
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", requests
    finally:
        server.close()
        await server.wait_closed()


# @lat: [[lat.md/iproyal-discovery-tests#IPRoyal Discovery Tests#Independent Request And Observation]]
@pytest.mark.parametrize(
    "delay,change,expected",
    [(0, False, "unchanged"), (0, True, "changed"), (0.2, True, "changed")],
)
def test_local_acknowledgement_and_timeout_are_independent_of_observed_change(
    tls_material, monkeypatch, delay, change, expected
):
    monkeypatch.setenv("SANDBOX_PROXY_USER", "sandbox-user")
    monkeypatch.setenv("SANDBOX_PROXY_PASS", "sandbox-pass")

    async def scenario():
        body = bytearray(b"198.51.100.8")
        async with controlled_proxy(
            tls_material, body=body, credentials="sandbox-user:sandbox-pass"
        ) as (item, tls, proxy, _):
            item = item.model_copy(
                update={
                    "credentials": endpoint(
                        credentials={
                            "username_env": "SANDBOX_PROXY_USER",
                            "password_env": "SANDBOX_PROXY_PASS",
                        }
                    ).credentials
                }
            )
            adapter = ExternalProxyAdapter(item, tls_context=tls)
            before = await adapter.observe()
            async with control_api(body, delay=delay, change=change) as (url, requests):
                request = "unknown"
                async with aiohttp.ClientSession(
                    trust_env=False, timeout=aiohttp.ClientTimeout(total=0.1)
                ) as client:
                    try:
                        async with client.post(
                            url + "/v1/reseller/orders/4g/rotate-ip/sandbox-key",
                            headers={"X-Access-Token": "sandbox-token"},
                            allow_redirects=False,
                        ) as response:
                            # This is HTTP-level acknowledgement, not parsed vendor success.
                            await response.read()
                            if 200 <= response.status < 300:
                                request = "acknowledged"
                    except asyncio.TimeoutError:
                        pass
                after = await adapter.observe()
                observed = "unknown"
                if before.current_egress_ip and after.current_egress_ip:
                    observed = (
                        "changed"
                        if before.current_egress_ip != after.current_egress_ip
                        else "unchanged"
                    )
                result = {
                    "request": request,
                    "exit_change": observed,
                    "connectivity": after.connectivity,
                }
                assert result["request"] == ("unknown" if delay else "acknowledged")
                assert result["exit_change"] == expected
                assert result["connectivity"] is True
                assert len(requests) == 1
                assert requests[0].startswith(
                    b"POST /v1/reseller/orders/4g/rotate-ip/sandbox-key HTTP/1.1"
                )
                assert b"X-Access-Token: sandbox-token" in requests[0]
                assert len(proxy["proxy_auth"]) == 2
                assert proxy["proxy_auth"][0] == proxy["proxy_auth"][1]
                assert adapter.identity == item.identity
                assert "sandbox-token" not in json.dumps(result)
                assert "sandbox-pass" not in json.dumps(result)

    asyncio.run(scenario())

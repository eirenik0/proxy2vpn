import asyncio
import pathlib
import sys

import pytest
from aiohttp import web

# Ensure src package importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters.http_client import (
    HTTPClient,
    HTTPClientConfig,
    RetryPolicy,
    HTTPClientError,
)


async def _start_test_server():
    app = web.Application()
    state = {"flaky_count": 0}

    async def ok(request):
        return web.json_response({"ok": True})

    async def flaky(request):
        if state["flaky_count"] == 0:
            state["flaky_count"] += 1
            raise web.HTTPInternalServerError()
        return web.json_response({"ok": True})

    async def error(request):  # pragma: no cover - simple error path
        raise web.HTTPInternalServerError()

    app.add_routes(
        [
            web.get("/ok", ok),
            web.get("/flaky", flaky),
            web.get("/error", error),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    return runner, base_url


def test_http_client_handles_requests_and_retries():
    async def runner():
        app_runner, base_url = await _start_test_server()
        cfg = HTTPClientConfig(
            base_url=base_url, retry=RetryPolicy(attempts=1, backoff=0)
        )
        async with HTTPClient(cfg) as client:
            data = await client.get("/ok")
            assert data == {"ok": True}
            # First call fails then succeeds due to retry
            data = await client.get("/flaky")
            assert data == {"ok": True}
            with pytest.raises(HTTPClientError):
                await client.get("/error")
        assert client._session is not None and client._session.closed
        await app_runner.cleanup()

    asyncio.run(runner())

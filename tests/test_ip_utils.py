import asyncio
import pathlib
import sys
from types import SimpleNamespace

from aiohttp import web

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters import ip_utils


def test_parse_ip_from_html():
    html = "<html><body>IP is 203.0.113.5</body></html>"
    assert ip_utils._parse_ip(html) == "203.0.113.5"


def test_parse_ip_invalid_text():
    assert ip_utils._parse_ip("<html></html>") == ""


async def _start_ip_server():
    app = web.Application()
    state = {"flaky": 0}

    async def fast(request):
        return web.Response(text="203.0.113.5")

    async def slow(request):
        await asyncio.sleep(0.1)
        return web.Response(text="203.0.113.6")

    async def flaky(request):
        if state["flaky"] == 0:
            state["flaky"] += 1
            raise web.HTTPInternalServerError()
        return web.Response(text="203.0.113.7")

    app.add_routes(
        [
            web.get("/fast", fast),
            web.get("/slow", slow),
            web.get("/flaky", flaky),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    return runner, base


def test_fetch_ip_async_returns_first_result(monkeypatch):
    async def runner():
        app_runner, base_url = await _start_ip_server()
        monkeypatch.setattr(
            ip_utils, "IP_SERVICES", (f"{base_url}/slow", f"{base_url}/fast")
        )
        ip = await ip_utils.fetch_ip_async()
        assert ip == "203.0.113.5"
        await app_runner.cleanup()

    asyncio.run(runner())


def test_fetch_ip_async_retries(monkeypatch):
    async def runner():
        app_runner, base_url = await _start_ip_server()
        monkeypatch.setattr(ip_utils, "IP_SERVICES", (f"{base_url}/flaky",))
        ip = await ip_utils.fetch_ip_async()
        assert ip == "203.0.113.7"
        await app_runner.cleanup()

    asyncio.run(runner())


def test_fetch_ip_async_prefers_curl_for_proxy(monkeypatch):
    calls = []

    monkeypatch.setattr(ip_utils.shutil, "which", lambda name: "/usr/bin/curl")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="1.1.1.1\n")

    monkeypatch.setattr(ip_utils.subprocess, "run", fake_run)

    result = asyncio.run(
        ip_utils.fetch_ip_async(
            proxies={"http": "http://user:pass@localhost:8080"},
            timeout=5,
        )
    )

    assert result == "1.1.1.1"
    assert calls
    command, kwargs = calls[0]
    assert command[:3] == ["curl", "-fsSL", "--connect-timeout"]
    assert "-x" in command
    assert "http://localhost:8080" in command
    assert "--proxy-user" in command
    assert "user:pass" in command
    assert kwargs["capture_output"] is True

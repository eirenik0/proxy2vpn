import asyncio
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import control_client


BASE_URL = "http://localhost:8000"


def test_get_status_calls_correct_url(monkeypatch):
    called: dict[str, str] = {}

    async def fake_request(
        method, url, action, **kwargs
    ):  # pragma: no cover - simple mock
        called["method"] = method
        called["url"] = url
        return {"status": "ok"}

    monkeypatch.setattr(control_client, "_request", fake_request)
    result = asyncio.run(control_client.get_status(BASE_URL))
    assert result == {"status": "ok"}
    assert called["method"] == "get"
    assert called["url"] == f"{BASE_URL}/status"


def test_set_openvpn_status_posts_payload(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request(
        method, url, action, **kwargs
    ):  # pragma: no cover - simple mock
        called["method"] = method
        called["url"] = url
        called["json"] = kwargs.get("json")
        return {"status": kwargs["json"]["status"]}

    monkeypatch.setattr(control_client, "_request", fake_request)
    result = asyncio.run(control_client.set_openvpn_status(BASE_URL, True))
    assert result == {"status": True}
    assert called["method"] == "post"
    assert called["url"] == f"{BASE_URL}/openvpn"
    assert called["json"] == {"status": True}


def test_get_public_ip_returns_ip(monkeypatch):
    called: dict[str, str] = {}

    async def fake_request(
        method, url, action, **kwargs
    ):  # pragma: no cover - simple mock
        called["url"] = url
        return {"ip": "1.2.3.4"}

    monkeypatch.setattr(control_client, "_request", fake_request)
    ip = asyncio.run(control_client.get_public_ip(BASE_URL))
    assert ip == "1.2.3.4"
    assert called["url"] == f"{BASE_URL}/ip"


def test_restart_tunnel_puts_status(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request(method, url, action, **kwargs):
        called["method"] = method
        called["url"] = url
        called["json"] = kwargs.get("json")
        return {"status": "restarted"}

    monkeypatch.setattr(control_client, "_request", fake_request)
    result = asyncio.run(control_client.restart_tunnel(BASE_URL))
    assert result == {"status": "restarted"}
    assert called["method"] == "put"
    assert called["url"] == f"{BASE_URL}/openvpn/status"
    assert called["json"] == {"status": "restarted"}

import asyncio
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters.http_client import (
    GluetunControlClient,
    OpenVPNResponse,
    IPResponse,
    OpenVPNStatusResponse,
    StatusResponse,
)

BASE_URL = "http://localhost:8000"


def test_get_status_calls_correct_url(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request(
        self, method, path, **kwargs
    ):  # pragma: no cover - simple mock
        called["method"] = method
        called["path"] = path
        return {"status": "ok"}

    client = GluetunControlClient(BASE_URL)
    monkeypatch.setattr(GluetunControlClient, "request", fake_request)
    result = asyncio.run(client.status())
    assert result == StatusResponse(status="ok")
    assert called["method"] == "GET"
    assert called["path"] == GluetunControlClient.ENDPOINTS["status"]


def test_set_openvpn_status_posts_payload(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request(
        self, method, path, **kwargs
    ):  # pragma: no cover - simple mock
        called["method"] = method
        called["path"] = path
        called["json"] = kwargs.get("json")
        return {"status": kwargs["json"]["status"]}

    client = GluetunControlClient(BASE_URL)
    monkeypatch.setattr(GluetunControlClient, "request", fake_request)
    result = asyncio.run(client.set_openvpn(True))
    assert result == OpenVPNResponse(status=True)
    assert called["method"] == "POST"
    assert called["path"] == GluetunControlClient.ENDPOINTS["openvpn"]
    assert called["json"] == {"status": True}


def test_get_public_ip_returns_ip(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request(
        self, method, path, **kwargs
    ):  # pragma: no cover - simple mock
        called["path"] = path
        return {"ip": "1.2.3.4"}

    client = GluetunControlClient(BASE_URL)
    monkeypatch.setattr(GluetunControlClient, "request", fake_request)
    ip = asyncio.run(client.public_ip())
    assert ip == IPResponse("1.2.3.4")
    assert called["path"] == GluetunControlClient.ENDPOINTS["ip"]


def test_restart_tunnel_puts_status(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request(
        self, method, path, **kwargs
    ):  # pragma: no cover - simple mock
        called["method"] = method
        called["path"] = path
        called["json"] = kwargs.get("json")
        return {"status": "restarted"}

    client = GluetunControlClient(BASE_URL)
    monkeypatch.setattr(GluetunControlClient, "request", fake_request)
    result = asyncio.run(client.restart_tunnel())
    assert result == OpenVPNStatusResponse(status="restarted")
    assert called["method"] == "PUT"
    assert called["path"] == GluetunControlClient.ENDPOINTS["openvpn_status"]
    assert called["json"] == {"status": "restarted"}

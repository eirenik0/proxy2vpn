import asyncio
import pytest

from proxy2vpn.adapters.http_client import (
    GluetunControlClient,
    HTTPClient,
    IPResponse,
    OpenVPNResponse,
    OpenVPNStatusResponse,
    DNSStatusResponse,
    UpdaterStatusResponse,
    PortForwardResponse,
    StatusResponse,
)

BASE_URL = "http://localhost:8000"


def test_status_calls_correct_path(monkeypatch):
    called = {}

    async def fake_get(self, path, **kwargs):  # pragma: no cover - simple mock
        called["path"] = path
        return {"status": "ok"}

    monkeypatch.setattr(HTTPClient, "get", fake_get)
    client = GluetunControlClient(BASE_URL)
    result = asyncio.run(client.status())
    assert result == StatusResponse(status="ok")
    assert called["path"] == GluetunControlClient.ENDPOINTS["status"]


def test_set_openvpn_posts_payload(monkeypatch):
    called = {}

    async def fake_post(self, path, **kwargs):  # pragma: no cover - simple mock
        called["path"] = path
        called["json"] = kwargs.get("json")
        return {"status": kwargs["json"]["status"]}

    monkeypatch.setattr(HTTPClient, "post", fake_post)
    client = GluetunControlClient(BASE_URL)
    result = asyncio.run(client.set_openvpn(True))
    assert result == OpenVPNResponse(status=True)
    assert called["path"] == GluetunControlClient.ENDPOINTS["openvpn"]
    assert called["json"] == {"status": True}


def test_public_ip_returns_ip(monkeypatch):
    called = {}

    async def fake_get(self, path, **kwargs):  # pragma: no cover - simple mock
        called["path"] = path
        return {"ip": "1.2.3.4"}

    monkeypatch.setattr(HTTPClient, "get", fake_get)
    client = GluetunControlClient(BASE_URL)
    result = asyncio.run(client.public_ip())
    assert result == IPResponse(ip="1.2.3.4")
    assert called["path"] == GluetunControlClient.ENDPOINTS["ip"]


def test_restart_tunnel_puts_status(monkeypatch):
    called = {}

    async def fake_request(
        self, method, path, **kwargs
    ):  # pragma: no cover - simple mock
        called["method"] = method
        called["path"] = path
        called["json"] = kwargs.get("json")
        return {"status": "restarted"}

    monkeypatch.setattr(HTTPClient, "request", fake_request)
    client = GluetunControlClient(BASE_URL)
    result = asyncio.run(client.restart_tunnel())
    assert result == OpenVPNStatusResponse(status="restarted")
    assert called["method"] == "PUT"
    assert called["path"] == GluetunControlClient.ENDPOINTS["openvpn_status"]
    assert called["json"] == {"status": "restarted"}


def test_dns_status_calls_correct_path(monkeypatch):
    called = {}

    async def fake_get(self, path, **kwargs):  # pragma: no cover - simple mock
        called["path"] = path
        return {"status": "running"}

    monkeypatch.setattr(HTTPClient, "get", fake_get)
    client = GluetunControlClient(BASE_URL)
    result = asyncio.run(client.dns_status())
    assert result == DNSStatusResponse(status="running")
    assert called["path"] == GluetunControlClient.ENDPOINTS["dns_status"]


def test_updater_status_calls_correct_path(monkeypatch):
    called = {}

    async def fake_get(self, path, **kwargs):  # pragma: no cover - simple mock
        called["path"] = path
        return {"status": "completed"}

    monkeypatch.setattr(HTTPClient, "get", fake_get)
    client = GluetunControlClient(BASE_URL)
    result = asyncio.run(client.updater_status())
    assert result == UpdaterStatusResponse(status="completed")
    assert called["path"] == GluetunControlClient.ENDPOINTS["updater_status"]


def test_port_forwarded_calls_correct_path(monkeypatch):
    called = {}

    async def fake_get(self, path, **kwargs):  # pragma: no cover - simple mock
        called["path"] = path
        return {"port": 9999}

    monkeypatch.setattr(HTTPClient, "get", fake_get)
    client = GluetunControlClient(BASE_URL)
    result = asyncio.run(client.port_forwarded())
    assert result == PortForwardResponse(port=9999)
    assert called["path"] == GluetunControlClient.ENDPOINTS["port_forward"]


def test_auth_from_env(monkeypatch):
    monkeypatch.setenv("GLUETUN_CONTROL_AUTH", "user:pass")
    client = GluetunControlClient(BASE_URL)
    assert client._config.auth == ("user", "pass")


def test_invalid_url():
    with pytest.raises(ValueError):
        GluetunControlClient("not a url")


def test_invalid_auth(monkeypatch):
    monkeypatch.setenv("GLUETUN_CONTROL_AUTH", "no_colon")
    with pytest.raises(ValueError):
        GluetunControlClient(BASE_URL)

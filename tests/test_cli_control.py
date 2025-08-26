import pathlib
from dataclasses import dataclass

from typer.testing import CliRunner

from proxy2vpn.cli.main import app
from proxy2vpn.adapters import http_client


COMPOSE_FILE = pathlib.Path(__file__).with_name("test_compose.yml")


@dataclass
class _Status:
    status: str
    openvpn: str


@dataclass
class _IP:
    ip: str


@dataclass
class _Restart:
    status: str


class DummyClient:
    def __init__(self, base_url):
        self.base_url = base_url

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def status(self):
        return _Status(status="running", openvpn="enabled")

    async def public_ip(self):
        return _IP(ip="1.2.3.4")

    async def restart_tunnel(self):
        return _Restart(status="restarted")


def test_vpn_status_uses_localhost(monkeypatch):
    runner = CliRunner()
    called = {}

    def fake_client(base_url):
        called["base_url"] = base_url
        return DummyClient(base_url)

    monkeypatch.setattr(http_client, "GluetunControlClient", fake_client)

    result = runner.invoke(
        app, ["--compose-file", str(COMPOSE_FILE), "vpn", "status", "testvpn1"]
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:30000/v1"


def test_vpn_public_ip_uses_localhost(monkeypatch):
    runner = CliRunner()
    called = {}

    def fake_client(base_url):
        called["base_url"] = base_url
        return DummyClient(base_url)

    monkeypatch.setattr(http_client, "GluetunControlClient", fake_client)

    result = runner.invoke(
        app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "public-ip", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:30000/v1"


def test_vpn_restart_tunnel_uses_localhost(monkeypatch):
    runner = CliRunner()
    called = {}

    def fake_client(base_url):
        called["base_url"] = base_url
        return DummyClient(base_url)

    monkeypatch.setattr(http_client, "GluetunControlClient", fake_client)

    result = runner.invoke(
        app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "restart-tunnel", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:30000/v1"

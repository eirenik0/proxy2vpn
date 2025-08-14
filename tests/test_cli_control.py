import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn import cli
from proxy2vpn.http_client import StatusResponse, IPResponse, OpenVPNStatusResponse


COMPOSE_FILE = pathlib.Path(__file__).with_name("test_compose.yml")


def test_vpn_status_uses_localhost_connection(monkeypatch):
    runner = CliRunner()
    called = {}

    class MockGluetunControlClient:
        def __init__(self, base_url):
            called["base_url"] = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def status(self):
            called["method_called"] = "status"
            return StatusResponse(status="running")

    monkeypatch.setattr("proxy2vpn.cli.GluetunControlClient", MockGluetunControlClient)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "status", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"].startswith("http://localhost:")
    assert called["method_called"] == "status"


def test_vpn_public_ip_uses_localhost_connection(monkeypatch):
    runner = CliRunner()
    called = {}

    class MockGluetunControlClient:
        def __init__(self, base_url):
            called["base_url"] = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def public_ip(self):
            called["method_called"] = "public_ip"
            return IPResponse(ip="1.2.3.4")

    monkeypatch.setattr("proxy2vpn.cli.GluetunControlClient", MockGluetunControlClient)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "public-ip", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"].startswith("http://localhost:")
    assert called["method_called"] == "public_ip"


def test_vpn_restart_tunnel_uses_localhost_connection(monkeypatch):
    runner = CliRunner()
    called = {}

    class MockGluetunControlClient:
        def __init__(self, base_url):
            called["base_url"] = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def restart_tunnel(self):
            called["method_called"] = "restart_tunnel"
            return OpenVPNStatusResponse(status="restarted")

    monkeypatch.setattr("proxy2vpn.cli.GluetunControlClient", MockGluetunControlClient)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "restart-tunnel", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"].startswith("http://localhost:")
    assert called["method_called"] == "restart_tunnel"

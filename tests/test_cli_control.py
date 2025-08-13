import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn import cli
from proxy2vpn.http_client import IPResponse, OpenVPNStatusResponse, StatusResponse


COMPOSE_FILE = pathlib.Path(__file__).with_name("test_compose.yml")


def test_vpn_status_uses_control_port(monkeypatch):
    runner = CliRunner()
    called = {}

    class FakeClient:
        def __init__(self, base_url, *args, **kwargs):
            called["base_url"] = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # pragma: no cover - trivial
            return False

        async def status(self):
            return StatusResponse(status="ok")

    monkeypatch.setattr(cli, "GluetunControlClient", FakeClient)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "status", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:19999/v1"


def test_vpn_public_ip_uses_control_port(monkeypatch):
    runner = CliRunner()
    called = {}

    class FakeClient:
        def __init__(self, base_url, *args, **kwargs):
            called["base_url"] = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # pragma: no cover - trivial
            return False

        async def public_ip(self):
            return IPResponse(ip="1.2.3.4")

    monkeypatch.setattr(cli, "GluetunControlClient", FakeClient)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "public-ip", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:19999/v1"


def test_vpn_restart_tunnel_uses_control_port(monkeypatch):
    runner = CliRunner()
    called = {}

    class FakeClient:
        def __init__(self, base_url, *args, **kwargs):
            called["base_url"] = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # pragma: no cover - trivial
            return False

        async def restart_tunnel(self):
            return OpenVPNStatusResponse(status="restarted")

    monkeypatch.setattr(cli, "GluetunControlClient", FakeClient)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "restart-tunnel", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:19999/v1"

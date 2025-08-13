import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn import cli, control_client


COMPOSE_FILE = pathlib.Path(__file__).with_name("test_compose.yml")


def test_vpn_status_uses_control_port(monkeypatch):
    runner = CliRunner()
    called = {}

    async def fake_get_status(base_url):
        called["base_url"] = base_url
        return {"status": "ok"}

    monkeypatch.setattr(control_client, "get_status", fake_get_status)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "status", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:19999/v1"


def test_vpn_public_ip_uses_control_port(monkeypatch):
    runner = CliRunner()
    called = {}

    async def fake_get_public_ip(base_url):
        called["base_url"] = base_url
        return "1.2.3.4"

    monkeypatch.setattr(control_client, "get_public_ip", fake_get_public_ip)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "public-ip", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:19999/v1"


def test_vpn_restart_tunnel_uses_control_port(monkeypatch):
    runner = CliRunner()
    called = {}

    async def fake_restart_tunnel(base_url):
        called["base_url"] = base_url
        return {}

    monkeypatch.setattr(control_client, "restart_tunnel", fake_restart_tunnel)

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "restart-tunnel", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["base_url"] == "http://localhost:19999/v1"

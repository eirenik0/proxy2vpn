import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn import cli


COMPOSE_FILE = pathlib.Path(__file__).with_name("test_compose.yml")


def test_vpn_status_uses_internal_networking(monkeypatch):
    runner = CliRunner()
    called = {}

    async def fake_async_docker_request(
        container_name, url_path, method="GET", json_data=None
    ):
        called["container_name"] = container_name
        called["url_path"] = url_path
        called["method"] = method
        called["json_data"] = json_data
        return '{"status": "running"}'

    # Mock the async docker network request function
    import proxy2vpn.docker_ops

    monkeypatch.setattr(
        proxy2vpn.docker_ops, "async_docker_network_request", fake_async_docker_request
    )

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "status", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["container_name"] == "testvpn1"
    assert called["url_path"] == "/status"


def test_vpn_public_ip_uses_internal_networking(monkeypatch):
    runner = CliRunner()
    called = {}

    async def fake_async_docker_request(
        container_name, url_path, method="GET", json_data=None
    ):
        called["container_name"] = container_name
        called["url_path"] = url_path
        called["method"] = method
        called["json_data"] = json_data
        return '{"ip": "1.2.3.4"}'

    import proxy2vpn.docker_ops

    monkeypatch.setattr(
        proxy2vpn.docker_ops, "async_docker_network_request", fake_async_docker_request
    )

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "public-ip", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["container_name"] == "testvpn1"
    assert called["url_path"] == "/ip"


def test_vpn_restart_tunnel_uses_internal_networking(monkeypatch):
    runner = CliRunner()
    called = {}

    async def fake_async_docker_request(
        container_name, url_path, method="GET", json_data=None
    ):
        called["container_name"] = container_name
        called["url_path"] = url_path
        called["method"] = method
        called["json_data"] = json_data
        return '{"status": "restarted"}'

    import proxy2vpn.docker_ops

    monkeypatch.setattr(
        proxy2vpn.docker_ops, "async_docker_network_request", fake_async_docker_request
    )

    result = runner.invoke(
        cli.app,
        ["--compose-file", str(COMPOSE_FILE), "vpn", "restart-tunnel", "testvpn1"],
    )
    assert result.exit_code == 0
    assert called["container_name"] == "testvpn1"
    assert called["url_path"] == "/openvpn/status"
    assert called["method"] == "PUT"

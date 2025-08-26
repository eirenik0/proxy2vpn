import pathlib
import sys

from typer.testing import CliRunner

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.cli.main import app
from proxy2vpn.adapters import docker_ops, compose_manager
from proxy2vpn.core.models import VPNService


def test_vpn_list_includes_provider_and_location(monkeypatch):
    runner = CliRunner()

    svc = VPNService.create(
        name="svc",
        port=8080,
        control_port=30000,
        provider="prov",
        profile="pro",
        location="US",
        environment={},
        labels={},
    )

    class DummyComposeManager:
        def __init__(self, *a, **k):
            pass

        def list_services(self):
            return [svc]

    class Container:
        name = "svc"
        status = "running"

    monkeypatch.setattr(
        docker_ops, "get_vpn_containers", lambda all=True: [Container()]
    )

    async def fake_get_ip(container):
        return "1.2.3.4"

    monkeypatch.setattr(docker_ops, "get_container_ip_async", fake_get_ip)
    monkeypatch.setattr(compose_manager, "ComposeManager", DummyComposeManager)

    result = runner.invoke(app, ["vpn", "list"])
    assert result.exit_code == 0
    assert "Provider" in result.stdout
    assert "Location" in result.stdout
    assert "US" in result.stdout

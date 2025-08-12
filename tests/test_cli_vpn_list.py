import pathlib
import sys

from typer.testing import CliRunner

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import cli, docker_ops
from proxy2vpn.models import VPNService


def test_vpn_list_ips_only_async(monkeypatch):
    runner = CliRunner()

    class C:
        name = "svc"
        status = "running"
        labels = {"vpn.port": "8080"}

    monkeypatch.setattr(docker_ops, "get_vpn_containers", lambda all=False: [C()])

    called = {"n": 0}

    async def fake_get_ip(container):
        called["n"] += 1
        return "1.2.3.4"

    monkeypatch.setattr(docker_ops, "get_container_ip_async", fake_get_ip)
    monkeypatch.setattr(cli, "ComposeManager", lambda *a, **k: None)

    result = runner.invoke(cli.app, ["vpn", "list", "--ips-only"])
    assert result.exit_code == 0
    assert "svc: 1.2.3.4" in result.stdout
    assert called["n"] == 1


def test_vpn_list_includes_location(monkeypatch):
    runner = CliRunner()

    svc = VPNService(
        name="svc",
        port=8080,
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
    monkeypatch.setattr(cli, "ComposeManager", DummyComposeManager)

    result = runner.invoke(cli.app, ["vpn", "list"])
    assert result.exit_code == 0
    assert "Location" in result.stdout
    assert "US" in result.stdout

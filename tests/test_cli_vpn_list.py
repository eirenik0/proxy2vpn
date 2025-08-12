import pathlib
import sys

from typer.testing import CliRunner

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import cli, docker_ops


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

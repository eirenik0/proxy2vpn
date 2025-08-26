import sys
import pathlib
from typer.testing import CliRunner
from types import SimpleNamespace

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.cli.main import app
from proxy2vpn.adapters import docker_ops, ip_utils


def test_vpn_export_proxies(monkeypatch, tmp_path):
    runner = CliRunner()

    # Mock the host machine's IP address
    async def fake_fetch_ip_async():
        return "203.0.113.1"  # Host machine's public IP

    # Mock ComposeManager.from_ctx to return a manager with one service
    class FakeService:
        name = "svc1"
        port = 20001
        location = "London"
        provider = "expressvpn"
        environment = {"HTTPPROXY_USER": "user", "HTTPPROXY_PASSWORD": "pass"}
        credentials = None

    class FakeManager:
        def list_services(self):
            return [FakeService()]

    from proxy2vpn.adapters import compose_manager as compose_manager_mod

    def fake_from_ctx(ctx):
        return FakeManager()

    # Mock container resolution by service name
    def fake_get_container_by_service_name(name: str):
        return SimpleNamespace(status="running")

    monkeypatch.setattr(ip_utils, "fetch_ip_async", fake_fetch_ip_async)
    monkeypatch.setattr(
        compose_manager_mod.ComposeManager,
        "from_ctx",
        classmethod(lambda cls, ctx: fake_from_ctx(ctx)),
    )
    monkeypatch.setattr(
        docker_ops, "get_container_by_service_name", fake_get_container_by_service_name
    )

    out = tmp_path / "proxies.csv"
    result = runner.invoke(app, ["vpn", "export-proxies", "--output", str(out)])
    assert result.exit_code == 0
    lines = out.read_text().splitlines()
    assert lines[0] == "host,port,username,password,location,provider,status"
    assert lines[1] == "203.0.113.1,20001,user,pass,London,expressvpn,active"

    # Test with --no-auth flag
    out_no = tmp_path / "proxies_no.csv"
    result2 = runner.invoke(
        app,
        ["vpn", "export-proxies", "--output", str(out_no), "--no-auth"],
    )
    assert result2.exit_code == 0
    fields = out_no.read_text().splitlines()[1].split(",")
    assert fields[0] == "203.0.113.1"  # host
    assert fields[1] == "20001"  # port
    assert fields[2] == ""  # username (empty due to --no-auth)
    assert fields[3] == ""  # password (empty due to --no-auth)
    assert fields[4] == "London"  # location
    assert fields[5] == "expressvpn"  # provider
    assert fields[6] == "active"  # status

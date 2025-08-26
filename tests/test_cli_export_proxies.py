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

    # Mock get_vpn_containers to return fake containers
    def fake_get_vpn_containers(all=True):
        container = SimpleNamespace()
        container.attrs = {
            "Config": {"Env": ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=pass"]},
            "State": {},
        }
        container.labels = {"vpn.port": "20001", "vpn.location": "London"}
        container.status = "running"
        return [container]

    monkeypatch.setattr(ip_utils, "fetch_ip_async", fake_fetch_ip_async)
    monkeypatch.setattr(docker_ops, "get_vpn_containers", fake_get_vpn_containers)

    out = tmp_path / "proxies.csv"
    result = runner.invoke(app, ["vpn", "export-proxies", "--output", str(out)])
    assert result.exit_code == 0
    lines = out.read_text().splitlines()
    assert lines[0] == "host,port,username,password,location,status"
    assert lines[1] == "203.0.113.1,20001,user,pass,London,active"

    # Test with --no-auth flag
    def fake_get_vpn_containers_no_auth(all=True):
        container = SimpleNamespace()
        container.attrs = {
            "Config": {"Env": ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=pass"]},
            "State": {},
        }
        container.labels = {"vpn.port": "20001", "vpn.location": "London"}
        container.status = "running"
        return [container]

    monkeypatch.setattr(
        docker_ops, "get_vpn_containers", fake_get_vpn_containers_no_auth
    )

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

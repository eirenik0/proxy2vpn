import sys
import pathlib
from typer.testing import CliRunner

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import cli, docker_ops


def test_vpn_export_proxies(monkeypatch, tmp_path):
    runner = CliRunner()

    async def fake_collect(include_credentials: bool = True):
        return [
            {
                "host": "1.2.3.4",
                "port": "20001",
                "username": "user" if include_credentials else "",
                "password": "pass" if include_credentials else "",
                "location": "London",
                "status": "active",
            }
        ]

    monkeypatch.setattr(docker_ops, "collect_proxy_info", fake_collect)

    out = tmp_path / "proxies.csv"
    result = runner.invoke(cli.app, ["vpn", "export-proxies", "--output", str(out)])
    assert result.exit_code == 0
    lines = out.read_text().splitlines()
    assert lines[0] == "host,port,username,password,location,status"
    assert lines[1] == "1.2.3.4,20001,user,pass,London,active"

    out_no = tmp_path / "proxies_no.csv"
    result2 = runner.invoke(
        cli.app,
        ["vpn", "export-proxies", "--output", str(out_no), "--no-auth"],
    )
    assert result2.exit_code == 0
    fields = out_no.read_text().splitlines()[1].split(",")
    assert fields[2] == ""
    assert fields[3] == ""

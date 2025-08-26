import os
import pathlib
import subprocess
import sys

from ruamel.yaml import YAML
from types import SimpleNamespace

from proxy2vpn.cli.commands.system import init as system_init
from proxy2vpn.adapters.server_manager import ServerManager


def _run_proxy2vpn(args, cwd):
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    env["HOME"] = str(cwd)
    return subprocess.run(
        [sys.executable, "-m", "proxy2vpn", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def test_init_creates_compose(tmp_path):
    cache_dir = tmp_path / ".cache" / "proxy2vpn"
    cache_dir.mkdir(parents=True)
    (cache_dir / "servers.json").write_text("{}")
    result = _run_proxy2vpn(["system", "init"], tmp_path)
    assert result.returncode == 0
    compose = tmp_path / "compose.yml"
    assert compose.exists()
    yaml = YAML()
    data = yaml.load(compose.read_text())
    assert data["services"] == {}
    assert "proxy2vpn_network" in data["networks"]
    assert "version" not in data


def test_init_requires_force(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")

    cache_dir = tmp_path / ".cache" / "proxy2vpn"
    cache_dir.mkdir(parents=True)
    (cache_dir / "servers.json").write_text("{}")

    result = _run_proxy2vpn(["system", "init"], tmp_path)
    assert result.returncode != 0

    result = _run_proxy2vpn(["system", "init", "--force"], tmp_path)
    assert result.returncode == 0


def test_system_init_updates_servers(tmp_path, monkeypatch):
    called = {}

    async def fake_update(self, verify=True):
        called["update"] = verify

    monkeypatch.setattr(ServerManager, "fetch_server_list_async", fake_update)
    ctx = SimpleNamespace(obj={"compose_file": tmp_path / "compose.yml"})
    system_init(ctx, force=True)
    assert called["update"] is True

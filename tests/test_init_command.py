import os
import pathlib
import subprocess
import sys
from time import time

from ruamel.yaml import YAML
from types import SimpleNamespace

from proxy2vpn.cli.commands.system import init as system_init
from proxy2vpn.cli.commands.system import validate as system_validate
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


def test_init_writes_auth_config_next_to_custom_compose_file(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    compose_file = state_dir / "compose.yml"

    cache_dir = tmp_path / ".cache" / "proxy2vpn"
    cache_dir.mkdir(parents=True)
    (cache_dir / "servers.json").write_text("{}")

    result = _run_proxy2vpn(
        [
            "--compose-file",
            str(compose_file),
            "system",
            "init",
            "--skip-server-refresh",
        ],
        tmp_path,
    )
    assert result.returncode == 0
    assert compose_file.exists()
    assert (state_dir / "control-server-auth.toml").exists()
    assert not (tmp_path / "control-server-auth.toml").exists()


def test_system_validate_allows_profile_only_workspace(tmp_path):
    cache_dir = tmp_path / ".cache" / "proxy2vpn"
    cache_dir.mkdir(parents=True)
    (cache_dir / "servers.json").write_text(
        '{"version": 1, "expressvpn": {"servers": []}}'
    )

    env_file = tmp_path / "profiles" / "dev.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "VPN_SERVICE_PROVIDER=expressvpn\nOPENVPN_USER=test\nOPENVPN_PASSWORD=secret\n"
    )

    result = _run_proxy2vpn(["system", "init", "--skip-server-refresh"], tmp_path)
    assert result.returncode == 0

    result = _run_proxy2vpn(["profile", "add", "dev", str(env_file)], tmp_path)
    assert result.returncode == 0

    result = _run_proxy2vpn(["system", "validate"], tmp_path)
    assert result.returncode == 0


def test_system_validate_rejects_named_auth_volume(tmp_path):
    env_file = tmp_path / "profiles" / "dev.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "VPN_SERVICE_PROVIDER=expressvpn\nOPENVPN_USER=test\nOPENVPN_PASSWORD=secret\n"
    )

    compose = tmp_path / "compose.yml"
    compose.write_text(
        f"""
x-vpn-base-dev: &vpn-base-dev
  image: qmcgaw/gluetun
  cap_add:
    - NET_ADMIN
  devices:
    - /dev/net/tun:/dev/net/tun
  env_file: {env_file}
services:
  devvpn:
    <<: *vpn-base-dev
    ports:
      - "0.0.0.0:20000:8888/tcp"
      - "127.0.0.1:30000:8000/tcp"
    environment:
      - VPN_SERVICE_PROVIDER=expressvpn
      - SERVER_CITIES=Toronto
    labels:
      vpn.type: vpn
      vpn.port: "20000"
      vpn.control_port: "30000"
      vpn.profile: dev
    volumes:
      - control-server-auth.toml:/gluetun/auth/config.toml:ro
"""
    )

    result = _run_proxy2vpn(["system", "validate"], tmp_path)
    assert result.returncode == 1
    assert "named volume" in result.stderr
    assert "control-server-auth.toml" in result.stderr


def test_system_init_updates_servers(tmp_path, monkeypatch):
    called = {}

    async def fake_update(self, verify=True):
        called["update"] = verify

    monkeypatch.setattr(ServerManager, "fetch_server_list_async", fake_update)
    ctx = SimpleNamespace(obj={"compose_file": tmp_path / "compose.yml"})
    system_init(ctx, force=True)
    assert called["update"] is True


def test_system_init_uses_cached_server_list_when_refresh_fails(tmp_path, monkeypatch):
    cache_file = tmp_path / "servers.json"
    cache_file.write_text("{}")
    cache_file.touch()
    os.utime(cache_file, (time() - 60, time() - 60))
    called = {}

    class OfflineManager:
        def __init__(self) -> None:
            self.cache_file = cache_file

        async def fetch_server_list_async(self, verify: bool = True):
            called["update"] = True
            raise RuntimeError("network down")

        def is_cache_fresh(self):
            return False

    # Redirect ServerManager creation to use a deterministic fake with stale cache.
    monkeypatch.setattr(
        "proxy2vpn.cli.commands.system.ServerManager", lambda: OfflineManager()
    )
    ctx = SimpleNamespace(obj={"compose_file": tmp_path / "compose.yml"})
    monkeypatch.chdir(tmp_path)
    # No exception should be raised when cached data exists and refresh fails.
    system_init(ctx, force=True)
    assert called["update"] is True


def test_system_validate_defaults_to_no_location_validation(monkeypatch):
    called = {}

    class FakeManager:
        def validate_compose_file(self, validate_locations: bool = False):
            called["validate_locations"] = validate_locations
            return []

    monkeypatch.setattr(
        "proxy2vpn.cli.commands.system.ComposeManager",
        type(
            "ComposeManager",
            (),
            {"from_ctx": classmethod(lambda _cls, _ctx: FakeManager())},
        ),
    )
    ctx = SimpleNamespace(obj={})
    system_validate(ctx)
    assert called["validate_locations"] is False


def test_system_validate_explicit_flag_triggers_location_checks(monkeypatch):
    called = {}

    class FakeManager:
        def validate_compose_file(self, validate_locations: bool = False):
            called["validate_locations"] = validate_locations
            return []

    monkeypatch.setattr(
        "proxy2vpn.cli.commands.system.ComposeManager",
        type(
            "ComposeManager",
            (),
            {"from_ctx": classmethod(lambda _cls, _ctx: FakeManager())},
        ),
    )
    ctx = SimpleNamespace(obj={})
    system_validate(ctx, validate_locations=True)
    assert called["validate_locations"] is True

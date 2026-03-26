import pathlib
import sys
from types import SimpleNamespace

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn.cli.main import app
from proxy2vpn.adapters import compose_manager
from proxy2vpn.adapters import docker_ops


def _dummy_manager():
    return SimpleNamespace(
        list_services=lambda: [SimpleNamespace(name="svc1", profile="prof1")],
        get_profile=lambda name: SimpleNamespace(
            env_file="env", image="img", cap_add=[], devices=[]
        ),
        get_service=lambda name: SimpleNamespace(name=name, profile="prof1"),
    )


def test_vpn_restart_all_restarts_in_place(monkeypatch):
    runner = CliRunner()
    dummy_mgr = _dummy_manager()

    monkeypatch.setattr(
        compose_manager.ComposeManager, "from_ctx", lambda ctx: dummy_mgr
    )

    calls = []

    def fake_restart(name):
        calls.append(("restart", name))

    monkeypatch.setattr(docker_ops, "restart_container", fake_restart)

    result = runner.invoke(app, ["vpn", "restart", "--all"])
    assert result.exit_code == 0
    assert "Restarted svc1" in result.stdout
    assert calls == [("restart", "svc1")]


def test_vpn_update_single_recreates_and_starts(monkeypatch):
    runner = CliRunner()
    dummy_mgr = _dummy_manager()

    monkeypatch.setattr(
        compose_manager.ComposeManager, "from_ctx", lambda ctx: dummy_mgr
    )

    calls = []

    def fake_update(service, profile):
        calls.append((service.name, profile.image))

    monkeypatch.setattr(docker_ops, "update_vpn_service", fake_update)
    monkeypatch.setattr(docker_ops, "analyze_container_logs", lambda *a, **k: [])

    result = runner.invoke(app, ["vpn", "update", "svc1"])
    assert result.exit_code == 0
    assert "Updated 'svc1'" in result.stdout
    assert calls == [("svc1", "img")]


def test_vpn_update_all_recreates_and_starts(monkeypatch):
    runner = CliRunner()
    dummy_mgr = _dummy_manager()

    monkeypatch.setattr(
        compose_manager.ComposeManager, "from_ctx", lambda ctx: dummy_mgr
    )

    calls = []

    def fake_update_all(manager):
        calls.append(manager)
        return ["svc1"]

    monkeypatch.setattr(docker_ops, "update_all_vpn_containers", fake_update_all)

    result = runner.invoke(app, ["vpn", "update", "--all"])
    assert result.exit_code == 0
    assert "Updated svc1" in result.stdout
    assert calls == [dummy_mgr]

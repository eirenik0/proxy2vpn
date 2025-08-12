import pathlib
import sys
from types import SimpleNamespace

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn import cli, docker_ops


def test_vpn_restart_all_recreates(monkeypatch):
    runner = CliRunner()

    dummy_mgr = SimpleNamespace(
        list_services=lambda: [SimpleNamespace(name="svc1", profile="prof1")],
        get_profile=lambda name: SimpleNamespace(
            env_file="env", image="img", cap_add=[], devices=[]
        ),
        get_service=lambda name: SimpleNamespace(name=name, profile="prof1"),
    )

    monkeypatch.setattr(cli, "ComposeManager", lambda path: dummy_mgr)

    calls = []

    def fake_recreate(service, profile):
        calls.append(("recreate", service.name))

    def fake_start(name):
        calls.append(("start", name))

    monkeypatch.setattr(docker_ops, "recreate_vpn_container", fake_recreate)
    monkeypatch.setattr(docker_ops, "start_container", fake_start)

    result = runner.invoke(cli.app, ["vpn", "restart", "--all"])
    assert result.exit_code == 0
    assert "Recreated and restarted svc1" in result.stdout
    assert ("recreate", "svc1") in calls
    assert ("start", "svc1") in calls

import pathlib
from contextlib import contextmanager

import typer
from typer.testing import CliRunner

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.cli.main import app
from proxy2vpn.cli.commands.vpn import add as vpn_add


def _copy_compose(tmp_path: pathlib.Path) -> pathlib.Path:
    src = pathlib.Path(__file__).parent / "test_compose.yml"
    env_path = tmp_path / "env.test"
    env_path.write_text(
        "VPN_SERVICE_PROVIDER=expressvpn\nOPENVPN_USER=user\nOPENVPN_PASSWORD=pass\n"
    )
    dest = tmp_path / "compose.yml"
    text = src.read_text().replace("env.test", str(env_path))
    dest.write_text(text)
    return dest


@contextmanager
def _cli_ctx(compose_path: pathlib.Path):
    command = typer.main.get_command(app)
    ctx = typer.Context(command, obj={"compose_file": compose_path})
    with ctx:
        yield ctx


def test_vpn_add_explicit(tmp_path):
    compose_path = _copy_compose(tmp_path)
    with _cli_ctx(compose_path) as ctx:
        manager = ComposeManager.from_ctx(ctx)
        profiles = {p.name for p in manager.list_profiles()}
        assert "test" in profiles
        vpn_add(
            ctx,
            "vpn3",
            profile="test",
            port=7777,
            control_port=0,
            location="",
            httpproxy_user=None,
            httpproxy_password=None,
            interactive=False,
            force=False,
        )
    manager = ComposeManager(compose_path)
    svc = manager.get_service("vpn3")
    assert svc.port == 7777
    assert svc.labels.get("vpn.port") == "7777"
    assert svc.control_port == 30002
    assert svc.labels.get("vpn.control_port") == "30002"
    assert svc.provider == "expressvpn"
    assert svc.environment["VPN_SERVICE_PROVIDER"] == "expressvpn"


def test_vpn_add_duplicate_service_exits_cleanly(tmp_path):
    compose_path = _copy_compose(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose_path),
            "vpn",
            "add",
            "testvpn1",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 1
    assert "Service 'testvpn1' already exists" in result.output
    assert "Traceback" not in result.output


def test_vpn_and_profile_help_reflect_service_definition_commands():
    runner = CliRunner()

    vpn_help = runner.invoke(app, ["vpn", "--help"])
    assert vpn_help.exit_code == 0
    assert "add" in vpn_help.output
    assert "create" not in vpn_help.output

    profile_help = runner.invoke(app, ["profile", "--help"])
    assert profile_help.exit_code == 0
    assert "apply" not in profile_help.output

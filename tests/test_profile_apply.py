import pathlib
from contextlib import contextmanager

import typer

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.cli.main import app
from proxy2vpn.cli.commands.profile import apply as profile_apply


def _copy_compose(tmp_path: pathlib.Path) -> pathlib.Path:
    src = pathlib.Path(__file__).parent / "test_compose.yml"
    env_path = tmp_path / "env.test"
    env_path.write_text("KEY=value\n")
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


def test_profile_apply(tmp_path):
    compose_path = _copy_compose(tmp_path)
    with _cli_ctx(compose_path) as ctx:
        manager = ComposeManager.from_ctx(ctx)
        profiles = {p.name for p in manager.list_profiles()}
        assert "test" in profiles
        profile_apply(ctx, "test", "vpn3", port=7777, control_port=0)
    manager = ComposeManager(compose_path)
    svc = manager.get_service("vpn3")
    assert svc.port == 7777
    assert svc.labels.get("vpn.port") == "7777"
    assert svc.control_port == 30002
    assert svc.labels.get("vpn.control_port") == "30002"

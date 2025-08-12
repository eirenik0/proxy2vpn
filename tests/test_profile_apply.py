import pathlib
from contextlib import contextmanager

import typer

from proxy2vpn.compose_manager import ComposeManager
from proxy2vpn import cli


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
    command = typer.main.get_command(cli.app)
    ctx = typer.Context(command, obj={"compose_file": compose_path})
    with ctx:
        yield ctx


def test_profile_apply(tmp_path):
    compose_path = _copy_compose(tmp_path)
    with _cli_ctx(compose_path) as ctx:
        manager = ComposeManager(compose_path)
        profiles = {p.name for p in manager.list_profiles()}
        assert "test" in profiles
        cli.profile_apply(ctx, "test", "vpn3", port=7777)
    manager = ComposeManager(compose_path)
    services = {s.name for s in manager.list_services()}
    assert "vpn3" in services

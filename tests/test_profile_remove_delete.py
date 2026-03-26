import pathlib
from contextlib import contextmanager

import typer

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.cli.main import app
from proxy2vpn.cli.commands.profile import (
    delete as profile_delete,
    remove as profile_remove,
)
from proxy2vpn.core.models import Profile


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


def test_profile_remove(tmp_path):
    compose_path = _copy_compose(tmp_path)
    with _cli_ctx(compose_path) as ctx:
        profile_remove(ctx, "test", force=True)
    manager = ComposeManager(compose_path)
    profiles = {p.name for p in manager.list_profiles()}
    assert "test" not in profiles


def test_profile_delete_env(tmp_path, monkeypatch):
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    env_file = tmp_path / "custom-location.env"
    env_file.write_text("KEY=value\n")
    manager = ComposeManager(compose_path)
    manager.add_profile(Profile(name="test", env_file=str(env_file)))
    monkeypatch.chdir(tmp_path)
    with _cli_ctx(compose_path) as ctx:
        profile_delete(ctx, "test", force=True)
    assert not env_file.exists()


def test_profile_delete_env_resolves_relative_to_compose_root(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    compose_path = state_dir / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    env_file = state_dir / "profiles" / "custom-location.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("KEY=value\n")
    manager = ComposeManager(compose_path)
    manager.add_profile(Profile(name="test", env_file="profiles/custom-location.env"))

    monkeypatch.chdir(tmp_path)
    with _cli_ctx(compose_path) as ctx:
        profile_delete(ctx, "test", force=True)
    assert not env_file.exists()

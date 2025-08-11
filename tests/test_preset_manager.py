import pathlib
from contextlib import contextmanager

import typer

from proxy2vpn.preset_manager import apply_preset, list_available_presets
from proxy2vpn.compose_manager import ComposeManager
from proxy2vpn import cli


def _copy_compose(tmp_path: pathlib.Path) -> pathlib.Path:
    src = pathlib.Path(__file__).parent / "test_compose.yml"
    dest = tmp_path / "compose.yml"
    dest.write_text(src.read_text())
    return dest


@contextmanager
def _cli_ctx(compose_path: pathlib.Path):
    command = typer.main.get_command(cli.app)
    ctx = typer.Context(command, obj={"compose_file": compose_path})
    with ctx:
        yield


def test_preset_operations(tmp_path):
    compose_path = _copy_compose(tmp_path)
    with _cli_ctx(compose_path):
        presets = list_available_presets()
        assert "test" in presets
        apply_preset("test", "vpn3", 7777)
    manager = ComposeManager(compose_path)
    services = {s.name for s in manager.list_services()}
    assert "vpn3" in services

import pathlib

from proxy2vpn.preset_manager import apply_preset, list_available_presets
from proxy2vpn.compose_manager import ComposeManager


def _copy_compose(tmp_path: pathlib.Path) -> pathlib.Path:
    src = pathlib.Path(__file__).parent / "test_compose.yml"
    dest = tmp_path / "compose.yml"
    dest.write_text(src.read_text())
    return dest


def test_preset_operations(tmp_path):
    compose_path = _copy_compose(tmp_path)
    presets = list_available_presets(compose_path)
    assert "test" in presets
    apply_preset("test", "vpn3", 7777, compose_path)
    manager = ComposeManager(compose_path)
    services = {s.name for s in manager.list_services()}
    assert "vpn3" in services

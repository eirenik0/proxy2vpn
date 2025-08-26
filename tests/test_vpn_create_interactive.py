import pathlib

from typer.testing import CliRunner

from proxy2vpn.cli.main import app
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.core.models import Profile


def _create_compose(tmp_path: pathlib.Path) -> pathlib.Path:
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    return compose_path


def test_vpn_create_interactive(tmp_path):
    compose_path = _create_compose(tmp_path)
    env_file = tmp_path / "env.test"
    env_file.write_text("VPN_SERVICE_PROVIDER=prov\n")
    manager = ComposeManager(compose_path)
    manager.add_profile(Profile(name="test", env_file=str(env_file)))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--compose-file", str(compose_path), "vpn", "create"],
        input="svc\ntest\n0\n0\n\n",
    )
    assert result.exit_code == 0
    assert "Available profiles" in result.stdout
    manager = ComposeManager(compose_path)
    svc = manager.get_service("svc")
    assert svc.profile == "test"
    assert svc.port == 20000
    assert svc.control_port == 30000

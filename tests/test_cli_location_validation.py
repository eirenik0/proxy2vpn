import pathlib
from typer.testing import CliRunner

from proxy2vpn.cli.main import app
from proxy2vpn.adapters import server_manager
from proxy2vpn.adapters.compose_manager import ComposeManager


def _copy_compose(tmp_path: pathlib.Path) -> pathlib.Path:
    src = pathlib.Path(__file__).parent / "test_compose.yml"
    env_path = tmp_path / "env.test"
    env_path.write_text("KEY=value\n")
    dest = tmp_path / "compose.yml"
    text = src.read_text().replace("env.test", str(env_path))
    dest.write_text(text)
    return dest


def test_vpn_create_location_validation(tmp_path, monkeypatch):
    compose_path = _copy_compose(tmp_path)
    runner = CliRunner()

    class DummyServerManager:
        def validate_location(self, provider, location):
            return location in {"Toronto", "CA", "Toronto,CA"}

        def parse_location(self, provider, location):
            if "," in location:
                city, country = location.split(",", 1)
                return city, country
            if location == "Toronto":
                return location, None
            return None, location

    monkeypatch.setattr(server_manager, "ServerManager", lambda: DummyServerManager())

    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose_path),
            "vpn",
            "create",
            "vpn3",
            "test",
            "--port",
            "7777",
            "--provider",
            "prov",
            "--location",
            "Toronto,CA",
        ],
    )
    assert result.exit_code == 0

    manager = ComposeManager(compose_path)
    svc = manager.get_service("vpn3")
    assert svc.environment["SERVER_CITIES"] == "Toronto"
    assert svc.environment["SERVER_COUNTRIES"] == "CA"

    class FailingServerManager:
        def validate_location(self, provider, location):
            return False

        def parse_location(self, provider, location):
            return location, None

    monkeypatch.setattr(server_manager, "ServerManager", lambda: FailingServerManager())

    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose_path),
            "vpn",
            "create",
            "vpn4",
            "test",
            "--port",
            "7778",
            "--provider",
            "prov",
            "--location",
            "Atlantis",
        ],
    )
    assert result.exit_code != 0

    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose_path),
            "vpn",
            "create",
            "vpn4",
            "test",
            "--port",
            "7778",
            "--provider",
            "prov",
            "--location",
            "Atlantis",
            "--force",
        ],
    )
    assert result.exit_code == 0


def test_vpn_start_requires_valid_location(tmp_path, monkeypatch):
    compose_path = _copy_compose(tmp_path)
    runner = CliRunner()

    class DummyServerManager:
        def validate_location(self, provider, location):
            return False

        def parse_location(self, provider, location):
            return location, None

    monkeypatch.setattr(server_manager, "ServerManager", lambda: DummyServerManager())
    from proxy2vpn.adapters import docker_ops

    monkeypatch.setattr(docker_ops, "recreate_vpn_container", lambda *a, **k: None)
    monkeypatch.setattr(docker_ops, "start_container", lambda *a, **k: None)
    monkeypatch.setattr(docker_ops, "analyze_container_logs", lambda *a, **k: [])

    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose_path),
            "vpn",
            "start",
            "testvpn1",
        ],
    )
    assert result.exit_code != 0

    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose_path),
            "vpn",
            "start",
            "testvpn1",
            "--force",
        ],
    )
    assert result.exit_code == 0

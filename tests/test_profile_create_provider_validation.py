import pathlib

from typer.testing import CliRunner

from proxy2vpn.cli.main import app
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.cli.commands import profile


def _create_compose(tmp_path: pathlib.Path) -> pathlib.Path:
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    return compose_path


def test_profile_create_rejects_unknown_provider(tmp_path, monkeypatch):
    compose_path = _create_compose(tmp_path)
    runner = CliRunner()

    class DummyServerManager:
        def list_providers(self):
            return ["prov"]

    monkeypatch.setattr(
        profile.server_manager, "ServerManager", lambda: DummyServerManager()
    )

    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            ["--compose-file", str(compose_path), "profile", "create", "test"],
            input="bad\n",
        )
        assert result.exit_code != 0
        assert not pathlib.Path("profiles/test.env").exists()


def test_profile_create_accepts_supported_provider(tmp_path, monkeypatch):
    compose_path = _create_compose(tmp_path)
    runner = CliRunner()

    class DummyServerManager:
        def list_providers(self):
            return ["prov"]

    monkeypatch.setattr(
        profile.server_manager, "ServerManager", lambda: DummyServerManager()
    )

    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            ["--compose-file", str(compose_path), "profile", "create", "test"],
            input="prov\n\nuser\npass\nn\nn\n",
        )
        assert result.exit_code == 0
        env_file = pathlib.Path("profiles/test.env")
        assert env_file.exists()
        content = env_file.read_text()
        assert "VPN_TYPE=openvpn" in content
        assert "VPN_SERVICE_PROVIDER=prov" in content
        assert "OPENVPN_USER=user" in content
        assert "OPENVPN_PASSWORD=pass" in content

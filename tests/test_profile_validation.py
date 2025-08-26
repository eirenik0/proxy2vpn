"""Tests for profile validation during CLI profile creation."""

import pathlib
from contextlib import contextmanager
import pytest
import typer
from click.exceptions import Exit

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.cli.main import app
from proxy2vpn.cli.commands.profile import add as profile_add
from proxy2vpn.adapters import server_manager


def _create_test_compose(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a test compose file."""
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    return compose_path


@contextmanager
def _cli_ctx(compose_path: pathlib.Path):
    """Create a CLI context for testing."""
    command = typer.main.get_command(app)
    ctx = typer.Context(command, obj={"compose_file": compose_path})
    with ctx:
        yield ctx


class DummyServerManager:
    def list_providers(self):
        return [
            "expressvpn",
            "nordvpn",
            "protonvpn",
            "surfshark",
            "mullvad",
        ]


@pytest.fixture(autouse=True)
def _patch_server_manager(monkeypatch):
    monkeypatch.setattr(server_manager, "ServerManager", lambda: DummyServerManager())


def test_profile_create_with_valid_env_file(tmp_path):
    """Test profile creation succeeds with valid env file."""
    compose_path = _create_test_compose(tmp_path)

    # Create valid profile env file
    env_file = tmp_path / "valid_profile.env"
    env_file.write_text(
        "VPN_SERVICE_PROVIDER=expressvpn\n"
        "OPENVPN_USER=test_user\n"
        "OPENVPN_PASSWORD=test_pass\n"
        "HTTPPROXY=on\n"
        "HTTPPROXY_USER=proxy_user\n"
        "HTTPPROXY_PASSWORD=proxy_pass\n"
    )

    # Test profile creation should succeed
    with _cli_ctx(compose_path) as ctx:
        try:
            profile_add(ctx, "test-profile", env_file)
            # Should not raise any exception
        except Exit:
            pytest.fail("Profile creation should not fail with valid env file")


def test_profile_create_fails_with_missing_vpn_service_provider(tmp_path):
    """Test profile creation fails with missing VPN_SERVICE_PROVIDER."""
    compose_path = _create_test_compose(tmp_path)

    # Create invalid profile env file (missing VPN_SERVICE_PROVIDER)
    env_file = tmp_path / "invalid_profile.env"
    env_file.write_text(
        "OPENVPN_USER=test_user\nOPENVPN_PASSWORD=test_pass\n"
        # Missing VPN_SERVICE_PROVIDER
    )

    # Test profile creation should fail
    with _cli_ctx(compose_path) as ctx:
        with pytest.raises(Exit):
            profile_add(ctx, "test-profile", env_file)


def test_profile_create_fails_with_missing_openvpn_credentials(tmp_path):
    """Test profile creation fails with missing OPENVPN credentials."""
    compose_path = _create_test_compose(tmp_path)

    # Create invalid profile env file (missing OPENVPN_USER and OPENVPN_PASSWORD)
    env_file = tmp_path / "invalid_profile.env"
    env_file.write_text("VPN_SERVICE_PROVIDER=nordvpn\n")

    # Test profile creation should fail
    with _cli_ctx(compose_path) as ctx:
        with pytest.raises(Exit):
            profile_add(ctx, "test-profile", env_file)


def test_profile_create_fails_with_httpproxy_enabled_missing_credentials(tmp_path):
    """Test profile creation fails when HTTPPROXY=on but credentials missing."""
    compose_path = _create_test_compose(tmp_path)

    # Create invalid profile env file (HTTPPROXY enabled but missing credentials)
    env_file = tmp_path / "invalid_profile.env"
    env_file.write_text(
        "VPN_SERVICE_PROVIDER=protonvpn\n"
        "OPENVPN_USER=test_user\n"
        "OPENVPN_PASSWORD=test_pass\n"
        "HTTPPROXY=on\n"
        # Missing HTTPPROXY_USER and HTTPPROXY_PASSWORD
    )

    # Test profile creation should fail
    with _cli_ctx(compose_path) as ctx:
        with pytest.raises(Exit):
            profile_add(ctx, "test-profile", env_file)


def test_profile_create_succeeds_without_httpproxy(tmp_path):
    """Test profile creation succeeds with minimal required fields (no HTTP proxy)."""
    compose_path = _create_test_compose(tmp_path)

    # Create minimal valid profile env file
    env_file = tmp_path / "minimal_profile.env"
    env_file.write_text(
        "VPN_SERVICE_PROVIDER=surfshark\nOPENVPN_USER=test_user\nOPENVPN_PASSWORD=test_pass\n"
        # No HTTPPROXY configuration - should be valid
    )

    # Test profile creation should succeed
    with _cli_ctx(compose_path) as ctx:
        try:
            profile_add(ctx, "minimal-profile", env_file)
            # Should not raise any exception
        except Exit:
            pytest.fail("Profile creation should not fail with minimal valid env file")


def test_profile_create_with_httpproxy_disabled_succeeds(tmp_path):
    """Test profile creation succeeds when HTTPPROXY=off (no credentials needed)."""
    compose_path = _create_test_compose(tmp_path)

    # Create profile with HTTP proxy explicitly disabled
    env_file = tmp_path / "no_proxy_profile.env"
    env_file.write_text(
        "VPN_SERVICE_PROVIDER=mullvad\n"
        "OPENVPN_USER=test_user\n"
        "OPENVPN_PASSWORD=test_pass\n"
        "HTTPPROXY=off\n"
        # No HTTPPROXY_USER/PASSWORD needed when disabled
    )

    # Test profile creation should succeed
    with _cli_ctx(compose_path) as ctx:
        try:
            profile_add(ctx, "no-proxy-profile", env_file)
            # Should not raise any exception
        except Exit:
            pytest.fail("Profile creation should not fail when HTTPPROXY=off")


def test_profile_create_fails_with_nonexistent_env_file(tmp_path):
    """Test profile creation fails when env file doesn't exist."""
    compose_path = _create_test_compose(tmp_path)

    # Reference non-existent env file
    env_file = tmp_path / "nonexistent.env"

    # Test profile creation should fail
    with _cli_ctx(compose_path) as ctx:
        with pytest.raises(Exit):
            profile_add(ctx, "test-profile", env_file)


def test_profile_create_wireguard_without_openvpn_credentials(tmp_path):
    """Profile with VPN_TYPE=wireguard should not require OPENVPN credentials."""
    compose_path = _create_test_compose(tmp_path)

    env_file = tmp_path / "wireguard.env"
    env_file.write_text("VPN_SERVICE_PROVIDER=expressvpn\nVPN_TYPE=wireguard\n")

    with _cli_ctx(compose_path) as ctx:
        try:
            profile_add(ctx, "wireguard-profile", env_file)
        except Exit:
            pytest.fail("Wireguard profile should not require OPENVPN credentials")


def test_profile_create_fails_with_invalid_vpn_type(tmp_path):
    """Invalid VPN_TYPE values should fail validation."""
    compose_path = _create_test_compose(tmp_path)

    env_file = tmp_path / "badtype.env"
    env_file.write_text("VPN_SERVICE_PROVIDER=expressvpn\nVPN_TYPE=bad\n")

    with _cli_ctx(compose_path) as ctx:
        with pytest.raises(Exit):
            profile_add(ctx, "bad-profile", env_file)

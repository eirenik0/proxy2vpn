"""CLI entry points and rejection paths across isolated compose workspaces."""

from types import SimpleNamespace
import json

import pytest
import typer
from typer.testing import CliRunner

from proxy2vpn.adapters import docker_ops, server_manager
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.cli.main import app


def _command_paths(command, prefix=()):
    yield prefix
    for name, child in getattr(command, "commands", {}).items():
        yield from _command_paths(child, (*prefix, name))


# @lat: [[lat.md/cli-regression-tests#CLI Regression Matrix#Command Help]]
@pytest.mark.parametrize("path", list(_command_paths(typer.main.get_command(app))))
def test_every_command_help(path):
    result = CliRunner().invoke(app, [*path, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


# @lat: [[lat.md/cli-regression-tests#CLI Regression Matrix#Malformed Profiles]]
@pytest.mark.parametrize(
    "contents, expected",
    [
        ("", "VPN_SERVICE_PROVIDER is required"),
        ("# comment only\nNOT_AN_ASSIGNMENT\n", "VPN_SERVICE_PROVIDER is required"),
        ("VPN_SERVICE_PROVIDER=nordvpn\n", "OPENVPN_USER is required"),
        (
            "VPN_SERVICE_PROVIDER=nordvpn\nOPENVPN_USER=test\nOPENVPN_PASSWORD=\n",
            "OPENVPN_PASSWORD is required",
        ),
        ("VPN_SERVICE_PROVIDER=not-a-provider\n", "Unsupported VPN_SERVICE_PROVIDER"),
        ("VPN_SERVICE_PROVIDER=nordvpn\nVPN_TYPE=invalid\n", "VPN_TYPE must be"),
        (
            "VPN_SERVICE_PROVIDER=nordvpn\nOPENVPN_USER=test\n"
            "OPENVPN_PASSWORD=test\nHTTPPROXY=on\n",
            "HTTPPROXY_USER is required",
        ),
    ],
)
def test_malformed_profile_rejection_is_atomic(
    tmp_path, monkeypatch, contents, expected
):
    monkeypatch.setattr(
        server_manager,
        "ServerManager",
        lambda: SimpleNamespace(list_providers=lambda: ["nordvpn"]),
    )
    compose = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose)
    before = compose.read_bytes()
    profile = tmp_path / "malformed.env"
    profile.write_text(contents)
    result = CliRunner().invoke(
        app, ["-f", str(compose), "profile", "add", "malformed", str(profile)]
    )
    assert result.exit_code == 1, result.output
    assert expected in result.output
    assert isinstance(result.exception, SystemExit)
    assert compose.read_bytes() == before
    assert ComposeManager(compose).list_profiles() == []
    assert profile.read_text() == contents


# @lat: [[lat.md/cli-regression-tests#CLI Regression Matrix#Partial Restart Failure]]
def test_restart_all_reports_failure_and_continues(monkeypatch):
    manager = SimpleNamespace(
        list_services=lambda: [
            SimpleNamespace(name="failed"),
            SimpleNamespace(name="healthy"),
        ]
    )
    monkeypatch.setattr(ComposeManager, "from_ctx", lambda ctx: manager)
    attempts = []

    def restart(name):
        attempts.append(name)
        if name == "failed":
            raise RuntimeError("Container failed is not available")

    monkeypatch.setattr(docker_ops, "restart_container", restart)
    result = CliRunner().invoke(app, ["vpn", "restart", "--all"])
    assert result.exit_code == 1
    assert attempts == ["failed", "healthy"]
    assert "Container failed is not available" in result.output
    assert "Restarted healthy" in result.output
    assert "Restarted failed" not in result.output


# @lat: [[lat.md/cli-regression-tests#CLI Regression Matrix#Failed Restoration]]
def test_restore_returns_failure_when_recreation_fails(monkeypatch):
    service = SimpleNamespace(name="failed", profile="test")
    manager = SimpleNamespace(
        get_service=lambda name: service,
        get_profile=lambda name: SimpleNamespace(name="test"),
    )
    monkeypatch.setattr(ComposeManager, "from_ctx", lambda ctx: manager)
    monkeypatch.setattr(docker_ops, "get_vpn_containers", lambda **kwargs: [])
    attempts = []

    def restart(name):
        attempts.append("restart")
        raise RuntimeError("container missing")

    def recreate(*args, **kwargs):
        attempts.append("recreate")
        raise RuntimeError("invalid profile")

    monkeypatch.setattr(docker_ops, "restart_container", restart)
    monkeypatch.setattr(docker_ops, "start_vpn_service", recreate)
    result = CliRunner().invoke(app, ["vpn", "restore", "failed"])
    assert attempts == ["restart", "recreate"]
    assert result.exit_code == 1
    assert "Restore failed: failed" in result.output


# @lat: [[lat.md/cli-regression-tests#CLI Regression Matrix#Structured Fleet Health]]
@pytest.mark.parametrize("format", ["json", "yaml"])
@pytest.mark.parametrize("show_health", [False, True])
def test_managed_fleet_status_is_machine_readable(
    tmp_path, monkeypatch, format, show_health
):
    from proxy2vpn.adapters import fleet_commands
    from ruamel.yaml import YAML

    manager = SimpleNamespace(
        get_fleet_status=lambda: {
            "total_services": 1,
            "services_by_provider": {},
            "profile_allocation": {},
            "country_counts": {},
            "profile_counts": {},
        }
    )
    monkeypatch.setattr(fleet_commands, "FleetManager", lambda **kwargs: manager)
    monkeypatch.setattr(fleet_commands, "load_external_endpoints", lambda path: [])
    health = {"health_score": 0, "health_class": "auth_failed"}

    class Monitor:
        last_assessments = {"vpn": SimpleNamespace(model_dump=lambda **kwargs: health)}

        async def check_fleet_health(self):
            fleet_commands.console.print("Human-readable probe progress")
            return {"vpn": False}

    monkeypatch.setattr(
        fleet_commands, "ServerMonitor", lambda *args, **kwargs: Monitor()
    )
    args = ["-f", str(tmp_path / "compose.yml"), "fleet", "status", "--format", format]
    if show_health:
        args.append("--show-health")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    payload = (
        json.loads(result.output)
        if format == "json"
        else YAML(typ="safe").load(result.output)
    )
    assert payload["total_services"] == 1
    assert "Human-readable" not in result.output
    if show_health:
        assert payload["health"]["vpn"] == health


# @lat: [[lat.md/cli-regression-tests#CLI Regression Matrix#Delete Missing Containers]]
@pytest.mark.parametrize("missing", [True, False])
def test_delete_handles_wrapped_docker_errors(monkeypatch, missing):
    from docker.errors import NotFound, APIError

    removed = []
    manager = SimpleNamespace(
        get_service=lambda name: SimpleNamespace(name=name),
        remove_service=lambda name: removed.append(name),
    )
    monkeypatch.setattr(ComposeManager, "from_ctx", lambda ctx: manager)

    def fail(name):
        cause = NotFound("missing") if missing else APIError("daemon unavailable")
        raise RuntimeError("Docker operation failed") from cause

    monkeypatch.setattr(docker_ops, "stop_container", fail)
    monkeypatch.setattr(docker_ops, "remove_container", fail)
    result = CliRunner().invoke(app, ["vpn", "delete", "vpn", "--force"])
    assert result.exit_code == (0 if missing else 1)
    assert removed == (["vpn"] if missing else [])


# @lat: [[lat.md/cli-regression-tests#CLI Regression Matrix#Referenced Profile Removal]]
def test_profile_remove_rejects_referenced_profile(tmp_path):
    from pathlib import Path

    compose = tmp_path / "compose.yml"
    compose.write_bytes((Path(__file__).parent / "test_compose.yml").read_bytes())
    before = compose.read_bytes()
    result = CliRunner().invoke(
        app, ["-f", str(compose), "profile", "remove", "test", "--force"]
    )
    assert result.exit_code == 1, result.output
    assert "is used by services" in result.output
    assert compose.read_bytes() == before

import pathlib
import sys
from types import SimpleNamespace

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn.cli.main import app
from proxy2vpn.cli.commands import vpn as vpn_commands
from proxy2vpn.core.services import diagnostics
from proxy2vpn.core.services.diagnostics import DiagnosticResult
from proxy2vpn.adapters import docker_ops


def test_system_diagnose_specific_container(monkeypatch):
    runner = CliRunner()

    container = SimpleNamespace(
        name="vpn1",
        attrs={"NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": "30000"}]}}},
    )
    monkeypatch.setattr(docker_ops, "get_vpn_containers", lambda all=True: [container])
    monkeypatch.setattr(
        docker_ops, "get_problematic_containers", lambda all=True: [container]
    )
    monkeypatch.setattr(
        docker_ops, "get_container_diagnostics", lambda c: {"status": "running"}
    )
    monkeypatch.setattr(
        docker_ops, "analyze_container_logs", lambda name, lines, analyzer: []
    )
    monkeypatch.setattr(
        diagnostics.DiagnosticAnalyzer, "health_score", lambda self, results: 100
    )
    monkeypatch.setattr(
        diagnostics.DiagnosticAnalyzer, "control_api_checks", lambda self, base_url: []
    )
    monkeypatch.setattr(
        docker_ops,
        "get_network_interconnection_diagnostics",
        lambda expected_containers=None, network_name="proxy2vpn_network": {
            "kind": "network",
            "network": network_name,
            "status": "healthy",
            "health": 100,
            "issues": [],
            "recommendations": [],
            "connected": ["vpn1"],
            "expected": expected_containers or ["vpn1"],
            "missing": [],
        },
    )

    result = runner.invoke(app, ["system", "diagnose", "vpn1"])
    assert result.exit_code == 0
    assert "proxy2vpn_network: status=healthy health=100 attached=1/1" in result.stdout
    assert "vpn1: status=running health=100" in result.stdout


def test_vpn_restore_force_recreates_and_rechecks(monkeypatch):
    runner = CliRunner()
    service = SimpleNamespace(name="vpn1", profile="p1")
    manager = SimpleNamespace(
        list_services=lambda: [service],
        get_profile=lambda name: object(),
    )
    scores = iter([0, 0, 100])
    force_flags = []

    monkeypatch.setattr(vpn_commands.ComposeManager, "from_ctx", lambda ctx: manager)
    monkeypatch.setattr(
        vpn_commands, "validate_service_exists", lambda manager, name: service
    )
    monkeypatch.setattr(
        docker_ops,
        "get_vpn_containers",
        lambda all=True: [SimpleNamespace(name="vpn1")],
    )
    monkeypatch.setattr(docker_ops, "restart_container", lambda name: None)
    monkeypatch.setattr(
        docker_ops,
        "start_vpn_service",
        lambda service, profile, force: force_flags.append(force),
    )
    monkeypatch.setattr(
        docker_ops,
        "analyze_container_logs",
        lambda name, analyzer=None: [
            DiagnosticResult(
                check="connectivity",
                passed=False,
                message="failed",
                recommendation="",
            )
        ],
    )
    monkeypatch.setattr(
        diagnostics.DiagnosticAnalyzer,
        "health_score",
        lambda self, results: next(scores),
    )

    result = runner.invoke(app, ["vpn", "restore", "vpn1"])

    assert result.exit_code == 0
    assert "Restored" in result.stdout
    assert force_flags == [True]


def test_vpn_restore_reports_failed_recreate(monkeypatch):
    runner = CliRunner()
    service = SimpleNamespace(name="vpn1", profile="p1")
    manager = SimpleNamespace(
        list_services=lambda: [service],
        get_profile=lambda name: object(),
    )
    scores = iter([0, 0, 0])

    monkeypatch.setattr(vpn_commands.ComposeManager, "from_ctx", lambda ctx: manager)
    monkeypatch.setattr(
        vpn_commands, "validate_service_exists", lambda manager, name: service
    )
    monkeypatch.setattr(
        docker_ops,
        "get_vpn_containers",
        lambda all=True: [SimpleNamespace(name="vpn1")],
    )
    monkeypatch.setattr(docker_ops, "restart_container", lambda name: None)
    monkeypatch.setattr(docker_ops, "start_vpn_service", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        docker_ops,
        "analyze_container_logs",
        lambda name, analyzer=None: [
            DiagnosticResult(
                check="connectivity",
                passed=False,
                message="failed",
                recommendation="",
            )
        ],
    )
    monkeypatch.setattr(
        diagnostics.DiagnosticAnalyzer,
        "health_score",
        lambda self, results: next(scores),
    )

    result = runner.invoke(app, ["vpn", "restore", "vpn1"])

    assert result.exit_code == 0
    assert "Restore failed" in result.stdout
    assert "Restored" not in result.stdout

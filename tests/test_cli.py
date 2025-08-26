import pathlib
import sys
from types import SimpleNamespace

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn.cli.main import app
from proxy2vpn.core.services import diagnostics
from proxy2vpn.adapters import docker_ops


def test_system_diagnose_specific_container(monkeypatch):
    runner = CliRunner()

    container = SimpleNamespace(name="vpn1")
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

    result = runner.invoke(app, ["system", "diagnose", "vpn1"])
    assert result.exit_code == 0
    assert "vpn1: status=running health=100" in result.stdout

import pathlib
import sys
from types import SimpleNamespace

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from proxy2vpn import cli, diagnostics, docker_ops


def test_system_diagnose_specific_container(monkeypatch):
    runner = CliRunner()

    container = SimpleNamespace(
        name="vpn1", status="running", labels={"vpn.port": "8080"}
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
    monkeypatch.setattr(docker_ops, "container_logs", lambda name, lines, follow: [])

    async def mock_analyze_full_async(
        self, logs, service, port=None, include_control_server=True
    ):
        return []

    monkeypatch.setattr(
        diagnostics.DiagnosticAnalyzer, "analyze_full_async", mock_analyze_full_async
    )

    result = runner.invoke(cli.app, ["system", "diagnose", "vpn1"])
    assert result.exit_code == 0
    assert "vpn1: status=running health=100" in result.stdout

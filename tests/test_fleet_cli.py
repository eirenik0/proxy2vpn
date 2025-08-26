from typer.testing import CliRunner

from proxy2vpn.adapters import fleet_commands
from proxy2vpn.cli.main import app


def test_fleet_deploy_cli_passes_context(monkeypatch):
    called = {}

    def fake_fleet_deploy(
        ctx, plan_file, parallel, validate_first, dry_run, force
    ) -> None:
        called["ctx"] = ctx
        called["plan_file"] = plan_file
        called["parallel"] = parallel
        called["validate_first"] = validate_first
        called["dry_run"] = dry_run
        called["force"] = force

    monkeypatch.setattr(fleet_commands, "fleet_deploy", fake_fleet_deploy)

    runner = CliRunner()
    result = runner.invoke(app, ["fleet", "deploy", "--dry-run"])

    assert result.exit_code == 0
    assert called["ctx"] is not None
    assert called["plan_file"] == "deployment-plan.yaml"
    assert called["parallel"] is True
    assert called["validate_first"] is True
    assert called["dry_run"] is True
    assert called["force"] is False

import pathlib
from contextlib import contextmanager
from types import SimpleNamespace

import typer
from typer.testing import CliRunner

from proxy2vpn.adapters import fleet_commands
from proxy2vpn.adapters.fleet_manager import DeploymentPlan
from proxy2vpn.cli.main import app


@contextmanager
def _cli_ctx(compose_path: pathlib.Path):
    command = typer.main.get_command(app)
    ctx = typer.Context(command, obj={"compose_file": compose_path})
    with ctx:
        yield ctx


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


def test_fleet_plan_uses_context_compose_file(monkeypatch, tmp_path):
    compose_path = tmp_path / "alt.yml"
    output = tmp_path / "plan.yml"
    captured = {}

    class FakeFleetManager:
        def __init__(self, compose_file_path=None):
            captured["compose_file_path"] = compose_file_path

        def plan_deployment(self, config_obj):
            return DeploymentPlan()

    monkeypatch.setattr(fleet_commands, "FleetManager", FakeFleetManager)

    with _cli_ctx(compose_path) as ctx:
        fleet_commands.fleet_plan(
            ctx,
            countries="Germany",
            profiles="acc1:1",
            port_start=20000,
            naming_template="{provider}-{country}-{city}",
            output=str(output),
            validate_servers=False,
            unique_ips=False,
        )

    assert captured["compose_file_path"] == compose_path
    assert output.exists()


def test_fleet_deploy_uses_context_compose_file(monkeypatch, tmp_path):
    compose_path = tmp_path / "alt.yml"
    plan_file = tmp_path / "plan.yml"
    plan_file.write_text("services: []\n")
    captured = {}

    class FakeFleetManager:
        def __init__(self, compose_file_path=None):
            captured["compose_file_path"] = compose_file_path

        async def deploy_fleet(self, plan, validate_servers, parallel, force):
            return SimpleNamespace(
                deployed=0,
                failed=0,
                errors=[],
                services=[],
            )

    monkeypatch.setattr(fleet_commands, "FleetManager", FakeFleetManager)
    monkeypatch.setattr(
        fleet_commands,
        "_show_fleet_status_sync",
        lambda services: None,
    )

    with _cli_ctx(compose_path) as ctx:
        fleet_commands.fleet_deploy(
            ctx,
            plan_file=str(plan_file),
            parallel=True,
            validate_first=True,
            dry_run=False,
            force=False,
        )

    assert captured["compose_file_path"] == compose_path


def test_fleet_status_uses_context_compose_file(monkeypatch, tmp_path):
    compose_path = tmp_path / "alt.yml"
    captured = {}

    class FakeFleetManager:
        def __init__(self, compose_file_path=None):
            captured["compose_file_path"] = compose_file_path

        def get_fleet_status(self):
            return {
                "total_services": 0,
                "services_by_provider": {},
                "profile_allocation": {},
                "country_counts": {},
                "profile_counts": {},
            }

    monkeypatch.setattr(fleet_commands, "FleetManager", FakeFleetManager)

    with _cli_ctx(compose_path) as ctx:
        fleet_commands.fleet_status(
            ctx,
            format="json",
            show_allocation=False,
            show_health=False,
        )

    assert captured["compose_file_path"] == compose_path

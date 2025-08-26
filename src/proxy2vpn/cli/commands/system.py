"""System-level CLI commands."""

import json
import logging
from pathlib import Path

import typer

from proxy2vpn.core import config
from proxy2vpn.cli.typer_ext import HelpfulTyper, run_async
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.compose_validator import validate_compose
from proxy2vpn.adapters.server_manager import ServerManager
from proxy2vpn.adapters.display_utils import console
from proxy2vpn.common import abort
from proxy2vpn.adapters.validators import sanitize_name
from proxy2vpn.adapters.logging_utils import get_logger, set_log_level

app = HelpfulTyper(help="System level operations")
logger = get_logger(__name__)


@app.command("init")
@run_async
async def init(
    ctx: typer.Context,
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing compose file if it exists"
    ),
):
    """Generate an initial compose.yml file."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    overwrite = force
    if compose_file.exists() and not force:
        typer.confirm(f"Overwrite existing '{compose_file}'?", abort=True)
        overwrite = True
    try:
        ComposeManager.create_initial_compose(compose_file, force=overwrite)
        logger.info("compose_initialized", extra={"file": str(compose_file)})
    except FileExistsError:
        abort(
            f"Compose file '{compose_file}' already exists",
            "Use --force to overwrite",
        )
    mgr = ServerManager()
    await mgr.fetch_server_list_async()
    console.print(f"[green]✓[/green] Created '{compose_file}' and updated server list.")


@app.command("validate")
def validate(compose_file: Path = typer.Option(config.COMPOSE_FILE)):
    """Validate that the compose file is well formed."""

    errors = validate_compose(compose_file)
    if errors:
        for err in errors:
            typer.echo(f"- {err}", err=True)
        raise typer.Exit(1)
    console.print("[green]✓[/green] compose file is valid.")


@app.command("diagnose")
def diagnose(
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    lines: int = typer.Option(
        100, "--lines", "-n", help="Number of log lines to analyze"
    ),
    all_containers: bool = typer.Option(
        False, "--all", help="Check all containers, not only problematic ones"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Diagnose VPN containers and report health."""

    # Configure verbose logging if requested
    if verbose:
        set_log_level(logging.DEBUG)
        logger.debug("diagnostic_started", extra={"verbose": True, "lines": lines})

    from proxy2vpn.adapters.docker_ops import (
        get_problematic_containers,
        get_vpn_containers,
        get_container_diagnostics,
        analyze_container_logs,
    )
    from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer

    analyzer = DiagnosticAnalyzer()
    if name and all_containers:
        abort("Cannot specify NAME when using --all")
    if name:
        logger.debug("analyzing_single_container", extra={"container_name": name})
        vpn_containers = {c.name: c for c in get_vpn_containers(all=True)}
        container = vpn_containers.get(name)
        if not container:
            abort(f"Container '{name}' not found")
        containers = [container]
    else:
        containers = (
            get_vpn_containers(all=True)
            if all_containers
            else get_problematic_containers(all=True)
        )
        logger.debug(
            "found_containers",
            extra={
                "count": len(containers),
                "all_containers": all_containers,
                "container_names": [c.name for c in containers],
            },
        )

    summary: list[dict[str, object]] = []
    for container in containers:
        if container is None or container.name is None:
            continue
        logger.debug("analyzing_container", extra={"container_name": container.name})
        diag = get_container_diagnostics(container)
        logger.debug(
            "container_diagnostics",
            extra={"container_name": container.name, "status": diag["status"]},
        )

        assert container.name is not None  # Type narrowing after null check
        results = analyze_container_logs(container.name, lines=lines, analyzer=analyzer)
        logger.debug(
            "log_analysis_complete",
            extra={"container_name": container.name, "issues_found": len(results)},
        )

        score = analyzer.health_score(results)
        logger.debug(
            "health_score_calculated",
            extra={"container_name": container.name, "health_score": score},
        )

        entry = {
            "container": container.name,
            "status": diag["status"],
            "health": score,
            "issues": [r.message for r in results],
            "recommendations": [r.recommendation for r in results],
        }
        summary.append(entry)

    logger.debug("diagnosis_complete", extra={"containers_analyzed": len(summary)})

    if json_output:
        typer.echo(json.dumps(summary, indent=2))
    else:
        if not summary:
            console.print("[yellow]⚠[/yellow] No containers to diagnose.")
        for entry in summary:
            typer.echo(
                f"{entry['container']}: status={entry['status']} health={entry['health']}"
            )
            if verbose or entry["issues"]:
                issues = entry["issues"] if isinstance(entry["issues"], list) else []
                recommendations = (
                    entry["recommendations"]
                    if isinstance(entry["recommendations"], list)
                    else []
                )
                for issue, rec in zip(issues, recommendations):
                    suffix = f": {rec}" if rec else ""
                    typer.echo(f"  - {issue}{suffix}")

    # Reset log level to avoid affecting other commands
    if verbose:
        set_log_level(logging.INFO)

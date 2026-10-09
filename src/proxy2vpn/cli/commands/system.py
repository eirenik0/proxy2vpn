"""System-level CLI commands."""

import json
import logging
from pathlib import Path

import typer

from proxy2vpn.core import config
from proxy2vpn.core.security import ensure_control_auth, validate_bind_address
from proxy2vpn.adapters.compose_utils import iter_port_mappings
from proxy2vpn.cli.typer_ext import HelpfulTyper, run_async
from proxy2vpn.adapters.compose_manager import ComposeManager
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
    refresh_servers: bool = typer.Option(
        True,
        "--refresh-servers/--skip-server-refresh",
        help="Refresh server list from control API during init.",
    ),
):
    """Generate an initial compose.yml file and bootstrap required config files."""

    compose_file = (ctx.obj or {}).get("compose_file", config.COMPOSE_FILE)
    overwrite = force
    # Track compose status and whether we should exit with error after doing other work
    compose_created = False
    need_exit_error = False

    if compose_file.exists() and not force:
        # Ask for confirmation but do not abort – we want to continue with other steps
        confirmed = typer.confirm(f"Overwrite existing '{compose_file}'?", abort=False)
        overwrite = bool(confirmed)

    try:
        ComposeManager.create_initial_compose(compose_file, force=overwrite)
        compose_created = True
        logger.info("compose_initialized", extra={"file": str(compose_file)})
    except FileExistsError:
        need_exit_error = True
        logger.info("compose_kept_existing", extra={"file": str(compose_file)})

    # Initialization never rotates credentials or overwrites custom auth files.
    auth_config = config.resolve_control_auth_config(compose_file)
    try:
        created = ensure_control_auth(compose_file)
    except (OSError, ValueError):
        abort(
            "Failed to initialize control authentication.",
            "Check file permissions and use 'system secure' for explicit migration.",
        )

    server_list_updated = False
    mgr = ServerManager()
    has_cache = mgr.cache_file.exists()

    if refresh_servers:
        try:
            await mgr.fetch_server_list_async()
            server_list_updated = True
        except Exception as exc:
            if has_cache:
                stale = not mgr.is_cache_fresh()
                cache_state = "stale" if stale else "cached"
                logger.warning(
                    "server_list_refresh_failed",
                    extra={"error": str(exc), "cache_state": cache_state},
                )
                console.print(
                    f"[yellow]⚠[/yellow] Could not refresh server list ({exc}); "
                    f"continuing with {cache_state} cache."
                )
            else:
                raise
    else:
        if has_cache:
            cache_state = "fresh" if mgr.is_cache_fresh() else "stale"
            if cache_state == "stale":
                console.print(
                    "[yellow]⚠[/yellow] Server list refresh disabled; using stale cache."
                )
            else:
                logger.debug(
                    "server_list_refresh_skipped", extra={"cache_state": cache_state}
                )
        else:
            console.print(
                "[yellow]⚠[/yellow] Server list refresh disabled and no cache is available."
            )
    # Build a user-friendly status for the auth file
    if created:
        auth_msg = f"generated '{auth_config}'"
    else:
        auth_msg = f"kept existing '{auth_config}'"

    # Print final message and exit appropriately
    if compose_created:
        console.print(
            f"[green]✓[/green] Created '{compose_file}', {auth_msg}, and "
            f"{'updated' if server_list_updated else 'kept existing'} server list."
        )
    else:
        # Compose not created (kept existing). Still inform the user about other actions.
        console.print(
            f"[yellow]⚠[/yellow] Kept existing '{compose_file}', {auth_msg}, and "
            f"{'updated' if server_list_updated else 'kept'} server list."
        )
        if need_exit_error:
            abort(
                f"Compose file '{compose_file}' already exists",
                "Use --force to overwrite",
            )


@app.command("secure")
def secure(
    ctx: typer.Context,
    proxy_bind_address: str = typer.Option(
        "127.0.0.1",
        "--proxy-bind-address",
        help="Bind existing proxies to this IP address.",
    ),
    replace_control_auth: bool = typer.Option(
        False,
        "--replace-control-auth",
        help="Back up and replace existing custom/legacy control authentication.",
    ),
):
    """Prepare secure deployment files; recreate VPN services to apply them."""
    compose_file = Path((ctx.obj or {}).get("compose_file", config.COMPOSE_FILE))
    try:
        bind = validate_bind_address(proxy_bind_address)
        manager = ComposeManager(compose_file)
        updates = []
        for service in manager.list_services():
            definition = manager.data["services"][service.name]
            ports = []
            for item in definition.get("ports", []) or []:
                if any(target == 8888 for _, target in iter_port_mappings([item])):
                    if isinstance(item, dict):
                        item = dict(item, host_ip=bind)
                    else:
                        parts = str(item).rsplit(":", 2)
                        rendered_bind = f"[{bind}]" if ":" in bind else bind
                        item = f"{rendered_bind}:{parts[-2]}:{parts[-1]}"
                ports.append(item)
            updates.append((definition, ports))
        changed_auth = ensure_control_auth(compose_file, replace=replace_control_auth)
        changed_bindings = any(
            definition.get("ports", []) != ports for definition, ports in updates
        )
        for definition, ports in updates:
            definition["ports"] = ports
        if changed_bindings:
            manager.save()
    except (OSError, ValueError):
        abort(
            "Could not prepare secure deployment files.",
            "Check the bind address, configuration, and file permissions.",
        )
    auth_state = "generated" if changed_auth else "preserved existing"
    console.print(f"Control authentication: {auth_state}; proxy bind: {bind}.")
    if not changed_auth:
        console.print(
            "Existing authentication was preserved. To migrate custom or legacy auth, "
            "use --replace-control-auth."
        )
    console.print(
        "Recreate VPN services with 'proxy2vpn vpn update --all' to apply these files. "
        "Backups are kept beside the originals; live containers were not changed."
    )


@app.command("validate")
def validate(
    ctx: typer.Context,
    validate_locations: bool = typer.Option(
        False,
        "--validate-locations",
        help="Enable server-location checks using available server list cache.",
    ),
):
    """Validate that the compose file is well formed."""
    compose_manager = ComposeManager.from_ctx(ctx)
    effective_validate_locations = (
        validate_locations if isinstance(validate_locations, bool) else False
    )
    errors = compose_manager.validate_compose_file(
        validate_locations=effective_validate_locations
    )
    if errors:
        for err in errors:
            typer.echo(f"- {err}", err=True)
        raise typer.Exit(1)
    console.print("[green]✓[/green] compose file is valid.")


@app.command("diagnose")
def diagnose(
    ctx: typer.Context,
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
    """Diagnose VPN containers and the Docker interconnection network."""

    # Configure verbose logging if requested
    if verbose:
        set_log_level(logging.DEBUG)
        logger.debug("diagnostic_started", extra={"verbose": True, "lines": lines})

    from proxy2vpn.adapters.docker_ops import (
        get_problematic_containers,
        get_vpn_containers,
        get_container_diagnostics,
        analyze_container_logs,
        get_network_interconnection_diagnostics,
    )
    from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer

    analyzer = DiagnosticAnalyzer()
    if name and all_containers:
        abort("Cannot specify NAME when using --all")
    all_vpn_containers: list = []
    if name or all_containers:
        all_vpn_containers = get_vpn_containers(all=True)
    else:
        try:
            all_vpn_containers = get_vpn_containers(all=True)
        except RuntimeError:
            all_vpn_containers = []
    if name:
        logger.debug("analyzing_single_container", extra={"container_name": name})
        vpn_containers = {c.name: c for c in all_vpn_containers}
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

    network_entry = get_network_interconnection_diagnostics(
        expected_containers=[c.name for c in all_vpn_containers if c.name]
    )
    summary: list[dict[str, object]] = []
    summary.append(network_entry)
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
        attrs = getattr(container, "attrs", None)
        network_settings = (
            attrs.get("NetworkSettings", {}) if isinstance(attrs, dict) else {}
        )
        ports = (
            network_settings.get("Ports", {})
            if isinstance(network_settings, dict)
            else {}
        )
        port_info = ports.get("8000/tcp") if isinstance(ports, dict) else None
        if (
            isinstance(port_info, list)
            and port_info
            and isinstance(port_info[0], dict)
            and port_info[0].get("HostPort")
        ):
            control_port = port_info[0].get("HostPort")
            base_url = f"http://localhost:{control_port}/v1"
            results.extend(
                analyzer.control_api_checks(
                    base_url,
                    compose_file=Path(
                        (getattr(container, "labels", {}) or {}).get(
                            config.COMPOSE_FILE_LABEL,
                            str(
                                (ctx.obj or {}).get("compose_file", config.COMPOSE_FILE)
                            ),
                        )
                    ),
                )
            )
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
            if entry.get("kind") == "network":
                network_name = entry.get("network", "proxy2vpn_network")
                connected = entry.get("connected", [])
                missing = entry.get("missing", [])
                connected_count = len(connected) if isinstance(connected, list) else 0
                expected = entry.get("expected", [])
                expected_count = len(expected) if isinstance(expected, list) else 0
                status_line = (
                    f"{network_name}: status={entry['status']} "
                    f"health={entry['health']} "
                    f"attached={connected_count}"
                )
                if expected_count:
                    status_line += f"/{expected_count}"
                if isinstance(missing, list) and missing:
                    status_line += (
                        f" missing={', '.join(str(item) for item in missing)}"
                    )
                typer.echo(status_line)
            else:
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

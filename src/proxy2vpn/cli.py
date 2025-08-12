"""Command line interface for proxy2vpn."""

from __future__ import annotations

from pathlib import Path
import json
import asyncio
import logging

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress
from .typer_ext import HelpfulTyper, run_async
from docker.errors import APIError, NotFound

from . import config
from .compose_manager import ComposeManager
from .models import Profile, VPNService
from .server_manager import ServerManager
from .compose_validator import validate_compose
from .utils import abort
from .validators import sanitize_name, sanitize_path, validate_port
from .logging_utils import configure_logging, get_logger, set_log_level

app = HelpfulTyper(help="proxy2vpn command line interface")

profile_app = HelpfulTyper(help="Manage VPN profiles and apply them to services")
vpn_app = HelpfulTyper(help="Manage VPN services")
server_app = HelpfulTyper(help="Manage cached server lists")
system_app = HelpfulTyper(help="System level operations")
fleet_app = HelpfulTyper(help="Manage VPN fleets across multiple cities")

app.add_typer(profile_app, name="profile")
app.add_typer(vpn_app, name="vpn")
app.add_typer(server_app, name="servers")
app.add_typer(system_app, name="system")
app.add_typer(fleet_app, name="fleet")

logger = get_logger(__name__)

console = Console()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    compose_file: Path = typer.Option(
        config.COMPOSE_FILE,
        "--compose-file",
        "-f",
        help="Path to compose file",
        callback=sanitize_path,
    ),
    log_file: Path | None = typer.Option(
        None, "--log-file", help="Write JSON logs to file"
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit",
        is_eager=True,
    ),
):
    """Store global options in context."""
    if log_file:
        log_file = log_file.expanduser().resolve()
    configure_logging(log_file=log_file)
    if version:
        from . import __version__

        typer.echo(__version__)
        raise typer.Exit()

    ctx.obj = ctx.obj or {}
    ctx.obj["compose_file"] = compose_file

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


# ---------------------------------------------------------------------------
# System commands
# ---------------------------------------------------------------------------


@system_app.command("init")
@run_async
async def system_init(
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


# ---------------------------------------------------------------------------
# Profile commands
# ---------------------------------------------------------------------------


@profile_app.command("create")
def profile_create(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name),
    env_file: Path = typer.Argument(..., callback=sanitize_path),
):
    """Create a new VPN profile."""

    if not env_file.exists():
        abort(
            f"Environment file '{env_file}' not found",
            "Create the file before creating the profile",
        )
    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    profile = Profile(name=name, env_file=str(env_file))
    manager.add_profile(profile)
    logger.info("profile_created", extra={"profile_name": name})
    console.print(f"[green]✓[/green] Profile '{name}' created.")


@profile_app.command("list")
def profile_list(ctx: typer.Context):
    """List available profiles."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    profiles = manager.list_profiles()
    if not profiles:
        console.print("[yellow]⚠[/yellow] No profiles found.")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("N", style="dim blue")
    table.add_column("Name", style="green")
    table.add_column("Env File", overflow="fold")

    for i, profile in enumerate(profiles, 1):
        table.add_row(str(i), profile.name, profile.env_file)

    console.print(table)


@profile_app.command("delete")
def profile_delete(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name),
    force: bool = typer.Option(False, "--force", "-f", help="Do not prompt"),
):
    """Delete a profile by NAME."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_profile(name)
    except KeyError:
        abort(f"Profile '{name}' not found")
    if not force:
        typer.confirm(f"Delete profile '{name}'?", abort=True)
    manager.remove_profile(name)
    console.print(f"[green]✓[/green] Profile '{name}' deleted.")


@profile_app.command("apply")
def profile_apply(
    ctx: typer.Context,
    profile: str,
    service: str,
    port: int = typer.Option(0, help="Host port to expose; 0 for auto"),
):
    """Create a VPN service from a profile."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_profile(profile)
    except KeyError:
        abort(
            f"Profile '{profile}' not found",
            "Create it with 'proxy2vpn profile create'",
        )
    if port == 0:
        port = manager.next_available_port(config.DEFAULT_PORT_START)
    env = {"VPN_SERVICE_PROVIDER": config.DEFAULT_PROVIDER}
    labels = {
        "vpn.type": "vpn",
        "vpn.port": str(port),
        "vpn.provider": config.DEFAULT_PROVIDER,
        "vpn.profile": profile,
        "vpn.location": "",
    }
    svc = VPNService(
        name=service,
        port=port,
        provider=config.DEFAULT_PROVIDER,
        profile=profile,
        location="",
        environment=env,
        labels=labels,
    )
    manager.add_service(svc)
    console.print(
        f"[green]✓[/green] Service '{service}' created from profile '{profile}' on port {port}."
    )


# ---------------------------------------------------------------------------
# VPN container commands
# ---------------------------------------------------------------------------


@vpn_app.command("create")
def vpn_create(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name),
    profile: str = typer.Argument(..., callback=sanitize_name),
    port: int = typer.Option(
        0,
        callback=validate_port,
        help="Host port to expose; 0 for auto",
    ),
    provider: str = typer.Option(config.DEFAULT_PROVIDER),
    location: str = typer.Option("", help="Optional location, e.g. city"),
):
    """Create a VPN service entry in the compose file."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_profile(profile)
    except KeyError:
        abort(
            f"Profile '{profile}' not found",
            "Create it with 'proxy2vpn profile create'",
        )
    if port == 0:
        port = manager.next_available_port(config.DEFAULT_PORT_START)
    env = {"VPN_SERVICE_PROVIDER": provider}
    location = location.strip()
    if location:
        env["SERVER_CITIES"] = location
    labels = {
        "vpn.type": "vpn",
        "vpn.port": str(port),
        "vpn.provider": provider,
        "vpn.profile": profile,
        "vpn.location": location,
    }
    svc = VPNService(
        name=name,
        port=port,
        provider=provider,
        profile=profile,
        location=location,
        environment=env,
        labels=labels,
    )
    manager.add_service(svc)
    console.print(f"[green]✓[/green] Service '{name}' created on port {port}.")


@vpn_app.command("list")
@run_async
async def vpn_list(
    ctx: typer.Context,
    diagnose: bool = typer.Option(
        False, "--diagnose", help="Include diagnostic health scores"
    ),
    ips_only: bool = typer.Option(
        False, "--ips-only", help="Show only container IP addresses"
    ),
):
    """List VPN services with their status and IP addresses."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    from .docker_ops import (
        get_vpn_containers,
        get_container_ip_async,
        analyze_container_logs,
    )
    from .diagnostics import DiagnosticAnalyzer

    if ips_only:
        containers = get_vpn_containers(all=False)
        ips = await asyncio.gather(
            *(get_container_ip_async(container) for container in containers)
        )
        for container, ip in zip(containers, ips):
            console.print(f"{container.name}: {ip}")
        return

    services = manager.list_services()
    containers = {c.name: c for c in get_vpn_containers(all=True)}
    analyzer = DiagnosticAnalyzer() if diagnose else None

    running = {name: c for name, c in containers.items() if c.status == "running"}
    ips = await asyncio.gather(
        *(get_container_ip_async(container) for container in running.values())
    )
    ip_map = dict(zip(running.keys(), ips))

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("N", style="dim blue")
    table.add_column("Name", style="green")
    table.add_column("Port")
    table.add_column("Profile")
    table.add_column("Location")
    table.add_column("Status")
    table.add_column("IP")
    if diagnose:
        table.add_column("Health")

    async def add_row(i: int, svc: VPNService):
        container = containers.get(svc.name)
        if container:
            status = container.status
            ip = ip_map.get(svc.name, "N/A")
            health = "N/A"
            if diagnose:
                results = analyze_container_logs(container.name, analyzer=analyzer)
                health = str(analyzer.health_score(results))
        else:
            status = "not created"
            ip = "N/A"
            health = "N/A"
        status_style = "green" if status == "running" else "red"
        row = [
            str(i),
            svc.name,
            str(svc.port),
            svc.profile,
            svc.location,
            f"[{status_style}]{status}[/{status_style}]",
            ip,
        ]
        if diagnose:
            row.append(health)
        table.add_row(*row)

    if diagnose:
        with Progress() as progress:
            task = progress.add_task("[cyan]Checking", total=len(services))
            for i, svc in enumerate(services, 1):
                await add_row(i, svc)
                progress.advance(task)
    else:
        for i, svc in enumerate(services, 1):
            await add_row(i, svc)

    console.print(table)


@vpn_app.command("start")
def vpn_start(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Start all VPN services"),
):
    """Start one or all VPN containers."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    if all and name is not None:
        abort("Cannot specify NAME when using --all")
    if all:
        from .docker_ops import start_all_vpn_containers

        results = start_all_vpn_containers(manager)
        for svc_name in results:
            console.print(f"[green]✓[/green] Recreated and started {svc_name}")
        return

    if name is None:
        abort("Specify a service NAME or use --all")
    try:
        svc = manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import (
        start_container,
        analyze_container_logs,
        recreate_vpn_container,
    )
    from .diagnostics import DiagnosticAnalyzer

    profile = manager.get_profile(svc.profile)
    try:
        recreate_vpn_container(svc, profile)
        start_container(name)
        console.print(f"[green]✓[/green] Recreated and started '{name}'.")
    except APIError as exc:
        analyzer = DiagnosticAnalyzer()
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        abort(f"Failed to start '{name}': {exc.explanation}")


@vpn_app.command("stop")
def vpn_stop(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Stop all VPN services"),
):
    """Stop one or all VPN containers."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    if all and name is not None:
        abort("Cannot specify NAME when using --all")
    if all:
        from .docker_ops import stop_all_vpn_containers

        results = stop_all_vpn_containers()
        for svc_name in results:
            console.print(f"[green]✓[/green] Stopped and removed {svc_name}")
        return

    if name is None:
        abort("Specify a service NAME or use --all")
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import stop_container, remove_container, analyze_container_logs
    from .diagnostics import DiagnosticAnalyzer

    try:
        stop_container(name)
        remove_container(name)
        console.print(f"[green]✓[/green] Stopped and removed '{name}'.")
    except NotFound:
        abort(f"Container '{name}' does not exist")
    except APIError as exc:
        analyzer = DiagnosticAnalyzer()
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        abort(f"Failed to stop '{name}': {exc.explanation}")


@vpn_app.command("restart")
def vpn_restart(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Restart all VPN services"),
):
    """Restart one or all VPN containers."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    if all and name is not None:
        abort("Cannot specify NAME when using --all")
    if all:
        from .docker_ops import recreate_vpn_container, start_container

        services = manager.list_services()
        for svc in services:
            profile = manager.get_profile(svc.profile)
            try:
                recreate_vpn_container(svc, profile)
                start_container(svc.name)
                console.print(f"[green]✓[/green] Recreated and restarted {svc.name}")
            except APIError as exc:
                typer.echo(
                    f"Failed to restart '{svc.name}': {exc.explanation}", err=True
                )
        return

    if name is None:
        abort("Specify a service NAME or use --all")
    try:
        svc = manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import (
        recreate_vpn_container,
        start_container,
        analyze_container_logs,
    )
    from .diagnostics import DiagnosticAnalyzer

    profile = manager.get_profile(svc.profile)
    try:
        recreate_vpn_container(svc, profile)
        start_container(name)
        console.print(f"[green]✓[/green] Recreated and restarted '{name}'.")
    except APIError as exc:
        analyzer = DiagnosticAnalyzer()
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        abort(f"Failed to restart '{name}': {exc.explanation}")


@vpn_app.command("logs")
def vpn_logs(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name),
    lines: int = typer.Option(100, "--lines", help="Number of lines to show"),
    follow: bool = typer.Option(False, "--follow", help="Follow log output"),
):
    """Show logs for a VPN container."""
    if lines <= 0:
        abort("LINES must be positive")
    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import container_logs

    try:
        for line in container_logs(name, lines=lines, follow=follow):
            typer.echo(line)
    except NotFound:
        abort(f"Container '{name}' does not exist")


@vpn_app.command("delete")
def vpn_delete(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Delete all VPN services"),
    force: bool = typer.Option(False, "--force", "-f", help="Do not prompt"),
):
    """Delete one or all VPN services and remove their containers."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    from .docker_ops import remove_container, stop_container

    if all and name is not None:
        abort("Cannot specify NAME when using --all")
    if all:
        services = manager.list_services()
        if not force and not typer.confirm("Delete all services?"):
            raise typer.Exit()
        for svc in services:
            try:
                stop_container(svc.name)
            except NotFound:
                pass
            try:
                remove_container(svc.name)
            except NotFound:
                pass
            manager.remove_service(svc.name)
            console.print(f"[green]✓[/green] Service '{svc.name}' deleted.")
        return

    if name is None:
        abort("Specify a service NAME or use --all")
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    if not force and not typer.confirm(f"Delete service '{name}'?"):
        raise typer.Exit()

    try:
        stop_container(name)
    except NotFound:
        pass
    try:
        remove_container(name)
    except NotFound:
        pass

    manager.remove_service(name)
    console.print(f"[green]✓[/green] Service '{name}' deleted.")


@vpn_app.command("test")
@run_async
async def vpn_test(
    ctx: typer.Context, name: str = typer.Argument(..., callback=sanitize_name)
):
    """Test that a VPN service proxy is working."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import test_vpn_connection_async

    if await test_vpn_connection_async(name):
        console.print("[green]✓[/green] VPN connection is active.")
    else:
        abort("VPN connection failed", "Check container logs")


@vpn_app.command("export-proxies")
@run_async
async def vpn_export_proxies(
    ctx: typer.Context,
    output: Path = typer.Option(
        ..., "--output", "-o", callback=sanitize_path, help="Path to CSV output"
    ),
    no_auth: bool = typer.Option(
        False, "--no-auth", help="Exclude proxy authentication credentials"
    ),
):
    """Export running VPN proxies to a CSV file."""

    from .docker_ops import collect_proxy_info

    proxies = await collect_proxy_info(include_credentials=not no_auth)
    import csv

    with output.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "host",
                "port",
                "username",
                "password",
                "location",
                "status",
            ],
        )
        writer.writeheader()
        for row in proxies:
            writer.writerow(row)
    console.print(
        f"[green]\u2713[/green] Exported {len(proxies)} proxies to '{output}'."
    )


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------


@server_app.command("update")
@run_async
async def servers_update(
    insecure: bool = typer.Option(
        False,
        "--insecure",
        help="Disable SSL certificate verification (for troubleshooting)",
    ),
):
    """Download and cache the latest server list."""

    mgr = ServerManager()
    verify = not insecure
    await mgr.fetch_server_list_async(verify=verify)
    console.print("[green]✓[/green] Server list updated.")


@server_app.command("list-providers")
def servers_list_providers():
    """List VPN providers from the cached server list."""

    mgr = ServerManager()
    for provider in mgr.list_providers():
        typer.echo(provider)


@server_app.command("list-countries")
def servers_list_countries(provider: str):
    """List countries for a VPN provider."""

    mgr = ServerManager()
    for country in mgr.list_countries(provider):
        typer.echo(country)


@server_app.command("list-cities")
def servers_list_cities(provider: str, country: str):
    """List cities for a VPN provider in a country."""

    mgr = ServerManager()
    for city in mgr.list_cities(provider, country):
        typer.echo(city)


@server_app.command("validate-location")
def servers_validate_location(provider: str, location: str):
    """Validate that a location exists for a provider."""

    mgr = ServerManager()
    if mgr.validate_location(provider, location):
        console.print("[green]✓[/green] valid")
    else:
        console.print("[red]❌[/red] invalid")
        raise typer.Exit(1)


@system_app.command("validate")
def system_validate(compose_file: Path = typer.Option(config.COMPOSE_FILE)):
    """Validate that the compose file is well formed."""

    errors = validate_compose(compose_file)
    if errors:
        for err in errors:
            typer.echo(f"- {err}", err=True)
        raise typer.Exit(1)
    console.print("[green]✓[/green] compose file is valid.")


@system_app.command("diagnose")
def system_diagnose(
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

    from .docker_ops import (
        get_problematic_containers,
        get_vpn_containers,
        get_container_diagnostics,
        analyze_container_logs,
    )
    from .diagnostics import DiagnosticAnalyzer

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
        logger.debug("analyzing_container", extra={"container_name": container.name})
        diag = get_container_diagnostics(container)
        logger.debug(
            "container_diagnostics",
            extra={"container_name": container.name, "status": diag["status"]},
        )

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
                for issue, rec in zip(entry["issues"], entry["recommendations"]):
                    typer.echo(f"  - {issue}: {rec}")

    # Reset log level to avoid affecting other commands
    if verbose:
        set_log_level(logging.INFO)


# ---------------------------------------------------------------------------
# Fleet management commands
# ---------------------------------------------------------------------------


@fleet_app.command("plan")
def fleet_plan_cmd(
    ctx: typer.Context,
    provider: str = typer.Option("protonvpn", help="VPN provider"),
    countries: str = typer.Option(..., help="Comma-separated country list"),
    profiles: str = typer.Option(..., help="Profile slots: acc1:2,acc2:8"),
    port_start: int = typer.Option(20000, help="Starting port number"),
    naming_template: str = typer.Option(
        "{provider}-{country}-{city}", help="Service naming template"
    ),
    output: str = typer.Option("deployment-plan.yaml", help="Save plan to file"),
    validate_servers: bool = typer.Option(True, help="Validate server availability"),
    unique_ips: bool = typer.Option(
        False, help="Ensure each service uses a unique city and server IP"
    ),
):
    """Plan bulk VPN deployment across cities"""
    from .fleet_commands import fleet_plan

    fleet_plan(
        ctx,
        provider,
        countries,
        profiles,
        port_start,
        naming_template,
        output,
        validate_servers,
        unique_ips,
    )


@fleet_app.command("deploy")
def fleet_deploy_cmd(
    ctx: typer.Context,
    plan_file: str = typer.Option("deployment-plan.yaml", help="Deployment plan file"),
    parallel: bool = typer.Option(True, help="Start containers in parallel"),
    validate_first: bool = typer.Option(
        True, help="Validate servers before deployment"
    ),
    dry_run: bool = typer.Option(False, help="Show what would be deployed"),
):
    """Deploy VPN fleet from plan file"""
    from .fleet_commands import fleet_deploy

    fleet_deploy(ctx, plan_file, parallel, validate_first, dry_run)


@fleet_app.command("status")
def fleet_status_cmd(
    ctx: typer.Context,
    format: str = typer.Option("table", help="table|json|yaml"),
    show_allocation: bool = typer.Option(True, help="Show profile allocation"),
    show_health: bool = typer.Option(False, help="Include health checks"),
):
    """Show current fleet status and profile allocation"""
    from .fleet_commands import fleet_status

    fleet_status(ctx, format, show_allocation, show_health)


@fleet_app.command("rotate")
def fleet_rotate_cmd(
    ctx: typer.Context,
    country: str = typer.Option(None, help="Rotate servers in specific country"),
    provider: str = typer.Option("protonvpn", help="VPN provider"),
    criteria: str = typer.Option("random", help="random|performance|load"),
    dry_run: bool = typer.Option(False, help="Show rotation plan only"),
):
    """Rotate VPN servers for better availability"""
    from .fleet_commands import fleet_rotate

    fleet_rotate(ctx, country, provider, criteria, dry_run)


@fleet_app.command("scale")
def fleet_scale_cmd(
    ctx: typer.Context,
    action: str = typer.Argument(..., help="up|down"),
    countries: str = typer.Option(None, help="Comma-separated countries to scale"),
    factor: int = typer.Option(1, help="Scale factor"),
    profile: str = typer.Option(None, help="Add services to specific profile"),
):
    """Scale VPN fleet up or down"""
    from .fleet_commands import fleet_scale

    fleet_scale(ctx, action, countries, factor, profile)


if __name__ == "__main__":
    app()

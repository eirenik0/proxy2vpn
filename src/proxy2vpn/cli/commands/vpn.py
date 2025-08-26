"""VPN service management CLI commands."""

import asyncio
import dataclasses
from pathlib import Path
import csv

import typer
from rich.table import Table
from rich.progress import Progress
from docker.errors import APIError, NotFound

from proxy2vpn.adapters.server_manager import ServerManager
from proxy2vpn.common import abort
from proxy2vpn.core import config
from proxy2vpn.cli.typer_ext import HelpfulTyper, run_async
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.display_utils import (
    console,
    format_success_message,
    format_bulk_success_message,
)
from proxy2vpn.core.models import VPNService
from proxy2vpn.adapters.validators import (
    validate_all_name_args,
    validate_service_exists,
    sanitize_name,
    sanitize_path,
    validate_port,
)
from proxy2vpn.adapters.logging_utils import get_logger

app = HelpfulTyper(help="Manage VPN services")
logger = get_logger(__name__)


def _service_control_base_url(ctx: typer.Context, name: str) -> str:
    manager = ComposeManager.from_ctx(ctx)
    svc = validate_service_exists(manager, name)
    return f"http://localhost:{svc.control_port}/v1"


def _resolve_service_name(ctx: typer.Context, service: str | None) -> str:
    """Resolve optional service name.

    - If provided, return it.
    - If not provided, auto-select when there is exactly one service.
    - If none or multiple exist, abort with a helpful message.
    """
    if service:
        return service
    manager = ComposeManager.from_ctx(ctx)
    services = manager.list_services()
    if not services:
        abort(
            "No VPN services found.",
            "Create one with 'proxy2vpn vpn create <name> <profile>'.",
        )
    if len(services) == 1:
        return services[0].name
    names = ", ".join(sorted(s.name for s in services))
    abort(
        "Multiple VPN services found; please specify SERVICE.",
        f"Available: {names}",
    )
    return ""  # unreachable, for typing


def _validate_service_locations(services: list[VPNService], force: bool) -> None:
    if force:
        return
    mgr = ServerManager()
    for svc in services:
        loc = getattr(svc, "location", "")
        provider = getattr(svc, "provider", "")
        if loc and not mgr.validate_location(provider, loc):
            abort(
                f"Invalid location '{loc}' for {provider}",
                "Use --force to override",
            )


@app.command("create")
def create(ctx: typer.Context) -> None:
    """Interactively create a VPN service entry in the compose file."""

    manager = ComposeManager.from_ctx(ctx)

    try:
        name = sanitize_name(typer.prompt("Service name"))
    except typer.BadParameter as exc:
        abort(str(exc))

    profiles = manager.list_profiles()
    if not profiles:
        abort(
            "No profiles found.",
            "Create one with 'proxy2vpn profile create'",
        )

    console.print("Available profiles:")
    for idx, p in enumerate(profiles, start=1):
        console.print(f"{idx}. {p.name}")

    selected = typer.prompt("Select profile", default="1").strip()

    if selected.isdigit():
        idx = int(selected)
        if not 1 <= idx <= len(profiles):
            abort(
                f"Invalid selection {selected}",
                f"Select a number between 1 and {len(profiles)}",
            )
        prof = profiles[idx - 1]
    else:
        try:
            selected = sanitize_name(selected)
        except typer.BadParameter as exc:
            abort(str(exc))
        prof_map = {p.name: p for p in profiles}
        if selected not in prof_map:
            abort(
                f"Profile '{selected}' not found",
                "Create one with 'proxy2vpn profile create'",
            )
        prof = prof_map[selected]

    profile = prof.name
    provider = prof.provider

    port = typer.prompt("Host port to expose (0 for auto)", default=0, type=int)
    try:
        port = validate_port(port)
    except typer.BadParameter as exc:
        abort(str(exc))

    control_port = typer.prompt("Control port (0 for auto)", default=0, type=int)
    try:
        control_port = validate_port(control_port)
    except typer.BadParameter as exc:
        abort(str(exc))

    location = typer.prompt("Location (optional)", default="").strip()
    force = False
    if location:
        force = typer.confirm("Ignore location validation?", default=False)

    if port == 0:
        port = manager.next_available_port(config.DEFAULT_PORT_START)
    if control_port == 0:
        control_port = manager.next_available_control_port(
            config.DEFAULT_CONTROL_PORT_START
        )

    env = {"VPN_SERVICE_PROVIDER": provider}
    if location:
        # Import via adapters module so tests can monkeypatch ServerManager
        from proxy2vpn.adapters import server_manager

        mgr = server_manager.ServerManager()
        if not force and not mgr.validate_location(provider, location):
            abort(
                f"Invalid location '{location}' for {provider}",
                "Use --force to override",
            )
        city, country = mgr.parse_location(provider, location)
        if city:
            env["SERVER_CITIES"] = city
        if country:
            env["SERVER_COUNTRIES"] = country

    labels = {
        "vpn.type": "vpn",
        "vpn.port": str(port),
        "vpn.control_port": str(control_port),
        "vpn.provider": provider,
        "vpn.profile": profile,
        "vpn.location": location,
    }
    svc = VPNService.create(
        name=name,
        port=port,
        control_port=control_port,
        provider=provider,
        profile=profile,
        location=location,
        environment=env,
        labels=labels,
    )
    manager.add_service(svc)
    console.print(
        f"[green]✓[/green] Service '{name}' created on port {port} (control {control_port})."
    )


@app.command("list")
@run_async
async def list_services(
    ctx: typer.Context,
):
    """List VPN services with their status and IP addresses."""

    # Construct via adapters.compose_manager so tests can monkeypatch behavior
    from proxy2vpn.adapters import compose_manager

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = compose_manager.ComposeManager(compose_file)
    from proxy2vpn.adapters.docker_ops import (
        get_vpn_containers,
        get_container_ip_async,
        analyze_container_logs,
    )
    from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer

    services = manager.list_services()
    containers = {c.name: c for c in get_vpn_containers(all=True)}
    analyzer = DiagnosticAnalyzer()

    # Get all running containers for IP resolution
    running = {name: c for name, c in containers.items() if c.status == "running"}

    # Fetch direct IP once for all containers (optimization)
    from proxy2vpn.adapters import ip_utils

    direct_ip = None
    try:
        direct_ip = ip_utils.fetch_ip(timeout=5)
    except Exception:
        pass  # Will be handled per-container if needed

    # Create async functions for concurrent execution
    async def get_container_health(container_name: str) -> int:
        """Async wrapper to run diagnostic analysis in thread pool"""

        loop = asyncio.get_event_loop()
        # Run the sync function in a thread pool to avoid blocking
        results = await loop.run_in_executor(
            None, analyze_container_logs, container_name, 20, analyzer, 5, direct_ip
        )
        return analyzer.health_score(results)

    # Create IP and health tasks
    ip_tasks = []
    ip_containers = []
    for container in running.values():
        if container.name:
            ip_tasks.append(get_container_ip_async(container))
            ip_containers.append(container.name)

    health_tasks = []
    running_services = []
    for svc in services:
        container = containers.get(svc.name)
        if container and container.status == "running" and container.name:
            health_tasks.append(get_container_health(container.name))
            running_services.append(svc.name)

    # Execute all async operations with incremental progress tracking
    with Progress() as progress:
        total_tasks = len(ip_tasks) + len(health_tasks)
        task_progress = progress.add_task("[cyan]Analyzing health", total=total_tasks)

        # Create all tasks for concurrent execution with progress tracking
        all_tasks = []
        task_mapping = {}

        # Add IP tasks
        for i, coro in enumerate(ip_tasks):
            task = asyncio.create_task(coro)
            all_tasks.append(task)
            task_mapping[task] = ("ip", i)

        # Add health tasks
        for i, coro in enumerate(health_tasks):
            task = asyncio.create_task(coro)
            all_tasks.append(task)
            task_mapping[task] = ("health", i)

        # Initialize result arrays
        ip_results = [None] * len(ip_tasks)
        health_results = [None] * len(health_tasks)

        # Process tasks concurrently as they complete
        pending = set(all_tasks)
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )

            for completed_task in done:
                try:
                    result = completed_task.result()
                except Exception as exc:
                    result = exc
                task_type, index = task_mapping[completed_task]

                if task_type == "ip":
                    ip_results[index] = result
                else:
                    health_results[index] = result

                progress.advance(task_progress, 1)

        # Create result maps
        ip_map = {}
        for container_name, result in zip(ip_containers, ip_results):
            if result is None or isinstance(result, Exception):
                continue
            ip_map[container_name] = result

        health_map = {}
        for service_name, result in zip(running_services, health_results):
            if result is None or isinstance(result, Exception):
                continue
            health_map[service_name] = result

    # Result maps created above

    # Build table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("N", style="dim blue")
    table.add_column("Name", style="green")
    table.add_column("Port")
    table.add_column("Provider")
    table.add_column("Profile")
    table.add_column("Location")
    table.add_column("Status")
    table.add_column("IP")
    table.add_column("Health")

    from proxy2vpn.adapters.display_utils import format_health_score

    for i, svc in enumerate(services, 1):
        container = containers.get(svc.name)

        if container:
            status = container.status
            ip = ip_map.get(svc.name, "N/A")
            # Get health score from our concurrent analysis
            if status == "running":
                health_val = health_map.get(svc.name, 0)
            else:
                health_val = 0
        else:
            status = "not created"
            ip = "N/A"
            health_val = "N/A"

        status_style = "green" if status == "running" else "red"
        row = [
            str(i),
            svc.name,
            str(svc.port),
            svc.provider,
            svc.profile,
            svc.location,
            f"[{status_style}]{status}[/{status_style}]",
            ip,
            format_health_score(health_val),
        ]
        table.add_row(*row)

    console.print(table)


@app.command("start")
def start(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Start all VPN services"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Ignore location validation"
    ),
):
    """Start one or all VPN containers."""

    # Construct via adapters.compose_manager so tests can monkeypatch behavior
    from proxy2vpn.adapters import compose_manager

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = compose_manager.ComposeManager(compose_file)
    validate_all_name_args(all, name)

    if all:
        services = manager.list_services()
        _validate_service_locations(services, force)
        from proxy2vpn.adapters.docker_ops import start_all_vpn_containers

        results = start_all_vpn_containers(manager)
        for svc_name in results:
            console.print(
                format_bulk_success_message("Recreated and started", svc_name)
            )
        return

    svc = validate_service_exists(manager, name)
    _validate_service_locations([svc], force)

    from proxy2vpn.adapters.docker_ops import (
        start_container,
        analyze_container_logs,
        recreate_vpn_container,
    )
    from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer

    profile = manager.get_profile(svc.profile)
    try:
        recreate_vpn_container(svc, profile)
        start_container(name)
        console.print(format_success_message("Recreated and started", name))
    except APIError as exc:
        analyzer = DiagnosticAnalyzer()
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        abort(f"Failed to start '{name}': {exc.explanation}")


@app.command("stop")
def stop(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Stop all VPN services"),
):
    """Stop one or all VPN containers."""

    manager = ComposeManager.from_ctx(ctx)
    validate_all_name_args(all, name)

    if all:
        from proxy2vpn.adapters.docker_ops import stop_all_vpn_containers

        results = stop_all_vpn_containers()
        for svc_name in results:
            console.print(format_bulk_success_message("Stopped and removed", svc_name))
        return

    validate_service_exists(manager, name)

    from proxy2vpn.adapters.docker_ops import (
        stop_container,
        remove_container,
        analyze_container_logs,
    )
    from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer

    try:
        stop_container(name)
        remove_container(name)
        console.print(format_success_message("Stopped and removed", name))
    except APIError as exc:
        analyzer = DiagnosticAnalyzer()
        assert name is not None  # Type narrowing for error handling
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        abort(f"Failed to stop '{name}': {exc.explanation}")


@app.command("restart")
def restart(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Restart all VPN services"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Ignore location validation"
    ),
):
    """Restart one or all VPN containers."""

    manager = ComposeManager.from_ctx(ctx)
    validate_all_name_args(all, name)

    if all:
        services = manager.list_services()
        _validate_service_locations(services, force)
        from proxy2vpn.adapters.docker_ops import (
            recreate_vpn_container,
            start_container,
        )

        for svc in services:
            profile = manager.get_profile(svc.profile)
            try:
                recreate_vpn_container(svc, profile)
                start_container(svc.name)
                console.print(
                    format_bulk_success_message("Recreated and restarted", svc.name)
                )
            except APIError as exc:
                typer.echo(
                    f"Failed to restart '{svc.name}': {exc.explanation}", err=True
                )
        return

    svc = validate_service_exists(manager, name)
    _validate_service_locations([svc], force)

    from proxy2vpn.adapters.docker_ops import (
        recreate_vpn_container,
        start_container,
        analyze_container_logs,
    )
    from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer

    profile = manager.get_profile(svc.profile)
    try:
        recreate_vpn_container(svc, profile)
        start_container(name)
        console.print(format_success_message("Recreated and restarted", name))
    except APIError as exc:
        analyzer = DiagnosticAnalyzer()
        assert name is not None  # Type narrowing for error handling
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        abort(f"Failed to restart '{name}': {exc.explanation}")


@app.command("logs")
def logs(
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

    from proxy2vpn.adapters.docker_ops import container_logs

    try:
        for line in container_logs(name, lines=lines, follow=follow):
            typer.echo(line)
    except NotFound:
        abort(f"Container '{name}' does not exist")


def _delete_service_containers(service_name: str):
    """Helper function to delete containers for a service."""
    from proxy2vpn.adapters.docker_ops import remove_container, stop_container

    try:
        stop_container(service_name)
    except NotFound:
        pass
    try:
        remove_container(service_name)
    except NotFound:
        pass


def _delete_all_services(manager: ComposeManager, force: bool):
    """Helper function to delete all services."""
    services = manager.list_services()
    if not force and not typer.confirm("Delete all services?"):
        raise typer.Exit()

    for svc in services:
        _delete_service_containers(svc.name)
        manager.remove_service(svc.name)
        console.print(f"[green]✓[/green] Service '{svc.name}' deleted.")


def _delete_single_service(manager: ComposeManager, name: str, force: bool):
    """Helper function to delete a single service."""
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    if not force and not typer.confirm(f"Delete service '{name}'?"):
        raise typer.Exit()

    _delete_service_containers(name)
    manager.remove_service(name)
    console.print(f"[green]✓[/green] Service '{name}' deleted.")


@app.command("delete")
def delete(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Delete all VPN services"),
    force: bool = typer.Option(False, "--force", "-f", help="Do not prompt"),
):
    """Delete one or all VPN services and remove their containers."""

    manager = ComposeManager.from_ctx(ctx)
    validate_all_name_args(all, name)

    if all:
        _delete_all_services(manager, force)
        return

    assert name is not None  # Type narrowing
    _delete_single_service(manager, name, force)


@app.command("test")
@run_async
async def test(
    ctx: typer.Context, name: str = typer.Argument(..., callback=sanitize_name)
):
    """Test that a VPN service proxy is working."""

    manager = ComposeManager.from_ctx(ctx)
    validate_service_exists(manager, name)

    from proxy2vpn.adapters.docker_ops import test_vpn_connection_async

    if await test_vpn_connection_async(name):
        console.print("[green]✓[/green] VPN connection is active.")
    else:
        abort("VPN connection failed", "Check container logs")


@app.command("export-proxies")
@run_async
async def export_proxies(
    ctx: typer.Context,
    output: Path = typer.Option(
        ..., "--output", "-o", callback=sanitize_path, help="Path to CSV output"
    ),
    no_auth: bool = typer.Option(
        False, "--no-auth", help="Exclude proxy authentication credentials"
    ),
):
    """Export VPN proxies defined in the compose file to a CSV file.

    This relies on the compose structure and exports only services defined in
    the project's compose.yml, instead of scanning all Docker containers with
    a VPN label. Adds the `provider` column.
    """

    from proxy2vpn.adapters import ip_utils
    from proxy2vpn.adapters import docker_ops

    manager = ComposeManager.from_ctx(ctx)
    services = manager.list_services()

    # Fetch host public IP once (used for running services)
    try:
        host_ip = await ip_utils.fetch_ip_async()
    except Exception:
        host_ip = ""

    rows: list[dict[str, str]] = []
    for svc in services:
        # Resolve runtime container to determine status
        container = docker_ops.get_container_by_service_name(svc.name)
        status = (
            "running"
            if (container and getattr(container, "status", "") == "running")
            else "stopped"
        )
        host = host_ip if status == "running" else ""

        # Build effective environment: start from profile env file, overlay service env, then apply credential overrides
        effective_env: dict[str, str] = {}
        try:
            profile = manager.get_profile(svc.profile)
            # Use internal loader for env files (consistent with validators)
            from proxy2vpn.adapters.docker_ops import _load_env_file as _load_env

            profile_env = (
                _load_env(str(profile._resolve_env_path()))
                if hasattr(profile, "_resolve_env_path")
                else {}
            )
            if isinstance(profile_env, dict):
                effective_env.update({k: str(v) for k, v in profile_env.items()})
        except Exception:
            # If profile/env file not resolvable, continue with empty base
            pass
        # Overlay service-specific environment from compose
        effective_env.update({k: str(v) for k, v in (svc.environment or {}).items()})

        # Apply service credential overrides (labels) to effective env
        if svc.credentials:
            if svc.credentials.httpproxy_user:
                effective_env["HTTPPROXY_USER"] = svc.credentials.httpproxy_user
            if svc.credentials.httpproxy_password:
                effective_env["HTTPPROXY_PASSWORD"] = svc.credentials.httpproxy_password

        # Extract credentials (or blank when --no-auth)
        username = ""
        password = ""
        if not no_auth:
            username = effective_env.get("HTTPPROXY_USER", "") or ""
            password = effective_env.get("HTTPPROXY_PASSWORD", "") or ""

        rows.append(
            {
                "host": host,
                "port": str(svc.port),
                "username": username,
                "password": password,
                "location": svc.location or "",
                "provider": svc.provider or "",
                "status": "active" if status == "running" else "stopped",
            }
        )

    with output.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "host",
                "port",
                "username",
                "password",
                "location",
                "provider",
                "status",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    console.print(f"[green]\u2713[/green] Exported {len(rows)} proxies to '{output}'.")


@app.command("status")
@run_async
async def status(
    ctx: typer.Context,
    service: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
):
    """Show control server status for SERVICE."""

    resolved = _resolve_service_name(ctx, service)
    base_url = _service_control_base_url(ctx, resolved)
    # Import via adapters module so tests can monkeypatch the client
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        data = await client.status()

    def _to_dict(obj):
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # Pydantic v2 models
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        if isinstance(obj, dict):
            return obj
        return {"value": str(obj)}

    console.print_json(data=_to_dict(data))


@app.command("public-ip")
@run_async
async def public_ip(
    ctx: typer.Context,
    service: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
):
    """Show public IP reported by the control API for SERVICE."""

    resolved = _resolve_service_name(ctx, service)
    base_url = _service_control_base_url(ctx, resolved)
    # Import via adapters module so tests can monkeypatch the client
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        ip = await client.public_ip()
    console.print(ip.ip)


@app.command("dns-status")
@run_async
async def dns_status(
    ctx: typer.Context,
    service: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
):
    """Show DNS service status for SERVICE."""

    resolved = _resolve_service_name(ctx, service)
    base_url = _service_control_base_url(ctx, resolved)
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        status = await client.dns_status()
    console.print(status.status)


@app.command("updater-status")
@run_async
async def updater_status(
    ctx: typer.Context,
    service: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
):
    """Show updater job status for SERVICE."""

    resolved = _resolve_service_name(ctx, service)
    base_url = _service_control_base_url(ctx, resolved)
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        status = await client.updater_status()
    console.print(status.status)


@app.command("port-forwarded")
@run_async
async def port_forwarded(
    ctx: typer.Context,
    service: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
):
    """Show port forwarded for SERVICE."""

    resolved = _resolve_service_name(ctx, service)
    base_url = _service_control_base_url(ctx, resolved)
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        pf = await client.port_forwarded()
    console.print(str(pf.port))


@app.command("restore")
def restore(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(
        False, "--all", help="Restore all VPN services below threshold"
    ),
    threshold: int = typer.Option(
        60, "--threshold", help="Health threshold to trigger restore (0-100)"
    ),
):
    """Attempt to restore unhealthy VPN services.

    Strategy: restart container; if still unhealthy, recreate container. If recreation
    still results in poor health, suggest changing location (not automated yet).
    """
    from proxy2vpn.adapters import compose_manager
    from proxy2vpn.adapters.docker_ops import (
        analyze_container_logs,
        restart_container,
        recreate_vpn_container,
        get_vpn_containers,
    )
    from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = compose_manager.ComposeManager(compose_file)

    if not all and not name:
        abort("Specify a service NAME or use --all")

    targets: list[VPNService]
    if all:
        targets = manager.list_services()
    else:
        targets = [validate_service_exists(manager, name or "")]  # type: ignore[arg-type]

    analyzer = DiagnosticAnalyzer()
    restored: list[str] = []
    unchanged: list[str] = []

    for svc in targets:
        # Compute current health
        containers = {c.name: c for c in get_vpn_containers(all=True)}
        score = 0
        if (container := containers.get(svc.name)) is not None:
            try:
                results = analyze_container_logs(container.name, analyzer=analyzer)
                score = analyzer.health_score(results)
            except Exception:
                score = 0
        if score >= threshold:
            unchanged.append(svc.name)
            continue
        # Try restart
        try:
            restart_container(svc.name)
        except Exception:
            pass
        # Re-evaluate
        containers = {c.name: c for c in get_vpn_containers(all=True)}
        score2 = 0
        if (container := containers.get(svc.name)) is not None:
            try:
                results = analyze_container_logs(container.name, analyzer=analyzer)
                score2 = analyzer.health_score(results)
            except Exception:
                score2 = 0
        if score2 >= threshold:
            restored.append(svc.name)
            continue
        # Recreate
        profile = manager.get_profile(svc.profile)
        try:
            recreate_vpn_container(svc, profile)
        except Exception:
            pass
        restored.append(svc.name)

    for n in restored:
        console.print(format_success_message("Restored", n))
    if unchanged:
        console.print("[yellow]No action needed:[/yellow] " + ", ".join(unchanged))


@app.command("restart-tunnel")
@run_async
async def restart_tunnel(
    ctx: typer.Context,
    service: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
):
    """Restart the VPN tunnel for SERVICE via the control API."""

    resolved = _resolve_service_name(ctx, service)
    base_url = _service_control_base_url(ctx, resolved)
    # Import via adapters module so tests can monkeypatch the client
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        await client.restart_tunnel()
    console.print("[green]\u2713[/green] Tunnel restart requested.")

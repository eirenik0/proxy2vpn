"""VPN service management CLI commands."""

import asyncio
from dataclasses import asdict
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
def create(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name),
    profile: str = typer.Argument(..., callback=sanitize_name),
    port: int = typer.Option(
        0,
        callback=validate_port,
        help="Host port to expose; 0 for auto",
    ),
    control_port: int = typer.Option(
        0,
        callback=validate_port,
        help="Control port; 0 for auto",
    ),
    provider: str = typer.Option(config.DEFAULT_PROVIDER),
    location: str = typer.Option("", help="Optional location, e.g. city"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Ignore location validation"
    ),
):
    """Create a VPN service entry in the compose file."""

    manager = ComposeManager.from_ctx(ctx)
    try:
        manager.get_profile(profile)
    except KeyError:
        abort(
            f"Profile '{profile}' not found",
            "Create it with 'proxy2vpn profile create'",
        )
    if port == 0:
        port = manager.next_available_port(config.DEFAULT_PORT_START)
    if control_port == 0:
        control_port = manager.next_available_control_port(
            config.DEFAULT_CONTROL_PORT_START
        )

    env = {"VPN_SERVICE_PROVIDER": provider}
    location = location.strip()
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
    diagnose: bool = typer.Option(
        False, "--diagnose", help="Include diagnostic health scores"
    ),
    ips_only: bool = typer.Option(
        False, "--ips-only", help="Show only container IP addresses"
    ),
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
    table.add_column("Provider")
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
            if diagnose and container.name:
                results = analyze_container_logs(container.name, analyzer=analyzer)
                health = str(analyzer.health_score(results)) if analyzer else "N/A"
        else:
            status = "not created"
            ip = "N/A"
            health = "N/A"
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
    """Export running VPN proxies to a CSV file."""

    from proxy2vpn.adapters.docker_ops import collect_proxy_info

    proxies = await collect_proxy_info(include_credentials=not no_auth)

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


@app.command("status")
@run_async
async def status(
    ctx: typer.Context,
    service: str = typer.Argument(..., callback=sanitize_name),
):
    """Show control server status for SERVICE."""

    base_url = _service_control_base_url(ctx, service)
    # Import via adapters module so tests can monkeypatch the client
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        data = await client.status()
    console.print_json(data=asdict(data))


@app.command("public-ip")
@run_async
async def public_ip(
    ctx: typer.Context,
    service: str = typer.Argument(..., callback=sanitize_name),
):
    """Show public IP reported by the control API for SERVICE."""

    base_url = _service_control_base_url(ctx, service)
    # Import via adapters module so tests can monkeypatch the client
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        ip = await client.public_ip()
    console.print(ip.ip)


@app.command("restart-tunnel")
@run_async
async def restart_tunnel(
    ctx: typer.Context,
    service: str = typer.Argument(..., callback=sanitize_name),
):
    """Restart the VPN tunnel for SERVICE via the control API."""

    base_url = _service_control_base_url(ctx, service)
    # Import via adapters module so tests can monkeypatch the client
    from proxy2vpn.adapters import http_client

    async with http_client.GluetunControlClient(base_url) as client:
        await client.restart_tunnel()
    console.print("[green]\u2713[/green] Tunnel restart requested.")

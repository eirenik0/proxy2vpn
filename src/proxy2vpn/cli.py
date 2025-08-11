"""Command line interface for proxy2vpn."""

from __future__ import annotations

from pathlib import Path
import json

import typer
from .typer_ext import HelpfulTyper
from docker.errors import APIError, NotFound

from . import config
from .compose_manager import ComposeManager
from .models import Profile, VPNService
from .server_manager import ServerManager
from .compose_validator import validate_compose

app = HelpfulTyper(help="proxy2vpn command line interface")

profile_app = HelpfulTyper(help="Manage VPN profiles")
vpn_app = HelpfulTyper(help="Manage VPN services")
server_app = HelpfulTyper(help="Manage cached server lists")
bulk_app = HelpfulTyper(help="Bulk container operations")
preset_app = HelpfulTyper(help="Manage presets")

app.add_typer(profile_app, name="profile")
app.add_typer(vpn_app, name="vpn")
app.add_typer(server_app, name="servers")
app.add_typer(bulk_app, name="bulk")
app.add_typer(preset_app, name="preset")


@app.callback()
def main(
    ctx: typer.Context,
    compose_file: Path = typer.Option(
        config.COMPOSE_FILE, "--compose-file", "-f", help="Path to compose file"
    ),
):
    """Store global options in context."""
    ctx.obj = ctx.obj or {}
    ctx.obj["compose_file"] = compose_file


# ---------------------------------------------------------------------------
# Initialization command
# ---------------------------------------------------------------------------


@app.command("init")
def init_command(
    ctx: typer.Context,
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing compose file if it exists"
    ),
):
    """Generate an initial compose.yml file."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    try:
        ComposeManager.create_initial_compose(compose_file, force=force)
    except FileExistsError:
        typer.echo(
            f"Compose file '{compose_file}' already exists. Use --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(f"Created '{compose_file}'.")


# ---------------------------------------------------------------------------
# Profile commands
# ---------------------------------------------------------------------------


@profile_app.command("create")
def profile_create(ctx: typer.Context, name: str, env_file: Path):
    """Create a new VPN profile."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    profile = Profile(name=name, env_file=str(env_file))
    manager.add_profile(profile)
    typer.echo(f"Profile '{name}' created.")


@profile_app.command("list")
def profile_list(ctx: typer.Context):
    """List available profiles."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    for profile in manager.list_profiles():
        typer.echo(profile.name)


@profile_app.command("delete")
def profile_delete(ctx: typer.Context, name: str):
    """Delete a profile by NAME."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    manager.remove_profile(name)
    typer.echo(f"Profile '{name}' deleted.")


# ---------------------------------------------------------------------------
# VPN container commands
# ---------------------------------------------------------------------------


@vpn_app.command("create")
def vpn_create(
    ctx: typer.Context,
    name: str,
    profile: str,
    port: int = typer.Option(0, help="Host port to expose; 0 for auto"),
    provider: str = typer.Option(config.DEFAULT_PROVIDER),
    location: str = typer.Option("", help="Optional location, e.g. city"),
):
    """Create a VPN service entry in the compose file."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    if port == 0:
        port = manager.next_available_port(config.DEFAULT_PORT_START)
    env = {"VPN_SERVICE_PROVIDER": provider}
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
    typer.echo(f"Service '{name}' created on port {port}.")


@vpn_app.command("list")
def vpn_list(
    ctx: typer.Context,
    diagnose: bool = typer.Option(
        False, "--diagnose", help="Include diagnostic health scores"
    ),
):
    """List VPN services with their status and IP addresses."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    from .docker_ops import (
        get_vpn_containers,
        get_container_ip,
        analyze_container_logs,
    )
    from .diagnostics import DiagnosticAnalyzer

    services = manager.list_services()
    containers = {c.name: c for c in get_vpn_containers(all=True)}
    analyzer = DiagnosticAnalyzer() if diagnose else None

    header = f"{'NAME':<15} {'PORT':<8} {'PROFILE':<12} {'STATUS':<10} {'IP':<15}"
    if diagnose:
        header += f" {'HEALTH':<7}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for svc in services:
        container = containers.get(svc.name)
        if container:
            status = container.status
            ip = get_container_ip(container) if status == "running" else "N/A"
            health = "N/A"
            if diagnose:
                results = analyze_container_logs(container.name, analyzer=analyzer)
                health = str(analyzer.health_score(results))
        else:
            status = "not created"
            ip = "N/A"
            health = "N/A"
        line = f"{svc.name:<15} {svc.port:<8} {svc.profile:<12} {status:<10} {ip:<15}"
        if diagnose:
            line += f" {health:<7}"
        typer.echo(line)


@vpn_app.command("start")
def vpn_start(ctx: typer.Context, name: str):
    """Start the container for a VPN service."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_service(name)
    except KeyError:
        typer.echo(f"Service '{name}' not found.", err=True)
        raise typer.Exit(1)

    from .docker_ops import start_container

    try:
        start_container(name)
        typer.echo(f"Started '{name}'.")
    except NotFound:
        typer.echo(f"Container '{name}' does not exist.", err=True)
        raise typer.Exit(1)
    except APIError as exc:
        typer.echo(f"Failed to start '{name}': {exc.explanation}", err=True)
        from .docker_ops import analyze_container_logs
        from .diagnostics import DiagnosticAnalyzer

        analyzer = DiagnosticAnalyzer()
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        raise typer.Exit(1)


@vpn_app.command("stop")
def vpn_stop(ctx: typer.Context, name: str):
    """Stop the container for a VPN service."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_service(name)
    except KeyError:
        typer.echo(f"Service '{name}' not found.", err=True)
        raise typer.Exit(1)

    from .docker_ops import stop_container

    try:
        stop_container(name)
        typer.echo(f"Stopped '{name}'.")
    except NotFound:
        typer.echo(f"Container '{name}' does not exist.", err=True)
        raise typer.Exit(1)
    except APIError as exc:
        typer.echo(f"Failed to stop '{name}': {exc.explanation}", err=True)
        from .docker_ops import analyze_container_logs
        from .diagnostics import DiagnosticAnalyzer

        analyzer = DiagnosticAnalyzer()
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        raise typer.Exit(1)


@vpn_app.command("restart")
def vpn_restart(ctx: typer.Context, name: str):
    """Restart a VPN container."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_service(name)
    except KeyError:
        typer.echo(f"Service '{name}' not found.", err=True)
        raise typer.Exit(1)

    from .docker_ops import restart_container

    try:
        restart_container(name)
        typer.echo(f"Restarted '{name}'.")
    except NotFound:
        typer.echo(f"Container '{name}' does not exist.", err=True)
        raise typer.Exit(1)
    except APIError as exc:
        typer.echo(f"Failed to restart '{name}': {exc.explanation}", err=True)
        from .docker_ops import analyze_container_logs
        from .diagnostics import DiagnosticAnalyzer

        analyzer = DiagnosticAnalyzer()
        results = analyze_container_logs(name, analyzer=analyzer)
        if results:
            typer.echo("Diagnostic hints:", err=True)
            for res in results:
                typer.echo(f" - {res.message}: {res.recommendation}", err=True)
        raise typer.Exit(1)


@vpn_app.command("logs")
def vpn_logs(
    ctx: typer.Context,
    name: str,
    lines: int = typer.Option(100, "--lines", help="Number of lines to show"),
    follow: bool = typer.Option(False, "--follow", help="Follow log output"),
):
    """Show logs for a VPN container."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_service(name)
    except KeyError:
        typer.echo(f"Service '{name}' not found.", err=True)
        raise typer.Exit(1)

    from .docker_ops import container_logs

    try:
        for line in container_logs(name, lines=lines, follow=follow):
            typer.echo(line)
    except NotFound:
        typer.echo(f"Container '{name}' does not exist.", err=True)
        raise typer.Exit(1)


@vpn_app.command("delete")
def vpn_delete(
    ctx: typer.Context,
    name: str,
    force: bool = typer.Option(False, "--force", "-f", help="Do not prompt"),
):
    """Delete a VPN service and remove its container."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_service(name)
    except KeyError:
        typer.echo(f"Service '{name}' not found.", err=True)
        raise typer.Exit(1)

    if not force and not typer.confirm(f"Delete service '{name}'?"):
        raise typer.Exit()

    from .docker_ops import remove_container, stop_container

    try:
        stop_container(name)
    except NotFound:
        pass
    try:
        remove_container(name)
    except NotFound:
        pass

    manager.remove_service(name)
    typer.echo(f"Service '{name}' deleted.")


# ---------------------------------------------------------------------------
# Bulk container commands
# ---------------------------------------------------------------------------


@bulk_app.command("up")
def bulk_up():
    """Start all VPN containers."""

    from .docker_ops import start_all_vpn_containers

    results = start_all_vpn_containers()
    for name, started in results:
        if started:
            typer.echo(f"\u2713 Started {name}")
        else:
            typer.echo(f"\u2192 {name} already running")


@bulk_app.command("down")
def bulk_down():
    """Stop all running VPN containers."""

    from .docker_ops import stop_all_vpn_containers

    results = stop_all_vpn_containers()
    for name in results:
        typer.echo(f"\u2713 Stopped {name}")


@bulk_app.command("status")
def bulk_status():
    """Show status and IP address for VPN containers."""

    from .docker_ops import get_vpn_containers, get_container_ip

    containers = get_vpn_containers(all=True)
    typer.echo(f"{'NAME':<15} {'STATUS':<10} {'PORT':<8} {'IP':<15}")
    typer.echo("-" * 50)
    for container in containers:
        port = container.labels.get("vpn.port", "N/A")
        ip = get_container_ip(container) if container.status == "running" else "N/A"
        typer.echo(f"{container.name:<15} {container.status:<10} {port:<8} {ip:<15}")


@bulk_app.command("ips")
def bulk_ips():
    """Show IP addresses of running VPN containers."""

    from .docker_ops import get_vpn_containers, get_container_ip

    containers = get_vpn_containers(all=False)
    for container in containers:
        ip = get_container_ip(container)
        typer.echo(f"{container.name}: {ip}")


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------


@server_app.command("update")
def servers_update(
    insecure: bool = typer.Option(
        False,
        "--insecure",
        help="Disable SSL certificate verification (for troubleshooting)",
    ),
):
    """Download and cache the latest server list."""

    mgr = ServerManager()
    verify = not insecure
    mgr.update_servers(verify=verify)
    typer.echo("Server list updated.")


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
        typer.echo("valid")
    else:
        typer.echo("invalid", err=True)
        raise typer.Exit(1)


@preset_app.command("list")
def preset_list():
    """List available presets."""

    from .preset_manager import list_available_presets

    for preset in list_available_presets():
        typer.echo(preset)


@preset_app.command("apply")
def preset_apply(
    ctx: typer.Context,
    preset: str,
    service: str,
    port: int = typer.Option(0, help="Host port to expose; 0 for auto"),
):
    """Create a VPN service from a preset."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    if port == 0:
        port = manager.next_available_port(config.DEFAULT_PORT_START)
    from .preset_manager import apply_preset

    apply_preset(preset, service, port)
    typer.echo(f"Service '{service}' created from preset '{preset}' on port {port}.")


@app.command("validate")
def validate(compose_file: Path = typer.Option(config.COMPOSE_FILE)):
    """Validate that the compose file is well formed."""

    errors = validate_compose(compose_file)
    if errors:
        for err in errors:
            typer.echo(f"- {err}", err=True)
        raise typer.Exit(1)
    typer.echo("compose file is valid.")


@app.command("test")
def test(service: str):
    """Test that a VPN service proxy is working."""

    from .docker_ops import test_vpn_connection

    if test_vpn_connection(service):
        typer.echo("VPN connection is active.")
    else:
        typer.echo("VPN connection failed.", err=True)
        raise typer.Exit(1)


@app.command("diagnose")
def diagnose_command(
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

    from .docker_ops import (
        get_problematic_containers,
        get_vpn_containers,
        get_container_diagnostics,
        analyze_container_logs,
    )
    from .diagnostics import DiagnosticAnalyzer

    analyzer = DiagnosticAnalyzer()
    containers = (
        get_vpn_containers(all=True)
        if all_containers
        else get_problematic_containers(all=True)
    )

    summary: list[dict[str, object]] = []
    for container in containers:
        diag = get_container_diagnostics(container)
        results = analyze_container_logs(container.name, lines=lines, analyzer=analyzer)
        score = analyzer.health_score(results)
        entry = {
            "container": container.name,
            "status": diag["status"],
            "health": score,
            "issues": [r.message for r in results],
            "recommendations": [r.recommendation for r in results],
        }
        summary.append(entry)

    if json_output:
        typer.echo(json.dumps(summary, indent=2))
    else:
        if not summary:
            typer.echo("No containers to diagnose.")
        for entry in summary:
            typer.echo(
                f"{entry['container']}: status={entry['status']} health={entry['health']}"
            )
            if verbose or entry["issues"]:
                for issue, rec in zip(entry["issues"], entry["recommendations"]):
                    typer.echo(f"  - {issue}: {rec}")


if __name__ == "__main__":
    app()

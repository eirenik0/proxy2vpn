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
from .utils import abort
from .validators import sanitize_name, sanitize_path, validate_port
from .logging_utils import configure_logging, get_logger

app = HelpfulTyper(help="proxy2vpn command line interface")

profile_app = HelpfulTyper(help="Manage VPN profiles")
vpn_app = HelpfulTyper(help="Manage VPN services")
server_app = HelpfulTyper(help="Manage cached server lists")
system_app = HelpfulTyper(help="System level operations")
bulk_app = HelpfulTyper(help="Bulk container operations")
preset_app = HelpfulTyper(help="Manage presets")

app.add_typer(profile_app, name="profile")
app.add_typer(vpn_app, name="vpn")
app.add_typer(server_app, name="servers")
app.add_typer(system_app, name="system")
app.add_typer(preset_app, name="preset")
app.add_typer(bulk_app, name="bulk", hidden=True)

logger = get_logger(__name__)


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
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit",
        is_eager=True,
    ),
):
    """Store global options in context."""
    configure_logging()
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
def system_init(
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
    typer.echo(f"Created '{compose_file}'.")


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
    typer.echo(f"Profile '{name}' created.")


@profile_app.command("list")
def profile_list(ctx: typer.Context):
    """List available profiles."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    for profile in manager.list_profiles():
        typer.echo(profile.name)


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
    typer.echo(f"Profile '{name}' deleted.")


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
    typer.echo(f"Service '{name}' created on port {port}.")


@vpn_app.command("list")
def vpn_list(
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
        get_container_ip,
        analyze_container_logs,
    )
    from .diagnostics import DiagnosticAnalyzer

    if ips_only:
        containers = get_vpn_containers(all=False)
        for container in containers:
            ip = get_container_ip(container)
            typer.echo(f"{container.name}: {ip}")
        return

    services = manager.list_services()
    containers = {c.name: c for c in get_vpn_containers(all=True)}
    analyzer = DiagnosticAnalyzer() if diagnose else None

    header = f"{'NAME':<15} {'PORT':<8} {'PROFILE':<12} {'STATUS':<10} {'IP':<15}"
    if diagnose:
        header += f" {'HEALTH':<7}"
    typer.echo(header)
    typer.echo("-" * len(header))
    iterator = typer.progressbar(services, label="Checking") if diagnose else services
    for svc in iterator:
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
def vpn_start(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, callback=lambda v: sanitize_name(v) if v else None
    ),
    all: bool = typer.Option(False, "--all", help="Start all VPN services"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Recreate container if it already exists"
    ),
):
    """Start one or all VPN containers."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    if all and name is not None:
        abort("Cannot specify NAME when using --all")
    if all:
        from .docker_ops import start_all_vpn_containers

        results = start_all_vpn_containers(manager, force=force)
        for svc_name, started in results:
            if started:
                if force:
                    typer.echo(f"\u2713 Recreated and started {svc_name}")
                else:
                    typer.echo(f"\u2713 Started {svc_name}")
            else:
                typer.echo(f"\u2192 {svc_name} already running")
        return

    if name is None:
        abort("Specify a service NAME or use --all")
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import (
        start_container,
        analyze_container_logs,
        create_vpn_container,
        recreate_vpn_container,
    )
    from .diagnostics import DiagnosticAnalyzer

    svc = manager.get_service(name)
    profile = manager.get_profile(svc.profile)
    if force:
        try:
            recreate_vpn_container(svc, profile)
            start_container(name)
            typer.echo(f"Recreated and started '{name}'.")
        except APIError as exc:
            analyzer = DiagnosticAnalyzer()
            results = analyze_container_logs(name, analyzer=analyzer)
            if results:
                typer.echo("Diagnostic hints:", err=True)
                for res in results:
                    typer.echo(f" - {res.message}: {res.recommendation}", err=True)
            abort(f"Failed to start '{name}': {exc.explanation}")
        return
    try:
        start_container(name)
        typer.echo(f"Started '{name}'.")
    except NotFound:
        try:
            create_vpn_container(svc, profile)
            start_container(name)
            typer.echo(f"Created and started '{name}'.")
        except APIError as exc:
            analyzer = DiagnosticAnalyzer()
            results = analyze_container_logs(name, analyzer=analyzer)
            if results:
                typer.echo("Diagnostic hints:", err=True)
                for res in results:
                    typer.echo(f" - {res.message}: {res.recommendation}", err=True)
            abort(f"Failed to start '{name}': {exc.explanation}")
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
            typer.echo(f"\u2713 Stopped {svc_name}")
        return

    if name is None:
        abort("Specify a service NAME or use --all")
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import stop_container, analyze_container_logs
    from .diagnostics import DiagnosticAnalyzer

    try:
        stop_container(name)
        typer.echo(f"Stopped '{name}'.")
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
        from .docker_ops import get_vpn_containers, restart_container

        containers = get_vpn_containers(all=True)
        for container in containers:
            try:
                restart_container(container.name)
                typer.echo(f"\u2713 Restarted {container.name}")
            except NotFound:
                typer.echo(f"Container '{container.name}' does not exist.", err=True)
            except APIError as exc:
                typer.echo(
                    f"Failed to restart '{container.name}': {exc.explanation}", err=True
                )
        return

    if name is None:
        abort("Specify a service NAME or use --all")
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import restart_container, analyze_container_logs
    from .diagnostics import DiagnosticAnalyzer

    try:
        restart_container(name)
        typer.echo(f"Restarted '{name}'.")
    except NotFound:
        abort(f"Container '{name}' does not exist")
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
            typer.echo(f"Service '{svc.name}' deleted.")
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
    typer.echo(f"Service '{name}' deleted.")


@vpn_app.command("test")
def vpn_test(
    ctx: typer.Context, name: str = typer.Argument(..., callback=sanitize_name)
):
    """Test that a VPN service proxy is working."""

    compose_file: Path = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    manager = ComposeManager(compose_file)
    try:
        manager.get_service(name)
    except KeyError:
        abort(f"Service '{name}' not found")

    from .docker_ops import test_vpn_connection

    if test_vpn_connection(name):
        typer.echo("VPN connection is active.")
    else:
        abort("VPN connection failed", "Check container logs")


# ---------------------------------------------------------------------------
# Bulk container commands
# ---------------------------------------------------------------------------


@bulk_app.command("up")
def bulk_up():
    """Start all VPN containers."""

    typer.echo("Deprecated: use 'vpn start --all' instead.", err=True)
    from .docker_ops import start_all_vpn_containers

    manager = ComposeManager(config.COMPOSE_FILE)
    results = start_all_vpn_containers(manager)
    for name, started in results:
        if started:
            typer.echo(f"\u2713 Started {name}")
        else:
            typer.echo(f"\u2192 {name} already running")


@bulk_app.command("down")
def bulk_down():
    """Stop all running VPN containers."""

    typer.echo("Deprecated: use 'vpn stop --all' instead.", err=True)
    from .docker_ops import stop_all_vpn_containers

    results = stop_all_vpn_containers()
    for name in results:
        typer.echo(f"\u2713 Stopped {name}")


@bulk_app.command("status")
def bulk_status(ctx: typer.Context):
    """Show status and IP address for VPN containers."""

    typer.echo("Deprecated: use 'vpn list' instead.", err=True)
    vpn_list(ctx)


@bulk_app.command("ips")
def bulk_ips(ctx: typer.Context):
    """Show IP addresses of running VPN containers."""

    typer.echo("Deprecated: use 'vpn list --ips-only' instead.", err=True)
    vpn_list(ctx, ips_only=True)


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


@system_app.command("validate")
def system_validate(compose_file: Path = typer.Option(config.COMPOSE_FILE)):
    """Validate that the compose file is well formed."""

    errors = validate_compose(compose_file)
    if errors:
        for err in errors:
            typer.echo(f"- {err}", err=True)
        raise typer.Exit(1)
    typer.echo("compose file is valid.")


@system_app.command("diagnose")
def system_diagnose(
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

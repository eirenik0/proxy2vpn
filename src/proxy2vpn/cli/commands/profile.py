"""Profile management CLI commands."""

from pathlib import Path
import typer
from rich.table import Table

from proxy2vpn.core import config
from proxy2vpn.cli.typer_ext import HelpfulTyper
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.display_utils import console
from proxy2vpn.core.models import Profile
from proxy2vpn.common import abort
from proxy2vpn.adapters.validators import sanitize_name, sanitize_path
from proxy2vpn.adapters.logging_utils import get_logger

app = HelpfulTyper(help="Manage VPN profiles and apply them to services")
logger = get_logger(__name__)


@app.command("create")
def create(
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
    manager = ComposeManager.from_ctx(ctx)
    profile = Profile(name=name, env_file=str(env_file))
    manager.add_profile(profile)
    logger.info("profile_created", extra={"profile_name": name})
    console.print(f"[green]✓[/green] Profile '{name}' created.")


@app.command("list")
def list_profiles(ctx: typer.Context):
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


@app.command("delete")
def delete(
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


@app.command("apply")
def apply(
    ctx: typer.Context,
    profile: str,
    service: str,
    port: int = typer.Option(0, help="Host port to expose; 0 for auto"),
    control_port: int = typer.Option(0, help="Control port; 0 for auto"),
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
    if control_port == 0:
        control_port = manager.next_available_control_port(
            config.DEFAULT_CONTROL_PORT_START
        )
    env = {"VPN_SERVICE_PROVIDER": config.DEFAULT_PROVIDER}
    labels = {
        "vpn.type": "vpn",
        "vpn.port": str(port),
        "vpn.control_port": str(control_port),
        "vpn.provider": config.DEFAULT_PROVIDER,
        "vpn.profile": profile,
        "vpn.location": "",
    }
    from proxy2vpn.core.models import VPNService

    svc = VPNService.create(
        name=service,
        port=port,
        control_port=control_port,
        provider=config.DEFAULT_PROVIDER,
        profile=profile,
        location="",
        environment=env,
        labels=labels,
    )
    manager.add_service(svc)
    console.print(
        f"[green]✓[/green] Service '{service}' created from profile '{profile}' on port {port} (control {control_port}).",
    )

"""Command line interface for proxy2vpn."""
from __future__ import annotations

from pathlib import Path

import typer

from . import config
from .compose_manager import ComposeManager
from .models import Profile, VPNService
from .server_manager import ServerManager

app = typer.Typer(help="proxy2vpn command line interface")

profile_app = typer.Typer(help="Manage VPN profiles")
vpn_app = typer.Typer(help="Manage VPN services")
server_app = typer.Typer(help="Manage cached server lists")

app.add_typer(profile_app, name="profile")
app.add_typer(vpn_app, name="vpn")
app.add_typer(server_app, name="servers")


# ---------------------------------------------------------------------------
# Profile commands
# ---------------------------------------------------------------------------


@profile_app.command("create")
def profile_create(name: str, env_file: Path):
    """Create a new VPN profile."""

    manager = ComposeManager(config.COMPOSE_FILE)
    profile = Profile(name=name, env_file=str(env_file))
    manager.add_profile(profile)
    typer.echo(f"Profile '{name}' created.")


@profile_app.command("list")
def profile_list():
    """List available profiles."""

    manager = ComposeManager(config.COMPOSE_FILE)
    for profile in manager.list_profiles():
        typer.echo(profile.name)


@profile_app.command("delete")
def profile_delete(name: str):
    """Delete a profile by NAME."""

    manager = ComposeManager(config.COMPOSE_FILE)
    manager.remove_profile(name)
    typer.echo(f"Profile '{name}' deleted.")


# ---------------------------------------------------------------------------
# VPN container commands
# ---------------------------------------------------------------------------


@vpn_app.command("create")
def vpn_create(
    name: str,
    profile: str,
    port: int = typer.Option(0, help="Host port to expose; 0 for auto"),
    provider: str = typer.Option(config.DEFAULT_PROVIDER),
    location: str = typer.Option("", help="Optional location, e.g. city"),
):
    """Create a VPN service entry in the compose file."""

    manager = ComposeManager(config.COMPOSE_FILE)
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
def vpn_list():
    """List VPN services defined in the compose file."""

    manager = ComposeManager(config.COMPOSE_FILE)
    for svc in manager.list_services():
        typer.echo(f"{svc.name}\t{svc.port}\t{svc.profile}")


@vpn_app.command("delete")
def vpn_delete(name: str):
    """Remove a VPN service from the compose file."""

    manager = ComposeManager(config.COMPOSE_FILE)
    manager.remove_service(name)
    typer.echo(f"Service '{name}' deleted.")


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


if __name__ == "__main__":
    app()

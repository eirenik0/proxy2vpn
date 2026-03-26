"""Profile management CLI commands."""

from pathlib import Path
import typer
from rich.table import Table

from proxy2vpn.core import config
from proxy2vpn.cli.typer_ext import HelpfulTyper
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.display_utils import console
from proxy2vpn.core.models import Profile, ServiceCredentials
from proxy2vpn.common import abort
from proxy2vpn.adapters.validators import sanitize_name
from proxy2vpn.adapters.logging_utils import get_logger
from proxy2vpn.adapters import server_manager

app = HelpfulTyper(help="Manage VPN profiles and apply them to services")
logger = get_logger(__name__)


def _compose_file_from_ctx(ctx: typer.Context) -> Path:
    return (ctx.obj or {}).get("compose_file", config.COMPOSE_FILE)


def _profile_env_file_paths(ctx: typer.Context, env_file: Path) -> tuple[Path, str]:
    compose_file = _compose_file_from_ctx(ctx)
    target = env_file.expanduser()
    if target.is_absolute():
        resolved = target.resolve()
        stored = str(resolved)
    else:
        resolved = (Path.cwd() / target).resolve()
        stored = config.relativize_path_for_compose(
            target, compose_file=compose_file, cwd=Path.cwd()
        )
    return resolved, stored


def _validate_and_add_profile(
    ctx: typer.Context,
    name: str,
    resolved_env_file: Path,
    stored_env_file: str,
) -> None:
    profile = Profile(name=name, env_file=stored_env_file)
    profile._base_dir = config.resolve_compose_root(_compose_file_from_ctx(ctx))
    validation_errors = profile.validate_env_file()

    if validation_errors:
        console.print(
            f"[red]❌ Profile validation failed for {resolved_env_file}:[/red]"
        )
        for error in validation_errors:
            console.print(f"[red]  • {error}[/red]")
        console.print("\n[yellow]💡 Example valid profile:[/yellow]")
        console.print("[green]VPN_TYPE=openvpn[/green]")
        console.print("[green]VPN_SERVICE_PROVIDER=expressvpn[/green]")
        console.print("[green]OPENVPN_USER=your_username[/green]")
        console.print("[green]OPENVPN_PASSWORD=your_password[/green]")
        console.print("[green]HTTPPROXY=on[/green]")
        console.print("[green]HTTPPROXY_USER=proxy_user[/green]")
        console.print("[green]HTTPPROXY_PASSWORD=proxy_pass[/green]")
        abort("Fix the environment file and try again")

    console.print(f"[blue]📋 Using provider: {profile.provider}[/blue]")

    manager = ComposeManager.from_ctx(ctx)
    manager.add_profile(profile)
    logger.info("profile_added", extra={"profile_name": name})
    console.print(f"[green]✓[/green] Profile '{name}' added.")


@app.command("create")
def create(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name, help="Profile name"),
):
    """Create a new environment file interactively."""

    compose_root = config.resolve_compose_root(_compose_file_from_ctx(ctx))
    env_file_path = compose_root / "profiles" / f"{name}.env"
    stored_env_file = config.relativize_path_for_compose(
        env_file_path, compose_file=_compose_file_from_ctx(ctx), cwd=compose_root
    )

    if env_file_path.exists():
        if not typer.confirm(
            f"Environment file '{env_file_path}' already exists. Overwrite?"
        ):
            abort("Environment file creation cancelled")

    console.print(f"[blue]📋 Creating environment file for profile '{name}'[/blue]")
    console.print("[yellow]💡 Enter the required VPN credentials:[/yellow]")

    # Required fields
    provider = (
        typer.prompt("VPN Provider (e.g., expressvpn, nordvpn, protonvpn)")
        .strip()
        .lower()
    )

    supported = server_manager.ServerManager().list_providers()
    if provider not in supported:
        abort(
            f"Unsupported provider '{provider}'",
            "Run 'proxy2vpn servers list-providers' to see supported providers",
        )

    vpn_type = typer.prompt("VPN type", default="openvpn").strip().lower()
    if vpn_type not in ("openvpn", "wireguard"):
        abort(
            f"Unsupported VPN type '{vpn_type}'",
            "Use 'openvpn' or 'wireguard'",
        )

    username = ""
    password = ""
    if vpn_type == "openvpn":
        username = typer.prompt("VPN Username")
        password = typer.prompt("VPN Password", hide_input=True)

    # Optional HTTP proxy
    enable_proxy = typer.confirm("Enable HTTP proxy?", default=False)
    proxy_user = None
    proxy_password = None

    if enable_proxy:
        proxy_user = typer.prompt("HTTP Proxy Username")
        proxy_password = typer.prompt("HTTP Proxy Password", hide_input=True)

    # Create profiles directory if it doesn't exist
    env_file_path.parent.mkdir(exist_ok=True)

    # Create the environment file
    env_content = [f"VPN_TYPE={vpn_type}", f"VPN_SERVICE_PROVIDER={provider}"]
    if vpn_type == "openvpn":
        env_content.extend(
            [
                f"OPENVPN_USER={username}",
                f"OPENVPN_PASSWORD={password}",
            ]
        )

    if enable_proxy:
        env_content.extend(
            [
                "HTTPPROXY=on",
                f"HTTPPROXY_USER={proxy_user}",
                f"HTTPPROXY_PASSWORD={proxy_password}",
            ]
        )

    env_file_path.write_text("\n".join(env_content) + "\n")

    console.print(f"[green]✓[/green] Environment file created at '{env_file_path}'")
    console.print(
        f"[blue]💡 Next: Create a profile with 'proxy2vpn profile add {name} {stored_env_file}'[/blue]"
    )
    add_profile = typer.confirm(
        f"Should we add profile with with {name}?", default=False
    )
    if add_profile:
        _validate_and_add_profile(ctx, name, env_file_path, stored_env_file)
        compose_file = _compose_file_from_ctx(ctx)
        console.print(f"[green]✓[/green] Profile has been added into '{compose_file}'")


@app.command("add")
def add(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name),
    env_file: Path = typer.Argument(...),
):
    """Add an existing environment file as a VPN profile."""

    resolved_env_file, stored_env_file = _profile_env_file_paths(ctx, env_file)
    if not resolved_env_file.exists():
        abort(
            f"Environment file '{env_file}' not found",
            "Create the file with 'proxy2vpn profile create' or manually",
        )

    _validate_and_add_profile(ctx, name, resolved_env_file, stored_env_file)


@app.command("list")
def list_profiles(ctx: typer.Context):
    """List available profiles."""
    manager = ComposeManager.from_ctx(ctx)
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


@app.command("remove")
def remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name),
    force: bool = typer.Option(False, "--force", "-f", help="Do not prompt"),
):
    """Remove a profile from the compose file."""
    manager = ComposeManager.from_ctx(ctx)
    try:
        manager.get_profile(name)
    except KeyError:
        abort(f"Profile '{name}' not found")
    if not force:
        typer.confirm(f"Remove profile '{name}'?", abort=True)
    manager.remove_profile(name)
    console.print(f"[green]✓[/green] Profile '{name}' removed from compose.")


@app.command("delete")
def delete(
    ctx: typer.Context,
    name: str = typer.Argument(..., callback=sanitize_name),
    force: bool = typer.Option(False, "--force", "-f", help="Do not prompt"),
):
    """Delete a profile's environment file."""
    manager = ComposeManager.from_ctx(ctx)
    try:
        profile = manager.get_profile(name)
    except KeyError:
        abort(f"Profile '{name}' not found")

    env_file_path = profile._resolve_env_path()
    if not env_file_path.exists():
        abort(f"Environment file '{env_file_path}' not found")
    if not force:
        typer.confirm(
            f"Delete environment file '{env_file_path}'?",
            abort=True,
        )
    env_file_path.unlink()
    console.print(f"[green]✓[/green] Environment file '{env_file_path}' deleted.")


@app.command("apply")
def apply(
    ctx: typer.Context,
    profile: str,
    service: str,
    port: int = typer.Option(0, help="Host port to expose; 0 for auto"),
    control_port: int = typer.Option(0, help="Control port; 0 for auto"),
    httpproxy_user: str = typer.Option(
        None, "--httpproxy-user", help="Override HTTP proxy username"
    ),
    httpproxy_password: str = typer.Option(
        None, "--httpproxy-password", help="Override HTTP proxy password"
    ),
):
    """Create a VPN service from a profile."""
    manager = ComposeManager.from_ctx(ctx)
    try:
        resolved_profile = manager.get_profile(profile)
    except KeyError:
        abort(
            f"Profile '{profile}' not found",
            "Create it with 'proxy2vpn profile create'",
        )
    try:
        provider = resolved_profile.provider
    except ValueError as exc:
        abort(str(exc))
    if port == 0:
        port = manager.next_available_port(config.DEFAULT_PORT_START)
    if control_port == 0:
        control_port = manager.next_available_control_port(
            config.DEFAULT_CONTROL_PORT_START
        )
    env = {"VPN_SERVICE_PROVIDER": provider}
    labels = {
        "vpn.type": "vpn",
        "vpn.port": str(port),
        "vpn.control_port": str(control_port),
        "vpn.provider": provider,
        "vpn.profile": profile,
        "vpn.location": "",
    }
    from proxy2vpn.core.models import VPNService

    # Create service credentials if overrides are provided
    credentials = None
    if httpproxy_user is not None or httpproxy_password is not None:
        credentials = ServiceCredentials(
            httpproxy_user=httpproxy_user,
            httpproxy_password=httpproxy_password,
        )
        console.print(
            f"[blue]🔑 Using custom HTTP proxy credentials for service '{service}'[/blue]"
        )

    svc = VPNService.create(
        name=service,
        port=port,
        control_port=control_port,
        provider=provider,
        profile=profile,
        location="",
        environment=env,
        labels=labels,
        credentials=credentials,
    )

    # Validate HTTP proxy configuration at service level
    proxy_errors = svc.validate_httpproxy_config()
    if proxy_errors:
        console.print(
            f"[red]❌ HTTP proxy validation failed for service '{service}':[/red]"
        )
        for error in proxy_errors:
            console.print(f"[red]  • {error}[/red]")
        console.print("\n[yellow]💡 Fix by either:[/yellow]")
        console.print(
            "[green]  1. Adding --httpproxy-user and --httpproxy-password options[/green]"
        )
        console.print(
            "[green]  2. Setting HTTPPROXY_USER and HTTPPROXY_PASSWORD in profile env file[/green]"
        )
        console.print(
            "[green]  3. Disabling HTTP proxy by removing HTTPPROXY=on from profile[/green]"
        )
        abort("Fix the HTTP proxy configuration and try again")

    manager.add_service(svc)

    if credentials:
        console.print(
            f"[green]✓[/green] Service '{service}' created from profile '{profile}' on port {port} (control {control_port}) with custom HTTP proxy credentials.",
        )
    else:
        console.print(
            f"[green]✓[/green] Service '{service}' created from profile '{profile}' on port {port} (control {control_port}).",
        )

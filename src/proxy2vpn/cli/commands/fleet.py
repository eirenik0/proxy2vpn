"""Fleet management CLI commands."""

import typer
from proxy2vpn.cli.typer_ext import HelpfulTyper

app = HelpfulTyper(help="Manage VPN fleets across multiple cities")


@app.command("plan")
def plan(
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
    from proxy2vpn.adapters.fleet_commands import fleet_plan

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


@app.command("deploy")
def deploy(
    ctx: typer.Context,
    plan_file: str = typer.Option("deployment-plan.yaml", help="Deployment plan file"),
    parallel: bool = typer.Option(True, help="Start containers in parallel"),
    validate_first: bool = typer.Option(
        True, help="Validate servers before deployment"
    ),
    dry_run: bool = typer.Option(False, help="Show what would be deployed"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Recreate containers and Docker network if they exist",
    ),
):
    """Deploy VPN fleet from plan file"""
    from proxy2vpn.adapters.fleet_commands import fleet_deploy

    fleet_deploy(ctx, plan_file, parallel, validate_first, dry_run, force)


@app.command("status")
def status(
    ctx: typer.Context,
    format: str = typer.Option("table", help="table|json|yaml"),
    show_allocation: bool = typer.Option(True, help="Show profile allocation"),
    show_health: bool = typer.Option(False, help="Include health checks"),
):
    """Show current fleet status and profile allocation"""
    from proxy2vpn.adapters.fleet_commands import fleet_status

    fleet_status(ctx, format, show_allocation, show_health)


@app.command("rotate")
def rotate(
    ctx: typer.Context,
    country: str = typer.Option(None, help="Rotate servers in specific country"),
    provider: str = typer.Option("protonvpn", help="VPN provider"),
    criteria: str = typer.Option("random", help="random|performance|load"),
    dry_run: bool = typer.Option(False, help="Show rotation plan only"),
):
    """Rotate VPN servers for better availability"""
    from proxy2vpn.adapters.fleet_commands import fleet_rotate

    fleet_rotate(ctx, country, provider, criteria, dry_run)


@app.command("scale")
def scale(
    ctx: typer.Context,
    action: str = typer.Argument(..., help="up|down"),
    countries: str = typer.Option(None, help="Comma-separated countries to scale"),
    factor: int = typer.Option(1, help="Scale factor"),
    profile: str = typer.Option(None, help="Add services to specific profile"),
):
    """Scale VPN fleet up or down"""
    from proxy2vpn.adapters.fleet_commands import fleet_scale

    fleet_scale(ctx, action, countries, factor, profile)

"""Fleet management CLI commands."""

import asyncio
import json

import typer
from ruamel.yaml import YAML
from rich.table import Table

from .display_utils import console
from .fleet_manager import FleetConfig, FleetManager, DeploymentPlan
from .http_client import HTTPClient, HTTPClientConfig
from .server_monitor import ServerMonitor


def fleet_plan(
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

    console.print(
        f"[blue]🎯 Planning deployment for {len(countries.split(','))} countries[/blue]"
    )
    console.print(f"[blue]📊 Profile allocation: {profiles}[/blue]")

    # Parse inputs
    country_list = [c.strip() for c in countries.split(",")]
    profile_config = {}
    try:
        for p in profiles.split(","):
            name, slots = p.split(":")
            profile_config[name.strip()] = int(slots)
    except ValueError:
        console.print("[red]❌ Invalid profiles format. Use: acc1:2,acc2:8[/red]")
        raise typer.Exit(1)

    # Create fleet configuration
    config_obj = FleetConfig(
        provider=provider,
        countries=country_list,
        profiles=profile_config,
        port_start=port_start,
        naming_template=naming_template,
        unique_ips=unique_ips,
    )

    console.print(
        f"[blue]🎯 Planning deployment for {len(country_list)} countries[/blue]"
    )
    console.print(f"[blue]📊 Profile allocation: {profiles}[/blue]")

    # Generate deployment plan
    try:
        fleet_manager = FleetManager()
        plan = fleet_manager.plan_deployment(config_obj)
    except Exception as e:
        console.print(f"[red]❌ Planning failed: {e}[/red]")
        import traceback

        console.print(f"[red]Traceback: {traceback.format_exc()}[/red]")
        raise typer.Exit(1)

    if validate_servers:
        console.print(
            "[yellow]🔍 Server validation will be done during deployment[/yellow]"
        )

    # Display plan summary
    _display_deployment_plan(plan, profile_config)

    # Save plan to file
    try:
        yaml = YAML()
        yaml.default_flow_style = False
        with open(output, "w") as f:
            yaml.dump(plan.to_dict(), f)
        console.print(f"[green]✓[/green] Deployment plan saved to {output}")
        console.print(
            f"[blue]💡 Run 'proxy2vpn fleet deploy {output}' to execute[/blue]"
        )
    except Exception as e:
        console.print(f"[red]❌ Failed to save plan: {e}[/red]")
        raise typer.Exit(1)


def fleet_deploy(
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

    # Load deployment plan
    try:
        yaml = YAML()
        with open(plan_file, "r") as f:
            plan_data = yaml.load(f)
        plan = DeploymentPlan.from_dict(plan_data)
    except FileNotFoundError:
        console.print(f"[red]❌ Plan file not found: {plan_file}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]❌ Failed to load plan: {e}[/red]")
        raise typer.Exit(1)

    if dry_run:
        _display_deployment_plan(plan)
        console.print("[yellow]🔍 Dry run complete - no changes made[/yellow]")
        return

    console.print(f"[green]🚀 Deploying {len(plan.services)} VPN services...[/green]")

    # Execute deployment
    fleet_manager = FleetManager()

    try:
        result = asyncio.run(
            fleet_manager.deploy_fleet(
                plan,
                validate_servers=validate_first,
                parallel=parallel,
                force=force,
            )
        )

        # Display results
        console.print("\n[green]✅ Deployment complete![/green]")
        console.print(f"  • Deployed: {result.deployed} services")
        if result.failed:
            console.print(f"  • Failed: {result.failed} services")
            for error in result.errors:
                console.print(f"    - {error}")

        # Show fleet status
        console.print("\n[bold]Fleet Status:[/bold]")
        _show_fleet_status_sync(result.services)

    except Exception as e:
        console.print(f"[red]❌ Deployment failed: {e}[/red]")
        raise typer.Exit(1)


def fleet_status(
    ctx: typer.Context,
    format: str = typer.Option("table", help="table|json|yaml"),
    show_allocation: bool = typer.Option(True, help="Show profile allocation"),
    show_health: bool = typer.Option(False, help="Include health checks"),
):
    """Show current fleet status and profile allocation"""

    fleet_manager = FleetManager()

    try:
        if show_allocation:
            allocation_status = fleet_manager.profile_allocator.get_allocation_status()
            _display_allocation_table(allocation_status)

        if show_health:
            console.print("\n[bold]Health Status:[/bold]")
            http_client = HTTPClient(HTTPClientConfig(base_url=""))
            server_monitor = ServerMonitor(fleet_manager, http_client=http_client)
            health_results = asyncio.run(server_monitor.check_fleet_health())
            asyncio.run(http_client.close())
            _display_health_results(health_results)

        # Show all VPN services grouped by provider
        fleet_status_data = fleet_manager.get_fleet_status()
        _display_fleet_services(fleet_status_data, format)

    except Exception as e:
        console.print(f"[red]❌ Failed to get fleet status: {e}[/red]")
        raise typer.Exit(1)


def fleet_rotate(
    ctx: typer.Context,
    country: str = typer.Option(None, help="Rotate servers in specific country"),
    provider: str = typer.Option("protonvpn", help="VPN provider"),
    criteria: str = typer.Option("random", help="random|performance|load"),
    dry_run: bool = typer.Option(False, help="Show rotation plan only"),
):
    """Rotate VPN servers for better availability"""

    fleet_manager = FleetManager()
    http_client = HTTPClient(HTTPClientConfig(base_url=""))
    server_monitor = ServerMonitor(fleet_manager, http_client=http_client)

    try:
        result = asyncio.run(server_monitor.rotate_failed_servers(dry_run=dry_run))

        if result.dry_run:
            console.print("[yellow]🔍 Dry run complete - no changes made[/yellow]")
        else:
            console.print("[green]✅ Server rotation complete[/green]")
            console.print(f"  • Rotated: {result.rotated} services")
            console.print(f"  • Failed: {result.failed} services")

    except Exception as e:
        console.print(f"[red]❌ Rotation failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        asyncio.run(http_client.close())


def fleet_scale(
    ctx: typer.Context,
    action: str = typer.Argument(..., help="up|down"),
    countries: str = typer.Option(None, help="Comma-separated countries to scale"),
    factor: int = typer.Option(1, help="Scale factor"),
    profile: str = typer.Option(None, help="Add services to specific profile"),
):
    """Scale VPN fleet up or down"""

    if action not in ["up", "down"]:
        console.print(f"[red]❌ Unknown action: {action}. Use 'up' or 'down'[/red]")
        raise typer.Exit(1)

    console.print("[yellow]⚠ Fleet scaling not yet implemented[/yellow]")
    console.print(f"  Requested: {action} by factor {factor}")
    if countries:
        console.print(f"  Countries: {countries}")
    if profile:
        console.print(f"  Profile: {profile}")


def _display_deployment_plan(
    plan: DeploymentPlan, profile_config: dict[str, int] | None = None
):
    """Display deployment plan in a formatted table"""

    table = Table(title=f"🚀 Fleet Deployment Plan - {plan.provider}")
    table.add_column("Service", style="cyan")
    table.add_column("Profile", style="magenta")
    table.add_column("Location", style="green")
    table.add_column("Country", style="blue")
    table.add_column("Port", style="yellow")
    has_hostname = any(s.hostname for s in plan.services)
    has_ip = any(s.ip for s in plan.services)
    if has_hostname:
        table.add_column("Hostname", style="white")
    if has_ip:
        table.add_column("IP", style="white")

    for service in plan.services:
        row = [
            service.name,
            service.profile,
            service.location,
            service.country,
            str(service.port),
        ]
        if has_hostname:
            row.append(service.hostname or "-")
        if has_ip:
            row.append(service.ip or "-")
        table.add_row(*row)

    console.print(table)

    # Show summary
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  • Total services: {len(plan.services)}")
    console.print(f"  • Provider: {plan.provider}")

    if profile_config:
        console.print("  • Profile allocation:")
        for profile, slots in profile_config.items():
            used = len([s for s in plan.services if s.profile == profile])
            console.print(f"    - {profile}: {used}/{slots} slots")


def _display_allocation_table(allocation_status: dict[str, dict]):
    """Display profile allocation status"""
    if not allocation_status:
        console.print("[yellow]No profiles found in fleet[/yellow]")
        return

    table = Table(title="📊 Profile Allocation Status")
    table.add_column("N", style="dim blue")
    table.add_column("Profile", style="cyan")
    table.add_column("Used/Total", style="magenta")
    table.add_column("Available", style="green")
    table.add_column("Utilization", style="yellow")
    table.add_column("Services", style="blue")

    for i, (profile, data) in enumerate(allocation_status.items(), 1):
        services_str = ", ".join(data["services"][:3])  # Show first 3
        if len(data["services"]) > 3:
            services_str += f", +{len(data['services']) - 3} more"

        table.add_row(
            str(i),
            profile,
            f"{data['used_slots']}/{data['total_slots']}",
            str(data["available_slots"]),
            data["utilization"],
            services_str or "-",
        )

    console.print(table)


def _display_health_results(health_results: dict[str, bool]):
    """Display health check results"""

    healthy = sum(1 for h in health_results.values() if h)
    total = len(health_results)

    console.print(f"Health: {healthy}/{total} services healthy")

    if total - healthy > 0:
        unhealthy_services = [
            name for name, healthy in health_results.items() if not healthy
        ]
        console.print(f"[red]Unhealthy services: {', '.join(unhealthy_services)}[/red]")


def _display_fleet_services(fleet_status: dict, format: str):
    """Display fleet services in specified format"""

    if format == "json":
        console.print(json.dumps(fleet_status, indent=2))
    elif format == "yaml":
        yaml = YAML()
        yaml.default_flow_style = False
        import io

        string_stream = io.StringIO()
        yaml.dump(fleet_status, string_stream)
        console.print(string_stream.getvalue())
    else:
        # Table format
        console.print(
            f"\n[bold]Fleet Overview:[/bold] Total services: {fleet_status['total_services']}"
        )

        all_services = []
        for provider, services in fleet_status.get("services_by_provider", {}).items():
            for service in services:
                all_services.append((provider.upper(), service))

        if all_services:
            from rich.table import Table

            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("N", style="dim blue")
            table.add_column("Provider", style="magenta")
            table.add_column("Name", style="green")
            table.add_column("Location", style="cyan")
            table.add_column("Profile", style="yellow")
            table.add_column("Port", style="blue")

            for i, (provider, service) in enumerate(all_services, 1):
                table.add_row(
                    str(i),
                    provider,
                    service.name,
                    service.location,
                    service.profile,
                    str(service.port),
                )

            console.print(table)
        else:
            console.print("[yellow]No services found in fleet[/yellow]")


def _show_fleet_status_sync(service_names: list[str]):
    """Show basic fleet status without async operations"""
    from .docker_ops import get_service_status_counts

    try:
        running, stopped = get_service_status_counts(service_names)
        console.print(f"  • Running: {running}")
        console.print(f"  • Stopped: {stopped}")

    except Exception as e:
        console.print(f"[red]Error getting status: {e}[/red]")

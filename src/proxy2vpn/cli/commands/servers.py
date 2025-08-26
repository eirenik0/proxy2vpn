"""Server management CLI commands."""

import typer
from proxy2vpn.cli.typer_ext import HelpfulTyper, run_async
from proxy2vpn.adapters.server_manager import ServerManager
from proxy2vpn.adapters.display_utils import console

app = HelpfulTyper(help="Manage cached server lists")


@app.command("update")
@run_async
async def update(
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


@app.command("list-providers")
def list_providers():
    """List VPN providers from the cached server list."""

    mgr = ServerManager()
    for provider in mgr.list_providers():
        typer.echo(provider)


@app.command("list-countries")
def list_countries(provider: str):
    """List countries for a VPN provider."""

    mgr = ServerManager()
    for country in mgr.list_countries(provider):
        typer.echo(country)


@app.command("list-cities")
def list_cities(provider: str, country: str):
    """List cities for a VPN provider in a country."""

    mgr = ServerManager()
    for city in mgr.list_cities(provider, country):
        typer.echo(city)


@app.command("validate-location")
def validate_location(provider: str, location: str):
    """Validate that a location exists for a provider."""

    mgr = ServerManager()
    if mgr.validate_location(provider, location):
        console.print("[green]✓[/green] valid")
    else:
        console.print("[red]❌[/red] invalid")
        raise typer.Exit(1)

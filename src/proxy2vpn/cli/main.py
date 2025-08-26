"""Main CLI application entry point."""

from pathlib import Path
import typer

from proxy2vpn.core import config
from proxy2vpn.adapters.validators import sanitize_path
from proxy2vpn.adapters.logging_utils import configure_logging
from .typer_ext import HelpfulTyper
from .commands import profile, vpn, servers, system, fleet

app = HelpfulTyper(help="proxy2vpn command line interface")

# Add command groups
app.add_typer(profile.app, name="profile")
app.add_typer(vpn.app, name="vpn")
app.add_typer(servers.app, name="servers")
app.add_typer(system.app, name="system")
app.add_typer(fleet.app, name="fleet")


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
    log_file: Path | None = typer.Option(
        None, "--log-file", help="Write JSON logs to file"
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
    if log_file:
        log_file = log_file.expanduser().resolve()
    configure_logging(log_file=log_file)
    if version:
        from proxy2vpn import __version__

        typer.echo(__version__)
        raise typer.Exit()

    ctx.obj = ctx.obj or {}
    ctx.obj["compose_file"] = compose_file

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


if __name__ == "__main__":
    app()

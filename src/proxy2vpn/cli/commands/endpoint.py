"""Explicit manual external-provider commands; no automatic recovery dispatch."""

import asyncio
from pathlib import Path
import typer

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.provider import MobileOperations, ProviderOperationError
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.core import config
from proxy2vpn.core.private_storage import StorageError
from proxy2vpn.cli.typer_ext import HelpfulTyper

app = HelpfulTyper(help="Manual external endpoint provider operations")


def coordinator(ctx):
    compose = Path(ctx.obj.get("compose_file", config.COMPOSE_FILE))
    return MobileOperations(AgentStateStore(compose, AgentSettings()))


@app.command("request-exit-ip")
def request_exit_ip(
    ctx: typer.Context,
    name: str,
    confirm_disruption: bool = typer.Option(False, "--confirm-disruption"),
):
    """Request a different exit IP, acknowledging possible active connection loss."""
    if not confirm_disruption:
        typer.echo("Use --confirm-disruption to acknowledge possible connection loss.")
        raise typer.Exit(2)
    try:
        result = asyncio.run(coordinator(ctx).request(name))
    except (ProviderOperationError, StorageError, ValueError):
        typer.echo(
            "Provider request failed or is guarded; inspect endpoint provider-status before retrying."
        )
        raise typer.Exit(1) from None
    typer.echo(result.model_dump_json(indent=2))


@app.command("provider-status")
def provider_status(ctx: typer.Context, name: str):
    """Show durable request facts without contacting the provider."""
    try:
        records = coordinator(ctx).status(name)
    except (StorageError, ValueError):
        typer.echo(
            "Provider evidence could not be read; repair storage before requesting again."
        )
        raise typer.Exit(1) from None
    for record in records:
        typer.echo(record.model_dump_json(indent=2))


@app.command("reconcile")
def reconcile(
    ctx: typer.Context,
    name: str,
    operation_id: str | None = typer.Option(None, "--operation-id"),
):
    """Probe the configured proxy and retain uncertainty; never replay a request."""
    try:
        record = asyncio.run(coordinator(ctx).reconcile(name, operation_id))
    except (ProviderOperationError, StorageError, ValueError):
        typer.echo(
            "Reconciliation could not complete; the provider guard remains in force."
        )
        raise typer.Exit(1) from None
    typer.echo(record.model_dump_json(indent=2))

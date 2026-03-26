"""Agent watchdog CLI commands."""

from __future__ import annotations

import json

from filelock import Timeout
import typer
from rich.table import Table

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.runtime import AgentWatchdog
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.adapters.display_utils import console
from proxy2vpn.cli.typer_ext import HelpfulTyper, run_async
from proxy2vpn.common import abort
from proxy2vpn.core import config

app = HelpfulTyper(help="Run the local service watchdog")
DEFAULT_AGENT_SETTINGS = AgentSettings()


@app.command("run")
@run_async
async def run(
    ctx: typer.Context,
    once: bool = typer.Option(
        False, "--once", help="Run one monitoring cycle and exit"
    ),
    interval: int = typer.Option(
        DEFAULT_AGENT_SETTINGS.interval_seconds,
        "--interval",
        help="Polling interval in seconds for foreground mode",
    ),
):
    """Run the local watchdog for the active compose root."""

    compose_file = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    watchdog = AgentWatchdog(compose_file, interval_seconds=interval)
    lock = watchdog.store.runtime_lock()
    try:
        lock.acquire(timeout=0)
    except Timeout:
        abort(
            f"Agent is already running for '{compose_file}'",
            "Use 'proxy2vpn agent status' to inspect the current watchdog state.",
        )

    try:
        if once:
            state = await watchdog.run_once()
            console.print(
                f"[green]✓[/green] Agent cycle complete: {state.status.service_count} services, {state.status.unhealthy_count} unhealthy."
            )
            return

        console.print(
            f"[green]✓[/green] Agent running for '{compose_file}' every {interval}s. Press Ctrl+C to stop."
        )
        try:
            await watchdog.run_forever()
        except KeyboardInterrupt:
            console.print("[yellow]Agent stopped.[/yellow]")
    finally:
        lock.release()


@app.command("status")
def status(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show persisted watchdog state for the active compose root."""

    compose_file = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    watchdog = AgentWatchdog(compose_file)
    state = watchdog.store.read_state() or watchdog.empty_state()

    payload = state.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    status_data = state.status
    console.print(
        f"Compose: {status_data.compose_path}\nMode: {status_data.daemon_mode}\nLast loop: {status_data.last_loop_at or 'never'}\nServices: {status_data.service_count}\nUnhealthy: {status_data.unhealthy_count}\nLLM: {status_data.llm_mode}"
    )
    if status_data.last_error:
        console.print(f"[red]Last error:[/red] {status_data.last_error}")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Service", style="green")
    table.add_column("Status")
    table.add_column("Health")
    table.add_column("Failures")
    table.add_column("Last Action")
    table.add_column("Result")

    for snapshot in state.services:
        table.add_row(
            snapshot.service_name,
            snapshot.container_status,
            str(snapshot.health_score),
            str(snapshot.consecutive_failures),
            snapshot.last_action or "",
            snapshot.last_action_result or "",
        )

    if state.services:
        console.print(table)

    if state.actions:
        console.print("\nRecent actions:")
        for action in state.actions[-5:]:
            console.print(
                f"- {action.ts}: {action.service_name} {action.action} [{action.result}]"
            )


@app.command("incidents")
def incidents(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    show_all: bool = typer.Option(False, "--all", help="Include closed incidents"),
):
    """List incidents for the active compose root."""

    compose_file = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    store = AgentStateStore(compose_file)
    incident_list = store.load_incidents()
    if not show_all:
        incident_list = [
            incident
            for incident in incident_list
            if incident.status not in {"resolved", "dismissed"}
        ]

    payload = {
        "incidents": [incident.model_dump(mode="json") for incident in incident_list]
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    if not incident_list:
        console.print("[green]No incidents.[/green]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim blue")
    table.add_column("Service", style="green")
    table.add_column("Type")
    table.add_column("Severity")
    table.add_column("Status")
    table.add_column("Action")
    table.add_column("Summary")
    table.add_column("Updated")

    for incident in incident_list:
        table.add_row(
            incident.id,
            incident.service_name,
            incident.type,
            incident.severity,
            incident.status,
            incident.recommended_action,
            incident.summary,
            str(incident.updated_at),
        )

    console.print(table)
    if any(incident.human_explanation for incident in incident_list):
        console.print("\nExplanations:")
        for incident in incident_list:
            if incident.human_explanation:
                console.print(f"- {incident.id}: {incident.human_explanation}")


@app.command("approve")
@run_async
async def approve(
    ctx: typer.Context,
    incident_id: str = typer.Argument(..., help="Incident identifier"),
):
    """Approve and execute the pending escalation for one incident."""

    compose_file = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    watchdog = AgentWatchdog(compose_file)
    try:
        incident = await watchdog.approve_incident(incident_id)
    except KeyError:
        abort(f"Incident '{incident_id}' not found")
    except RuntimeError as exc:
        abort(str(exc))

    if incident.status == "resolved":
        console.print(
            f"[green]✓[/green] Approved incident '{incident_id}' and completed the rotation."
        )
    else:
        abort(
            f"Approved incident '{incident_id}', but the rotation failed.",
            incident.summary,
        )


@app.command("dismiss")
def dismiss(
    ctx: typer.Context,
    incident_id: str = typer.Argument(..., help="Incident identifier"),
):
    """Dismiss one incident and suppress immediate re-opening."""

    compose_file = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    watchdog = AgentWatchdog(compose_file)
    try:
        incident = watchdog.dismiss_incident(incident_id)
    except KeyError:
        abort(f"Incident '{incident_id}' not found")
    except RuntimeError as exc:
        abort(str(exc))

    console.print(
        f"[green]✓[/green] Dismissed incident '{incident.id}' for service '{incident.service_name}'."
    )

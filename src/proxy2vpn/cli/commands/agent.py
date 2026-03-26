"""Agent watchdog CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from filelock import Timeout
import psutil
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


def _mark_agent_inactive(store: AgentStateStore) -> None:
    state = store.read_state()
    if state is None:
        return
    state.status.daemon_mode = "inactive"
    store.write_state(state)


def _daemon_payload(store: AgentStateStore) -> dict[str, object]:
    process = store.daemon_process()
    return {
        "running": process is not None,
        "pid": process.pid if process is not None else store.read_daemon_pid(),
        "log_file": str(store.daemon_log_path),
    }


def _install_termination_handlers() -> None:
    def _handle_stop(_signum, _frame):
        raise KeyboardInterrupt()

    for signame in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            signal.signal(signum, _handle_stop)


def _spawn_daemon_process(
    compose_file: Path, interval: int, store: AgentStateStore
) -> tuple[int, Path]:
    lock = store.runtime_lock()
    acquired = False
    try:
        lock.acquire(timeout=0)
        acquired = True
    except Timeout:
        abort(
            f"Agent is already running for '{compose_file}'",
            "Use 'proxy2vpn agent status' to inspect the current watchdog state.",
        )
    finally:
        if acquired:
            lock.release()

    command = [
        sys.executable,
        "-m",
        "proxy2vpn",
        "--compose-file",
        str(compose_file),
        "--log-file",
        str(store.daemon_log_path),
        "agent",
        "run",
        "--interval",
        str(interval),
        "--daemon-child",
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(Path.cwd()),
        "env": os.environ.copy(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    time.sleep(0.2)
    if process.poll() is not None:
        abort(
            f"Agent daemon for '{compose_file}' exited before startup completed.",
            f"Inspect logs in '{store.daemon_log_path}'.",
        )
    return process.pid, store.daemon_log_path


@app.command("run")
@run_async
async def run(
    ctx: typer.Context,
    once: bool = typer.Option(
        False, "--once", help="Run one monitoring cycle and exit"
    ),
    daemon: bool = typer.Option(
        False, "--daemon", help="Run in the background as a detached daemon"
    ),
    interval: int = typer.Option(
        DEFAULT_AGENT_SETTINGS.interval_seconds,
        "--interval",
        help="Polling interval in seconds for foreground mode",
    ),
    daemon_child: bool = typer.Option(False, "--daemon-child", hidden=True),
):
    """Run the local watchdog for the active compose root."""

    if once and daemon:
        abort("Cannot combine '--once' with '--daemon'")

    compose_file = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    watchdog = AgentWatchdog(compose_file, interval_seconds=interval)
    if daemon:
        pid, log_path = _spawn_daemon_process(compose_file, interval, watchdog.store)
        console.print(
            f"[green]✓[/green] Agent daemon started for '{compose_file}' with PID {pid}."
        )
        console.print(f"Log file: {log_path}")
        return

    lock = watchdog.store.runtime_lock()
    try:
        lock.acquire(timeout=0)
    except Timeout:
        abort(
            f"Agent is already running for '{compose_file}'",
            "Use 'proxy2vpn agent status' to inspect the current watchdog state.",
        )

    try:
        if daemon_child:
            watchdog.store.write_daemon_pid(os.getpid())
            _install_termination_handlers()

        if once:
            state = await watchdog.run_once()
            console.print(
                f"[green]✓[/green] Agent cycle complete: {state.status.service_count} services, {state.status.unhealthy_count} unhealthy."
            )
            return

        if not daemon_child:
            console.print(
                f"[green]✓[/green] Agent running for '{compose_file}' every {interval}s. Press Ctrl+C to stop."
            )
        try:
            await watchdog.run_forever(
                daemon_mode="daemon" if daemon_child else "foreground"
            )
        except KeyboardInterrupt:
            if not daemon_child:
                console.print("[yellow]Agent stopped.[/yellow]")
    finally:
        if daemon_child:
            watchdog.store.clear_daemon_pid(os.getpid())
            _mark_agent_inactive(watchdog.store)
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
    daemon_data = _daemon_payload(watchdog.store)

    payload = state.model_dump(mode="json")
    payload["daemon"] = daemon_data
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    status_data = state.status
    daemon_status = "running" if daemon_data["running"] else "stopped"
    daemon_pid = daemon_data["pid"]
    daemon_line = (
        f"{daemon_status} (pid {daemon_pid})"
        if daemon_pid is not None
        else daemon_status
    )
    console.print(
        f"Compose: {status_data.compose_path}\nMode: {status_data.daemon_mode}\nDaemon: {daemon_line}\nLog file: {daemon_data['log_file']}\nLast loop: {status_data.last_loop_at or 'never'}\nServices: {status_data.service_count}\nUnhealthy: {status_data.unhealthy_count}\nLLM: {status_data.llm_mode}"
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


@app.command("investigate")
@run_async
async def investigate(
    ctx: typer.Context,
    incident_id: str = typer.Argument(..., help="Incident identifier"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Investigate one incident and print an operator action plan."""

    compose_file = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    watchdog = AgentWatchdog(compose_file)
    try:
        incident = await watchdog.investigate_incident(incident_id)
    except KeyError:
        abort(f"Incident '{incident_id}' not found")

    payload = {"incident": incident.model_dump(mode="json")}
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    investigation = incident.investigation
    if investigation is None:
        abort(f"Incident '{incident_id}' did not produce an investigation result")

    console.print(
        f"Incident: {incident.id}\n"
        f"Service: {incident.service_name}\n"
        f"Type: {incident.type}\n"
        f"Severity: {incident.severity}\n"
        f"Status: {incident.status}\n"
        f"Recommended action: {incident.recommended_action}\n"
        f"Investigated at: {investigation.investigated_at}"
    )
    console.print(f"\nSummary: {investigation.summary}")

    if investigation.findings:
        console.print("\nFindings:")
        for finding in investigation.findings:
            console.print(f"- {finding}")

    if investigation.action_plan:
        console.print("\nAction plan:")
        for index, step in enumerate(investigation.action_plan, start=1):
            console.print(f"{index}. {step}")


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


@app.command("stop")
def stop(ctx: typer.Context):
    """Stop the detached watchdog daemon for the active compose root."""

    compose_file = ctx.obj.get("compose_file", config.COMPOSE_FILE)
    store = AgentStateStore(compose_file)
    pid = store.read_daemon_pid()
    if pid is None:
        abort(
            f"Agent daemon is not running for '{compose_file}'",
            "Use 'proxy2vpn agent run --daemon' to start it.",
        )

    process = store.daemon_process()
    if process is None:
        store.clear_daemon_pid(pid)
        _mark_agent_inactive(store)
        console.print(
            "[yellow]No running agent daemon was found. Cleared the stale PID file.[/yellow]"
        )
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

    store.clear_daemon_pid(pid)
    _mark_agent_inactive(store)
    console.print(
        f"[green]✓[/green] Stopped agent daemon for '{compose_file}' (PID {pid})."
    )

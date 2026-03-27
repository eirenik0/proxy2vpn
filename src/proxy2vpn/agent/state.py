"""Compose-root state persistence for the proxy2vpn agent."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

from filelock import FileLock
import psutil

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import AgentIncident, AgentState, AgentStatus
from proxy2vpn.core import config


class AgentStateStore:
    """Persist state, incidents, and runtime lock files for one compose root."""

    def __init__(
        self, compose_file: Path, settings: AgentSettings | None = None
    ) -> None:
        self.compose_file = compose_file.expanduser().resolve()
        self.compose_root = config.resolve_compose_root(self.compose_file)
        self.settings = settings or AgentSettings()
        self.agent_dir = self.compose_root / self.settings.state_dirname
        self.state_file = self.agent_dir / self.settings.state_file
        self.incidents_file = self.agent_dir / self.settings.incidents_file
        self.runtime_lock_path = self.agent_dir / self.settings.runtime_lock_file
        self.daemon_pid_path = self.agent_dir / self.settings.daemon_pid_file
        self.daemon_log_path = self.agent_dir / self.settings.daemon_log_file

    def ensure_dir(self) -> None:
        self.agent_dir.mkdir(parents=True, exist_ok=True)

    def runtime_lock(self) -> FileLock:
        self.ensure_dir()
        return FileLock(str(self.runtime_lock_path))

    def write_daemon_pid(self, pid: int) -> None:
        self.ensure_dir()
        self.daemon_pid_path.write_text(f"{pid}\n", encoding="utf-8")

    def read_daemon_pid(self) -> int | None:
        if not self.daemon_pid_path.exists():
            return None
        try:
            return int(self.daemon_pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            return None

    def clear_daemon_pid(self, pid: int | None = None) -> None:
        if not self.daemon_pid_path.exists():
            return
        if pid is not None:
            current = self.read_daemon_pid()
            if current is not None and current != pid:
                return
        with suppress(FileNotFoundError):
            self.daemon_pid_path.unlink()

    def daemon_process(self) -> psutil.Process | None:
        pid = self.read_daemon_pid()
        if pid is None:
            return None
        try:
            process = psutil.Process(pid)
        except psutil.Error:
            self.clear_daemon_pid(pid)
            return None
        if not process.is_running():
            self.clear_daemon_pid(pid)
            return None
        with suppress(psutil.Error):
            if process.status() == psutil.STATUS_ZOMBIE:
                self.clear_daemon_pid(pid)
                return None
        with suppress(psutil.Error):
            cmdline = process.cmdline()
            if cmdline and not ("proxy2vpn" in cmdline and "--daemon-child" in cmdline):
                self.clear_daemon_pid(pid)
                return None
        return process

    def daemon_is_running(self) -> bool:
        return self.daemon_process() is not None

    def read_state(self) -> AgentState | None:
        if not self.state_file.exists():
            return None
        return AgentState.model_validate_json(self.state_file.read_text())

    def write_state(self, state: AgentState) -> None:
        self.ensure_dir()
        tmp_file = self.state_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(state.model_dump(mode="json"), indent=2))
        tmp_file.replace(self.state_file)

    def reset_monitoring_state(self) -> None:
        """Clear persisted incidents and service history while preserving runtime metadata."""

        self.ensure_dir()
        previous_state = None
        with suppress(Exception):
            previous_state = self.read_state()

        if previous_state is not None:
            status = previous_state.status.model_copy(
                update={
                    "compose_path": str(self.compose_file),
                    "service_count": 0,
                    "unhealthy_count": 0,
                    "last_error": None,
                    "active_cycle_started_at": None,
                    "active_cycle_phase": None,
                    "active_cycle_service_name": None,
                    "last_loop_at": None,
                    "last_progress_at": None,
                }
            )
        else:
            status = AgentStatus(
                compose_path=str(self.compose_file),
                daemon_mode="daemon" if self.daemon_is_running() else "inactive",
                interval_seconds=self.settings.interval_seconds,
                llm_mode=self.settings.llm_mode,
            )

        self.write_state(AgentState(status=status))
        with suppress(FileNotFoundError):
            self.incidents_file.unlink()

    def load_incidents(self) -> list[AgentIncident]:
        if not self.incidents_file.exists():
            return []

        latest_by_id: dict[str, AgentIncident] = {}
        for line in self.incidents_file.read_text().splitlines():
            if not line.strip():
                continue
            incident = AgentIncident.model_validate_json(line)
            latest_by_id[incident.id] = incident
        return sorted(
            latest_by_id.values(),
            key=lambda incident: incident.updated_at,
            reverse=True,
        )

    def append_incident(self, incident: AgentIncident) -> None:
        self.ensure_dir()
        with self.incidents_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(incident.model_dump(mode="json")))
            handle.write("\n")

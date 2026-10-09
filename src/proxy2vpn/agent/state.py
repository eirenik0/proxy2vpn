"""Compose-root state persistence for the proxy2vpn agent."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from filelock import BaseFileLock, FileLock
import psutil

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.evidence import EvidenceSanitizer
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

    def runtime_lock(self) -> BaseFileLock:
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

    def sanitizer(self) -> EvidenceSanitizer:
        return EvidenceSanitizer.from_compose(
            self.compose_file, self.settings.external_proxies_file
        )

    def _storage_lock(self) -> BaseFileLock:
        self.ensure_dir()
        return FileLock(str(self.agent_dir / "evidence.lock"))

    def _atomic_write(self, path: Path, text: str) -> None:
        fd, name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=self.agent_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            Path(name).replace(path)
        finally:
            with suppress(FileNotFoundError):
                Path(name).unlink()

    def read_state(self) -> AgentState | None:
        with self._storage_lock():
            if not self.state_file.exists():
                return None
            original = self.state_file.read_text()
            sanitized = self.sanitizer().sanitize(json.loads(original))
            state = AgentState.model_validate(sanitized)
            text = json.dumps(state.model_dump(mode="json"), indent=2)
            if text != original:
                self._atomic_write(self.state_file, text)
            return state

    def write_state(self, state: AgentState) -> None:
        with self._storage_lock():
            sanitized = self.sanitizer().model(state)
            self._atomic_write(
                self.state_file, json.dumps(sanitized.model_dump(mode="json"), indent=2)
            )

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
        with self._storage_lock():
            with suppress(FileNotFoundError):
                self.incidents_file.unlink()

    def _scrub_incidents(self) -> list[AgentIncident]:
        if not self.incidents_file.exists():
            return []
        original = self.incidents_file.read_text()
        sanitizer = self.sanitizer()
        records = [
            AgentIncident.model_validate(sanitizer.sanitize(json.loads(line)))
            for line in original.splitlines()
            if line.strip()
        ]
        text = "".join(
            json.dumps(record.model_dump(mode="json")) + "\n" for record in records
        )
        if text != original:
            self._atomic_write(self.incidents_file, text)
        return records

    def load_incidents(self) -> list[AgentIncident]:
        with self._storage_lock():
            records = self._scrub_incidents()
        latest_by_id = {incident.id: incident for incident in records}
        return sorted(
            latest_by_id.values(),
            key=lambda incident: incident.updated_at,
            reverse=True,
        )

    def append_incident(self, incident: AgentIncident) -> None:
        with self._storage_lock():
            self._scrub_incidents()
            sanitized = self.sanitizer().model(incident)
            with self.incidents_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(sanitized.model_dump(mode="json")) + "\n")

"""Compose-root state persistence for the proxy2vpn agent."""

from __future__ import annotations

import json
from contextlib import contextmanager, suppress
from pathlib import Path
from contextvars import ContextVar
from collections.abc import Generator

from filelock import BaseFileLock, Timeout
import psutil

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.evidence import EvidenceSanitizer
from proxy2vpn.agent.models import ActionRecord, AgentIncident, AgentState, AgentStatus
from proxy2vpn.agent.retention import (
    CompactionReport,
    as_utc,
    parse_history,
    plan_compaction,
)
from proxy2vpn.core.private_storage import (
    StorageError,
    atomic_write,
    managed_directory,
    private_lock,
    sync_directory,
)
from proxy2vpn.core import config


class StorageConflict(StorageError):
    """An operation tried to commit evidence from an obsolete snapshot."""


_operation_generation: ContextVar[tuple[Path, int] | None] = ContextVar(
    "agent_generation", default=None
)


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
        self.journal_path = self.agent_dir / "transaction.json"
        self.ensure_dir()
        self._lock = private_lock(self.agent_dir / "evidence.lock")

    def ensure_dir(self) -> None:
        managed_directory(self.agent_dir, self.settings.storage_artifact_names)

    def runtime_lock(self) -> BaseFileLock:
        self.ensure_dir()
        return private_lock(self.runtime_lock_path)

    def write_daemon_pid(self, pid: int) -> None:
        self.ensure_dir()
        with self.transaction():
            self._atomic_write(self.daemon_pid_path, f"{pid}\n")

    def read_daemon_pid(self) -> int | None:
        self.ensure_dir()
        if not self.daemon_pid_path.exists():
            return None
        try:
            return int(self.daemon_pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            return None

    def clear_daemon_pid(self, pid: int | None = None) -> None:
        with self.transaction():
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
            self.compose_file,
            self.settings.external_proxies_file,
            storage_names=self.settings.storage_artifact_names,
        )

    @contextmanager
    def transaction(self) -> Generator[None]:
        """Serialize short storage work; callers must not perform network work here."""
        self.ensure_dir()
        try:
            with self._lock:
                self._recover_transaction()
                yield
        except Timeout:
            raise StorageError(
                "Agent storage lock timed out; retry the operation"
            ) from None

    @contextmanager
    def provider_transaction(self) -> Generator[None]:
        """Read fresh provider facts across reset without reviving old observations."""
        token = _operation_generation.set(None)
        try:
            with self.transaction():
                yield
        finally:
            _operation_generation.reset(token)

    @contextmanager
    def observation_session(self, generation: int) -> Generator[None]:
        """Fence delayed async work against reset without holding a lock."""
        token = _operation_generation.set((self.compose_file, generation))
        try:
            yield
        finally:
            _operation_generation.reset(token)

    def _recover_transaction(self) -> None:
        if not self.journal_path.exists():
            return
        try:
            document = json.loads(self.journal_path.read_text())
            if set(document) != {"state", "incidents"}:
                raise ValueError
            AgentState.model_validate_json(document["state"])
            for line in document["incidents"].splitlines():
                AgentIncident.model_validate_json(line)
        except (ValueError, TypeError, KeyError):
            raise StorageError(
                "Invalid agent storage transaction; repair is required"
            ) from None
        self._atomic_write(self.state_file, document["state"])
        self._atomic_write(self.incidents_file, document["incidents"])
        self.journal_path.unlink()
        sync_directory(self.agent_dir)

    def replace_monitoring(
        self, state: AgentState, incidents: list[AgentIncident]
    ) -> None:
        """Commit a recoverable state/history replacement inside a transaction."""
        if not self._lock.is_locked:
            raise StorageError("Monitoring replacement requires a storage transaction")
        sanitizer = self.sanitizer()
        document = {
            "state": json.dumps(sanitizer.model(state).model_dump(mode="json")),
            "incidents": "".join(
                json.dumps(sanitizer.model(item).model_dump(mode="json")) + "\n"
                for item in incidents
            ),
        }
        self._atomic_write(self.journal_path, json.dumps(document))
        self._recover_transaction()

    def _atomic_write(self, path: Path, text: str) -> None:
        atomic_write(path, text.encode("utf-8"))

    def check_generation(self, generation: int) -> None:
        current = self.read_state()
        if generation != (current.generation if current else 0):
            raise StorageConflict("Monitoring was reset; refresh before retrying")

    def read_state(self) -> AgentState | None:
        with self.transaction():
            if not self.state_file.exists():
                return None
            original = self.state_file.read_text()
            sanitizer = self.sanitizer()
            try:
                sanitized = sanitizer.sanitize(json.loads(original))
                state = AgentState.model_validate(sanitized)
            except ValueError:
                raise StorageError("Corrupt agent state; repair is required") from None
            text = json.dumps(state.model_dump(mode="json"), indent=2)
            if text != original:
                self._atomic_write(self.state_file, text)
            return AgentState.model_validate(
                sanitizer.restore_identities(state.model_dump(mode="json"))
            )

    def write_state(self, state: AgentState) -> None:
        with self.transaction():
            current = self.read_state()
            session = _operation_generation.get()
            if (
                session is not None
                and session[0] == self.compose_file
                and session[1] != (current.generation if current else 0)
            ):
                raise StorageConflict("Monitoring was reset; refresh before retrying")
            if current is not None and (
                state.generation != current.generation
                or state.revision != current.revision
            ):
                raise StorageConflict("Agent state changed; refresh before retrying")
            updated = state.model_copy(update={"revision": state.revision + 1})
            sanitized = self.sanitizer().model(updated)
            self._atomic_write(
                self.state_file, json.dumps(sanitized.model_dump(mode="json"), indent=2)
            )
            state.revision = updated.revision

    def append_action(self, state: AgentState, action: ActionRecord) -> None:
        """Retain completed operation evidence even if another writer changed status."""
        with self.transaction():
            session = _operation_generation.get()
            if session is not None and session[0] == self.compose_file:
                self.check_generation(session[1])
            self.check_generation(state.generation)
            current = self.read_state()
            if current is None or current.revision == state.revision:
                candidate = state.model_copy(deep=True)
                candidate.metrics.count_recovery(action, candidate.services)
                self.write_state(candidate)
                state.metrics = candidate.metrics
                state.revision = candidate.revision
                return
            current.metrics.count_recovery(action, current.services)
            current.actions.append(action)
            current.actions = current.actions[-self.settings.action_history_limit :]
            self.write_state(current)

    def finish_metric_cycle(self, generation, token, outcome) -> None:
        """Finalize only this cycle's metrics without overwriting concurrent evidence."""
        with self.transaction():
            current = self.read_state()
            if (
                current is None
                or current.generation != generation
                or current.metrics.cycle_run_id != token
            ):
                return
            current.metrics.count_cycle(outcome)
            current.metrics.cycle_outcome = outcome
            current.metrics.cycle_run_id = None
            self.write_state(current)

    def reset_monitoring_state(self) -> None:
        """Clear persisted incidents and service history while preserving runtime metadata."""

        with self.transaction():
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

            state = AgentState(
                status=status,
                provider_operations=previous_state.provider_operations
                if previous_state
                else [],
                metrics=(
                    previous_state.metrics.clear_monitoring()
                    if previous_state
                    else AgentState(status=status).metrics.clear_monitoring()
                ),
                generation=(previous_state.generation if previous_state else 0) + 1,
                revision=(previous_state.revision if previous_state else 0) + 1,
            )
            self.replace_monitoring(state, [])

    def _scrub_incidents(self) -> list[AgentIncident]:
        if not self.incidents_file.exists():
            return []
        original = self.incidents_file.read_text()
        sanitizer = self.sanitizer()
        records = parse_history(original, sanitizer)
        # Zero denotes a never-persisted object to append_incident. Promote legacy
        # records before exposing them, so pruning cannot turn stale copies new.
        records = [
            record.model_copy(update={"revision": 1})
            if record.revision == 0
            else record
            for record in records
        ]
        text = "".join(
            json.dumps(record.model_dump(mode="json")) + "\n" for record in records
        )
        if text != original:
            self._atomic_write(self.incidents_file, text)
        return [
            AgentIncident.model_validate(
                sanitizer.restore_identities(record.model_dump(mode="json"))
            )
            for record in records
        ]

    def load_incidents(self) -> list[AgentIncident]:
        with self.transaction():
            records = self._scrub_incidents()
        latest_by_id = {incident.id: incident for incident in records}
        return sorted(
            latest_by_id.values(),
            key=lambda incident: as_utc(incident.updated_at),
            reverse=True,
        )

    def compact_incidents(self, *, now=None) -> CompactionReport:
        """Re-plan and atomically compact current sanitized evidence under its lock."""
        with self.transaction():
            session = _operation_generation.get()
            if session is not None and session[0] == self.compose_file:
                self.check_generation(session[1])
            records = self._scrub_incidents()
            report, kept = plan_compaction(records, self.settings, now=now)
            if not self.incidents_file.exists():
                return report
            sanitizer = self.sanitizer()
            text = "".join(
                json.dumps(sanitizer.model(item).model_dump(mode="json")) + "\n"
                for item in kept
            )
            if text != self.incidents_file.read_text():
                self._atomic_write(self.incidents_file, text)
            return report

    def append_incident(self, incident: AgentIncident) -> None:
        with self.transaction():
            session = _operation_generation.get()
            expected = (
                session[1]
                if session is not None and session[0] == self.compose_file
                else None
            )
            if expected is not None:
                self.check_generation(expected)
            records = self._scrub_incidents()
            current = next(
                (item for item in reversed(records) if item.id == incident.id), None
            )
            if current is None:
                if incident.revision != 0:
                    raise StorageConflict(
                        "Incident no longer exists; refresh before retrying"
                    )
                self.check_generation(
                    expected if expected is not None else incident.generation
                )
                if expected is not None:
                    incident.generation = expected
            elif (
                incident.revision != current.revision
                or incident.generation != current.generation
            ):
                raise StorageConflict("Incident changed; refresh before retrying")
            updated = incident.model_copy(update={"revision": incident.revision + 1})
            sanitized = self.sanitizer().model(updated)
            # Replace the complete sanitized history rather than risking a partial append.
            text = (
                self.incidents_file.read_text() if self.incidents_file.exists() else ""
            )
            self._atomic_write(
                self.incidents_file,
                text + json.dumps(sanitized.model_dump(mode="json")) + "\n",
            )
            incident.revision = updated.revision

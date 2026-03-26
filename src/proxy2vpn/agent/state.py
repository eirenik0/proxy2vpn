"""Compose-root state persistence for the proxy2vpn agent."""

from __future__ import annotations

import json
from pathlib import Path

from filelock import FileLock

from proxy2vpn.agent.models import AgentIncident, AgentState
from proxy2vpn.core import config


class AgentStateStore:
    """Persist state, incidents, and runtime lock files for one compose root."""

    def __init__(self, compose_file: Path):
        self.compose_file = compose_file.expanduser().resolve()
        self.compose_root = config.resolve_compose_root(self.compose_file)
        self.agent_dir = self.compose_root / config.AGENT_STATE_DIRNAME
        self.state_file = self.agent_dir / config.AGENT_STATE_FILE
        self.incidents_file = self.agent_dir / config.AGENT_INCIDENTS_FILE
        self.runtime_lock_path = self.agent_dir / config.AGENT_RUNTIME_LOCK_FILE

    def ensure_dir(self) -> None:
        self.agent_dir.mkdir(parents=True, exist_ok=True)

    def runtime_lock(self) -> FileLock:
        self.ensure_dir()
        return FileLock(str(self.runtime_lock_path))

    def read_state(self) -> AgentState | None:
        if not self.state_file.exists():
            return None
        return AgentState.model_validate_json(self.state_file.read_text())

    def write_state(self, state: AgentState) -> None:
        self.ensure_dir()
        tmp_file = self.state_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(state.model_dump(mode="json"), indent=2))
        tmp_file.replace(self.state_file)

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

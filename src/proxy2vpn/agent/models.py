"""Stable persisted models for the proxy2vpn agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


IncidentSeverity = Literal["low", "medium", "high"]
IncidentStatus = Literal["open", "approved", "resolved", "dismissed", "failed"]
DaemonMode = Literal["inactive", "once", "foreground", "daemon"]


class AgentStatus(BaseModel):
    """Top-level status for the local watchdog."""

    compose_path: str
    daemon_mode: DaemonMode = "inactive"
    started_at: datetime | None = None
    active_cycle_started_at: datetime | None = None
    active_cycle_phase: str | None = None
    active_cycle_service_name: str | None = None
    last_loop_at: datetime | None = None
    last_progress_at: datetime | None = None
    interval_seconds: int
    service_count: int = 0
    unhealthy_count: int = 0
    last_error: str | None = None
    llm_mode: str = "disabled"

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class ServiceSnapshot(BaseModel):
    """Most recent evaluation for a managed service."""

    service_name: str
    container_status: str
    health_score: int
    consecutive_failures: int = 0
    degraded_since: datetime | None = None
    last_check_at: datetime
    last_action: str | None = None
    last_action_result: str | None = None

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class IncidentInvestigation(BaseModel):
    """Persisted investigation summary and action plan for one incident."""

    summary: str
    findings: list[str] = Field(default_factory=list)
    log_evidence: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    investigated_at: datetime

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class AgentIncident(BaseModel):
    """Persisted incident for service failures that need attention."""

    id: str
    service_name: str
    type: str
    severity: IncidentSeverity
    status: IncidentStatus = "open"
    created_at: datetime
    updated_at: datetime
    failure_count: int = 1
    summary: str
    recommended_action: str
    approval_required: bool = False
    approved_at: datetime | None = None
    resolved_at: datetime | None = None
    human_explanation: str | None = None
    investigation: IncidentInvestigation | None = None

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class ActionRecord(BaseModel):
    """Audit log record for an action taken by the agent."""

    ts: datetime
    service_name: str
    action: str
    trigger: str
    result: str
    details: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class AgentState(BaseModel):
    """Persisted state for the local watchdog."""

    status: AgentStatus
    services: list[ServiceSnapshot] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

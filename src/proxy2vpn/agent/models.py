"""Stable persisted models for the proxy2vpn agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from proxy2vpn.agent.metrics_models import AgentMetrics, MetricSource
from proxy2vpn.core.egress import EgressCapabilities, GLUETUN_CAPABILITIES


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
    source: str = "gluetun"
    capabilities: EgressCapabilities = Field(
        default_factory=lambda: GLUETUN_CAPABILITIES
    )
    health_class: str | None = None
    failing_checks: list[str] = Field(default_factory=list)
    current_egress_ip: str | None = None
    authentication: bool | None = None
    connectivity: bool | None = None
    latency_ms: float | None = None
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

    revision: int = Field(default=0, ge=0)
    generation: int = Field(default=0, ge=0)
    source: str = "gluetun"
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

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_external_source(cls, value):
        # The first external-only incident kind predates the explicit source field.
        if (
            isinstance(value, dict)
            and "source" not in value
            and value.get("type") == "endpoint_unhealthy"
        ):
            return {**value, "source": "external_proxy"}
        return value


class ActionRecord(BaseModel):
    """Audit log record for an action taken by the agent."""

    source: MetricSource = "unknown"
    ts: datetime
    service_name: str
    action: str
    trigger: str
    result: str
    details: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class AgentState(BaseModel):
    """Persisted state for the local watchdog."""

    revision: int = Field(default=0, ge=0)
    generation: int = Field(default=0, ge=0)
    status: AgentStatus
    services: list[ServiceSnapshot] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)
    metrics: AgentMetrics = Field(default_factory=AgentMetrics)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

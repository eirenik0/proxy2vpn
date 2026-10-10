"""Persisted metrics evidence; never reconstruct counters from bounded history."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MetricSource = Literal["gluetun", "external_proxy", "unknown"]
CycleOutcome = Literal[
    "unknown",
    "running",
    "success",
    "failed",
    "cancelled",
    "partial",
    "conflict",
    "interrupted",
]
RecoveryAction = Literal["restart_tunnel", "restore", "rotate", "other"]
RecoveryResult = Literal[
    "success", "failed", "accepted", "rejected", "unsupported", "unknown"
]


class EndpointMetrics(BaseModel):
    """Observation evidence for one opaque configured endpoint identity."""

    endpoint_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: MetricSource
    last_attempt_at: datetime | None = None
    observed_at: datetime | None = None
    observation_complete: bool = False
    available: bool | None = None
    authentication: bool | None = None
    connectivity: bool | None = None
    health_ok: bool | None = None
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reported_latency_seconds: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )

    model_config = ConfigDict(validate_assignment=True, extra="forbid")


class CycleCounter(BaseModel):
    """Durable cycle finalizations grouped by a fixed outcome vocabulary."""

    cycle_outcome: CycleOutcome
    metric_count: int = Field(default=0, ge=0)


class RecoveryCounter(BaseModel):
    """Committed request/audit outcomes, independently of subsequent rechecks."""

    source: MetricSource
    recovery_action: RecoveryAction
    recovery_result: RecoveryResult
    metric_count: int = Field(default=0, ge=0)


class AgentMetrics(BaseModel):
    """Independent producer evidence, including incomplete cycles after death."""

    deployment_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    initialized_at: datetime | None = None
    cycle_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    cycle_outcome: CycleOutcome = "unknown"
    cycles_attempted: int = Field(default=0, ge=0)
    monitoring_resets: int = Field(default=0, ge=0)
    cycle_counters: list[CycleCounter] = Field(default_factory=list)
    recovery_counters: list[RecoveryCounter] = Field(default_factory=list)
    endpoint_observations: list[EndpointMetrics] = Field(default_factory=list)

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    def count_cycle(self, outcome: CycleOutcome) -> None:
        counter = next(
            (c for c in self.cycle_counters if c.cycle_outcome == outcome), None
        )
        if counter is None:
            counter = CycleCounter(cycle_outcome=outcome)
            self.cycle_counters.append(counter)
        counter.metric_count += 1

    def clear_monitoring(self) -> "AgentMetrics":
        reset = self.model_copy(deep=True)
        if reset.cycle_run_id is not None:
            reset.count_cycle("interrupted")
        reset.monitoring_resets += 1
        reset.cycle_run_id = None
        reset.last_attempt_at = None
        reset.last_success_at = None
        reset.cycle_outcome = "unknown"
        reset.endpoint_observations = []
        return reset

    def count_recovery(self, action, services) -> None:
        # These are committed audit outcomes, not exactly-once physical requests.
        if action.action == "investigate":
            return
        source = action.source
        kind = (
            action.action
            if action.action in {"restart_tunnel", "restore", "rotate"}
            else "other"
        )
        result = action.details.get("runtime_request_result", action.result)
        if (
            action.details.get("cancelled") == "true"
            and "runtime_request_result" not in action.details
        ):
            result = "unknown"
        if result not in {"success", "failed", "accepted", "rejected", "unsupported"}:
            result = "unknown"
        counter = next(
            (
                c
                for c in self.recovery_counters
                if (c.source, c.recovery_action, c.recovery_result)
                == (source, kind, result)
            ),
            None,
        )
        if counter is None:
            counter = RecoveryCounter(
                source=source, recovery_action=kind, recovery_result=result
            )
            self.recovery_counters.append(counter)
        counter.metric_count += 1

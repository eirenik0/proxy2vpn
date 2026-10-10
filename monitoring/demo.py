"""Isolated synthetic producer for the local monitoring demonstration."""

from datetime import datetime, timezone
from pathlib import Path
import os
import time

from proxy2vpn.agent.metrics_models import EndpointMetrics
from proxy2vpn.agent.models import AgentState, AgentStatus
from proxy2vpn.agent.state import AgentStateStore

root = Path("/evidence")
compose = root / "compose.yml"
compose.write_text("services: {}\n")
store = AgentStateStore(compose)
while True:
    now = datetime.now(timezone.utc)
    state = store.read_state() or AgentState(
        status=AgentStatus(compose_path=str(compose), interval_seconds=15)
    )
    metrics = state.metrics
    metrics.initialized_at = metrics.initialized_at or now
    metrics.deployment_id = store.sanitizer().metric_identity(
        "demo", domain="deployment"
    )
    metrics.cycles_attempted += 1
    metrics.last_attempt_at = now
    metrics.last_success_at = now
    metrics.cycle_outcome = "success"
    metrics.count_cycle("success")
    metrics.endpoint_observations = [
        EndpointMetrics(
            endpoint_id=store.sanitizer().metric_identity(source, domain="endpoint"),
            source=source,
            last_attempt_at=now,
            observed_at=now,
            observation_complete=True,
            available=True,
            authentication=True if source == "external_proxy" else None,
            connectivity=os.environ.get("DEMO_CONNECTIVITY_FAILURE") != "1",
            health_ok=os.environ.get("DEMO_CONNECTIVITY_FAILURE") != "1",
            duration_seconds=0.05,
        )
        for source in ("gluetun", "external_proxy")
    ]
    store.write_state(state)
    time.sleep(15)

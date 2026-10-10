"""Metrics counters, freshness and read-only collection boundaries."""

from datetime import datetime, timedelta, timezone

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.metrics import collect_metrics
from proxy2vpn.agent.metrics_models import EndpointMetrics
from proxy2vpn.agent.models import ActionRecord, AgentState, AgentStatus
from proxy2vpn.agent.state import AgentStateStore

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def setup(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    store = AgentStateStore(compose, AgentSettings(action_history_limit=1))
    state = AgentState(
        status=AgentStatus(compose_path=str(compose), interval_seconds=30)
    )
    state.metrics.deployment_id = "a" * 64
    state.metrics.initialized_at = NOW
    store.write_state(state)
    return store, state


# @lat: [[lat.md/metrics-tests#Metrics Tests#Durable Request Counters]]
def test_counters_survive_history_truncation_stale_action_merge_and_reset(tmp_path):
    store, state = setup(tmp_path)
    stale = state.model_copy(deep=True)
    for target in (state, stale):
        action = ActionRecord(
            ts=NOW,
            service_name="vpn",
            action="restart_tunnel",
            trigger="manual",
            result="failed",
            details={"runtime_request_result": "success"},
        )
        target.actions.append(action)
        store.append_action(target, action)
    persisted = store.read_state()
    assert persisted.metrics.recovery_counters[0].metric_count == 2
    assert persisted.metrics.recovery_counters[0].recovery_result == "success"
    assert len(persisted.actions) == 1
    store.reset_monitoring_state()
    reset = store.read_state()
    assert reset.metrics.monitoring_resets == 1
    assert reset.metrics.recovery_counters == persisted.metrics.recovery_counters
    assert not reset.actions


# @lat: [[lat.md/metrics-tests#Metrics Tests#Freshness And Unknown Evidence]]
def test_freshness_does_not_follow_scrape_or_failed_cycle(tmp_path):
    store, state = setup(tmp_path)
    state.metrics.last_success_at = NOW
    state.metrics.cycle_outcome = "success"
    state.metrics.endpoint_observations = [
        EndpointMetrics(
            endpoint_id="b" * 64,
            source="external_proxy",
            observed_at=NOW,
            observation_complete=True,
            available=True,
        )
    ]
    store.write_state(state)
    before = store.state_file.read_bytes()
    fresh = collect_metrics(store.compose_file, store.settings, now=NOW)
    assert 'proxy2vpn_cycle_success_fresh{deployment="' + "a" * 64 + '"} 1.0' in fresh
    assert "endpoint_authentication_known" in fresh
    assert "endpoint_authentication{" not in fresh
    old = collect_metrics(
        store.compose_file, store.settings, now=NOW + timedelta(seconds=121)
    )
    assert 'proxy2vpn_cycle_success_fresh{deployment="' + "a" * 64 + '"} 0.0' in old
    assert store.state_file.read_bytes() == before
    state.metrics.cycle_outcome = "failed"
    store.write_state(state)
    failed = collect_metrics(store.compose_file, store.settings, now=NOW)
    assert 'proxy2vpn_cycle_success_fresh{deployment="' + "a" * 64 + '"} 0.0' in failed


# @lat: [[lat.md/metrics-tests#Metrics Tests#Read Only Failure Boundaries]]
def test_missing_corrupt_pending_collection_never_repairs_or_creates(tmp_path):
    compose = tmp_path / "compose.yml"
    settings = AgentSettings()
    missing = collect_metrics(compose, settings, now=NOW)
    assert "proxy2vpn_state_present 0.0" in missing
    assert not (tmp_path / settings.state_dirname).exists()
    store, state = setup(tmp_path)
    store.state_file.write_text("{broken")
    before = store.state_file.read_bytes()
    assert "proxy2vpn_exporter_collection_success 0.0" in collect_metrics(
        compose, settings
    )
    assert store.state_file.read_bytes() == before
    store.journal_path.write_text("pending")
    assert "proxy2vpn_exporter_collection_success 0.0" in collect_metrics(
        compose, settings
    )
    assert store.journal_path.read_text() == "pending"


# @lat: [[lat.md/metrics-tests#Metrics Tests#Cycle Fencing]]
def test_conflict_finalization_preserves_concurrent_state_and_reset_fences(tmp_path):
    store, state = setup(tmp_path)
    state.metrics.cycle_run_id = "c" * 32
    state.metrics.cycle_outcome = "running"
    store.write_state(state)
    current = store.read_state()
    current.status.service_count = 17
    store.write_state(current)
    store.finish_metric_cycle(state.generation, state.metrics.cycle_run_id, "conflict")
    result = store.read_state()
    assert result.status.service_count == 17
    assert result.metrics.cycle_counters[0].cycle_outcome == "conflict"
    assert result.metrics.cycle_run_id is None
    store.reset_monitoring_state()
    store.read_state()  # Normal storage reads may normalize JSON formatting.
    before = store.state_file.read_bytes()
    store.finish_metric_cycle(state.generation, "c" * 32, "success")
    assert store.state_file.read_bytes() == before


# @lat: [[lat.md/metrics-tests#Metrics Tests#Renamed Endpoints]]
def test_rotation_invalidates_old_endpoint_observation(tmp_path):
    from proxy2vpn.agent.runtime import AgentWatchdog

    store, state = setup(tmp_path)
    sanitizer = store.sanitizer()
    old_id = sanitizer.metric_identity("gluetun:old", domain="endpoint")
    new_id = sanitizer.metric_identity("gluetun:new", domain="endpoint")
    state.metrics.endpoint_observations = [
        EndpointMetrics(
            endpoint_id=old_id,
            source="gluetun",
            observed_at=NOW,
            observation_complete=True,
            available=False,
        )
    ]
    watchdog = AgentWatchdog(store.compose_file, store=store)
    watchdog._rename_metric_endpoint(state, "old", "new")
    assert len(state.metrics.endpoint_observations) == 1
    assert state.metrics.endpoint_observations[0].endpoint_id == new_id
    assert state.metrics.endpoint_observations[0].observed_at is None


# @lat: [[lat.md/metrics-tests#Metrics Tests#Failed Counter Commit]]
def test_failed_action_commit_cannot_double_increment_on_retry(tmp_path, monkeypatch):
    import pytest

    store, state = setup(tmp_path)
    action = ActionRecord(
        source="gluetun",
        ts=NOW,
        service_name="vpn",
        action="restart_tunnel",
        trigger="manual",
        result="failed",
        details={"cancelled": "true"},
    )
    state.actions.append(action)
    original = store.write_state

    def fail(candidate):
        raise OSError("write failed")

    monkeypatch.setattr(store, "write_state", fail)
    with pytest.raises(OSError):
        store.append_action(state, action)
    assert state.metrics.recovery_counters == []
    monkeypatch.setattr(store, "write_state", original)
    store.append_action(state, action)
    result = store.read_state().metrics.recovery_counters
    assert result[0].metric_count == 1
    assert result[0].recovery_result == "unknown"
    assert result[0].source == "gluetun"


# @lat: [[lat.md/metrics-tests#Metrics Tests#Partial Probe Persistence]]
def test_completed_probe_survives_batch_cancellation(tmp_path, monkeypatch):
    import asyncio
    from proxy2vpn.agent.runtime import AgentWatchdog
    from proxy2vpn.core.external_proxy import ExternalProxyEndpoint, ProxyConnection
    from proxy2vpn.core.services.health_assessment import HealthAssessment

    store, state = setup(tmp_path)
    watchdog = AgentWatchdog(store.compose_file, store=store)
    endpoints = [
        ExternalProxyEndpoint(
            id=name, connection=ProxyConnection(host="127.0.0.1", port=8888)
        )
        for name in ("first", "hung")
    ]
    watchdog._record_metric_inventory(state, endpoints)
    assessor = watchdog._health_assessor
    assessor.observation_callback = lambda result: watchdog._record_metric_observation(
        state, result
    )

    async def scenario():
        completed = asyncio.Event()

        async def fake(service, **kwargs):
            if service.name == "hung":
                await asyncio.Event().wait()
            return HealthAssessment(
                service_name=service.name,
                assessed_at=NOW,
                source="external_proxy",
                container_status="not_applicable",
                health_score=100,
                health_class="healthy",
                available=True,
            )

        monkeypatch.setattr(assessor, "_assess_service", fake)

        def progress(name):
            if name == "first":
                completed.set()

        task = asyncio.create_task(
            assessor.assess_services(endpoints, progress_callback=progress)
        )
        await asyncio.wait_for(completed.wait(), timeout=2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    rows = store.read_state().metrics.endpoint_observations
    assert sum(row.observed_at is not None for row in rows) == 1
    assert sum(row.observation_complete for row in rows) == 1
    assert (
        next(row for row in rows if row.observed_at is not None).duration_seconds
        is not None
    )


# @lat: [[lat.md/metrics-tests#Metrics Tests#HTTP And CLI Isolation]]
def test_exporter_http_and_cli_read_without_probe_or_storage_changes(
    tmp_path, monkeypatch
):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    from typer.testing import CliRunner
    import proxy2vpn.agent.metrics as module
    from proxy2vpn.cli.main import app

    store, state = setup(tmp_path)
    before = store.state_file.read_bytes()
    servers = []

    def factory(address, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        servers.append(server)
        return server

    monkeypatch.setattr(module, "ThreadingHTTPServer", factory)
    thread = threading.Thread(
        target=module.serve_metrics,
        args=(store.compose_file, store.settings),
        daemon=True,
    )
    thread.start()
    # An Event avoids arbitrary sleeps while waiting for the factory's result.
    import time

    deadline = time.monotonic() + 2
    while not servers and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert servers
    server = servers[0]
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/metrics", timeout=2
        ) as response:
            assert response.status == 200
            assert "text/plain" in response.headers["Content-Type"]
            assert b"proxy2vpn_exporter_collection_success 1.0" in response.read()
    finally:
        server.shutdown()
        thread.join(timeout=2)
    result = CliRunner().invoke(
        app, ["--compose-file", str(store.compose_file), "agent", "metrics", "--once"]
    )
    assert result.exit_code == 0, result.output
    assert "proxy2vpn_exporter_collection_success 1.0" in result.output
    assert store.state_file.read_bytes() == before


# @lat: [[lat.md/metrics-tests#Metrics Tests#Corrupt Metric Structure]]
def test_rendering_failure_and_duplicate_series_discard_partial_output(tmp_path):
    import json

    store, state = setup(tmp_path)
    payload = json.loads(store.state_file.read_text())
    payload["metrics"]["cycles_attempted"] = 10**400
    store.state_file.write_text(json.dumps(payload))
    output = collect_metrics(store.compose_file, store.settings)
    assert "proxy2vpn_exporter_collection_success 0.0" in output
    assert "state_present" not in output
    payload["metrics"]["cycles_attempted"] = 0
    row = EndpointMetrics(endpoint_id="b" * 64, source="external_proxy")
    payload["metrics"]["endpoint_observations"] = [row.model_dump(mode="json")] * 2
    store.state_file.write_text(json.dumps(payload))
    output = collect_metrics(store.compose_file, store.settings)
    assert "proxy2vpn_exporter_collection_success 0.0" in output
    assert "endpoint_observation" not in output


# @lat: [[lat.md/metrics-tests#Metrics Tests#Restart And Source Persistence]]
def test_store_restart_preserves_counters_and_both_endpoint_sources(tmp_path):
    store, state = setup(tmp_path)
    state.metrics.cycles_attempted = 3
    state.metrics.count_cycle("cancelled")
    state.metrics.endpoint_observations = [
        EndpointMetrics(endpoint_id=char * 64, source=source)
        for char, source in (("b", "gluetun"), ("c", "external_proxy"))
    ]
    store.write_state(state)
    restarted = AgentStateStore(store.compose_file, store.settings)
    recovered = restarted.read_state()
    assert recovered.metrics == state.metrics
    output = collect_metrics(restarted.compose_file, restarted.settings, now=NOW)
    assert 'source="gluetun"' in output
    assert 'source="external_proxy"' in output
    assert "endpoint_available{" not in output


# @lat: [[lat.md/monitoring-tests#Monitoring Tests#Current Recovery Intervention]]
def test_recovery_blocked_gauge_counts_current_incident_evidence(tmp_path):
    from proxy2vpn.agent.models import AgentIncident

    store, state = setup(tmp_path)
    incident = AgentIncident(
        id="000000000001",
        service_name="vpn",
        source="gluetun",
        type="rotation_exhausted",
        severity="high",
        status="open",
        created_at=NOW,
        updated_at=NOW,
        summary="Policy blocked",
        recommended_action="investigate",
    )
    store.append_incident(incident)
    output = collect_metrics(store.compose_file, store.settings, now=NOW)
    line = next(
        line
        for line in output.splitlines()
        if line.startswith("proxy2vpn_recovery_blocked_incidents{")
        and 'source="gluetun"' in line
    )
    assert line.endswith("1.0")
    incident.status = "resolved"
    store.append_incident(incident)
    output = collect_metrics(store.compose_file, store.settings, now=NOW)
    line = next(
        line
        for line in output.splitlines()
        if line.startswith("proxy2vpn_recovery_blocked_incidents{")
        and 'source="gluetun"' in line
    )
    assert line.endswith("0.0")

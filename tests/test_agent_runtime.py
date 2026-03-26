import asyncio
from pathlib import Path
from types import SimpleNamespace

from filelock import Timeout
import pytest

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import (
    AgentIncident,
    AgentState,
    AgentStatus,
    ServiceSnapshot,
)
from proxy2vpn.agent.llm import IncidentEnrichment
from proxy2vpn.agent.runtime import AgentWatchdog, utc_now
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.core import config
from proxy2vpn.core.services.diagnostics import DiagnosticResult
import proxy2vpn.agent.runtime as agent_runtime


def _write_agent_compose(tmp_path: Path) -> Path:
    compose_file = tmp_path / "compose.yml"
    (tmp_path / "env.test").write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    (tmp_path / config.CONTROL_AUTH_CONFIG_FILE).write_text(
        '[[roles]]\nname = "proxy2vpn"\nauth = "none"\n'
    )
    compose_file.write_text(
        """
x-vpn-base-test: &vpn-base-test
  image: qmcgaw/gluetun
  cap_add:
    - NET_ADMIN
  devices:
    - /dev/net/tun:/dev/net/tun
  env_file:
    - env.test

services:
  protonvpn-united-states-new-york:
    <<: *vpn-base-test
    ports:
      - "0.0.0.0:9999:8888/tcp"
      - "127.0.0.1:30000:8000/tcp"
    environment:
      - VPN_SERVICE_PROVIDER=protonvpn
      - SERVER_CITIES=New York
      - SERVER_COUNTRIES=United States
    labels:
      vpn.type: vpn
      vpn.port: "9999"
      vpn.control_port: "30000"
      vpn.profile: test
      vpn.provider: protonvpn
      vpn.location: New York
""".strip()
    )
    return compose_file


class DummyContainer:
    def __init__(self, status: str = "running"):
        self.status = status

    def reload(self):
        return None


def healthy_results() -> list[DiagnosticResult]:
    return [
        DiagnosticResult(
            check="connectivity",
            passed=True,
            message="VPN working",
            recommendation="",
        )
    ]


def unhealthy_results() -> list[DiagnosticResult]:
    return [
        DiagnosticResult(
            check="connectivity",
            passed=False,
            message="VPN proxy connection failed",
            recommendation="Check the service",
        )
    ]


@pytest.fixture
def agent_compose_file(tmp_path):
    return _write_agent_compose(tmp_path)


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    async def _sleep(*args, **kwargs):
        return None

    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(agent_runtime.asyncio, "sleep", _sleep)
    monkeypatch.setattr(agent_runtime.asyncio, "to_thread", _to_thread)


@pytest.fixture
def control_client_factory():
    calls = {"status": 0, "restart_tunnel": 0}

    class DummyControlClient:
        def __init__(self, base_url):
            self.base_url = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def status(self):
            calls["status"] += 1
            return SimpleNamespace(status="running")

        async def restart_tunnel(self):
            calls["restart_tunnel"] += 1
            return SimpleNamespace(status="restarted")

    return DummyControlClient, calls


def test_agent_run_once_healthy_updates_snapshots_only(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, calls = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: healthy_results(),
    )

    watchdog = AgentWatchdog(agent_compose_file)
    state = asyncio.run(watchdog.run_once())

    assert state.status.service_count == 1
    assert state.status.unhealthy_count == 0
    assert state.services[0].health_score == 100
    assert state.actions == []
    assert watchdog.store.state_file.exists()
    assert watchdog.store.load_incidents() == []
    assert calls["status"] == 1


def test_agent_first_unhealthy_cycle_restarts_tunnel(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, calls = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: unhealthy_results(),
    )

    async def fake_evaluate(service):
        return {
            "container_status": "running",
            "health_score": 100,
            "results": healthy_results(),
        }

    watchdog = AgentWatchdog(agent_compose_file)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)

    state = asyncio.run(watchdog.run_once())

    assert [action.action for action in state.actions] == ["restart_tunnel"]
    assert state.actions[0].result == "success"
    assert state.services[0].last_action == "restart_tunnel"
    assert state.services[0].health_score == 100
    assert watchdog.store.load_incidents() == []
    assert calls["restart_tunnel"] == 1


def test_agent_unhealthy_after_restart_triggers_restore(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, calls = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: unhealthy_results(),
    )

    started = {"count": 0}

    def fake_start(service, profile, force):
        started["count"] += 1
        return None

    evaluations = iter(
        [
            {
                "container_status": "running",
                "health_score": 0,
                "results": unhealthy_results(),
            },
            {
                "container_status": "running",
                "health_score": 100,
                "results": healthy_results(),
            },
        ]
    )

    async def fake_evaluate(service):
        return next(evaluations)

    monkeypatch.setattr(agent_runtime.docker_ops, "start_vpn_service", fake_start)
    watchdog = AgentWatchdog(agent_compose_file)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)

    state = asyncio.run(watchdog.run_once())

    assert [action.action for action in state.actions] == ["restart_tunnel", "restore"]
    assert state.actions[-1].result == "success"
    assert state.services[0].last_action == "restore"
    assert state.services[0].health_score == 100
    assert watchdog.store.load_incidents() == []
    assert started["count"] == 1
    assert calls["restart_tunnel"] == 1


def test_agent_missing_container_triggers_restore(agent_compose_file, monkeypatch):
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: None,
    )

    started = {"count": 0}

    def fake_start(service, profile, force):
        started["count"] += 1
        return None

    async def fake_evaluate(service):
        return {
            "container_status": "running",
            "health_score": 100,
            "results": healthy_results(),
        }

    monkeypatch.setattr(agent_runtime.docker_ops, "start_vpn_service", fake_start)
    watchdog = AgentWatchdog(agent_compose_file)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)

    state = asyncio.run(watchdog.run_once())

    assert state.actions[0].action == "restore"
    assert state.actions[0].result == "success"
    assert state.services[0].container_status == "running"
    assert state.services[0].health_score == 100
    assert started["count"] == 1


def test_agent_persistent_auth_failure_creates_high_severity_incident(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, _ = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: [
            DiagnosticResult(
                check="auth_failure",
                passed=False,
                message="Recent authentication failure detected",
                recommendation="Verify credentials",
                persistent=True,
            )
        ],
    )

    start_calls = {"count": 0}

    def fake_start(service, profile, force):
        start_calls["count"] += 1
        return None

    monkeypatch.setattr(agent_runtime.docker_ops, "start_vpn_service", fake_start)
    watchdog = AgentWatchdog(agent_compose_file)

    state = asyncio.run(watchdog.run_once())
    incidents = watchdog.store.load_incidents()

    assert state.actions == []
    assert start_calls["count"] == 0
    assert incidents[0].type == "auth_config_failure"
    assert incidents[0].severity == "high"
    assert incidents[0].approval_required is False


def test_agent_openai_enrichment_populates_human_explanation(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, _ = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: [
            DiagnosticResult(
                check="auth_failure",
                passed=False,
                message="Recent authentication failure detected",
                recommendation="Verify credentials",
                persistent=True,
            )
        ],
    )

    class DummyEnricher:
        def enrich(self, context):
            return IncidentEnrichment(
                summary=f"{context.service_name}: credentials are rejected by provider",
                human_explanation="The VPN provider is rejecting the configured credentials. Automatic restart is unlikely to help.",
            )

    watchdog = AgentWatchdog(agent_compose_file, llm_mode="openai")
    monkeypatch.setattr(watchdog, "_incident_enricher", DummyEnricher())

    asyncio.run(watchdog.run_once())
    incidents = watchdog.store.load_incidents()

    assert incidents[0].summary.endswith("credentials are rejected by provider")
    assert incidents[0].human_explanation is not None
    assert "rejecting" in incidents[0].human_explanation


def test_agent_restore_failure_creates_rotation_incident(
    agent_compose_file, monkeypatch
):
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: unhealthy_results(),
    )

    def fake_start(service, profile, force):
        raise RuntimeError("restore failed")

    async def fake_control_api(service):
        return False

    monkeypatch.setattr(agent_runtime.docker_ops, "start_vpn_service", fake_start)
    watchdog = AgentWatchdog(agent_compose_file)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)

    state = asyncio.run(watchdog.run_once())
    incidents = watchdog.store.load_incidents()

    assert state.actions[0].action == "restore"
    assert state.actions[0].result == "failed"
    assert incidents[0].recommended_action == "rotate"
    assert incidents[0].approval_required is True
    assert incidents[0].status == "open"


def test_approve_incident_rotates_once_and_resolves(agent_compose_file, monkeypatch):
    store = AgentStateStore(agent_compose_file)
    watchdog = AgentWatchdog(agent_compose_file, store=store)
    state = AgentState(
        status=AgentStatus(
            compose_path=str(agent_compose_file),
            daemon_mode="once",
            interval_seconds=AgentSettings().interval_seconds,
            llm_mode="disabled",
        ),
        services=[
            ServiceSnapshot(
                service_name="protonvpn-united-states-new-york",
                container_status="running",
                health_score=0,
                consecutive_failures=2,
                last_check_at=utc_now(),
            )
        ],
    )
    store.write_state(state)
    incident = AgentIncident(
        id="incident123",
        service_name="protonvpn-united-states-new-york",
        type="rotation_required",
        severity="medium",
        status="open",
        created_at=utc_now(),
        updated_at=utc_now(),
        failure_count=2,
        summary="Needs rotation",
        recommended_action="rotate",
        approval_required=True,
    )
    store.append_incident(incident)

    class DummyFleetManager:
        def __init__(self, compose_file):
            self.compose_file = compose_file

        async def rotate_service(self, service_name, config_obj):
            return SimpleNamespace(success=True, errors=[])

        async def close(self):
            return None

    monkeypatch.setattr(agent_runtime, "FleetStateManager", DummyFleetManager)

    resolved = asyncio.run(watchdog.approve_incident("incident123"))
    incidents = store.load_incidents()
    persisted = store.read_state()

    assert resolved.status == "resolved"
    assert incidents[0].status == "resolved"
    assert persisted is not None
    assert persisted.actions[-1].action == "rotate"
    assert persisted.actions[-1].result == "success"

    with pytest.raises(RuntimeError):
        asyncio.run(watchdog.approve_incident("incident123"))


def test_failed_incident_allows_new_rotation_incident(agent_compose_file, monkeypatch):
    store = AgentStateStore(agent_compose_file)
    failed_incident = AgentIncident(
        id="incident123",
        service_name="protonvpn-united-states-new-york",
        type="rotation_required",
        severity="medium",
        status="failed",
        created_at=utc_now(),
        updated_at=utc_now(),
        failure_count=2,
        summary="Previous rotation failed",
        recommended_action="rotate",
        approval_required=True,
    )
    store.append_incident(failed_incident)

    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: unhealthy_results(),
    )

    def fake_start(service, profile, force):
        raise RuntimeError("restore failed")

    async def fake_control_api(service):
        return False

    monkeypatch.setattr(agent_runtime.docker_ops, "start_vpn_service", fake_start)
    watchdog = AgentWatchdog(agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)

    asyncio.run(watchdog.run_once())
    incidents = store.load_incidents()

    assert incidents[0].status == "open"
    assert incidents[0].id != failed_incident.id
    assert incidents[1].status == "failed"
    assert incidents[1].id == failed_incident.id


def test_state_persists_across_watchdog_restarts(agent_compose_file, monkeypatch):
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: unhealthy_results(),
    )

    def fake_start(service, profile, force):
        raise RuntimeError("restore failed")

    async def fake_control_api(service):
        return False

    monkeypatch.setattr(agent_runtime.docker_ops, "start_vpn_service", fake_start)
    watchdog = AgentWatchdog(agent_compose_file)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)

    asyncio.run(watchdog.run_once())

    second_watchdog = AgentWatchdog(agent_compose_file)
    state = second_watchdog.store.read_state()
    incidents = second_watchdog.store.load_incidents()

    assert state is not None
    assert state.status.unhealthy_count == 1
    assert len(incidents) == 1
    assert incidents[0].status == "open"


def test_runtime_lock_prevents_duplicate_watchdogs(agent_compose_file):
    first = AgentStateStore(agent_compose_file).runtime_lock()
    second = AgentStateStore(agent_compose_file).runtime_lock()
    try:
        first.acquire(timeout=0)
        with pytest.raises(Timeout):
            second.acquire(timeout=0)
    finally:
        first.release()

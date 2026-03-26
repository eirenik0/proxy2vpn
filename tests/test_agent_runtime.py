import asyncio
from asyncio import threads as asyncio_threads
from pathlib import Path
from types import SimpleNamespace

from filelock import Timeout
import pytest

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import (
    ActionRecord,
    AgentIncident,
    AgentState,
    AgentStatus,
    ServiceSnapshot,
)
from proxy2vpn.agent.llm import IncidentEnrichment, InvestigationPlan
from proxy2vpn.agent.runtime import AgentWatchdog, utc_now
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.fleet_state_manager import RotationChange
from proxy2vpn.core import config
from proxy2vpn.core.models import ServiceCredentials
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


def _write_shared_profile_agent_compose(tmp_path: Path) -> Path:
    compose_file = tmp_path / "compose.yml"
    (tmp_path / "env.test").write_text(
        "VPN_SERVICE_PROVIDER=protonvpn\n"
        "OPENVPN_USER=test-user\n"
        "OPENVPN_PASSWORD=test-pass\n"
    )
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
  protonvpn-united-states-boston:
    <<: *vpn-base-test
    ports:
      - "0.0.0.0:9998:8888/tcp"
      - "127.0.0.1:30001:8000/tcp"
    environment:
      - VPN_SERVICE_PROVIDER=protonvpn
      - SERVER_CITIES=Boston
      - SERVER_COUNTRIES=United States
    labels:
      vpn.type: vpn
      vpn.port: "9998"
      vpn.control_port: "30001"
      vpn.profile: test
      vpn.provider: protonvpn
      vpn.location: Boston
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


@pytest.fixture
def shared_profile_agent_compose_file(tmp_path):
    return _write_shared_profile_agent_compose(tmp_path)


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


def test_agent_run_once_executes_sync_diagnostics_off_event_loop(
    agent_compose_file, monkeypatch
):
    monkeypatch.setattr(agent_runtime.asyncio, "to_thread", asyncio_threads.to_thread)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )

    def fake_analyze_container_logs(*args, **kwargs):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return healthy_results()

    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        fake_analyze_container_logs,
    )

    watchdog = AgentWatchdog(agent_compose_file)
    state = asyncio.run(watchdog.run_once())

    assert state.status.unhealthy_count == 0
    assert state.services[0].health_score == 100
    assert watchdog.store.load_incidents() == []


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


def test_agent_persistent_auth_failure_with_healthy_shared_profile_restarts_tunnel(
    shared_profile_agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, calls = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )

    async def fake_analyze(service_name, analyzer, lines=20, timeout=5):
        if service_name == "protonvpn-united-states-new-york":
            return [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials",
                    persistent=True,
                )
            ]
        return healthy_results()

    async def fake_evaluate(service):
        return {
            "container_status": "running",
            "health_score": 100,
            "results": healthy_results(),
        }

    watchdog = AgentWatchdog(shared_profile_agent_compose_file)
    monkeypatch.setattr(watchdog, "_analyze_service_logs", fake_analyze)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)

    state = asyncio.run(watchdog.run_once())
    incidents = watchdog.store.load_incidents()

    assert incidents == []
    assert state.services[0].service_name == "protonvpn-united-states-new-york"
    assert state.services[0].health_score == 100
    assert state.services[0].last_action == "restart_tunnel"
    assert state.services[0].last_action_result == "success"
    assert state.actions[0].trigger == "isolated_auth_failure"
    assert calls["restart_tunnel"] == 1


def test_agent_open_auth_incident_blocks_repeated_isolated_auth_restart(
    shared_profile_agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, calls = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )

    store = AgentStateStore(shared_profile_agent_compose_file)
    store.write_state(
        AgentState(
            status=AgentStatus(
                compose_path=str(shared_profile_agent_compose_file),
                daemon_mode="once",
                interval_seconds=AgentSettings().interval_seconds,
                llm_mode="disabled",
            ),
            services=[
                ServiceSnapshot(
                    service_name="protonvpn-united-states-new-york",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=3,
                    last_check_at=utc_now(),
                ),
                ServiceSnapshot(
                    service_name="protonvpn-united-states-boston",
                    container_status="running",
                    health_score=100,
                    consecutive_failures=0,
                    last_check_at=utc_now(),
                ),
            ],
        )
    )
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="auth_config_failure",
            severity="high",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=3,
            summary="Persistent authentication failure detected",
            recommended_action="investigate",
            approval_required=False,
        )
    )

    async def fake_analyze(service_name, analyzer, lines=20, timeout=5):
        if service_name == "protonvpn-united-states-new-york":
            return [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials",
                    persistent=True,
                )
            ]
        return healthy_results()

    watchdog = AgentWatchdog(shared_profile_agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_analyze_service_logs", fake_analyze)

    state = asyncio.run(watchdog.run_once())
    incidents = watchdog.store.load_incidents()

    assert calls["restart_tunnel"] == 0
    assert state.actions == []
    assert incidents[0].status == "open"


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


def test_investigate_incident_persists_action_plan(agent_compose_file, monkeypatch):
    store = AgentStateStore(agent_compose_file)
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="auth_config_failure",
            severity="high",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=3,
            summary="Profile configuration issue detected",
            recommended_action="investigate",
            approval_required=False,
        )
    )

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
                check="config_error",
                passed=False,
                message="Recent configuration issue detected",
                recommendation="Verify profile env file and service settings.",
                persistent=True,
            ),
            DiagnosticResult(
                check="connectivity",
                passed=False,
                message="VPN proxy connection failed",
                recommendation="Check container status and port accessibility.",
            ),
        ],
    )

    async def fake_control_api(service):
        return True

    watchdog = AgentWatchdog(agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)

    investigated = asyncio.run(watchdog.investigate_incident("incident123"))
    persisted = store.load_incidents()[0]

    assert investigated.investigation is not None
    assert persisted.investigation is not None
    assert "configuration" in investigated.investigation.summary.lower()
    assert any(
        "OPENVPN_PASSWORD is missing" in finding
        for finding in investigated.investigation.findings
    )
    assert any(
        "proxy2vpn vpn update protonvpn-united-states-new-york" in step
        for step in investigated.investigation.action_plan
    )


def test_investigate_incident_rejects_closed_incidents(agent_compose_file):
    store = AgentStateStore(agent_compose_file)
    incident = AgentIncident(
        id="incident123",
        service_name="protonvpn-united-states-new-york",
        type="auth_config_failure",
        severity="high",
        status="dismissed",
        created_at=utc_now(),
        updated_at=utc_now(),
        failure_count=2,
        summary="Dismissed incident",
        recommended_action="investigate",
        approval_required=False,
    )
    store.append_incident(incident)
    watchdog = AgentWatchdog(agent_compose_file, store=store)

    with pytest.raises(RuntimeError, match="already closed"):
        asyncio.run(watchdog.investigate_incident("incident123"))

    persisted = store.load_incidents()[0]
    assert persisted.status == "dismissed"
    assert persisted.updated_at == incident.updated_at
    assert persisted.investigation is None


def test_investigation_validation_honors_service_proxy_overrides(
    agent_compose_file, tmp_path
):
    env_path = tmp_path / "override.env"
    env_path.write_text("VPN_SERVICE_PROVIDER=protonvpn\nHTTPPROXY=on\n")

    manager = ComposeManager(agent_compose_file)
    service, profile = manager.get_service_with_profile(
        "protonvpn-united-states-new-york"
    )
    profile.env_file = str(env_path)
    profile._base_dir = tmp_path
    service.credentials = ServiceCredentials(
        httpproxy_user="override-user",
        httpproxy_password="override-pass",
    )

    watchdog = AgentWatchdog(agent_compose_file)
    errors = watchdog._validate_profile_for_investigation(profile, service)

    assert "HTTPPROXY_USER is required when HTTPPROXY=on." not in errors
    assert "HTTPPROXY_PASSWORD is required when HTTPPROXY=on." not in errors


def test_openai_investigation_replaces_fallback_plan(agent_compose_file, monkeypatch):
    store = AgentStateStore(agent_compose_file)
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="auth_config_failure",
            severity="high",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=2,
            summary="Profile configuration issue detected",
            recommended_action="investigate",
            approval_required=False,
        )
    )

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

    async def fake_control_api(service):
        return True

    class DummyInvestigator:
        def investigate(self, context):
            return InvestigationPlan(
                summary=f"{context.service_name}: provider credentials need rotation",
                findings=["Provider rejected the stored credentials."],
                action_plan=[
                    "Update the profile credentials.",
                    "Run proxy2vpn vpn update for the service.",
                ],
            )

    watchdog = AgentWatchdog(agent_compose_file, llm_mode="openai", store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)
    monkeypatch.setattr(watchdog, "_incident_investigator", DummyInvestigator())

    investigated = asyncio.run(watchdog.investigate_incident("incident123"))

    assert investigated.investigation is not None
    assert investigated.investigation.summary.endswith(
        "provider credentials need rotation"
    )
    assert (
        investigated.investigation.action_plan[0] == "Update the profile credentials."
    )


def test_investigate_incident_deprioritizes_accountwide_issue_when_shared_profile_is_healthy(
    shared_profile_agent_compose_file, monkeypatch
):
    store = AgentStateStore(shared_profile_agent_compose_file)
    store.write_state(
        AgentState(
            status=AgentStatus(
                compose_path=str(shared_profile_agent_compose_file),
                daemon_mode="once",
                interval_seconds=AgentSettings().interval_seconds,
                llm_mode="disabled",
            ),
            services=[
                ServiceSnapshot(
                    service_name="protonvpn-united-states-new-york",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=3,
                    last_check_at=utc_now(),
                ),
                ServiceSnapshot(
                    service_name="protonvpn-united-states-boston",
                    container_status="running",
                    health_score=100,
                    consecutive_failures=0,
                    last_check_at=utc_now(),
                ),
            ],
        )
    )
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="auth_config_failure",
            severity="high",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=3,
            summary="Persistent authentication failure detected",
            recommended_action="investigate",
            approval_required=False,
        )
    )

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

    async def fake_control_api(service):
        return True

    watchdog = AgentWatchdog(shared_profile_agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)

    investigated = asyncio.run(watchdog.investigate_incident("incident123"))

    assert investigated.investigation is not None
    assert "healthy in 1 other container" in investigated.investigation.summary
    assert any(
        "weakens suspicion of a profile-wide or account-wide provider issue" in finding
        for finding in investigated.investigation.findings
    )
    assert any(
        "Do not rotate the shared profile credentials yet" in step
        for step in investigated.investigation.action_plan
    )
    restart_step = next(
        step
        for step in investigated.investigation.action_plan
        if "proxy2vpn vpn restart-tunnel protonvpn-united-states-new-york" in step
    )
    update_step = next(
        step
        for step in investigated.investigation.action_plan
        if "proxy2vpn vpn update protonvpn-united-states-new-york" in step
    )
    assert investigated.investigation.action_plan.index(restart_step) < (
        investigated.investigation.action_plan.index(update_step)
    )


def test_investigate_incident_keeps_accountwide_suspicion_when_shared_profile_peers_are_unhealthy(
    shared_profile_agent_compose_file, monkeypatch
):
    store = AgentStateStore(shared_profile_agent_compose_file)
    store.write_state(
        AgentState(
            status=AgentStatus(
                compose_path=str(shared_profile_agent_compose_file),
                daemon_mode="once",
                interval_seconds=AgentSettings().interval_seconds,
                llm_mode="disabled",
            ),
            services=[
                ServiceSnapshot(
                    service_name="protonvpn-united-states-new-york",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=3,
                    last_check_at=utc_now(),
                ),
                ServiceSnapshot(
                    service_name="protonvpn-united-states-boston",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=2,
                    last_check_at=utc_now(),
                ),
            ],
        )
    )
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="auth_config_failure",
            severity="high",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=3,
            summary="Persistent authentication failure detected",
            recommended_action="investigate",
            approval_required=False,
        )
    )

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

    async def fake_control_api(service):
        return False

    watchdog = AgentWatchdog(shared_profile_agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)

    investigated = asyncio.run(watchdog.investigate_incident("incident123"))

    assert investigated.investigation is not None
    assert "account/profile-wide issue" in investigated.investigation.summary
    assert any(
        "supports an account/profile-wide issue" in finding
        for finding in investigated.investigation.findings
    )


def test_investigate_incident_handles_shared_profile_peer_probe_failure(
    shared_profile_agent_compose_file, monkeypatch
):
    store = AgentStateStore(shared_profile_agent_compose_file)
    store.write_state(
        AgentState(
            status=AgentStatus(
                compose_path=str(shared_profile_agent_compose_file),
                daemon_mode="once",
                interval_seconds=AgentSettings().interval_seconds,
                llm_mode="disabled",
            ),
            services=[
                ServiceSnapshot(
                    service_name="protonvpn-united-states-new-york",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=3,
                    last_check_at=utc_now(),
                )
            ],
        )
    )
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="auth_config_failure",
            severity="high",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=3,
            summary="Persistent authentication failure detected",
            recommended_action="investigate",
            approval_required=False,
        )
    )

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

    async def fake_control_api(service):
        return False

    async def fake_evaluate(service):
        if service.name == "protonvpn-united-states-boston":
            raise RuntimeError("docker inspect failed")
        return {
            "container_status": "running",
            "health_score": 0,
            "results": [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials",
                    persistent=True,
                )
            ],
        }

    watchdog = AgentWatchdog(shared_profile_agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)

    investigated = asyncio.run(watchdog.investigate_incident("incident123"))
    persisted = store.load_incidents()[0]

    assert investigated.investigation is not None
    assert persisted.investigation is not None
    assert any(
        "Peer evidence is incomplete" in finding
        for finding in investigated.investigation.findings
    )


def test_investigate_incident_does_not_infer_accountwide_auth_issue_from_generic_unhealthy_peer(
    shared_profile_agent_compose_file, monkeypatch
):
    store = AgentStateStore(shared_profile_agent_compose_file)
    store.write_state(
        AgentState(
            status=AgentStatus(
                compose_path=str(shared_profile_agent_compose_file),
                daemon_mode="once",
                interval_seconds=AgentSettings().interval_seconds,
                llm_mode="disabled",
            ),
            services=[
                ServiceSnapshot(
                    service_name="protonvpn-united-states-new-york",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=3,
                    last_check_at=utc_now(),
                ),
                ServiceSnapshot(
                    service_name="protonvpn-united-states-boston",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=2,
                    last_check_at=utc_now(),
                ),
            ],
        )
    )
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="auth_config_failure",
            severity="high",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=3,
            summary="Persistent authentication failure detected",
            recommended_action="investigate",
            approval_required=False,
        )
    )

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

    async def fake_control_api(service):
        return False

    async def fake_evaluate(service):
        if service.name == "protonvpn-united-states-boston":
            return {
                "container_status": "running",
                "health_score": 0,
                "results": unhealthy_results(),
            }
        return {
            "container_status": "running",
            "health_score": 0,
            "results": [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials",
                    persistent=True,
                )
            ],
        }

    watchdog = AgentWatchdog(shared_profile_agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)

    investigated = asyncio.run(watchdog.investigate_incident("incident123"))

    assert investigated.investigation is not None
    assert "account/profile-wide issue" not in investigated.investigation.summary
    assert any(
        "does not show the same auth/config failure" in finding
        for finding in investigated.investigation.findings
    )


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
    captured_config = {}

    class DummyFleetManager:
        def __init__(self, compose_file):
            self.compose_file = compose_file

        async def rotate_service(self, service_name, config_obj):
            captured_config["criteria"] = config_obj.criteria
            return SimpleNamespace(
                success=True,
                errors=[],
                rotation_changes=[
                    RotationChange(
                        requested_service_name=service_name,
                        final_service_name="protonvpn-united-states-boston",
                        old_location="New York",
                        new_location="Boston",
                        candidate_locations=["Boston", "Chicago"],
                        attempted_locations=["Boston"],
                    )
                ],
            )

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
    assert persisted.actions[-1].service_name == "protonvpn-united-states-boston"
    assert (
        persisted.actions[-1].details["requested_service_name"]
        == "protonvpn-united-states-new-york"
    )
    assert (
        persisted.actions[-1].details["final_service_name"]
        == "protonvpn-united-states-boston"
    )
    assert persisted.actions[-1].details["old_location"] == "New York"
    assert persisted.actions[-1].details["new_location"] == "Boston"
    assert persisted.actions[-1].details["candidate_locations"] == "Boston, Chicago"
    assert persisted.actions[-1].details["attempted_locations"] == "Boston"
    assert persisted.services[0].service_name == "protonvpn-united-states-boston"
    assert persisted.services[0].last_action == "rotate"
    assert captured_config["criteria"] == agent_runtime.RotationCriteria.PERFORMANCE

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


def test_investigation_context_keeps_rotation_history_after_service_rename(
    agent_compose_file, monkeypatch
):
    store = AgentStateStore(agent_compose_file)
    store.write_state(
        AgentState(
            status=AgentStatus(
                compose_path=str(agent_compose_file),
                daemon_mode="once",
                interval_seconds=AgentSettings().interval_seconds,
                llm_mode="disabled",
            ),
            services=[
                ServiceSnapshot(
                    service_name="protonvpn-united-states-boston",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=2,
                    last_check_at=utc_now(),
                    last_action="rotate",
                    last_action_result="success",
                )
            ],
            actions=[
                ActionRecord(
                    ts=utc_now(),
                    service_name="protonvpn-united-states-new-york",
                    action="rotate",
                    trigger="manual_approval",
                    result="success",
                    details={
                        "requested_service_name": "protonvpn-united-states-new-york",
                        "final_service_name": "protonvpn-united-states-boston",
                        "old_location": "New York",
                        "new_location": "Boston",
                        "candidate_locations": "Boston, Chicago",
                        "attempted_locations": "Boston",
                    },
                )
            ],
        )
    )
    incident = AgentIncident(
        id="incident123",
        service_name="protonvpn-united-states-boston",
        type="rotation_required",
        severity="medium",
        status="open",
        created_at=utc_now(),
        updated_at=utc_now(),
        failure_count=2,
        summary="Service remained unhealthy after rotation.",
        recommended_action="investigate",
        approval_required=False,
    )
    store.append_incident(incident)

    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: None,
    )

    watchdog = AgentWatchdog(agent_compose_file, store=store)
    context = asyncio.run(watchdog._build_investigation_context(incident))

    assert len(context.recent_actions) == 1
    assert (
        context.recent_actions[0]["requested_service_name"]
        == "protonvpn-united-states-new-york"
    )
    assert (
        context.recent_actions[0]["final_service_name"]
        == "protonvpn-united-states-boston"
    )
    assert context.recent_actions[0]["old_location"] == "New York"
    assert context.recent_actions[0]["new_location"] == "Boston"
    assert context.recent_actions[0]["candidate_locations"] == "Boston, Chicago"
    assert context.recent_actions[0]["attempted_locations"] == "Boston"


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

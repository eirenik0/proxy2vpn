import asyncio
from asyncio import threads as asyncio_threads
from datetime import timedelta
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
from proxy2vpn.core.models import VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticResult
import proxy2vpn.core.services.health_assessment as health_assessment
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


def tls_unhealthy_results() -> list[DiagnosticResult]:
    return [
        DiagnosticResult(
            check="tls_error",
            passed=False,
            message="Recent TLS or certificate issue detected",
            recommendation="Check certificates and TLS settings.",
        )
    ]


def persistent_route_results() -> list[DiagnosticResult]:
    return [
        DiagnosticResult(
            check="route_error",
            passed=False,
            message="Recent OpenVPN route setup issue detected",
            recommendation="Inspect duplicate or stale routes on tun0.",
            persistent=True,
        ),
        DiagnosticResult(
            check="connectivity",
            passed=False,
            message="VPN proxy connection failed",
            recommendation="Check container status and port accessibility.",
        ),
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
        def __init__(self, base_url, *args, **kwargs):
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
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda service_name, analyzer, lines=20, timeout=5, direct_ip=None: (
            [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials",
                    persistent=True,
                )
            ]
            if service_name == "protonvpn-united-states-new-york"
            else healthy_results()
        ),
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


def test_agent_run_cycle_persists_active_cycle_progress(
    agent_compose_file, monkeypatch
):
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "cleanup_orphaned_containers",
        lambda manager: [],
    )
    service = ComposeManager(agent_compose_file).list_services()[0]
    observed = {}

    async def fake_assess(services, lines=20, timeout=None, progress_callback=None):
        if progress_callback is not None:
            callback_result = progress_callback(service.name)
            if asyncio.iscoroutine(callback_result):
                await callback_result
        persisted = watchdog.store.read_state()
        observed["phase"] = persisted.status.active_cycle_phase if persisted else None
        observed["started"] = (
            persisted.status.active_cycle_started_at if persisted else None
        )
        observed["service_name"] = (
            persisted.status.active_cycle_service_name if persisted else None
        )
        observed["progress"] = persisted.status.last_progress_at if persisted else None
        return {
            service.name: health_assessment.HealthAssessment(
                service_name=service.name,
                assessed_at=utc_now(),
                container_status="running",
                health_score=100,
                health_class="healthy",
                failing_checks=[],
                results=healthy_results(),
                control_api_reachable=True,
            )
        }

    async def fake_process_service(**kwargs):
        service = kwargs["service"]
        return ServiceSnapshot(
            service_name=service.name,
            container_status="running",
            health_score=100,
            last_check_at=utc_now(),
        )

    watchdog = AgentWatchdog(agent_compose_file)
    monkeypatch.setattr(watchdog._health_assessor, "assess_services", fake_assess)
    monkeypatch.setattr(watchdog, "_process_service", fake_process_service)

    state = asyncio.run(watchdog.run_once())

    assert observed["phase"] == "assessing_services"
    assert observed["started"] is not None
    assert observed["service_name"] == service.name
    assert observed["progress"] is not None
    assert state.status.active_cycle_phase is None
    assert state.status.active_cycle_started_at is None


def test_agent_run_cycle_persists_inflight_service_progress(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, _calls = control_client_factory
    service = ComposeManager(agent_compose_file).list_services()[0]

    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "cleanup_orphaned_containers",
        lambda manager: [],
    )
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

    watchdog = AgentWatchdog(agent_compose_file)

    async def fake_evaluate(_service):
        return {
            "container_status": "running",
            "health_score": 100,
            "results": healthy_results(),
        }

    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)

    persisted_states: list[AgentState] = []
    original_write_state = watchdog.store.write_state

    def capture_write_state(state: AgentState) -> None:
        persisted_states.append(state.model_copy(deep=True))
        original_write_state(state)

    monkeypatch.setattr(watchdog.store, "write_state", capture_write_state)

    state = asyncio.run(watchdog.run_once())

    assert any(
        persisted.status.active_cycle_phase == "processing_services"
        and persisted.status.active_cycle_service_name == service.name
        and persisted.status.last_progress_at is not None
        and any(action.action == "restart_tunnel" for action in persisted.actions)
        for persisted in persisted_states
    )
    assert state.status.active_cycle_service_name is None
    assert state.status.last_progress_at is not None


@pytest.mark.parametrize(
    ("failure_stage", "message"),
    [
        ("cleanup", "docker unavailable"),
        ("assess", "assessment failed"),
    ],
)
def test_agent_run_cycle_clears_active_cycle_state_on_setup_failure(
    agent_compose_file, monkeypatch, failure_stage, message
):
    watchdog = AgentWatchdog(agent_compose_file)

    def fail_cleanup(manager):
        raise RuntimeError(message)

    async def fail_assess(services, lines=20, timeout=None, progress_callback=None):
        raise RuntimeError(message)

    if failure_stage == "cleanup":
        monkeypatch.setattr(
            agent_runtime.docker_ops,
            "cleanup_orphaned_containers",
            fail_cleanup,
        )
    else:
        monkeypatch.setattr(
            agent_runtime.docker_ops,
            "cleanup_orphaned_containers",
            lambda manager: [],
        )
        monkeypatch.setattr(watchdog._health_assessor, "assess_services", fail_assess)

    with pytest.raises(RuntimeError, match=message):
        asyncio.run(watchdog.run_once())

    persisted = watchdog.store.read_state()
    assert persisted is not None
    assert persisted.status.active_cycle_phase is None
    assert persisted.status.active_cycle_started_at is None
    assert persisted.status.last_error == message
    assert persisted.status.last_loop_at is not None


def test_agent_treats_confirmed_connectivity_as_healthy_despite_stale_auth_logs(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, calls = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda service_name, analyzer, lines=20, timeout=5, direct_ip=None: (
            [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials",
                    persistent=True,
                )
            ]
            if service_name == "protonvpn-united-states-new-york"
            else healthy_results()
        ),
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
            ),
            DiagnosticResult(
                check="connectivity",
                passed=True,
                message="VPN working: real=2.2.2.2 vpn=1.1.1.1",
                recommendation="",
            ),
        ],
    )

    watchdog = AgentWatchdog(agent_compose_file)
    state = asyncio.run(watchdog.run_once())

    assert state.status.unhealthy_count == 0
    assert state.services[0].health_score == 85
    assert state.services[0].consecutive_failures == 0
    assert state.actions == []
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


def test_agent_run_forever_persists_daemon_mode_immediately(
    agent_compose_file, monkeypatch
):
    watchdog = AgentWatchdog(agent_compose_file)

    async def fake_run_cycle(state):
        raise KeyboardInterrupt()

    monkeypatch.setattr(watchdog, "run_cycle", fake_run_cycle)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(watchdog.run_forever("daemon"))

    persisted = watchdog.store.read_state()
    assert persisted is not None
    assert persisted.status.daemon_mode == "daemon"
    assert persisted.status.started_at is not None


def test_agent_first_unhealthy_cycle_restarts_tunnel(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, calls = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
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
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
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


def test_agent_run_cycle_cleans_orphaned_containers(agent_compose_file, monkeypatch):
    cleaned = []

    def fake_cleanup(manager):
        cleaned.append(str(manager.compose_path))
        return ["orphan-vpn"]

    async def fake_assess_services(services, progress_callback=None):
        return {}

    async def fake_process_service(
        manager,
        service,
        previous,
        state,
        incidents,
        assessment,
        assessment_map,
    ):
        return ServiceSnapshot(
            service_name=service.name,
            container_status="running",
            health_score=100,
            consecutive_failures=0,
            last_check_at=utc_now(),
        )

    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "cleanup_orphaned_containers",
        fake_cleanup,
    )

    watchdog = AgentWatchdog(agent_compose_file)
    monkeypatch.setattr(
        watchdog._health_assessor,
        "assess_services",
        fake_assess_services,
    )
    monkeypatch.setattr(watchdog, "_process_service", fake_process_service)

    state = asyncio.run(watchdog.run_once())

    assert cleaned == [str(agent_compose_file)]
    assert state.status.service_count == 1
    assert state.status.unhealthy_count == 0


def test_agent_persistent_auth_failure_creates_high_severity_incident(
    agent_compose_file, monkeypatch, control_client_factory
):
    dummy_client, _ = control_client_factory
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
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
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda service_name, analyzer, lines=20, timeout=5, direct_ip=None: (
            [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials",
                    persistent=True,
                )
            ]
            if service_name == "protonvpn-united-states-new-york"
            else healthy_results()
        ),
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
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda service_name, analyzer, lines=20, timeout=5, direct_ip=None: (
            [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials",
                    persistent=True,
                )
            ]
            if service_name == "protonvpn-united-states-new-york"
            else healthy_results()
        ),
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
    monkeypatch.setattr(health_assessment, "GluetunControlClient", dummy_client)
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
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "container_logs",
        lambda *args, **kwargs: iter(
            [
                "2026-03-27T10:19:07Z ERROR [openvpn] OpenVPN tried to add an IP route which already exists (RTNETLINK answers: File exists)",
                "2026-03-27T10:19:07Z WARN [openvpn] Previous error details: Linux route add command failed: external program exited with error status: 2",
            ]
        ),
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
    assert investigated.investigation.log_evidence == [
        "2026-03-27T10:19:07Z ERROR [openvpn] OpenVPN tried to add an IP route which already exists (RTNETLINK answers: File exists)",
        "2026-03-27T10:19:07Z WARN [openvpn] Previous error details: Linux route add command failed: external program exited with error status: 2",
    ]
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


def test_investigate_incident_uses_route_logs_to_shape_generic_action_plan(
    agent_compose_file, monkeypatch
):
    store = AgentStateStore(agent_compose_file)
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="rotation_exhausted",
            severity="medium",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=3,
            summary="Automatic remediation failed",
            recommended_action="rotate",
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
                check="route_error",
                passed=False,
                message="Recent OpenVPN route setup issue detected",
                recommendation="Inspect duplicate or stale routes on tun0.",
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
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "container_logs",
        lambda *args, **kwargs: iter(
            [
                "2026-03-27T10:19:07Z INFO [openvpn] sitnl_send: rtnl: generic error (-101): Network unreachable",
                "2026-03-27T10:19:07Z ERROR [openvpn] OpenVPN tried to add an IP route which already exists (RTNETLINK answers: File exists)",
                "2026-03-27T10:19:07Z WARN [openvpn] Previous error details: Linux route add command failed: external program exited with error status: 2",
            ]
        ),
    )

    async def fake_control_api(service):
        return True

    watchdog = AgentWatchdog(agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)

    investigated = asyncio.run(watchdog.investigate_incident("incident123"))

    assert investigated.investigation is not None
    assert "route setup errors" in investigated.investigation.summary
    assert investigated.investigation.log_evidence[0].endswith(
        "generic error (-101): Network unreachable"
    )
    assert investigated.investigation.action_plan[0].startswith(
        "Review the attached route-related log evidence"
    )


def test_select_log_evidence_prioritizes_causative_route_lines(agent_compose_file):
    watchdog = AgentWatchdog(agent_compose_file)

    selected = watchdog._select_log_evidence(
        [
            "2026-03-27T10:19:07Z ERROR [openvpn] OpenVPN tried to add an IP route which already exists (RTNETLINK answers: File exists)",
            "2026-03-27T10:19:07Z WARN [openvpn] Previous error details: Linux route add command failed: external program exited with error status: 2",
            "2026-03-27T10:19:08Z INFO [healthcheck] proxy port probe failed, retry scheduled",
            "2026-03-27T10:19:09Z INFO [vpn] tun0 statistics refreshed",
        ],
        issues=[
            DiagnosticResult(
                check="route_error",
                passed=False,
                message="Recent OpenVPN route setup issue detected",
                recommendation="Inspect duplicate or stale routes on tun0.",
                persistent=True,
            ),
            DiagnosticResult(
                check="connectivity",
                passed=False,
                message="VPN proxy connection failed",
                recommendation="Check container status and port accessibility.",
            ),
        ],
        max_lines=2,
    )

    assert selected == [
        "2026-03-27T10:19:07Z ERROR [openvpn] OpenVPN tried to add an IP route which already exists (RTNETLINK answers: File exists)",
        "2026-03-27T10:19:07Z WARN [openvpn] Previous error details: Linux route add command failed: external program exited with error status: 2",
    ]


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


def test_service_country_fallback_uses_full_country_slug(agent_compose_file):
    service = VPNService.create(
        name="protonvpn-united-kingdom-london",
        port=8080,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="London",
        environment={},
        labels={},
    )

    watchdog = AgentWatchdog(agent_compose_file)

    assert watchdog._service_country(service) == "United Kingdom"


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


def test_agent_restore_failure_tracks_degradation_before_rotation_incident(
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
    assert state.services[0].degraded_since is not None
    assert incidents == []


def test_agent_restore_failure_creates_rotation_incident_after_grace_period(
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
                    service_name="protonvpn-united-states-new-york",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=2,
                    degraded_since=utc_now() - timedelta(minutes=6),
                    last_check_at=utc_now(),
                )
            ],
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
        lambda *args, **kwargs: unhealthy_results(),
    )

    def fake_start(service, profile, force):
        raise RuntimeError("restore failed")

    async def fake_control_api(service):
        return False

    monkeypatch.setattr(agent_runtime.docker_ops, "start_vpn_service", fake_start)
    watchdog = AgentWatchdog(agent_compose_file, store=store)
    monkeypatch.setattr(watchdog, "_control_api_reachable", fake_control_api)

    state = asyncio.run(watchdog.run_once())
    incidents = watchdog.store.load_incidents()

    assert state.actions[0].action == "restore"
    assert state.actions[0].result == "failed"
    assert state.services[0].degraded_since is not None
    assert incidents[0].recommended_action == "rotate"
    assert incidents[0].approval_required is False
    assert incidents[0].status == "open"


def test_agent_tls_failure_after_restart_rotates_immediately(
    agent_compose_file, monkeypatch
):
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "cleanup_orphaned_containers",
        lambda manager: [],
    )
    service = ComposeManager(agent_compose_file).list_services()[0]
    assessment = health_assessment.HealthAssessment(
        service_name=service.name,
        assessed_at=utc_now(),
        container_status="running",
        health_score=0,
        health_class="connectivity",
        failing_checks=["connectivity"],
        results=unhealthy_results(),
        control_api_reachable=True,
    )

    watchdog = AgentWatchdog(agent_compose_file)

    async def fake_assess(services, lines=20, timeout=None, progress_callback=None):
        return {service.name: assessment}

    async def fake_evaluate(_service):
        return {
            "container_status": "running",
            "health_score": 0,
            "results": tls_unhealthy_results(),
        }

    async def fake_restart(_service, _state, trigger="first_unhealthy_cycle"):
        return "success"

    async def fake_rotate(service_name, state=None):
        return SimpleNamespace(
            success=True,
            errors=[],
            rotation_changes=[
                RotationChange(
                    requested_service_name=service_name,
                    final_service_name="protonvpn-united-states-boston",
                    old_location="United States / New York",
                    new_location="United States / Boston",
                    candidate_locations=["United States / Boston"],
                    attempted_locations=["United States / Boston"],
                )
            ],
        )

    async def fail_restore(*args, **kwargs):
        raise AssertionError("tls failures should skip restore and rotate immediately")

    monkeypatch.setattr(watchdog._health_assessor, "assess_services", fake_assess)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)
    monkeypatch.setattr(watchdog, "_restart_tunnel", fake_restart)
    monkeypatch.setattr(watchdog, "_restore_service", fail_restore)
    monkeypatch.setattr(watchdog, "_rotate_service_via_fleet", fake_rotate)

    state = asyncio.run(watchdog.run_once())

    assert state.actions[-1].action == "rotate"
    assert state.actions[-1].result == "success"
    assert state.services[0].service_name == "protonvpn-united-states-boston"
    assert state.services[0].last_action == "rotate"


def test_agent_persistent_route_failure_rotates_on_next_cycle_after_one_restore(
    agent_compose_file, monkeypatch
):
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "cleanup_orphaned_containers",
        lambda manager: [],
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops, "start_vpn_service", lambda *args: None
    )
    service = ComposeManager(agent_compose_file).list_services()[0]
    assessment = health_assessment.HealthAssessment(
        service_name=service.name,
        assessed_at=utc_now(),
        container_status="running",
        health_score=0,
        health_class="connectivity",
        failing_checks=["route_error", "connectivity"],
        results=persistent_route_results(),
        control_api_reachable=False,
    )

    watchdog = AgentWatchdog(agent_compose_file)
    rotate_calls: list[str] = []

    async def fake_assess(services, lines=20, timeout=None, progress_callback=None):
        return {service.name: assessment}

    async def fake_evaluate(_service):
        return {
            "container_status": "running",
            "health_score": 0,
            "results": persistent_route_results(),
        }

    async def fake_rotate(service_name, state=None):
        rotate_calls.append(service_name)
        return SimpleNamespace(
            success=True,
            errors=[],
            rotation_changes=[
                RotationChange(
                    requested_service_name=service_name,
                    final_service_name="protonvpn-united-states-boston",
                    old_location="United States / New York",
                    new_location="United States / Boston",
                    candidate_locations=["United States / Boston"],
                    attempted_locations=["United States / Boston"],
                )
            ],
        )

    monkeypatch.setattr(watchdog._health_assessor, "assess_services", fake_assess)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)
    monkeypatch.setattr(watchdog, "_rotate_service_via_fleet", fake_rotate)

    first_state = asyncio.run(watchdog.run_once())
    second_state = asyncio.run(watchdog.run_once())

    assert first_state.actions[-1].action == "restore"
    assert rotate_calls == [service.name]
    assert second_state.actions[-1].action == "rotate"
    assert second_state.services[0].service_name == "protonvpn-united-states-boston"


def test_rotate_service_via_fleet_passes_provider_fallback_policy(
    agent_compose_file, monkeypatch
):
    captured = {}
    settings = AgentSettings(
        fallback_countries_by_provider={"protonvpn": ["Canada", "Netherlands"]},
        probe_timeout_seconds=4,
    )

    class DummyFleetManager:
        def __init__(self, compose_file):
            self.compose_file = compose_file

        async def rotate_service(
            self, service_name, config_obj, progress_callback=None
        ):
            captured["service_name"] = service_name
            captured["fallback_countries"] = config_obj.fallback_countries
            captured["require_unique_egress_ip"] = config_obj.require_unique_egress_ip
            captured["health_check_timeout"] = config_obj.health_check_timeout
            return SimpleNamespace(success=True, errors=[], rotation_changes=[])

        async def close(self):
            return None

    monkeypatch.setattr(agent_runtime, "FleetStateManager", DummyFleetManager)

    watchdog = AgentWatchdog(agent_compose_file, settings=settings)
    asyncio.run(watchdog._rotate_service_via_fleet("protonvpn-united-states-new-york"))

    assert captured == {
        "service_name": "protonvpn-united-states-new-york",
        "fallback_countries": ["Canada", "Netherlands"],
        "require_unique_egress_ip": True,
        "health_check_timeout": 4,
    }


def test_rotate_service_via_fleet_returns_failed_result_when_service_missing(
    agent_compose_file, monkeypatch
):
    captured = {}

    class DummyFleetManager:
        def __init__(self, compose_file):
            self.compose_file = compose_file

        async def rotate_service(
            self, service_name, config_obj, progress_callback=None
        ):
            captured["service_name"] = service_name
            captured["fallback_countries"] = config_obj.fallback_countries
            return SimpleNamespace(
                success=False,
                errors=[f"Service '{service_name}' not found"],
                rotation_changes=[],
            )

        async def close(self):
            return None

    monkeypatch.setattr(agent_runtime, "FleetStateManager", DummyFleetManager)

    watchdog = AgentWatchdog(
        agent_compose_file,
        settings=AgentSettings(
            fallback_countries_by_provider={"protonvpn": ["Canada", "Netherlands"]}
        ),
    )
    result = asyncio.run(
        watchdog._rotate_service_via_fleet("protonvpn-united-states-boston")
    )

    assert result.success is False
    assert result.errors == ["Service 'protonvpn-united-states-boston' not found"]
    assert captured == {
        "service_name": "protonvpn-united-states-boston",
        "fallback_countries": [],
    }


def test_rotate_service_via_fleet_persists_progress_and_live_service_name(
    agent_compose_file, monkeypatch
):
    store = AgentStateStore(agent_compose_file)
    watchdog = AgentWatchdog(agent_compose_file, store=store)
    state = AgentState(
        status=AgentStatus(
            compose_path=str(agent_compose_file),
            daemon_mode="once",
            interval_seconds=AgentSettings().interval_seconds,
            llm_mode="disabled",
            active_cycle_started_at=utc_now(),
            active_cycle_phase="processing_services",
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
    observed = {}

    class DummyFleetManager:
        def __init__(self, compose_file):
            self.compose_file = compose_file

        async def rotate_service(
            self, service_name, config_obj, progress_callback=None
        ):
            assert progress_callback is not None
            progress_callback(
                service_name,
                "plan_selected",
                "United States / Boston",
            )
            persisted = store.read_state()
            observed["plan_service_name"] = persisted.status.active_cycle_service_name
            observed["plan_progress"] = persisted.status.last_progress_at
            progress_callback(
                service_name,
                "candidate_applied",
                "United States / Boston",
                current_live_service_name="protonvpn-united-states-boston",
            )
            persisted = store.read_state()
            observed["phase"] = persisted.status.active_cycle_phase
            observed["live_service_name"] = persisted.status.active_cycle_service_name
            observed["snapshot_service_name"] = persisted.services[0].service_name
            observed["live_progress"] = persisted.status.last_progress_at
            return SimpleNamespace(
                success=True,
                errors=[],
                rotation_changes=[
                    RotationChange(
                        requested_service_name=service_name,
                        final_service_name="protonvpn-united-states-boston",
                        old_location="United States / New York",
                        new_location="United States / Boston",
                        candidate_locations=["United States / Boston"],
                        attempted_locations=["United States / Boston"],
                    )
                ],
            )

        async def close(self):
            return None

    monkeypatch.setattr(agent_runtime, "FleetStateManager", DummyFleetManager)

    result = asyncio.run(
        watchdog._rotate_service_via_fleet(
            "protonvpn-united-states-new-york",
            state=state,
        )
    )

    assert result.success is True
    assert observed["plan_service_name"] == "protonvpn-united-states-new-york"
    assert observed["plan_progress"] is not None
    assert observed["phase"] == "processing_services"
    assert observed["live_service_name"] == "protonvpn-united-states-boston"
    assert observed["snapshot_service_name"] == "protonvpn-united-states-boston"
    assert observed["live_progress"] is not None


def test_agent_rotation_failure_clears_active_cycle_state_after_inflight_rename(
    agent_compose_file, monkeypatch
):
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "cleanup_orphaned_containers",
        lambda manager: [],
    )
    service = ComposeManager(agent_compose_file).list_services()[0]
    assessment = health_assessment.HealthAssessment(
        service_name=service.name,
        assessed_at=utc_now(),
        container_status="running",
        health_score=0,
        health_class="connectivity",
        failing_checks=["connectivity"],
        results=unhealthy_results(),
        control_api_reachable=True,
    )

    watchdog = AgentWatchdog(agent_compose_file)

    async def fake_assess(services, lines=20, timeout=None, progress_callback=None):
        return {service.name: assessment}

    async def fake_evaluate(_service):
        return {
            "container_status": "running",
            "health_score": 0,
            "results": tls_unhealthy_results(),
        }

    async def fake_restart(_service, _state, trigger="first_unhealthy_cycle"):
        return "success"

    class DummyFleetManager:
        def __init__(self, compose_file):
            self.compose_file = compose_file

        async def rotate_service(
            self, service_name, config_obj, progress_callback=None
        ):
            assert progress_callback is not None
            progress_callback(
                service_name,
                "candidate_applied",
                "United States / Boston",
                current_live_service_name="protonvpn-united-states-boston",
            )
            progress_callback(
                service_name,
                "rollback_completed",
                "United States / New York",
                current_live_service_name=service_name,
            )
            return SimpleNamespace(
                success=False,
                errors=["No rotation candidates available"],
                rotation_changes=[],
            )

        async def close(self):
            return None

    monkeypatch.setattr(watchdog._health_assessor, "assess_services", fake_assess)
    monkeypatch.setattr(watchdog, "_evaluate_health", fake_evaluate)
    monkeypatch.setattr(watchdog, "_restart_tunnel", fake_restart)
    monkeypatch.setattr(agent_runtime, "FleetStateManager", DummyFleetManager)

    state = asyncio.run(watchdog.run_once())
    persisted = watchdog.store.read_state()

    assert state.status.active_cycle_phase is None
    assert state.status.active_cycle_service_name is None
    assert persisted is not None
    assert persisted.status.active_cycle_phase is None
    assert persisted.status.active_cycle_service_name is None
    assert persisted.services[0].service_name == "protonvpn-united-states-new-york"


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
        type="rotation_exhausted",
        severity="medium",
        status="open",
        created_at=utc_now(),
        updated_at=utc_now(),
        failure_count=2,
        summary="Needs rotation",
        recommended_action="rotate",
        approval_required=False,
    )
    store.append_incident(incident)
    captured_config = {}

    class DummyFleetManager:
        def __init__(self, compose_file):
            self.compose_file = compose_file

        async def rotate_service(
            self, service_name, config_obj, progress_callback=None
        ):
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


def test_approve_incident_migrates_other_open_incidents_after_service_rename(
    agent_compose_file, monkeypatch
):
    store = AgentStateStore(agent_compose_file)
    watchdog = AgentWatchdog(agent_compose_file, store=store)
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
                    service_name="protonvpn-united-states-new-york",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=2,
                    last_check_at=utc_now(),
                )
            ],
        )
    )
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="rotation_exhausted",
            severity="medium",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=2,
            summary="Needs rotation",
            recommended_action="rotate",
            approval_required=False,
        )
    )
    store.append_incident(
        AgentIncident(
            id="incident456",
            service_name="protonvpn-united-states-new-york",
            type="auth_config_failure",
            severity="high",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=3,
            summary="Auth issue still open",
            recommended_action="investigate",
            approval_required=False,
        )
    )

    class DummyFleetManager:
        def __init__(self, compose_file):
            self.compose_file = compose_file

        async def rotate_service(
            self, service_name, config_obj, progress_callback=None
        ):
            return SimpleNamespace(
                success=True,
                errors=[],
                rotation_changes=[
                    RotationChange(
                        requested_service_name=service_name,
                        final_service_name="protonvpn-united-states-boston",
                        old_location="New York",
                        new_location="Boston",
                        candidate_locations=["Boston"],
                        attempted_locations=["Boston"],
                    )
                ],
            )

        async def close(self):
            return None

    monkeypatch.setattr(agent_runtime, "FleetStateManager", DummyFleetManager)

    asyncio.run(watchdog.approve_incident("incident123"))
    incidents = store.load_incidents()
    migrated_incident = next(item for item in incidents if item.id == "incident456")
    rotation_incident = next(item for item in incidents if item.id == "incident123")

    assert migrated_incident.service_name == "protonvpn-united-states-boston"
    assert migrated_incident.status == "open"
    assert rotation_incident.service_name == "protonvpn-united-states-new-york"
    assert rotation_incident.status == "resolved"


def test_failed_incident_allows_new_rotation_incident(agent_compose_file, monkeypatch):
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
                    service_name="protonvpn-united-states-new-york",
                    container_status="running",
                    health_score=0,
                    consecutive_failures=2,
                    degraded_since=utc_now() - timedelta(minutes=6),
                    last_check_at=utc_now(),
                )
            ],
        )
    )
    failed_incident = AgentIncident(
        id="incident123",
        service_name="protonvpn-united-states-new-york",
        type="rotation_exhausted",
        severity="medium",
        status="failed",
        created_at=utc_now(),
        updated_at=utc_now(),
        failure_count=2,
        summary="Previous rotation failed",
        recommended_action="rotate",
        approval_required=False,
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
        type="rotation_exhausted",
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


def test_degraded_since_persists_across_watchdog_restarts(
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

    asyncio.run(watchdog.run_once())

    second_watchdog = AgentWatchdog(agent_compose_file)
    state = second_watchdog.store.read_state()
    incidents = second_watchdog.store.load_incidents()

    assert state is not None
    assert state.status.unhealthy_count == 1
    assert state.services[0].degraded_since is not None
    assert incidents == []


def test_runtime_lock_prevents_duplicate_watchdogs(agent_compose_file):
    first = AgentStateStore(agent_compose_file).runtime_lock()
    second = AgentStateStore(agent_compose_file).runtime_lock()
    try:
        first.acquire(timeout=0)
        with pytest.raises(Timeout):
            second.acquire(timeout=0)
    finally:
        first.release()

"""Exercise the runtime interface without a Docker daemon or network requests."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from proxy2vpn.adapters.gluetun_runtime import GluetunRuntime
from proxy2vpn.adapters.http_client import GluetunControlClient
from proxy2vpn.core.models import Profile, VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticResult


def service():
    return VPNService.create(
        name="vpn",
        port=8080,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="Boston",
        environment={},
        labels={},
    )


class Backend:
    def __init__(self, status="running", port=None):
        self.container = (
            None
            if status == "missing"
            else SimpleNamespace(
                status=status,
                labels={"vpn.port": port} if port else {},
                reload=lambda: None,
            )
        )
        self.results = [
            DiagnosticResult(
                check="logs", passed=True, message="healthy", recommendation=""
            )
        ]
        self.logs = ["  VPN ready  "]
        self.calls = []
        self.failures = {}

    def record(self, operation, *args):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        self.calls.append((operation, args))
        if operation in self.failures:
            raise RuntimeError(self.failures[operation])

    def get_container_by_service_name(self, name, *, strict=False):
        assert strict is True
        self.record("inspection", name)
        return self.container

    def analyze_container_logs(self, *args):
        self.record("diagnostics", *args)
        return self.results

    def container_logs(self, name, **kwargs):
        self.record("logs", name, kwargs)
        return iter(self.logs)

    async def get_container_ip_async(self, container, timeout):
        if "egress_ip" in self.failures:
            raise RuntimeError(self.failures["egress_ip"])
        assert container is self.container
        self.calls.append(("egress_ip", (timeout,)))
        return "198.51.100.1"

    def start_vpn_service(self, svc, profile, force):
        self.record("restore", svc, profile, force)
        self.container = SimpleNamespace(
            status="running", labels={}, reload=lambda: None
        )
        return self.container

    def cleanup_orphaned_containers(self, manager):
        self.record("cleanup", manager)
        return ["orphan"]


class Control:
    def __init__(self, url, **kwargs):
        self.url, self.options = url, kwargs
        self.error = None
        self.closed = False
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def status(self):
        self.calls.append("status")
        if self.error is not None:
            raise self.error

    async def restart_tunnel(self):
        self.calls.append("restart_tunnel")
        if self.error is not None:
            raise self.error


def runtime(backend, *, control_error=None, direct_error=None):
    clients = []

    def factory(url, **kwargs):
        client = Control(url, **kwargs)
        client.error = control_error
        clients.append(client)
        return client

    def direct_ip(timeout):
        backend.record("direct_ip", timeout)
        if direct_error is not None:
            raise direct_error
        return "203.0.113.1"

    return GluetunRuntime(
        docker_backend=backend,
        control_client_factory=factory,
        direct_ip_fetcher=direct_ip,
        probe_timeout=3,
        control_api_timeout=1.25,
        control_api_retry_attempts=2,
    ), clients


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Missing And Stopped Containers]]
@pytest.mark.parametrize("status", ["missing", "exited", "running"])
def test_inspection_normalizes_runtime_state(status):
    backend = Backend(status)
    adapter, clients = runtime(backend)
    observed = asyncio.run(adapter.inspect(service()))
    assert observed.container_status == status
    assert observed.failure is None
    if status == "running":
        assert observed.results == backend.results
        assert observed.control_api_reachable
    else:
        assert observed.results == []
        assert not clients
    assert observed.current_egress_ip is None
    assert observed.direct_ip is None


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Probe Configuration And Threads]]
def test_inspection_collects_authenticated_probe_inputs_off_loop():
    backend = Backend(port="8080")
    adapter, clients = runtime(backend)
    observed = asyncio.run(adapter.inspect(service(), lines=8, timeout=7))
    assert observed.direct_ip == "203.0.113.1"
    assert observed.current_egress_ip == "198.51.100.1"
    assert observed.control_api_reachable
    diagnostics = next(args for op, args in backend.calls if op == "diagnostics")
    assert diagnostics[:2] == ("vpn", 8)
    assert diagnostics[3:] == (7, "203.0.113.1")
    assert ("direct_ip", (7,)) in backend.calls
    assert clients[0].url == "http://localhost:30000/v1"
    assert clients[0].options == {
        "timeout": 1.25,
        "retry_attempts": 2,
        "compose_file": Path("compose.yml"),
    }
    assert clients[0].closed


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Explicit Inspection Failures]]
@pytest.mark.parametrize("operation", ["inspection", "diagnostics"])
@pytest.mark.parametrize("message", ["unavailable", ""])
def test_inspection_failures_are_distinct_from_missing_containers(operation, message):
    backend = Backend()
    backend.failures[operation] = message
    adapter, _ = runtime(backend)
    observation = asyncio.run(adapter.inspect(service()))
    assert observation.failure == message
    assert observation.errors[operation] == message
    assert observation.container_status != "missing"


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Partial Probe Failures]]
def test_optional_probe_failures_preserve_available_diagnostics():
    backend = Backend(port="8080")
    backend.failures["egress_ip"] = "egress unavailable"
    adapter, _ = runtime(
        backend,
        control_error=RuntimeError("control failed"),
        direct_error=RuntimeError("direct failed"),
    )
    observation = asyncio.run(adapter.inspect(service()))
    assert observation.failure is None
    assert observation.results == backend.results
    assert observation.errors == {
        "control": "control failed",
        "direct_ip": "direct failed",
        "egress_ip": "egress unavailable",
    }
    assert observation.direct_ip is None
    assert observation.current_egress_ip is None
    assert not observation.control_api_reachable


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Control Outcomes And Authentication]]
@pytest.mark.parametrize("operation", ["control_status", "restart_tunnel"])
@pytest.mark.parametrize("fails", [False, True])
def test_control_results_and_auth_settings(monkeypatch, operation, fails):
    monkeypatch.setenv("GLUETUN_CONTROL_AUTH", "operator:control-test-password")
    clients = []

    class AuthenticatedControl(GluetunControlClient):
        async def __aenter__(self):
            clients.append(self)
            return self

        async def __aexit__(self, *_):
            return None

        async def status(self):
            if fails:
                raise RuntimeError("control request failed")

        async def restart_tunnel(self):
            await self.status()

    adapter = GluetunRuntime(
        control_client_factory=AuthenticatedControl,
        control_api_timeout=2.5,
        control_api_retry_attempts=1,
    )
    result = asyncio.run(getattr(adapter, operation)(service()))
    assert result.success is (not fails)
    assert result.error == ("control request failed" if fails else None)
    assert clients[0]._config.auth == ("operator", "control-test-password")
    assert clients[0]._config.timeout == 2.5
    assert clients[0]._config.retry.attempts == 1


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Restore And Recovery Outcomes]]
@pytest.mark.parametrize("fails", [False, True])
def test_restore_keeps_profile_ownership_and_returns_recovery_evidence(tmp_path, fails):
    backend = Backend("missing")
    if fails:
        backend.failures["restore"] = "recreation failed"
    profile = Profile(name="test", env_file="env.test")
    profile._base_dir = tmp_path
    adapter, _ = runtime(backend)

    async def recover():
        before = await adapter.inspect(service())
        action = await adapter.restore(service(), profile)
        after = await adapter.inspect(service())
        return before, action, after

    before, action, after = asyncio.run(recover())
    assert before.container_status == "missing"
    assert action.success is (not fails)
    assert action.error == ("recreation failed" if fails else None)
    assert after.container_status == ("missing" if fails else "running")
    assert bool(after.results) is (not fails)
    recreated = next(args for op, args in backend.calls if op == "restore")
    assert recreated[1] is profile
    assert recreated[1]._resolve_env_path() == tmp_path / "env.test"
    assert recreated[2] is True


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Diagnostic Evidence Failures]]
@pytest.mark.parametrize("failure", [None, "logs", "diagnostics"])
def test_evidence_retains_independent_log_and_diagnostic_results(failure):
    backend = Backend()
    if failure:
        backend.failures[failure] = "evidence unavailable"
    adapter, _ = runtime(backend)
    evidence = asyncio.run(adapter.collect_evidence("vpn", lines=12, timeout=4))
    assert evidence.container_status == "running"
    assert evidence.log_lines == ([] if failure == "logs" else ["VPN ready"])
    assert evidence.results == ([] if failure == "diagnostics" else backend.results)
    assert evidence.errors == ({failure: "evidence unavailable"} if failure else {})
    diagnostics = next(args for op, args in backend.calls if op == "diagnostics")
    assert diagnostics[1] == 12
    assert diagnostics[3] == 4


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Cleanup Ownership And Failure]]
@pytest.mark.parametrize("fails", [False, True])
def test_orphan_cleanup_uses_the_callers_compose_root(tmp_path, fails):
    from proxy2vpn.adapters.compose_manager import ComposeManager

    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    manager = ComposeManager(compose)
    backend = Backend()
    if fails:
        backend.failures["cleanup"] = "cleanup failed"
    adapter, _ = runtime(backend)
    result = asyncio.run(adapter.cleanup_orphans(manager))
    assert result.removed == ([] if fails else ["orphan"])
    assert result.error == ("cleanup failed" if fails else None)
    assert backend.calls[0] == ("cleanup", (manager,))


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Cancellation]]
@pytest.mark.parametrize(
    "operation",
    [
        "inspect",
        "collect_evidence",
        "control_status",
        "restart_tunnel",
        "restore",
        "cleanup_orphans",
    ],
)
def test_cancellation_is_propagated_and_control_clients_close(monkeypatch, operation):
    backend = Backend()
    adapter, clients = runtime(backend, control_error=asyncio.CancelledError())

    async def cancel_thread(*_args, **_kwargs):
        raise asyncio.CancelledError()

    if operation not in {"control_status", "restart_tunnel"}:
        monkeypatch.setattr(asyncio, "to_thread", cancel_thread)
    argument = "vpn" if operation == "collect_evidence" else service()
    arguments = (
        (argument, Profile(name="test", env_file="env.test"))
        if operation == "restore"
        else (argument,)
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(getattr(adapter, operation)(*arguments))
    assert all(client.closed for client in clients)


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Strict Docker Enumeration]]
@pytest.mark.parametrize("outage", [False, True])
def test_actual_lookup_distinguishes_docker_outage_from_absence(monkeypatch, outage):
    from proxy2vpn.adapters import docker_ops
    from proxy2vpn.core.services.health_assessment import HealthAssessmentService

    def enumerate_containers(all=False):
        assert all is True
        if outage:
            raise RuntimeError("Docker daemon unavailable")
        return []

    monkeypatch.setattr(docker_ops, "get_vpn_containers", enumerate_containers)
    adapter = GluetunRuntime()
    observation = asyncio.run(adapter.inspect(service()))
    assert observation.container_status == ("unknown" if outage else "missing")
    assert observation.failure == ("Docker daemon unavailable" if outage else None)
    assessment = asyncio.run(
        HealthAssessmentService(runtime=adapter).assess_services([service()])
    )["vpn"]
    assert assessment.health_class == ("assessment_failed" if outage else "missing")
    assert assessment.failing_checks == (
        ["assessment_error"] if outage else ["container_missing"]
    )
    # Keep the historical tolerant behavior for existing CLI/helper callers.
    assert docker_ops.get_container_by_service_name("vpn") is None
    if outage:
        with pytest.raises(RuntimeError, match="Docker daemon unavailable"):
            docker_ops.get_container_by_service_name("vpn", strict=True)

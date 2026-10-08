import proxy2vpn.adapters.gluetun_runtime as gluetun_runtime
import asyncio

import proxy2vpn.core.services.health_assessment as health_assessment
from proxy2vpn.core.models import VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticResult


class DummyContainer:
    def __init__(self, status: str = "running") -> None:
        self.status = status
        self.labels = {}

    def reload(self) -> None:
        return None


class DummyControlClient:
    def __init__(self, base_url, *args, **kwargs):
        self.base_url = base_url

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def status(self):
        return {"status": "running"}


def _service(name: str) -> VPNService:
    return VPNService.create(
        name=name,
        port=8080,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="New York" if "new-york" in name else "Boston",
        environment={},
        labels={},
    )


def test_assess_services_isolates_per_service_failures(monkeypatch):
    monkeypatch.setattr(gluetun_runtime, "GluetunControlClient", DummyControlClient)
    monkeypatch.setattr(
        gluetun_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name, *, strict=False: DummyContainer(),
    )

    def fake_analyze(name, lines=20, analyzer=None, timeout=5, direct_ip=None):
        if name == "protonvpn-united-states-new-york":
            raise RuntimeError("container disappeared")
        return [
            DiagnosticResult(
                check="connectivity",
                passed=True,
                message="VPN working",
                recommendation="",
            )
        ]

    monkeypatch.setattr(
        gluetun_runtime.docker_ops,
        "analyze_container_logs",
        fake_analyze,
    )

    assessor = health_assessment.HealthAssessmentService()
    assessments = asyncio.run(
        assessor.assess_services(
            [
                _service("protonvpn-united-states-new-york"),
                _service("protonvpn-united-states-boston"),
            ]
        )
    )

    assert set(assessments) == {
        "protonvpn-united-states-new-york",
        "protonvpn-united-states-boston",
    }
    assert (
        assessments["protonvpn-united-states-new-york"].health_class
        == "assessment_failed"
    )
    assert assessments["protonvpn-united-states-new-york"].health_score == 0
    assert assessments["protonvpn-united-states-boston"].health_class == "healthy"
    assert assessments["protonvpn-united-states-boston"].health_score == 100


def test_assess_services_reports_progress_as_services_complete(monkeypatch):
    assessor = health_assessment.HealthAssessmentService()
    completed: list[str] = []

    async def fake_assess_service(
        service, *, peer_assessments=None, lines=20, timeout=None
    ):
        if service.name.endswith("new-york"):
            await asyncio.sleep(0.01)
        return health_assessment.HealthAssessment(
            service_name=service.name,
            assessed_at=health_assessment.datetime.now(health_assessment.timezone.utc),
            container_status="running",
            health_score=100,
            health_class="healthy",
            results=[
                DiagnosticResult(
                    check="connectivity",
                    passed=True,
                    message="VPN working",
                    recommendation="",
                )
            ],
            control_api_reachable=True,
        )

    async def progress(service_name: str):
        completed.append(service_name)

    monkeypatch.setattr(assessor, "assess_service", fake_assess_service)

    assessments = asyncio.run(
        assessor.assess_services(
            [
                _service("protonvpn-united-states-new-york"),
                _service("protonvpn-united-states-boston"),
            ],
            progress_callback=progress,
        )
    )

    assert set(assessments) == {
        "protonvpn-united-states-new-york",
        "protonvpn-united-states-boston",
    }
    assert completed == [
        "protonvpn-united-states-boston",
        "protonvpn-united-states-new-york",
    ]


def test_assess_service_uses_shared_profile_peer_evidence(monkeypatch):
    monkeypatch.setattr(gluetun_runtime, "GluetunControlClient", DummyControlClient)
    monkeypatch.setattr(
        gluetun_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name, *, strict=False: DummyContainer(),
    )
    monkeypatch.setattr(
        gluetun_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: [
            DiagnosticResult(
                check="logs",
                passed=True,
                message="No critical log errors",
                recommendation="",
            )
        ],
    )

    assessor = health_assessment.HealthAssessmentService()
    peer_assessments = {
        "protonvpn-united-states-boston": health_assessment.HealthAssessment(
            service_name="protonvpn-united-states-boston",
            profile_name="test",
            assessed_at=health_assessment.datetime.now(health_assessment.timezone.utc),
            container_status="running",
            health_score=100,
            health_class="healthy",
            results=[],
            control_api_reachable=True,
        ),
        "nordvpn-united-states-boston": health_assessment.HealthAssessment(
            service_name="nordvpn-united-states-boston",
            profile_name="other",
            assessed_at=health_assessment.datetime.now(health_assessment.timezone.utc),
            container_status="running",
            health_score=100,
            health_class="healthy",
            results=[],
            control_api_reachable=True,
        ),
    }

    assessment = asyncio.run(
        assessor.assess_service(
            _service("protonvpn-united-states-new-york"),
            peer_assessments=peer_assessments,
        )
    )

    assert assessment.profile_name == "test"
    assert assessment.peer_evidence.healthy == ["protonvpn-united-states-boston"]
    assert assessment.peer_evidence.auth_config == []
    assert assessment.peer_evidence.other_unhealthy == []


# @lat: [[lat.md/logging-tests#Logging Tests#Concurrent Service Context]]
def test_concurrent_checks_keep_service_context_in_tasks_and_threads(
    tmp_path, monkeypatch, isolated_logging
):
    import json
    import structlog
    from proxy2vpn.adapters.logging_utils import (
        configure_logging,
        get_event_logger,
        get_logger,
        logging_context,
    )

    log_file = tmp_path / "checks.log"
    configure_logging(log_file=log_file)
    assessor = health_assessment.HealthAssessmentService()
    services = [_service("service-a"), _service("service-b")]
    services[1].config.provider = "nordvpn"
    completed_contexts = []

    async def run_checks():
        started = 0
        both_started = asyncio.Event()

        async def fake_assessment(service, **kwargs):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            get_event_logger("probe").info("async_probe")
            await asyncio.to_thread(get_logger("thread").info, "thread_probe")
            if service.name == "service-b":
                raise RuntimeError("token=probe-secret")
            return health_assessment.HealthAssessment(
                service_name=service.name,
                assessed_at=health_assessment.datetime.now(
                    health_assessment.timezone.utc
                ),
                container_status="running",
                health_score=100,
                health_class="healthy",
            )

        monkeypatch.setattr(assessor, "_assess_service", fake_assessment)
        with logging_context(cycle_id="shared-cycle"):
            results = await assessor.assess_services(
                services,
                progress_callback=lambda _: completed_contexts.append(
                    structlog.contextvars.get_contextvars()
                ),
            )
            assert structlog.contextvars.get_contextvars() == {
                "cycle_id": "shared-cycle"
            }
            get_logger("parent").info("batch_finished")
        assert structlog.contextvars.get_contextvars() == {}
        return results

    assessments = asyncio.run(run_checks())
    assert assessments["service-a"].health_class == "healthy"
    assert assessments["service-b"].health_class == "assessment_failed"
    records = [json.loads(line) for line in log_file.read_text().splitlines()]
    probes = [
        record
        for record in records
        if record["event"] in {"async_probe", "thread_probe"}
    ]
    assert len(probes) == 4
    for record in records:
        assert record["cycle_id"] == "shared-cycle"
        if record["event"] != "batch_finished":
            assert (
                record["provider"]
                == {"service-a": "protonvpn", "service-b": "nordvpn"}[
                    record["service_name"]
                ]
            )
    failure = next(
        record for record in records if record["event"] == "health_assessment_failed"
    )
    assert failure["service_name"] == "service-b"
    assert "probe-secret" not in log_file.read_text()
    assert completed_contexts == [{"cycle_id": "shared-cycle"}] * 2
    assert "service_name" not in records[-1]
    assert "provider" not in records[-1]


# @lat: [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests#Shared Assessment Runtime Results]]
def test_assessment_accepts_runtime_results_without_docker_patches(
    fake_gluetun_runtime,
):
    runtime = fake_gluetun_runtime
    observations = {
        "healthy": gluetun_runtime.RuntimeInspection(
            "running",
            results=[
                DiagnosticResult(
                    check="logs", passed=True, message="healthy", recommendation=""
                )
            ],
            current_egress_ip="198.51.100.1",
            direct_ip="203.0.113.1",
            errors={"control": "unavailable"},
        ),
        "missing": gluetun_runtime.RuntimeInspection("missing"),
        "stopped": gluetun_runtime.RuntimeInspection("exited"),
        "failed": gluetun_runtime.RuntimeInspection(
            "unknown", errors={"inspection": "Docker unavailable"}
        ),
    }

    async def inspect(service, **kwargs):
        return observations[service.name]

    runtime.inspect.side_effect = inspect
    assessor = health_assessment.HealthAssessmentService(runtime=runtime)
    assessed = asyncio.run(
        assessor.assess_services(
            [_service(name) for name in observations], lines=11, timeout=2
        )
    )
    assert assessed["healthy"].health_class == "healthy"
    assert assessed["healthy"].health_score == 100
    assert assessed["healthy"].current_egress_ip == "198.51.100.1"
    assert assessed["healthy"].direct_ip == "203.0.113.1"
    assert not assessed["healthy"].control_api_reachable
    assert assessed["missing"].failing_checks == ["container_missing"]
    assert assessed["stopped"].failing_checks == ["container_not_running"]
    assert assessed["failed"].health_class == "assessment_failed"
    assert assessed["failed"].health_score == 0
    assert runtime.inspect.await_count == 4
    assert all(
        call.kwargs == {"lines": 11, "timeout": 2}
        for call in runtime.inspect.call_args_list
    )
    runtime.control_status.assert_not_awaited()

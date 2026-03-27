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
    monkeypatch.setattr(health_assessment, "GluetunControlClient", DummyControlClient)
    monkeypatch.setattr(
        health_assessment.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer(),
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
        health_assessment.docker_ops,
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

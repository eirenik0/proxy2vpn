import asyncio
from datetime import datetime, timezone

from proxy2vpn import docker_ops, server_monitor
from proxy2vpn.core import models
from proxy2vpn.core.services.health_assessment import HealthAssessment, PeerEvidence


class DummyContainer:
    status = "running"
    attrs = {"Config": {"Env": ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=pass"]}}

    def reload(self):
        pass


def _assessment(service_name: str, score: int, health_class: str):
    return HealthAssessment(
        service_name=service_name,
        assessed_at=datetime.now(timezone.utc),
        container_status="running" if score else "missing",
        health_score=score,
        health_class=health_class,
        failing_checks=[] if score else ["connectivity"],
        control_api_reachable=score >= 60,
        current_egress_ip="203.0.113.10" if score >= 60 else None,
        peer_evidence=PeerEvidence(),
    )


def test_check_service_health_uses_shared_assessor(monkeypatch):
    service = models.VPNService.create(
        name="vpn-test",
        port=8080,
        control_port=30000,
        provider="",
        profile="",
        location="",
        environment={},
        labels={},
    )

    monkeypatch.setattr(
        docker_ops, "get_container_by_service_name", lambda name: DummyContainer()
    )

    monitor = server_monitor.ServerMonitor(fleet_manager=None, http_client=None)

    async def fake_assess_service(
        service_obj, timeout=30, peer_assessments=None, lines=20
    ):
        return _assessment(service_obj.name, 100, "healthy")

    monkeypatch.setattr(monitor.assessor, "assess_service", fake_assess_service)

    assert asyncio.run(monitor.check_service_health(service))
    assert monitor.last_assessments["vpn-test"].health_score == 100


def test_check_service_health_records_failed_assessment(monkeypatch):
    service = models.VPNService.create(
        name="vpn-test",
        port=8080,
        control_port=30000,
        provider="",
        profile="",
        location="",
        environment={},
        labels={},
    )

    monkeypatch.setattr(
        docker_ops, "get_container_by_service_name", lambda name: DummyContainer()
    )

    monitor = server_monitor.ServerMonitor(fleet_manager=None, http_client=None)

    async def fake_assess_service(
        service_obj, timeout=30, peer_assessments=None, lines=20
    ):
        return _assessment(service_obj.name, 0, "auth_config")

    monkeypatch.setattr(monitor.assessor, "assess_service", fake_assess_service)

    assert asyncio.run(monitor.check_service_health(service)) is False
    assert monitor.last_assessments["vpn-test"].health_class == "auth_config"


def test_execute_service_rotation_updates_service_location(monkeypatch):
    service = models.VPNService.create(
        name="protonvpn-canada-toronto",
        port=8080,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="Toronto",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "Toronto",
            "SERVER_COUNTRIES": "Canada",
        },
        labels={"vpn.location": "Toronto"},
    )
    updated = {"called": False}

    class DummyComposeManager:
        def get_service(self, name):
            assert name == "protonvpn-canada-toronto"
            return service

        def replace_service(self, old_name, updated_service):
            updated["called"] = True
            assert old_name == "protonvpn-canada-toronto"
            assert updated_service.name == "protonvpn-canada-montreal"
            assert updated_service.location == "Montreal"

        def get_profile(self, name):
            return object()

        def list_services(self):
            return [service]

    class DummyFleetManager:
        compose_manager = DummyComposeManager()

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(updated_service):
        assert updated_service.location == "Montreal"
        return True

    monkeypatch.setattr(server_monitor.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        docker_ops, "recreate_vpn_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(docker_ops, "start_container", lambda *args, **kwargs: None)

    monitor = server_monitor.ServerMonitor(fleet_manager=DummyFleetManager())
    monkeypatch.setattr(monitor, "check_service_health", fake_check)

    asyncio.run(
        monitor._execute_service_rotation(
            server_monitor.ServiceRotation(
                service_name="protonvpn-canada-toronto",
                old_location="Toronto",
                new_location="Montreal",
                reason="health_check_failed",
            )
        )
    )

    assert updated["called"] is True
    assert service.name == "protonvpn-canada-montreal"
    assert service.location == "Montreal"
    assert service.environment["SERVER_CITIES"] == "Montreal"
    assert service.labels["vpn.location"] == "Montreal"

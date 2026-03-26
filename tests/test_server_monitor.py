import asyncio

from proxy2vpn import server_monitor, docker_ops
from proxy2vpn.core import models


class DummyContainer:
    status = "running"
    attrs = {"Config": {"Env": ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=pass"]}}

    def reload(self):
        pass


def test_check_service_health_uses_authenticated_proxy(monkeypatch):
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

    container = DummyContainer()
    captured: dict[str, str] = {}

    class DummyHTTPClient:
        async def get(self, url, **kwargs):
            captured.update(kwargs)
            return {}

    monkeypatch.setattr(
        docker_ops, "get_container_by_service_name", lambda name: container
    )

    client = DummyHTTPClient()
    monitor = server_monitor.ServerMonitor(fleet_manager=None, http_client=client)
    assert asyncio.run(monitor.check_service_health(service))
    assert captured["proxy"] == "http://user:pass@localhost:8080"


def test_check_service_health_redacts_proxy_errors(monkeypatch):
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
    captured_logs: list[str] = []

    class DummyContainer:
        status = "running"
        labels = {"vpn.port": "8080"}
        attrs = {"Config": {"Env": ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=pass"]}}

        def reload(self):
            pass

    class DummyHTTPClient:
        async def get(self, url, **kwargs):
            raise RuntimeError("failed: http://user:pass@localhost:8080")

    monkeypatch.setattr(
        docker_ops, "get_container_by_service_name", lambda name: DummyContainer()
    )
    # Ensure HTTP client exception is routed through the HTTPClientError branch.
    monkeypatch.setattr(
        server_monitor,
        "HTTPClientError",
        RuntimeError,
    )

    def fake_error(msg):
        captured_logs.append(str(msg))

    monkeypatch.setattr(server_monitor.logger, "error", fake_error)

    monitor = server_monitor.ServerMonitor(
        fleet_manager=None, http_client=DummyHTTPClient()
    )
    assert asyncio.run(monitor.check_service_health(service)) is False
    assert any("***:***" in message for message in captured_logs)
    assert all("user:pass" not in message for message in captured_logs)


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

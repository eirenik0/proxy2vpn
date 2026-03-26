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

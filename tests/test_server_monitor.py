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

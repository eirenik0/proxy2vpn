import asyncio
import types

from proxy2vpn import server_monitor, models, docker_ops


class DummyContainer:
    status = "running"
    attrs = {"Config": {"Env": ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=pass"]}}

    def reload(self):
        pass


def test_check_service_health_uses_authenticated_proxy(monkeypatch):
    service = models.VPNService(
        name="vpn-test",
        port=8080,
        provider="",
        profile="",
        location="",
        environment={},
        labels={},
    )

    container = DummyContainer()

    captured = {}

    def fake_get(url, proxies, timeout):
        captured["proxies"] = proxies

        class Resp:
            status_code = 200

        return Resp()

    monkeypatch.setattr(server_monitor, "requests", types.SimpleNamespace(get=fake_get))
    monkeypatch.setattr(
        docker_ops, "get_container_by_service_name", lambda name: container
    )

    monitor = server_monitor.ServerMonitor(fleet_manager=None)
    assert asyncio.run(monitor.check_service_health(service))
    assert captured["proxies"]["http"] == "http://user:pass@localhost:8080"

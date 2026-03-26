import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import docker_ops, monitoring


def docker_available() -> bool:
    try:
        client = docker_ops._client()  # type: ignore[attr-defined]
        client.ping()
        return True
    except Exception:
        return False


def test_collect_system_metrics():
    metrics = monitoring.collect_system_metrics()
    assert "cpu_percent" in metrics
    assert "memory_percent" in metrics


def test_monitor_health_handles_errors(monkeypatch):
    monkeypatch.setattr(
        monitoring,
        "get_vpn_containers",
        lambda **_: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    assert monitoring.monitor_vpn_health() == []


@pytest.mark.skipif(not docker_available(), reason="Docker is not available")
def test_monitor_vpn_health(docker_profile_factory, docker_service_factory):
    profile = docker_profile_factory(image="nginx:alpine")
    service = docker_service_factory(
        proxy_port_base=24000,
        control_port_base=34000,
    )
    docker_ops.create_vpn_container(service, profile)
    docker_ops.start_container(service.name)
    diagnostics = monitoring.monitor_vpn_health()
    names = {d["name"] for d in diagnostics}
    assert service.name in names

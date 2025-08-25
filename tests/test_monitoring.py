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
def test_monitor_vpn_health(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("")
    profile = docker_ops.Profile(
        name="test", env_file=str(env_file), image="alpine", cap_add=[], devices=[]
    )
    service = docker_ops.VPNService.create(
        name="vpn-test",
        port=12345,
        control_port=30000,
        provider="",
        profile="test",
        location="",
        environment={},
        labels={"vpn.type": "vpn", "vpn.port": "12345", "vpn.control_port": "30000"},
    )
    docker_ops.create_vpn_container(service, profile)
    docker_ops.start_container(service.name)
    diagnostics = monitoring.monitor_vpn_health()
    names = {d["name"] for d in diagnostics}
    assert service.name in names
    docker_ops.stop_container(service.name)
    docker_ops.remove_container(service.name)

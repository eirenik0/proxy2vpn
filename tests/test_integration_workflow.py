import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn import docker_ops, monitoring


def docker_available() -> bool:
    try:
        client = docker_ops._client()  # type: ignore[attr-defined]
        client.ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not docker_available(), reason="Docker is not available")
def test_end_to_end_workflow(
    docker_test_root, docker_profile_factory, docker_service_factory
):
    compose_path = docker_test_root / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)
    profile = docker_profile_factory(image="nginx:alpine")
    manager.add_profile(profile)
    service = docker_service_factory(
        base_name="vpn1",
        proxy_port_base=25000,
        control_port_base=35000,
        labels={"vpn.profile": "test"},
    )
    manager.add_service(service)
    docker_ops.create_vpn_container(service, profile)
    docker_ops.start_container(service.name)
    diagnostics = monitoring.monitor_vpn_health()
    names = {d["name"] for d in diagnostics}
    assert service.name in names

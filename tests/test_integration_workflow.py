import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.core.models import Profile, VPNService
from proxy2vpn import docker_ops, monitoring


def docker_available() -> bool:
    try:
        client = docker_ops._client()  # type: ignore[attr-defined]
        client.ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not docker_available(), reason="Docker is not available")
def test_end_to_end_workflow(tmp_path):
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)
    env_file = tmp_path / "test.env"
    env_file.write_text("")
    profile = Profile(
        name="test", env_file=str(env_file), image="alpine", cap_add=[], devices=[]
    )
    manager.add_profile(profile)
    service = VPNService.create(
        name="vpn1",
        port=12346,
        control_port=30000,
        provider="",
        profile="test",
        location="",
        environment={},
        labels={
            "vpn.type": "vpn",
            "vpn.port": "12346",
            "vpn.control_port": "30000",
            "vpn.profile": "test",
        },
    )
    manager.add_service(service)
    docker_ops.create_vpn_container(service, profile)
    docker_ops.start_container(service.name)
    diagnostics = monitoring.monitor_vpn_health()
    names = {d["name"] for d in diagnostics}
    assert service.name in names
    docker_ops.stop_container(service.name)
    docker_ops.remove_container(service.name)

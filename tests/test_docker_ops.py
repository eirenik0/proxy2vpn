import pathlib
import sys

import pytest

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import docker_ops


def docker_available() -> bool:
    try:
        client = docker_ops._client()  # type: ignore[attr-defined]
        client.ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not docker_available(), reason="Docker is not available")
def test_container_lifecycle():
    name = "proxy2vpn-test"
    image = "alpine"
    docker_ops.create_container(name=name, image=image, command=["sleep", "5"])
    docker_ops.start_container(name)
    containers = [c.name for c in docker_ops.list_containers(all=True)]
    assert name in containers
    docker_ops.stop_container(name)
    docker_ops.remove_container(name)
    containers = [c.name for c in docker_ops.list_containers(all=True)]
    assert name not in containers


@pytest.mark.skipif(not docker_available(), reason="Docker is not available")
def test_restart_and_logs():
    name = "proxy2vpn-test-logs"
    image = "alpine"
    docker_ops.create_container(
        name=name, image=image, command=["sh", "-c", "echo ready && sleep 5"]
    )
    docker_ops.start_container(name)
    logs = list(docker_ops.container_logs(name, lines=10))
    assert any("ready" in line for line in logs)
    container = docker_ops.restart_container(name)
    assert container.status == "running"
    docker_ops.stop_container(name)
    docker_ops.remove_container(name)


@pytest.mark.skipif(not docker_available(), reason="Docker is not available")
def test_create_vpn_container_merges_env(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("FOO=bar\nVAR=base\n")
    profile = docker_ops.Profile(
        name="test",
        env_file=str(env_file),
        image="alpine",
        cap_add=[],
        devices=[],
    )
    service = docker_ops.VPNService(
        name="vpn-test",
        port=12345,
        provider="",
        profile="test",
        location="",
        environment={"VAR": "override"},
        labels={"vpn.type": "vpn", "vpn.port": "12345"},
    )
    container = docker_ops.create_vpn_container(service, profile)
    env_vars = container.attrs["Config"]["Env"]
    assert "FOO=bar" in env_vars
    assert "VAR=override" in env_vars
    docker_ops.remove_container("vpn-test")

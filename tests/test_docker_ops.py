import pathlib
import sys
import asyncio

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


def test_retry_logic():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ValueError("fail")
        return "ok"

    result = docker_ops._retry(flaky, retries=3, exceptions=(ValueError,))
    assert result == "ok"
    assert calls["n"] == 2


def test_get_container_ip(monkeypatch):
    class C:
        status = "running"
        labels = {"vpn.port": "8080"}

    monkeypatch.setattr(
        docker_ops.ip_utils,
        "fetch_ip",
        lambda proxies=None, timeout=5: "1.1.1.1" if proxies else "2.2.2.2",
    )
    assert docker_ops.get_container_ip(C()) == "1.1.1.1"


def test_get_container_ip_async(monkeypatch):
    class C:
        status = "running"
        labels = {"vpn.port": "8080"}

    async def fake_fetch_ip_async(proxies=None, timeout=3):
        return "1.1.1.1" if proxies else "2.2.2.2"

    monkeypatch.setattr(docker_ops.ip_utils, "fetch_ip_async", fake_fetch_ip_async)
    result = asyncio.run(docker_ops.get_container_ip_async(C()))
    assert result == "1.1.1.1"


def test_test_vpn_connection(monkeypatch):
    class Cont:
        status = "running"
        labels = {"vpn.port": "8080"}

    class Client:
        class Containers:
            def get(self, name):
                return Cont()

        containers = Containers()

    async def fake_fetch_ip_async(proxies=None, timeout=3):
        return "2.2.2.2" if not proxies else "1.1.1.1"

    monkeypatch.setattr(docker_ops, "_client", lambda: Client())
    monkeypatch.setattr(docker_ops.ip_utils, "fetch_ip_async", fake_fetch_ip_async)
    assert docker_ops.test_vpn_connection("name") is True


def test_cleanup_orphans(monkeypatch):
    class C:
        def __init__(self, name: str) -> None:
            self.name = name
            self.removed = False

        def remove(self, force: bool = False) -> None:
            self.removed = True

    containers = [C("a"), C("b")]
    monkeypatch.setattr(docker_ops, "get_vpn_containers", lambda all=True: containers)

    class M:
        def list_services(self):
            class S:
                name = "a"

            return [S()]

    removed = docker_ops.cleanup_orphaned_containers(M())
    assert removed == ["b"]
    assert containers[1].removed


def test_collect_proxy_info(monkeypatch):
    class C:
        status = "running"
        labels = {"vpn.port": "20001", "vpn.location": "New York"}
        attrs = {
            "Config": {"Env": ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=pass"]},
            "State": {"ExitCode": 0},
        }

    monkeypatch.setattr(docker_ops, "get_vpn_containers", lambda all=True: [C()])

    async def fake_ip(container):
        return "1.2.3.4"

    monkeypatch.setattr(docker_ops, "get_container_ip_async", fake_ip)

    result = asyncio.run(docker_ops.collect_proxy_info())
    assert result == [
        {
            "host": "1.2.3.4",
            "port": "20001",
            "username": "user",
            "password": "pass",
            "location": "New York",
            "status": "active",
        }
    ]

    result_no_auth = asyncio.run(
        docker_ops.collect_proxy_info(include_credentials=False)
    )
    assert result_no_auth[0]["username"] == ""
    assert result_no_auth[0]["password"] == ""


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


@pytest.mark.skipif(not docker_available(), reason="Docker is not available")
def test_recreate_vpn_container():
    profile = docker_ops.Profile(
        name="test", env_file="", image="alpine", cap_add=[], devices=[]
    )
    service = docker_ops.VPNService(
        name="vpn-recreate",
        port=12346,
        provider="",
        profile="test",
        location="",
        environment={},
        labels={"vpn.type": "vpn", "vpn.port": "12346"},
    )
    first = docker_ops.create_vpn_container(service, profile)
    first_id = first.id
    second = docker_ops.recreate_vpn_container(service, profile)
    second_id = second.id
    assert first_id != second_id
    docker_ops.remove_container(service.name)


def test_start_all_vpn_containers_recreates(monkeypatch):
    svc = docker_ops.VPNService(
        name="svc",
        port=1,
        provider="",
        profile="p",
        location="",
        environment={},
        labels={},
    )
    profile = docker_ops.Profile(
        name="p", env_file="", image="alpine", cap_add=[], devices=[]
    )

    class Manager:
        def list_services(self):
            return [svc]

        def get_profile(self, name):
            return profile

    class DummyContainer:
        status = "created"

        def start(self):
            return None

    called = {"recreate": 0}

    def fake_recreate(service, profile):
        called["recreate"] += 1
        return DummyContainer()

    monkeypatch.setattr(docker_ops, "recreate_vpn_container", fake_recreate)
    monkeypatch.setattr(docker_ops, "_retry", lambda func, **kw: func())

    results = docker_ops.start_all_vpn_containers(Manager())
    assert results == ["svc"]
    assert called["recreate"] == 1

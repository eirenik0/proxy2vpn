import re

import pytest
from proxy2vpn import compose_validator, docker_ops


class _AlwaysValidServerManager:
    def update_servers(self):
        self.data = {}
        return self.data

    def validate_location(self, provider, location):
        return True


@pytest.fixture(autouse=True)
def _patch_server_manager(monkeypatch):
    monkeypatch.setattr(
        compose_validator, "ServerManager", lambda: _AlwaysValidServerManager()
    )


@pytest.fixture
def docker_test_root(tmp_path):
    auth_config = tmp_path / docker_ops.config.CONTROL_AUTH_CONFIG_FILE
    auth_config.write_text('[[roles]]\nname = "proxy2vpn"\nauth = "none"\n')
    return tmp_path


@pytest.fixture
def docker_test_suffix(tmp_path):
    return sum(ord(ch) for ch in re.sub(r"[^A-Za-z0-9]", "", tmp_path.name)) % 1000


@pytest.fixture
def managed_docker_resources():
    container_names: list[str] = []

    def register(container_name: str) -> str:
        container_names.append(container_name)
        return container_name

    yield register

    for container_name in reversed(container_names):
        for cleanup in (docker_ops.stop_container, docker_ops.remove_container):
            try:
                cleanup(container_name)
            except Exception:
                pass


@pytest.fixture
def docker_profile_factory(docker_test_root):
    def factory(
        *,
        name: str = "test",
        env_file_name: str = "test.env",
        env_contents: str = "",
        image: str = "alpine",
        cap_add: list[str] | None = None,
        devices: list[str] | None = None,
    ):
        env_path = docker_test_root / env_file_name
        env_path.write_text(env_contents)
        profile = docker_ops.Profile(
            name=name,
            env_file=str(env_path),
            image=image,
            cap_add=cap_add or [],
            devices=devices or [],
        )
        profile._base_dir = docker_test_root
        return profile

    return factory


@pytest.fixture
def docker_service_factory(docker_test_suffix, managed_docker_resources):
    def factory(
        *,
        base_name: str = "vpn-test",
        proxy_port_base: int = 20000,
        control_port_base: int = 30000,
        provider: str = "",
        profile: str = "test",
        location: str = "",
        environment: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ):
        name = managed_docker_resources(f"{base_name}-{docker_test_suffix}")
        proxy_port = proxy_port_base + docker_test_suffix
        control_port = control_port_base + docker_test_suffix
        service_labels = {
            "vpn.type": "vpn",
            "vpn.port": str(proxy_port),
            "vpn.control_port": str(control_port),
        }
        if labels:
            service_labels.update(labels)
        return docker_ops.VPNService.create(
            name=name,
            port=proxy_port,
            control_port=control_port,
            provider=provider,
            profile=profile,
            location=location,
            environment=environment or {},
            labels=service_labels,
        )

    return factory

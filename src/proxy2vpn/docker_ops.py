"""Interactions with Docker using the docker SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

from .compose_manager import ComposeManager
from .diagnostics import DiagnosticAnalyzer, DiagnosticResult
from .models import Profile, VPNService

import docker
import requests
from docker.models.containers import Container


def _client() -> docker.DockerClient:
    """Return a Docker client configured from environment."""
    return docker.from_env()


def create_container(
    name: str, image: str, command: Iterable[str] | None = None
) -> Container:
    """Create a container with the given name and image.

    The image is pulled if it is not available locally.
    """
    client = _client()
    client.images.pull(image)
    return client.containers.create(image, name=name, command=command, detach=True)


def _load_env_file(path: str) -> dict[str, str]:
    """Return environment variables loaded from PATH."""

    env: dict[str, str] = {}
    if not path:
        return env
    file_path = Path(path)
    if not file_path.exists():
        return env
    for line in file_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def create_vpn_container(service: VPNService, profile: Profile) -> Container:
    """Create a container for a VPN service using its profile."""

    client = _client()
    client.images.pull(profile.image)
    env = _load_env_file(profile.env_file)
    env.update(service.environment)
    network_name = "proxy2vpn_network"
    if not client.networks.list(names=[network_name]):
        client.networks.create(network_name, driver="bridge", name=network_name)
    return client.containers.create(
        profile.image,
        name=service.name,
        detach=True,
        ports={"8888/tcp": service.port},
        environment=env,
        labels=service.labels,
        cap_add=profile.cap_add,
        devices=profile.devices,
        network=network_name,
    )


def start_container(name: str) -> Container:
    """Start an existing container by name."""
    client = _client()
    container = client.containers.get(name)
    container.start()
    return container


def stop_container(name: str) -> Container:
    """Stop a running container by name."""
    client = _client()
    container = client.containers.get(name)
    container.stop()
    return container


def restart_container(name: str) -> Container:
    """Restart a container by name and return it."""
    client = _client()
    container = client.containers.get(name)
    container.restart()
    container.reload()
    return container


def remove_container(name: str) -> None:
    """Remove a container by name."""
    client = _client()
    container = client.containers.get(name)
    container.remove(force=True)


def container_logs(name: str, lines: int = 100, follow: bool = False) -> Iterator[str]:
    """Yield log lines from a container.

    If ``follow`` is ``True`` the generator will yield new log lines as they
    arrive until the container stops or the caller interrupts.  Otherwise the
    last ``lines`` lines are returned.
    """

    client = _client()
    container = client.containers.get(name)
    if follow:
        for line in container.logs(stream=True, follow=True, tail=lines):
            yield line.decode().rstrip()
    else:
        output = container.logs(tail=lines).decode().splitlines()
        for line in output:
            yield line


def list_containers(all: bool = False) -> list[Container]:
    """List containers."""
    client = _client()
    return client.containers.list(all=all)


def get_vpn_containers(all: bool = False) -> list[Container]:
    """Return containers labeled as VPN services."""
    client = _client()
    return client.containers.list(all=all, filters={"label": "vpn.type=vpn"})


def get_problematic_containers(all: bool = False) -> list[Container]:
    """Return containers that are not running properly."""

    containers = get_vpn_containers(all=all)
    problematic: list[Container] = []
    for container in containers:
        container.reload()
        state = container.attrs.get("State", {})
        if (
            container.status != "running"
            or state.get("ExitCode", 0) != 0
            or state.get("RestartCount", 0) > 0
        ):
            problematic.append(container)
    return problematic


def get_container_diagnostics(container: Container) -> dict:
    """Return diagnostic information for a container."""

    container.reload()
    state = container.attrs.get("State", {})
    return {
        "name": container.name,
        "status": container.status,
        "exit_code": state.get("ExitCode"),
        "restart_count": state.get("RestartCount", 0),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
    }


def analyze_container_logs(
    name: str, lines: int = 100, analyzer: DiagnosticAnalyzer | None = None
) -> list[DiagnosticResult]:
    """Analyze container logs and return diagnostic results."""

    if analyzer is None:
        analyzer = DiagnosticAnalyzer()
    logs = list(container_logs(name, lines=lines, follow=False))
    return analyzer.analyze(logs)


def start_all_vpn_containers(manager: ComposeManager) -> list[tuple[str, bool]]:
    """Start all VPN containers, creating any missing ones."""

    client = _client()
    existing = {c.name: c for c in client.containers.list(all=True)}
    results: list[tuple[str, bool]] = []
    for svc in manager.list_services():
        container = existing.get(svc.name)
        if container is None:
            profile = manager.get_profile(svc.profile)
            container = create_vpn_container(svc, profile)
        if container.status != "running":
            container.start()
            results.append((svc.name, True))
        else:
            results.append((svc.name, False))
    return results


def stop_all_vpn_containers() -> list[str]:
    """Stop all running VPN containers.

    Returns a list of container names that were stopped.
    """

    containers = get_vpn_containers(all=False)
    results: list[str] = []
    for container in containers:
        container.stop()
        results.append(container.name)
    return results


def get_container_ip(container: Container) -> str:
    """Return the external IP address for a running container.

    The IP address is retrieved via ``ifconfig.me`` through the proxy exposed on
    the port specified by the ``vpn.port`` label. If the container is not
    running, has no port label or the request fails, ``"N/A"`` is returned.
    """

    port = container.labels.get("vpn.port")
    if not port or container.status != "running":
        return "N/A"
    try:
        response = requests.get(
            "https://ifconfig.me",
            proxies={"http": f"localhost:{port}", "https": f"localhost:{port}"},
            timeout=5,
        )
        return response.text.strip()
    except Exception:
        return "N/A"


def test_vpn_connection(name: str) -> bool:
    """Return ``True`` if the VPN proxy for NAME appears to work."""

    client = _client()
    try:
        container = client.containers.get(name)
    except Exception:
        return False
    port = container.labels.get("vpn.port")
    if not port or container.status != "running":
        return False
    try:
        direct = requests.get("https://ifconfig.me", timeout=5).text.strip()
        proxied = requests.get(
            "https://ifconfig.me",
            proxies={
                "http": f"http://localhost:{port}",
                "https": f"http://localhost:{port}",
            },
            timeout=5,
        ).text.strip()
        return proxied != "" and proxied != direct
    except Exception:
        return False

"""Interactions with Docker using the docker SDK."""

from __future__ import annotations

from typing import Iterable, Iterator

from .diagnostics import DiagnosticAnalyzer, DiagnosticResult

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
        if container.status != "running" or state.get("ExitCode", 0) != 0 or state.get(
            "RestartCount", 0
        ) > 0:
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


def start_all_vpn_containers() -> list[tuple[str, bool]]:
    """Start all VPN containers.

    Returns a list of tuples ``(name, started)`` where ``started`` is ``True``
    if the container was started by this function and ``False`` if it was
    already running.
    """

    containers = get_vpn_containers(all=True)
    results: list[tuple[str, bool]] = []
    for container in containers:
        if container.status != "running":
            container.start()
            results.append((container.name, True))
        else:
            results.append((container.name, False))
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

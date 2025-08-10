"""Interactions with Docker using the docker SDK."""
from __future__ import annotations

from typing import Iterable

import docker
import requests
from docker.models.containers import Container

def _client() -> docker.DockerClient:
    """Return a Docker client configured from environment."""
    return docker.from_env()

def create_container(name: str, image: str, command: Iterable[str] | None = None) -> Container:
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

def remove_container(name: str) -> None:
    """Remove a container by name."""
    client = _client()
    container = client.containers.get(name)
    container.remove(force=True)

def list_containers(all: bool = False) -> list[Container]:
    """List containers."""
    client = _client()
    return client.containers.list(all=all)


def get_vpn_containers(all: bool = False) -> list[Container]:
    """Return containers labeled as VPN services."""
    client = _client()
    return client.containers.list(all=all, filters={"label": "vpn.type=vpn"})


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

"""Interactions with Docker using the docker SDK."""
from __future__ import annotations

from typing import Iterable

import docker
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

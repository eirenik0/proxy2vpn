"""Utilities for manipulating docker-compose YAML files."""

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

yaml = YAML()


def load_compose(path: Path) -> dict[str, Any]:
    """Load a docker-compose YAML file."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.load(f)


def save_compose(data: dict[str, Any], path: Path) -> None:
    """Save a docker-compose YAML file."""
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)


def set_service_image(compose_path: Path, service: str, image: str) -> None:
    """Update the image of a service in the compose file.

    Args:
        compose_path: Path to the docker-compose.yml file.
        service: Name of the service to update.
        image: New image string.
    """
    data = load_compose(compose_path)
    services = data.get("services", {})
    if service not in services:
        raise KeyError(f"Service '{service}' not found")
    services[service]["image"] = image
    save_compose(data, compose_path)

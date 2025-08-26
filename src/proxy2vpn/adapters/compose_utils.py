"""Utilities for manipulating docker-compose YAML files.

This module centralizes small helpers for parsing common variants of
compose fields we accept in user files (e.g., environment and ports).
Keeping this logic in one place avoids scattered special cases.
"""

from pathlib import Path
from typing import Any, Iterator, Tuple

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


# ----------------------------
# Parsing helpers
# ----------------------------


def parse_env(env: Any) -> dict[str, str]:
    """Normalize compose ``environment`` into a dict[str,str].

    Accepts a dict or a list of "KEY=VAL" entries; ignores invalid lines.
    """
    if not env:
        return {}
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    result: dict[str, str] = {}
    for item in env or []:
        try:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                result[k] = v
        except Exception:
            # Ignore malformed entries; validator will surface errors elsewhere
            continue
    return result


def iter_port_mappings(ports: Any) -> Iterator[Tuple[int, int]]:
    """Yield ``(host_port, container_port)`` pairs from compose ``ports``.

    Supports string formats like "8888:8888", "0.0.0.0:8888:8888/tcp" and
    mapping forms like {target: 8888, published: 20000}.
    """
    if not ports:
        return
    for p in ports or []:
        try:
            if isinstance(p, dict):
                target = p.get("target")
                published = p.get("published") or p.get("host_port")
                if target is None or published is None:
                    continue
                yield int(published), int(target)
                continue
            s = str(p)
            parts = s.split(":")
            cont_raw = parts[-1]
            cont_port = int(cont_raw.split("/")[0])
            if len(parts) == 2:
                host_port = int(parts[0])
            elif len(parts) >= 3:
                host_port = int(parts[-2])
            else:
                continue
            yield host_port, cont_port
        except Exception:
            # Skip invalid entries; callers may treat absence as an error
            continue


def find_host_port_for_target(ports: Any, target: int) -> int | None:
    """Return host port published for the given ``target`` container port."""
    for host, cont in iter_port_mappings(ports):
        if cont == target:
            return host
    return None

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
    return parse_env_with_issues(env)[0]


def parse_env_with_issues(env: Any) -> tuple[dict[str, str], list[str]]:
    """Parse compose ``environment`` entries and return parse issues.

    Returns ``(parsed, issues)`` where ``parsed`` is a normalized mapping and
    ``issues`` contains human-readable parse errors.
    """
    if not env:
        return {}, []
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}, []
    if not isinstance(env, list):
        return {}, ["environment must be a mapping or list of 'KEY=VALUE' entries"]

    result: dict[str, str] = {}
    issues: list[str] = []
    for item in env:
        try:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                result[k] = v
            else:
                issues.append(f"invalid environment entry: {item!r}")
        except Exception:
            # Ignore malformed entries; caller handles issues from this helper.
            issues.append(f"invalid environment entry: {item!r}")
    return result, issues


def iter_port_mappings(ports: Any) -> Iterator[Tuple[int, int]]:
    """Yield ``(host_port, container_port)`` pairs from compose ``ports``.

    Supports string formats like "8888:8888", "0.0.0.0:8888:8888/tcp" and
    mapping forms like {target: 8888, published: 20000}.
    """
    for host, cont in iter_port_mappings_with_issues(ports)[0]:
        yield host, cont


def iter_volume_mappings_with_issues(
    volumes: Any,
) -> tuple[list[Tuple[str, str, str | None]], list[str]]:
    """Parse compose ``volumes`` entries and return parse issues.

    Returns ``(parsed, issues)`` where ``parsed`` is a list of
    ``(source, target, mode)`` tuples. The mode value is ``None`` when not
    provided in the short syntax or long-form mapping.
    """
    if not volumes:
        return [], []
    if not isinstance(volumes, list):
        return [], ["volumes must be a list"]

    parsed: list[Tuple[str, str, str | None]] = []
    issues: list[str] = []
    for v in volumes:
        try:
            if isinstance(v, dict):
                source = v.get("source") or v.get("src") or v.get("host")
                target = v.get("target") or v.get("dst")
                if source is None or target is None:
                    issues.append(f"invalid volume mount: {v!r}")
                    continue
                mode = v.get("mode")
                parsed.append(
                    (str(source), str(target), str(mode) if mode is not None else None)
                )
                continue

            s = str(v)
            parts = s.split(":")
            if len(parts) < 2:
                issues.append(f"invalid volume mount: {v!r}")
                continue
            source = parts[0]
            target = parts[1]
            if not source or not target:
                issues.append(f"invalid volume mount: {v!r}")
                continue
            mode = ":".join(parts[2:]) if len(parts) > 2 else None
            parsed.append((source, target, mode))
        except Exception:
            issues.append(f"invalid volume mount: {v!r}")
    return parsed, issues


def volume_source_is_path_like(source: str) -> bool:
    """Return ``True`` when SOURCE should be treated as a bind-mount path."""
    return (
        source.startswith(("./", "../", "~/", "/")) or "/" in source or "\\" in source
    )


def iter_port_mappings_with_issues(
    ports: Any,
) -> tuple[list[Tuple[int, int]], list[str]]:
    """Parse compose ``ports`` entries and return parse issues.

    Returns ``(parsed, issues)`` where ``parsed`` is a list of ``(host, target)``
    tuples.
    """
    if not ports:
        return [], []
    if not isinstance(ports, list):
        return [], ["ports must be a list"]

    parsed: list[Tuple[int, int]] = []
    issues: list[str] = []
    for p in ports:
        try:
            if isinstance(p, dict):
                target = p.get("target")
                published = p.get("published") or p.get("host_port")
                if target is None or published is None:
                    issues.append(f"invalid port mapping: {p!r}")
                    continue
                parsed.append((int(published), int(target)))
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
                issues.append(f"invalid port mapping: {p!r}")
                continue
            parsed.append((host_port, cont_port))
        except Exception:
            # Ignore malformed entries and return structured issues for validation.
            issues.append(f"invalid port mapping: {p!r}")
    return parsed, issues


def find_host_port_for_target(ports: Any, target: int) -> int | None:
    """Return host port published for the given ``target`` container port."""
    for host, cont in iter_port_mappings(ports):
        if cont == target:
            return host
    return None

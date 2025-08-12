"""Interactions with Docker using the docker SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, Callable, Any
import time

from .compose_manager import ComposeManager
from .diagnostics import DiagnosticAnalyzer, DiagnosticResult
from .models import Profile, VPNService

import docker
import requests
from docker.models.containers import Container
from docker.errors import DockerException

DEFAULT_TIMEOUT = 10
MAX_RETRIES = 3


def _retry(
    func: Callable[..., Any],
    *args: Any,
    retries: int = MAX_RETRIES,
    backoff: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> Any:
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except exceptions:
            if attempt == retries - 1:
                raise
            time.sleep(backoff * (2**attempt))


def _client(timeout: int = DEFAULT_TIMEOUT) -> docker.DockerClient:
    """Return a Docker client configured from environment."""
    try:
        return docker.from_env(timeout=timeout)
    except DockerException as exc:  # pragma: no cover - connection errors
        raise RuntimeError(f"Docker unavailable: {exc}") from exc


def create_container(
    name: str, image: str, command: Iterable[str] | None = None
) -> Container:
    """Create a container with the given name and image.

    The image is pulled if it is not available locally.
    """
    client = _client()
    try:
        _retry(client.images.pull, image, exceptions=(DockerException,))
        return client.containers.create(image, name=name, command=command, detach=True)
    except DockerException as exc:
        raise RuntimeError(f"Failed to create container {name}: {exc}") from exc


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
    try:
        _retry(client.images.pull, profile.image, exceptions=(DockerException,))
        env = _load_env_file(profile.env_file)
        env.update(service.environment)
        network_name = "proxy2vpn_network"
        if not client.networks.list(names=[network_name]):
            client.networks.create(name=network_name, driver="bridge")
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
    except DockerException as exc:
        raise RuntimeError(
            f"Failed to create VPN container {service.name}: {exc}"
        ) from exc


def start_container(name: str) -> Container:
    """Start an existing container by name."""
    client = _client()
    try:
        container = client.containers.get(name)
        container.start()
        return container
    except DockerException as exc:
        raise RuntimeError(f"Failed to start container {name}: {exc}") from exc


def stop_container(name: str) -> Container:
    """Stop a running container by name."""
    client = _client()
    try:
        container = client.containers.get(name)
        container.stop()
        return container
    except DockerException as exc:
        raise RuntimeError(f"Failed to stop container {name}: {exc}") from exc


def restart_container(name: str) -> Container:
    """Restart a container by name and return it."""
    client = _client()
    try:
        container = client.containers.get(name)
        container.restart()
        container.reload()
        return container
    except DockerException as exc:
        raise RuntimeError(f"Failed to restart container {name}: {exc}") from exc


def remove_container(name: str) -> None:
    """Remove a container by name."""
    client = _client()
    try:
        container = client.containers.get(name)
        container.remove(force=True)
    except DockerException as exc:
        raise RuntimeError(f"Failed to remove container {name}: {exc}") from exc


def container_logs(name: str, lines: int = 100, follow: bool = False) -> Iterator[str]:
    """Yield log lines from a container.

    If ``follow`` is ``True`` the generator will yield new log lines as they
    arrive until the container stops or the caller interrupts.  Otherwise the
    last ``lines`` lines are returned.
    """

    client = _client()
    try:
        container = client.containers.get(name)
        if follow:
            for line in container.logs(stream=True, follow=True, tail=lines):
                yield line.decode().rstrip()
        else:
            output = container.logs(tail=lines).decode().splitlines()
            for line in output:
                yield line
    except DockerException as exc:
        raise RuntimeError(f"Failed to fetch logs for {name}: {exc}") from exc


def list_containers(all: bool = False) -> list[Container]:
    """List containers."""
    client = _client()
    try:
        return client.containers.list(all=all)
    except DockerException as exc:
        raise RuntimeError(f"Failed to list containers: {exc}") from exc


def get_vpn_containers(all: bool = False) -> list[Container]:
    """Return containers labeled as VPN services."""
    client = _client()
    try:
        return client.containers.list(all=all, filters={"label": "vpn.type=vpn"})
    except DockerException as exc:
        raise RuntimeError(f"Failed to list VPN containers: {exc}") from exc


def get_problematic_containers(all: bool = False) -> list[Container]:
    """Return containers that are not running properly."""

    try:
        containers = get_vpn_containers(all=all)
    except RuntimeError:
        return []
    problematic: list[Container] = []
    for container in containers:
        try:
            container.reload()
            state = container.attrs.get("State", {})
            if (
                container.status != "running"
                or state.get("ExitCode", 0) != 0
                or state.get("RestartCount", 0) > 0
            ):
                problematic.append(container)
        except DockerException:
            problematic.append(container)
    return problematic


def get_container_diagnostics(container: Container) -> dict:
    """Return diagnostic information for a container."""

    try:
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
    except DockerException as exc:
        raise RuntimeError(
            f"Failed to inspect container {container.name}: {exc}"
        ) from exc


def analyze_container_logs(
    name: str, lines: int = 100, analyzer: DiagnosticAnalyzer | None = None
) -> list[DiagnosticResult]:
    """Analyze container logs and return diagnostic results."""
    client = _client()
    try:
        container = client.containers.get(name)
        if analyzer is None:
            analyzer = DiagnosticAnalyzer()
        logs = list(container_logs(name, lines=lines, follow=False))
        port_label = container.labels.get("vpn.port")
        port = int(port_label) if port_label and port_label.isdigit() else None
        return analyzer.analyze(logs, port=port)
    except DockerException as exc:
        raise RuntimeError(f"Failed to analyze logs for {name}: {exc}") from exc


def start_all_vpn_containers(manager: ComposeManager) -> list[tuple[str, bool]]:
    """Start all VPN containers, creating any missing ones."""
    client = _client()
    results: list[tuple[str, bool]] = []
    try:
        existing = {c.name: c for c in client.containers.list(all=True)}
        for svc in manager.list_services():
            container = existing.get(svc.name)
            if container is None:
                profile = manager.get_profile(svc.profile)
                container = create_vpn_container(svc, profile)
            if container.status != "running":
                _retry(container.start, exceptions=(DockerException,))
                results.append((svc.name, True))
            else:
                results.append((svc.name, False))
    except DockerException as exc:
        raise RuntimeError(f"Failed to start containers: {exc}") from exc
    return results


def stop_all_vpn_containers() -> list[str]:
    """Stop all running VPN containers.

    Returns a list of container names that were stopped.
    """

    try:
        containers = get_vpn_containers(all=False)
    except RuntimeError:
        return []
    results: list[str] = []
    for container in containers:
        try:
            container.stop()
            results.append(container.name)
        except DockerException:
            continue
    return results


def cleanup_orphaned_containers(manager: ComposeManager) -> list[str]:
    """Remove containers not defined in compose file."""

    try:
        containers = get_vpn_containers(all=True)
    except RuntimeError:
        return []
    defined = {svc.name for svc in manager.list_services()}
    removed: list[str] = []
    for container in containers:
        if container.name not in defined:
            try:
                container.remove(force=True)
                removed.append(container.name)
            except DockerException:
                continue
    return removed


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
        response = _retry(
            requests.get,
            "https://ifconfig.me",
            proxies={"http": f"localhost:{port}", "https": f"localhost:{port}"},
            timeout=5,
            exceptions=(requests.RequestException,),
        )
        return response.text.strip()
    except Exception:
        return "N/A"


def test_vpn_connection(name: str) -> bool:
    """Return ``True`` if the VPN proxy for NAME appears to work."""

    client = _client()
    try:
        container = client.containers.get(name)
    except DockerException:
        return False
    port = container.labels.get("vpn.port")
    if not port or container.status != "running":
        return False
    try:
        direct = _retry(requests.get, "https://ifconfig.me", timeout=5)
        proxied = _retry(
            requests.get,
            "https://ifconfig.me",
            proxies={
                "http": f"http://localhost:{port}",
                "https": f"http://localhost:{port}",
            },
            timeout=5,
        )
        return proxied.text.strip() not in {"", direct.text.strip()}
    except Exception:
        return False

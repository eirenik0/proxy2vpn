"""Interactions with Docker using the docker SDK."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Iterable, Iterator

from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer, DiagnosticResult
from proxy2vpn.core.models import Profile, VPNService
from proxy2vpn.core import config
from .compose_manager import ComposeManager
from .display_utils import console
from .logging_utils import get_logger
from .proxy_utils import (
    build_proxy_urls_from_container,
    extract_proxy_credentials_from_env,
)
from . import ip_utils

import docker
from docker.models.containers import Container
from docker.errors import DockerException, NotFound

DEFAULT_TIMEOUT = 60
LOG_WAIT_TIMEOUT = 2.0
LOG_WAIT_INTERVAL = 0.1

logger = get_logger(__name__)


def _client(timeout: int = DEFAULT_TIMEOUT) -> docker.DockerClient:
    """Return a Docker client configured from environment."""
    try:
        return docker.from_env(timeout=timeout)
    except DockerException as exc:  # pragma: no cover - connection errors
        raise RuntimeError(f"Docker unavailable: {exc}") from exc


def _retry(
    func, retries: int = 3, exceptions: tuple[type[Exception], ...] = (Exception,)
):
    """Call func with simple retry on given exceptions.

    Retries up to ``retries`` times on listed ``exceptions`` and returns the result
    of the first successful call. Re-raises the last exception if all attempts fail.
    """
    attempt = 0
    while True:
        try:
            return func()
        except exceptions:  # type: ignore[misc]
            attempt += 1
            if attempt > retries:
                raise


def _container_attrs(container: Container) -> dict[str, Any]:
    """Return container attrs as a dict, tolerating partially mocked containers."""

    attrs = getattr(container, "attrs", None)
    return attrs if isinstance(attrs, dict) else {}


def create_container(
    name: str, image: str, command: Iterable[str] | None = None
) -> Container:
    """Create a container with the given name and image.

    If a container with the same name already exists, it will be removed first
    to avoid name conflicts. The image is pulled if it is not available locally.
    """
    client = _client()
    try:
        # Remove any existing container with the same name to avoid conflicts
        try:
            existing = client.containers.get(name)
            try:
                existing.remove(force=True)
            except DockerException:
                pass
        except NotFound:
            pass

        client.images.pull(image)
        container = client.containers.create(
            image, name=name, command=list(command) if command else None, detach=True
        )
        logger.info("container_created", extra={"container_name": name, "image": image})
        console.print(f"[green]✅ Created container:[/green] {name}")
        return container
    except DockerException as exc:
        logger.error(
            "container_creation_failed",
            extra={"container_name": name, "error": str(exc)},
        )
        raise RuntimeError(f"Failed to create container {name}: {exc}") from exc


def _load_env_file(path: str) -> dict[str, str]:
    """Return environment variables loaded from PATH.

    If PATH is empty, does not exist, or is not a regular file, return an empty dict.
    """

    env: dict[str, str] = {}
    if not path:
        return env
    file_path = Path(path)
    # Only proceed if it's a regular file; ignore directories or non-existing paths
    if not file_path.is_file():
        return env
    for line in file_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def ensure_network(recreate: bool = False) -> None:
    """Ensure the proxy2vpn Docker network exists."""
    client = _client()
    network_name = "proxy2vpn_network"

    networks = client.networks.list(names=[network_name])
    if networks and not recreate:
        return

    if networks and recreate:
        network = networks[0]
        try:
            network.reload()
            # Force disconnect all containers
            for container in network.containers:
                try:
                    network.disconnect(container, force=True)
                except DockerException:
                    pass  # Ignore disconnect failures
            network.remove()
        except DockerException as exc:
            raise RuntimeError(
                f"Failed to remove network {network_name}: {exc}"
            ) from exc

    client.networks.create(name=network_name, driver="bridge")


def create_vpn_container(service: VPNService, profile: Profile) -> Container:
    """Create a container for a VPN service using its profile."""

    client = _client()
    try:
        # Remove any existing container with the same name to avoid conflicts
        try:
            existing = client.containers.get(service.name)
            try:
                existing.remove(force=True)
            except DockerException:
                pass
        except NotFound:
            pass

        client.images.pull(profile.image)
        env = _load_env_file(str(profile._resolve_env_path()))
        env.update(service.environment)
        ensure_network()
        port_bindings = {
            "8888/tcp": service.port,
            "8000/tcp": ("127.0.0.1", service.control_port),
        }
        auth_config = config.resolve_control_auth_config(compose_root=profile._base_dir)
        if not auth_config.exists():
            raise RuntimeError(
                f"Missing '{auth_config}'. Run 'proxy2vpn system init' to create required files."
            )
        volumes = {
            str(auth_config.resolve()): {
                "bind": "/gluetun/auth/config.toml",
                "mode": "ro",
            }
        }
        container = client.containers.create(
            profile.image,
            name=service.name,
            detach=True,
            ports=port_bindings,
            environment=env,
            labels=service.labels,
            cap_add=profile.cap_add,
            devices=profile.devices,
            network="proxy2vpn_network",
            volumes=volumes,
        )
        logger.info(
            "vpn_container_created",
            extra={"container_name": service.name, "image": profile.image},
        )
        console.print(f"[green]✅ Created VPN container:[/green] {service.name}")
        return container
    except DockerException as exc:
        logger.error(
            "vpn_container_creation_failed",
            extra={"container_name": service.name, "error": str(exc)},
        )
        raise RuntimeError(
            f"Failed to create VPN container {service.name}: {exc}"
        ) from exc


def recreate_vpn_container(service: VPNService, profile: Profile) -> Container:
    """Recreate a container for a VPN service."""

    try:
        remove_container(service.name)
    except RuntimeError:
        pass
    return create_vpn_container(service, profile)


def _should_cleanup_failed_start(container: Container, exc: DockerException) -> bool:
    """Return True when a failed start should remove the container."""

    error_text = str(exc).lower()
    if "port is already allocated" in error_text:
        return True
    if "address already in use" in error_text:
        return True
    if "failed to create endpoint" in error_text:
        return True

    try:
        container.reload()
    except DockerException:
        pass

    state: dict[str, Any] = {}
    try:
        state = _container_attrs(container).get("State", {})
    except Exception:
        state = {}

    status = str(state.get("Status") or getattr(container, "status", "")).lower()
    return status == "created"


def _cleanup_failed_start(container: Container, exc: DockerException) -> None:
    """Remove a container that failed to start cleanly."""

    if not _should_cleanup_failed_start(container, exc):
        return

    container_name = getattr(container, "name", "<unknown>")
    try:
        container.remove(force=True)
        logger.warning(
            "container_start_failed_cleanup",
            extra={"container_name": container_name, "error": str(exc)},
        )
        console.print(f"[yellow]🧹 Removed failed container:[/yellow] {container_name}")
    except DockerException as cleanup_exc:
        logger.warning(
            "container_start_cleanup_failed",
            extra={
                "container_name": container_name,
                "error": str(exc),
                "cleanup_error": str(cleanup_exc),
            },
        )


def start_container(name: str) -> Container:
    """Start an existing container by name."""
    client = _client()
    try:
        container = client.containers.get(name)
    except DockerException as exc:
        logger.error(
            "container_start_failed", extra={"container_name": name, "error": str(exc)}
        )
        raise  # Let the original exception propagate to preserve NotFound type

    try:
        container.start()
        logger.info("container_started", extra={"container_name": name})
        console.print(f"[green]🚀 Started container:[/green] {name}")
        return container
    except DockerException as exc:
        _cleanup_failed_start(container, exc)
        logger.error(
            "container_start_failed",
            extra={"container_name": name, "error": str(exc)},
        )
        raise  # Let the original exception propagate to preserve NotFound type


def start_vpn_service(service: VPNService, profile: Profile, force: bool) -> Container:
    """Ensure a VPN service container exists and is running."""

    if force:
        recreate_vpn_container(service, profile)
        return start_container(service.name)

    try:
        return start_container(service.name)
    except NotFound:
        create_vpn_container(service, profile)
        return start_container(service.name)
    except DockerException:
        raise


def stop_container(name: str) -> Container:
    """Stop a running container by name."""
    client = _client()
    try:
        container = client.containers.get(name)
        container.stop()
        logger.info("container_stopped", extra={"container_name": name})
        console.print(f"[yellow]🛑 Stopped container:[/yellow] {name}")
        return container
    except DockerException as exc:
        logger.error(
            "container_stop_failed", extra={"container_name": name, "error": str(exc)}
        )
        raise RuntimeError(f"Failed to stop container {name}: {exc}") from exc


def restart_container(name: str) -> Container:
    """Restart a container by name and return it."""
    client = _client()
    try:
        container = client.containers.get(name)
        container.restart()
        container.reload()
        logger.info("container_restarted", extra={"container_name": name})
        console.print(f"[blue]🔄 Restarted container:[/blue] {name}")
        return container
    except DockerException as exc:
        logger.error(
            "container_restart_failed",
            extra={"container_name": name, "error": str(exc)},
        )
        raise RuntimeError(f"Failed to restart container {name}: {exc}") from exc


def remove_container(name: str) -> None:
    """Remove a container by name."""
    client = _client()
    try:
        container = client.containers.get(name)
        container.remove(force=True)
        logger.info("container_removed", extra={"container_name": name})
        console.print(f"[red]🗑️ Removed container:[/red] {name}")
    except DockerException as exc:
        logger.error(
            "container_remove_failed", extra={"container_name": name, "error": str(exc)}
        )
        raise RuntimeError(f"Failed to remove container {name}: {exc}") from exc


def _decode_log_output(output: bytes | list[bytes] | list[str] | str) -> list[str]:
    """Return decoded log lines from docker SDK output."""
    if isinstance(output, list):
        lines: list[str] = []
        for line in output:
            lines.append(
                line.decode(errors="replace").rstrip()
                if isinstance(line, bytes)
                else str(line).rstrip()
            )
        return lines

    if isinstance(output, bytes):
        return output.decode(errors="replace").splitlines()

    return str(output).splitlines()


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
                if isinstance(line, bytes):
                    yield line.decode(errors="replace").rstrip()
                else:
                    yield str(line).rstrip()
        else:
            deadline = time.monotonic() + LOG_WAIT_TIMEOUT
            while True:
                output = container.logs(tail=lines)
                decoded = _decode_log_output(output)
                if decoded:
                    for line in decoded:
                        yield line
                    return

                container.reload()
                state = _container_attrs(container).get("State", {})
                status = state.get("Status") or getattr(container, "status", "")
                if status not in {"created", "running", "restarting"}:
                    return
                if time.monotonic() >= deadline:
                    return
                time.sleep(LOG_WAIT_INTERVAL)
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


def get_container_by_service_name(service_name: str) -> Container | None:
    """Get container by service name"""
    try:
        containers = get_vpn_containers(all=True)
        for container in containers:
            if container.name == service_name:
                return container
        return None
    except RuntimeError:
        return None


def get_service_status_counts(names: list[str]) -> tuple[int, int]:
    """Return counts of running and stopped services for given names."""
    containers = {c.name: c for c in get_vpn_containers(all=True)}
    running = sum(
        1
        for name in names
        if (container := containers.get(name)) and container.status == "running"
    )
    return running, len(names) - running


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
            state = _container_attrs(container).get("State", {})
            if (
                container.status != "running"
                or state.get("ExitCode", 0) != 0
                or state.get("RestartCount", 0) > 0
            ):
                problematic.append(container)
        except DockerException:
            problematic.append(container)
    return problematic


def _network_container_names(network) -> list[str]:
    """Return container names attached to a Docker network."""

    names: list[str] = []
    network_attrs = getattr(network, "attrs", {}) or {}
    raw_containers = network_attrs.get("Containers", {}) or {}
    if isinstance(raw_containers, dict):
        for container_info in raw_containers.values():
            if not isinstance(container_info, dict):
                continue
            name = container_info.get("Name")
            if name:
                names.append(str(name))

    if names:
        return sorted(set(names))

    containers = getattr(network, "containers", None) or []
    for container in containers:
        name = getattr(container, "name", None)
        if name:
            names.append(str(name))
    return sorted(set(names))


def get_network_interconnection_diagnostics(
    expected_containers: Iterable[str] | None = None,
    network_name: str = "proxy2vpn_network",
) -> dict[str, object]:
    """Inspect the proxy2vpn Docker network and report attached containers.

    The returned mapping is designed for human-readable CLI output and JSON
    output alike.  It reports whether the network exists, which VPN containers
    are attached to it, and which expected containers are missing.
    """

    client = _client()
    expected = sorted({name for name in expected_containers or [] if name})
    try:
        network = client.networks.get(network_name)
        try:
            network.reload()
        except DockerException:
            pass
    except NotFound:
        return {
            "kind": "network",
            "network": network_name,
            "status": "missing",
            "health": 0,
            "issues": [f"Docker network '{network_name}' not found"],
            "recommendations": [
                "Run 'proxy2vpn system init' to create the proxy2vpn network."
            ],
            "connected": [],
            "expected": expected,
            "missing": expected,
        }
    except DockerException as exc:
        return {
            "kind": "network",
            "network": network_name,
            "status": "unavailable",
            "health": 0,
            "issues": [f"Failed to inspect Docker network '{network_name}': {exc}"],
            "recommendations": ["Check Docker daemon access and network permissions."],
            "connected": [],
            "expected": expected,
            "missing": expected,
        }

    connected = _network_container_names(network)
    connected_set = set(connected)
    missing = [name for name in expected if name not in connected_set]

    issues: list[str] = []
    recommendations: list[str] = []
    if expected and missing:
        issues.append(
            f"Containers not attached to '{network_name}': {', '.join(missing)}"
        )
        recommendations.append(
            "Recreate or start the missing containers so they join the shared network."
        )
    elif not connected:
        issues.append(f"No containers are attached to '{network_name}'")
        recommendations.append(
            "Create or start VPN containers so the interconnection network is populated."
        )

    health = 100 if not issues else 0
    status = "healthy" if not issues else "degraded"
    if connected and not expected:
        recommendations.append("Pass expected container names to verify full topology.")

    return {
        "kind": "network",
        "network": network_name,
        "status": status,
        "health": health,
        "issues": issues,
        "recommendations": recommendations,
        "connected": connected,
        "expected": expected,
        "missing": missing,
    }


def get_container_diagnostics(container: Container) -> dict[str, Any]:
    """Return diagnostic information for a container."""

    try:
        container.reload()
        state = _container_attrs(container).get("State", {})
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
    name: str,
    lines: int = 100,
    analyzer: DiagnosticAnalyzer | None = None,
    timeout: int = 5,
    direct_ip: str | None = None,
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

        # Extract HTTP proxy credentials from container environment
        env_vars = _container_attrs(container).get("Config", {}).get("Env", [])
        proxy_user, proxy_password = extract_proxy_credentials_from_env(env_vars)

        return analyzer.analyze(
            logs,
            port=port,
            proxy_user=proxy_user,
            proxy_password=proxy_password,
            timeout=timeout,
            direct_ip=direct_ip,
        )
    except DockerException as exc:
        raise RuntimeError(f"Failed to analyze logs for {name}: {exc}") from exc


def start_all_vpn_containers(manager: ComposeManager) -> list[str]:
    """Ensure all VPN containers exist and are running."""

    results: list[str] = []
    for svc in manager.list_services():
        profile = manager.get_profile(svc.profile)
        start_vpn_service(svc, profile, force=False)
        results.append(svc.name)
    return results


def update_vpn_service(service: VPNService, profile: Profile) -> Container:
    """Pull, recreate, and start a VPN service container."""

    recreate_vpn_container(service, profile)
    return start_container(service.name)


def update_all_vpn_containers(manager: ComposeManager) -> list[str]:
    """Pull, recreate, and start all VPN containers."""

    results: list[str] = []
    for svc in manager.list_services():
        profile = manager.get_profile(svc.profile)
        update_vpn_service(svc, profile)
        results.append(svc.name)
    return results


def stop_all_vpn_containers() -> list[str]:
    """Stop and remove all running VPN containers.

    Returns a list of container names that were removed.
    """

    try:
        containers = get_vpn_containers(all=False)
    except RuntimeError:
        return []
    results: list[str] = []
    for container in containers:
        try:
            container.stop()
            container.remove(force=True)
            if container.name is not None:
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
    compose_owner = str(manager.compose_path.expanduser().resolve())
    defined = {svc.name for svc in manager.list_services()}
    removed: list[str] = []
    for container in containers:
        owner = (container.labels or {}).get(config.COMPOSE_FILE_LABEL, "").strip()
        if owner != compose_owner:
            continue
        if container.name not in defined:
            try:
                container.remove(force=True)
                if container.name is not None:
                    removed.append(container.name)
            except DockerException:
                continue
    return removed


def _get_authenticated_proxy_url(container: Container, port: str) -> dict[str, str]:
    """Return authenticated proxy URLs for HTTP and HTTPS protocols.

    Extracts HTTPPROXY_USER and HTTPPROXY_PASSWORD from container environment
    variables and constructs authenticated proxy URLs. Falls back to
    unauthenticated URLs if credentials are not available.
    """
    try:
        return build_proxy_urls_from_container(container, port)
    except Exception:
        # Fall back to unauthenticated proxy URLs on any error
        return {"http": f"http://localhost:{port}", "https": f"http://localhost:{port}"}


def get_container_ip(container: Container, timeout: int = 3) -> str:
    """Return the external IP address for a running container.

    The IP address is retrieved from external services through the proxy
    exposed on the port specified by the ``vpn.port`` label. If the container
    is not running, has no port label or the request fails, ``"N/A"`` is
    returned.
    """

    port = container.labels.get("vpn.port")
    if not port or container.status != "running":
        return "N/A"
    proxies = _get_authenticated_proxy_url(container, port)
    ip = ip_utils.fetch_ip(proxies=proxies, timeout=timeout)
    return ip or "N/A"


async def get_container_ip_async(container: Container, timeout: int = 3) -> str:
    """Asynchronously return the external IP address for a running container.

    This uses :func:`ip_utils.fetch_ip_async` to concurrently query IP services.
    If the container is not running, lacks a port label or the request fails,
    ``"N/A"`` is returned.
    """

    port = container.labels.get("vpn.port")
    if not port or container.status != "running":
        return "N/A"
    proxies = _get_authenticated_proxy_url(container, port)
    ip = await ip_utils.fetch_ip_async(proxies=proxies, timeout=timeout)
    return ip or "N/A"


async def collect_proxy_info(include_credentials: bool = True) -> list[dict[str, str]]:
    """Return proxy connection details for VPN containers."""
    try:
        containers = get_vpn_containers(all=True)
    except RuntimeError:
        return []

    host_ip = await ip_utils.fetch_ip_async()
    results = []

    for container in containers:
        # Extract environment variables simply
        env_vars = _container_attrs(container).get("Config", {}).get("Env", [])
        proxy_user, proxy_password = extract_proxy_credentials_from_env(env_vars)

        status = "active" if container.status == "running" else "stopped"
        host = host_ip if container.status == "running" else ""

        results.append(
            {
                "host": host,
                "port": container.labels.get("vpn.port", ""),
                "username": proxy_user or "" if include_credentials else "",
                "password": proxy_password or "" if include_credentials else "",
                "location": container.labels.get("vpn.location", ""),
                "status": status,
            }
        )

    return results


async def test_vpn_connection_async(name: str, timeout: int = 3) -> bool:
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
        proxies = _get_authenticated_proxy_url(container, port)
        # Fetch both IPs concurrently for faster testing
        import asyncio

        direct_task = asyncio.create_task(ip_utils.fetch_ip_async(timeout=timeout))
        proxied_task = asyncio.create_task(
            ip_utils.fetch_ip_async(proxies=proxies, timeout=timeout)
        )

        direct, proxied = await asyncio.gather(direct_task, proxied_task)
        return proxied not in {"", direct}
    except Exception:
        return False


def test_vpn_connection(name: str, timeout: int = 3) -> bool:
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
        proxies = _get_authenticated_proxy_url(container, port)
        # Use sync IP fetching for simplicity
        direct = ip_utils.fetch_ip(timeout=timeout)
        proxied = ip_utils.fetch_ip(proxies=proxies, timeout=timeout)
        return proxied not in {"", direct}
    except Exception:
        return False

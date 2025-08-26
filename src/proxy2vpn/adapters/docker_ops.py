"""Interactions with Docker using the docker SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer, DiagnosticResult
from proxy2vpn.core.models import Profile, VPNService
from proxy2vpn.core import config
from .compose_manager import ComposeManager
from .display_utils import console
from .logging_utils import get_logger
from . import ip_utils

import docker
from docker.models.containers import Container
from docker.errors import DockerException, NotFound

DEFAULT_TIMEOUT = 60

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


def create_container(
    name: str, image: str, command: Iterable[str] | None = None
) -> Container:
    """Create a container with the given name and image.

    The image is pulled if it is not available locally.
    """
    client = _client()
    try:
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
        client.images.pull(profile.image)
        env = _load_env_file(profile.env_file)
        env.update(service.environment)
        ensure_network()
        port_bindings = {
            "8888/tcp": service.port,
            "8000/tcp": ("127.0.0.1", service.control_port),
        }
        auth_config = config.CONTROL_AUTH_CONFIG_FILE
        if not auth_config.exists():
            auth_config.parent.mkdir(parents=True, exist_ok=True)
            auth_config.write_text(config.CONTROL_AUTH_CONFIG_TEMPLATE)
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


def start_container(name: str) -> Container:
    """Start an existing container by name."""
    client = _client()
    try:
        container = client.containers.get(name)
        container.start()
        logger.info("container_started", extra={"container_name": name})
        console.print(f"[green]🚀 Started container:[/green] {name}")
        return container
    except DockerException as exc:
        logger.error(
            "container_start_failed", extra={"container_name": name, "error": str(exc)}
        )
        raise  # Let the original exception propagate to preserve NotFound type


def start_vpn_service(service: VPNService, profile: Profile, force: bool) -> Container:
    """Ensure a VPN service container exists and is running."""

    if force:
        container = recreate_vpn_container(service, profile)
        container.start()
        return container

    try:
        return start_container(service.name)
    except NotFound:
        container = create_vpn_container(service, profile)
        container.start()
        return container
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


def start_all_vpn_containers(manager: ComposeManager) -> list[str]:
    """Recreate and start all VPN containers."""

    results: list[str] = []
    for svc in manager.list_services():
        profile = manager.get_profile(svc.profile)
        start_vpn_service(svc, profile, force=True)
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
    defined = {svc.name for svc in manager.list_services()}
    removed: list[str] = []
    for container in containers:
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
        # Extract environment variables from container
        env_list = container.attrs.get("Config", {}).get("Env", [])
        env_vars = {}
        for env_var in env_list:
            if "=" in env_var:
                key, value = env_var.split("=", 1)
                env_vars[key] = value

        # Check for proxy credentials
        proxy_user = env_vars.get("HTTPPROXY_USER")
        proxy_password = env_vars.get("HTTPPROXY_PASSWORD")

        if proxy_user and proxy_password:
            # Use authenticated proxy URLs
            auth_url = f"http://{proxy_user}:{proxy_password}@localhost:{port}"
            return {"http": auth_url, "https": auth_url}
        else:
            # Fall back to unauthenticated proxy URLs
            base_url = f"http://localhost:{port}"
            return {"http": base_url, "https": base_url}
    except Exception:
        # Fall back to unauthenticated proxy URLs on any error
        base_url = f"http://localhost:{port}"
        return {"http": base_url, "https": base_url}


def get_container_ip(container: Container) -> str:
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
    ip = ip_utils.fetch_ip(proxies=proxies)
    return ip or "N/A"


async def get_container_ip_async(container: Container) -> str:
    """Asynchronously return the external IP address for a running container.

    This uses :func:`ip_utils.fetch_ip_async` to concurrently query IP services.
    If the container is not running, lacks a port label or the request fails,
    ``"N/A"`` is returned.
    """

    port = container.labels.get("vpn.port")
    if not port or container.status != "running":
        return "N/A"
    proxies = _get_authenticated_proxy_url(container, port)
    ip = await ip_utils.fetch_ip_async(proxies=proxies)
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
        env_vars = {}
        if hasattr(container, "attrs") and container.attrs:
            for env_var in container.attrs.get("Config", {}).get("Env", []):
                if "=" in env_var:
                    key, value = env_var.split("=", 1)
                    env_vars[key] = value

        status = "active" if container.status == "running" else "stopped"
        host = host_ip if container.status == "running" else ""

        results.append(
            {
                "host": host,
                "port": container.labels.get("vpn.port", ""),
                "username": env_vars.get("HTTPPROXY_USER", "")
                if include_credentials
                else "",
                "password": env_vars.get("HTTPPROXY_PASSWORD", "")
                if include_credentials
                else "",
                "location": container.labels.get("vpn.location", ""),
                "status": status,
            }
        )

    return results


async def test_vpn_connection_async(name: str) -> bool:
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

        direct_task = asyncio.create_task(ip_utils.fetch_ip_async())
        proxied_task = asyncio.create_task(ip_utils.fetch_ip_async(proxies=proxies))

        direct, proxied = await asyncio.gather(direct_task, proxied_task)
        return proxied not in {"", direct}
    except Exception:
        return False


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
        proxies = _get_authenticated_proxy_url(container, port)
        # Use sync IP fetching for simplicity
        direct = ip_utils.fetch_ip()
        proxied = ip_utils.fetch_ip(proxies=proxies)
        return proxied not in {"", direct}
    except Exception:
        return False

"""Gluetun runtime operations, with Docker and control clients kept internal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from proxy2vpn.adapters import docker_ops, ip_utils
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.http_client import GluetunControlClient
from proxy2vpn.core.models import Profile, VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer, DiagnosticResult


@dataclass
class RuntimeInspection:
    """Observed health inputs; inspection/diagnostics errors make it incomplete."""

    container_status: str
    results: list[DiagnosticResult] = field(default_factory=list)
    control_api_reachable: bool = False
    current_egress_ip: str | None = None
    direct_ip: str | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def failure(self) -> str | None:
        return self.errors.get("inspection", self.errors.get("diagnostics"))


@dataclass
class RuntimeEvidence:
    """Recent diagnostic evidence; None status means no live container found."""

    container_status: str | None
    log_lines: list[str] = field(default_factory=list)
    results: list[DiagnosticResult] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeActionResult:
    """Whether a runtime request completed, independently of later health checks."""

    success: bool
    error: str | None = None


@dataclass
class RuntimeCleanupResult:
    """Names removed from this compose root, or a failure to perform cleanup."""

    removed: list[str] = field(default_factory=list)
    error: str | None = None


class GluetunRuntimeInterface(Protocol):
    """The runtime seam used by orchestration and its in-memory test doubles."""

    async def inspect(
        self, service: VPNService, *, lines: int = 20, timeout: int | None = None
    ) -> RuntimeInspection: ...

    async def collect_evidence(
        self, service_name: str, *, lines: int = 20, timeout: int = 5
    ) -> RuntimeEvidence: ...

    async def control_status(self, service: VPNService) -> RuntimeActionResult: ...

    async def restart_tunnel(self, service: VPNService) -> RuntimeActionResult: ...

    async def restore(
        self, service: VPNService, profile: Profile
    ) -> RuntimeActionResult: ...

    async def cleanup_orphans(
        self, manager: ComposeManager
    ) -> RuntimeCleanupResult: ...


# @lat: [[lat.md/gluetun-runtime#Gluetun Runtime]]
class GluetunRuntime:
    """Own Gluetun runtime I/O, but never desired compose state or recovery policy."""

    def __init__(
        self,
        *,
        probe_timeout: int = 5,
        control_api_timeout: float = 5.0,
        control_api_retry_attempts: int = 0,
        docker_backend: Any = None,
        control_client_factory: Callable[..., Any] | None = None,
        direct_ip_fetcher: Callable[..., str | None] | None = None,
    ) -> None:
        self.probe_timeout = probe_timeout
        self.control_api_timeout = control_api_timeout
        self.control_api_retry_attempts = control_api_retry_attempts
        self._docker = docker_backend if docker_backend is not None else docker_ops
        self._control_client_factory = control_client_factory
        self._direct_ip_fetcher = direct_ip_fetcher

    def _container(self, service_name: str) -> tuple[Any, str, dict[str, str]]:
        container = self._docker.get_container_by_service_name(
            service_name, strict=True
        )
        if container is None:
            return None, "missing", {}
        errors: dict[str, str] = {}
        try:
            container.reload()
        except Exception as exc:
            # Preserve the last known SDK state when a refresh fails.
            errors["reload"] = str(exc)
        return container, getattr(container, "status", "unknown") or "unknown", errors

    async def inspect(
        self, service: VPNService, *, lines: int = 20, timeout: int | None = None
    ) -> RuntimeInspection:
        effective_timeout = timeout or self.probe_timeout
        try:
            container, status, errors = await asyncio.to_thread(
                self._container, service.name
            )
        except Exception as exc:
            return RuntimeInspection("unknown", errors={"inspection": str(exc)})
        observation = RuntimeInspection(status, errors=errors)
        if container is None or status != "running":
            return observation
        labels = getattr(container, "labels", {}) or {}
        has_proxy_port = isinstance(labels, dict) and bool(labels.get("vpn.port"))
        if has_proxy_port:
            try:
                observation.direct_ip = await asyncio.to_thread(
                    self._direct_ip_fetcher or ip_utils.fetch_ip,
                    timeout=effective_timeout,
                )
            except Exception as exc:
                observation.errors["direct_ip"] = str(exc)
        try:
            observation.results = await asyncio.to_thread(
                self._docker.analyze_container_logs,
                service.name,
                lines,
                DiagnosticAnalyzer(),
                effective_timeout,
                observation.direct_ip,
            )
        except Exception as exc:
            observation.errors["diagnostics"] = str(exc)
            return observation
        control = await self.control_status(service)
        observation.control_api_reachable = control.success
        if control.error is not None:
            observation.errors["control"] = control.error
        if has_proxy_port:
            try:
                ip = await self._docker.get_container_ip_async(
                    container, timeout=effective_timeout
                )
                observation.current_egress_ip = None if ip == "N/A" else ip
            except Exception as exc:
                observation.errors["egress_ip"] = str(exc)
        return observation

    async def collect_evidence(
        self, service_name: str, *, lines: int = 20, timeout: int = 5
    ) -> RuntimeEvidence:
        try:
            container, status, errors = await asyncio.to_thread(
                self._container, service_name
            )
        except Exception as exc:
            return RuntimeEvidence(None, errors={"inspection": str(exc)})
        evidence = RuntimeEvidence(
            status if container is not None else None, errors=errors
        )
        if status != "running":
            return evidence

        def read_logs() -> list[str]:
            return [
                str(line).strip()
                for line in self._docker.container_logs(
                    service_name, lines=lines, follow=False
                )
            ]

        try:
            evidence.log_lines = await asyncio.to_thread(read_logs)
        except Exception as exc:
            evidence.errors["logs"] = str(exc)
        try:
            evidence.results = await asyncio.to_thread(
                self._docker.analyze_container_logs,
                service_name,
                lines,
                DiagnosticAnalyzer(),
                timeout,
            )
        except Exception as exc:
            evidence.errors["diagnostics"] = str(exc)
        return evidence

    async def _control_request(
        self, service: VPNService, *, restart: bool
    ) -> RuntimeActionResult:
        factory = self._control_client_factory or GluetunControlClient
        try:
            async with factory(
                f"http://localhost:{service.control_port}/v1",
                timeout=self.control_api_timeout,
                retry_attempts=self.control_api_retry_attempts,
            ) as client:
                if restart:
                    await client.restart_tunnel()
                else:
                    await client.status()
            return RuntimeActionResult(True)
        except Exception as exc:
            return RuntimeActionResult(False, str(exc))

    async def control_status(self, service: VPNService) -> RuntimeActionResult:
        return await self._control_request(service, restart=False)

    async def restart_tunnel(self, service: VPNService) -> RuntimeActionResult:
        return await self._control_request(service, restart=True)

    async def restore(
        self, service: VPNService, profile: Profile
    ) -> RuntimeActionResult:
        try:
            # Reuse recreation semantics, relative paths and auth mounts from
            # the existing CLI helpers; discard their Docker SDK return value.
            await asyncio.to_thread(
                self._docker.start_vpn_service, service, profile, True
            )
            return RuntimeActionResult(True)
        except Exception as exc:
            return RuntimeActionResult(False, str(exc))

    async def cleanup_orphans(self, manager: ComposeManager) -> RuntimeCleanupResult:
        try:
            removed = await asyncio.to_thread(
                self._docker.cleanup_orphaned_containers, manager
            )
            return RuntimeCleanupResult(removed)
        except Exception as exc:
            return RuntimeCleanupResult(error=str(exc))

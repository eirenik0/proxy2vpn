"""Shared service health assessment for watchdog and fleet workflows."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from proxy2vpn.adapters import docker_ops, ip_utils
from proxy2vpn.adapters.http_client import GluetunControlClient
from proxy2vpn.adapters.logging_utils import get_logger
from proxy2vpn.core.models import VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer, DiagnosticResult


logger = get_logger(__name__)


class PeerEvidence(BaseModel):
    """Peer health classification for services sharing a profile."""

    healthy: list[str] = Field(default_factory=list)
    auth_config: list[str] = Field(default_factory=list)
    other_unhealthy: list[str] = Field(default_factory=list)
    probe_failed: list[str] = Field(default_factory=list)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class HealthAssessment(BaseModel):
    """Shared assessment result for a VPN service."""

    service_name: str
    assessed_at: datetime
    container_status: str
    health_score: int
    health_class: str
    failing_checks: list[str] = Field(default_factory=list)
    results: list[DiagnosticResult] = Field(default_factory=list)
    control_api_reachable: bool = False
    current_egress_ip: str | None = None
    direct_ip: str | None = None
    peer_evidence: PeerEvidence = Field(default_factory=PeerEvidence)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class HealthAssessmentService:
    """Assess VPN service health using the shared diagnostic stack."""

    def __init__(
        self,
        threshold: int = 60,
        *,
        probe_timeout: int = 5,
        control_api_timeout: float = 5.0,
        control_api_retry_attempts: int = 0,
    ) -> None:
        self.threshold = threshold
        self.probe_timeout = probe_timeout
        self.control_api_timeout = control_api_timeout
        self.control_api_retry_attempts = control_api_retry_attempts

    async def assess_service(
        self,
        service: VPNService,
        *,
        peer_assessments: dict[str, HealthAssessment] | None = None,
        lines: int = 20,
        timeout: int | None = None,
    ) -> HealthAssessment:
        """Return a complete health assessment for one service."""

        effective_timeout = timeout or self.probe_timeout
        container = docker_ops.get_container_by_service_name(service.name)
        assessed_at = datetime.now(timezone.utc)
        if container is None:
            return HealthAssessment(
                service_name=service.name,
                assessed_at=assessed_at,
                container_status="missing",
                health_score=0,
                health_class="missing",
                failing_checks=["container_missing"],
                control_api_reachable=False,
                peer_evidence=self._peer_evidence(service, peer_assessments),
            )

        try:
            container.reload()
        except Exception:
            pass

        container_status = getattr(container, "status", "unknown") or "unknown"
        container_labels = getattr(container, "labels", {}) or {}
        has_proxy_port = isinstance(container_labels, dict) and bool(
            container_labels.get("vpn.port")
        )
        if container_status != "running":
            return HealthAssessment(
                service_name=service.name,
                assessed_at=assessed_at,
                container_status=container_status,
                health_score=0,
                health_class="container_stopped",
                failing_checks=["container_not_running"],
                control_api_reachable=False,
                peer_evidence=self._peer_evidence(service, peer_assessments),
            )

        analyzer = DiagnosticAnalyzer()
        direct_ip = await self._direct_ip(effective_timeout) if has_proxy_port else None
        results = await asyncio.to_thread(
            docker_ops.analyze_container_logs,
            service.name,
            lines,
            analyzer,
            effective_timeout,
            direct_ip,
        )
        health_score = analyzer.health_score(results)
        failing_checks = [result.check for result in results if not result.passed]
        control_api_reachable = await self._control_api_reachable(service)
        current_egress_ip = None
        if has_proxy_port:
            try:
                current_egress_ip = await docker_ops.get_container_ip_async(
                    container, timeout=effective_timeout
                )
            except Exception:
                current_egress_ip = None
        if current_egress_ip == "N/A":
            current_egress_ip = None

        return HealthAssessment(
            service_name=service.name,
            assessed_at=assessed_at,
            container_status=container_status,
            health_score=health_score,
            health_class=self._classify(container_status, health_score, results),
            failing_checks=failing_checks,
            results=results,
            control_api_reachable=control_api_reachable,
            current_egress_ip=current_egress_ip,
            direct_ip=direct_ip,
            peer_evidence=self._peer_evidence(service, peer_assessments),
        )

    async def assess_services(
        self,
        services: list[VPNService],
        *,
        lines: int = 20,
        timeout: int | None = None,
        progress_callback: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> dict[str, HealthAssessment]:
        """Assess a batch of services and enrich each result with peer evidence."""

        assessments: dict[str, HealthAssessment] = {}

        async def _assess(
            service: VPNService,
        ) -> tuple[VPNService, HealthAssessment | None, Exception | None]:
            try:
                assessment = await self.assess_service(
                    service,
                    lines=lines,
                    timeout=timeout,
                )
                return service, assessment, None
            except Exception as exc:
                return service, None, exc

        assessment_tasks = [
            asyncio.create_task(_assess(service)) for service in services
        ]

        for task in asyncio.as_completed(assessment_tasks):
            service, assessment, error = await task
            if assessment is not None:
                assessments[assessment.service_name] = assessment
            else:
                logger.warning(
                    "health_assessment_failed",
                    extra={"service_name": service.name, "error": str(error)},
                )
                assessments[service.name] = HealthAssessment(
                    service_name=service.name,
                    assessed_at=datetime.now(timezone.utc),
                    container_status="unknown",
                    health_score=0,
                    health_class="assessment_failed",
                    failing_checks=["assessment_error"],
                    control_api_reachable=False,
                )

            if progress_callback is not None:
                callback_result = progress_callback(service.name)
                if isawaitable(callback_result):
                    await callback_result

        enriched = {
            name: assessment.model_copy(
                update={
                    "peer_evidence": self._peer_evidence_from_map(
                        services, assessments, name
                    )
                }
            )
            for name, assessment in assessments.items()
        }
        return enriched

    def _classify(
        self,
        container_status: str,
        health_score: int,
        results: list[DiagnosticResult],
    ) -> str:
        if health_score >= self.threshold:
            return "healthy"
        checks = {result.check for result in results if not result.passed}
        if "auth_failure" in checks or "config_error" in checks:
            return "auth_config"
        if "connectivity" in checks:
            return "connectivity"
        if container_status not in {"running", "unknown"}:
            return "container_stopped"
        return "degraded"

    async def _direct_ip(self, timeout: int) -> str | None:
        try:
            return await asyncio.to_thread(ip_utils.fetch_ip, timeout=timeout)
        except Exception:
            return None

    async def _control_api_reachable(self, service: VPNService) -> bool:
        base_url = f"http://localhost:{service.control_port}/v1"
        try:
            async with GluetunControlClient(
                base_url,
                timeout=self.control_api_timeout,
                retry_attempts=self.control_api_retry_attempts,
            ) as client:
                await client.status()
            return True
        except Exception:
            return False

    def _peer_evidence(
        self,
        service: VPNService,
        peer_assessments: dict[str, HealthAssessment] | None,
    ) -> PeerEvidence:
        if not peer_assessments:
            return PeerEvidence()
        return self._peer_evidence_from_map([service], peer_assessments, service.name)

    def _peer_evidence_from_map(
        self,
        services: list[VPNService],
        peer_assessments: dict[str, HealthAssessment],
        service_name: str,
    ) -> PeerEvidence:
        current = next(
            (service for service in services if service.name == service_name), None
        )
        if current is None:
            return PeerEvidence()

        evidence = PeerEvidence()
        for candidate in services:
            if candidate.name == current.name or candidate.profile != current.profile:
                continue
            assessment = peer_assessments.get(candidate.name)
            if assessment is None:
                evidence.probe_failed.append(candidate.name)
                continue
            is_healthy = (
                assessment.container_status == "running"
                and assessment.health_score >= self.threshold
            )
            if is_healthy:
                evidence.healthy.append(candidate.name)
                continue
            if any(
                result.check in {"auth_failure", "config_error"} and not result.passed
                for result in assessment.results
            ):
                evidence.auth_config.append(candidate.name)
            else:
                evidence.other_unhealthy.append(candidate.name)

        for names in (
            evidence.healthy,
            evidence.auth_config,
            evidence.other_unhealthy,
            evidence.probe_failed,
        ):
            names.sort()
        return evidence

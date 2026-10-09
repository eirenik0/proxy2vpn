"""Shared service health assessment for watchdog and fleet workflows."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from proxy2vpn.adapters.gluetun_runtime import GluetunRuntime, GluetunRuntimeInterface
from proxy2vpn.adapters.logging_utils import get_event_logger, logging_context
from proxy2vpn.adapters.egress import GluetunEgressAdapter
from proxy2vpn.adapters.external_proxy import ExternalProxyAdapter
from proxy2vpn.core.egress import (
    EgressAdapter,
    EgressCapabilities,
    GLUETUN_CAPABILITIES,
)
from proxy2vpn.core.external_proxy import ExternalProxyEndpoint
from proxy2vpn.core.models import VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticResult


logger = get_event_logger(__name__)


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
    profile_name: str | None = None
    assessed_at: datetime
    container_status: str
    health_score: int
    health_class: str
    failing_checks: list[str] = Field(default_factory=list)
    results: list[DiagnosticResult] = Field(default_factory=list)
    control_api_reachable: bool | None = False
    source: str = "gluetun"
    capabilities: EgressCapabilities = Field(
        default_factory=lambda: GLUETUN_CAPABILITIES
    )
    available: bool | None = None
    restart_ready: bool | None = None
    authentication: bool | None = None
    connectivity: bool | None = None
    latency_ms: float | None = None
    current_egress_ip: str | None = None
    direct_ip: str | None = None
    peer_evidence: PeerEvidence = Field(default_factory=PeerEvidence)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    @model_validator(mode="after")
    def normalize_legacy_evidence(self) -> "HealthAssessment":
        # Existing callers/state fixtures still construct Gluetun health objects.
        if self.source == "gluetun":
            if self.available is None:
                object.__setattr__(
                    self, "available", self.container_status == "running"
                )
            if self.restart_ready is None:
                object.__setattr__(
                    self, "restart_ready", bool(self.control_api_reachable)
                )
        return self


# @lat: [[lat.md/health#Health Assessment]]
class HealthAssessmentService:
    """Assess VPN service health using the shared diagnostic stack."""

    def __init__(
        self,
        threshold: int = 60,
        *,
        probe_timeout: int = 5,
        control_api_timeout: float = 5.0,
        control_api_retry_attempts: int = 0,
        runtime: GluetunRuntimeInterface | None = None,
    ) -> None:
        self.threshold = threshold
        self.probe_timeout = probe_timeout
        self.control_api_timeout = control_api_timeout
        self.control_api_retry_attempts = control_api_retry_attempts
        self.runtime = (
            runtime
            if runtime is not None
            else GluetunRuntime(
                probe_timeout=probe_timeout,
                control_api_timeout=control_api_timeout,
                control_api_retry_attempts=control_api_retry_attempts,
            )
        )

    async def assess_service(
        self,
        service: VPNService | ExternalProxyEndpoint,
        *,
        peer_assessments: dict[str, HealthAssessment] | None = None,
        lines: int = 20,
        timeout: int | None = None,
    ) -> HealthAssessment:
        """Return a complete health assessment for one service."""

        with logging_context(service_name=service.name, provider=service.provider):
            logger.debug("health_assessment_started")
            assessment = await self._assess_service(
                service,
                peer_assessments=peer_assessments,
                lines=lines,
                timeout=timeout,
            )
            logger.info(
                "health_assessment_completed",
                health_class=assessment.health_class,
                health_score=assessment.health_score,
                failing_checks=assessment.failing_checks,
            )
            return assessment

    async def _assess_service(
        self,
        service: VPNService | ExternalProxyEndpoint,
        *,
        peer_assessments: dict[str, HealthAssessment] | None,
        lines: int,
        timeout: int | None,
    ) -> HealthAssessment:
        """Collect diagnostics inside the service's logging context."""

        assessed_at = datetime.now(timezone.utc)
        adapter: EgressAdapter
        container_status = "not_applicable"
        control_api_reachable = None
        direct_ip = None
        if isinstance(service, VPNService):
            gluetun = GluetunEgressAdapter(service, self.runtime, self.threshold)
            adapter = gluetun
            observation = await adapter.observe(lines=lines, timeout=timeout)
            assert gluetun.inspection is not None
            container_status = gluetun.inspection.container_status
            control_api_reachable = gluetun.inspection.control_api_reachable
            direct_ip = gluetun.inspection.direct_ip
        else:
            adapter = ExternalProxyAdapter(service, self.probe_timeout)
            observation = await adapter.observe(lines=lines, timeout=timeout)
        failing_checks = [r.check for r in observation.results if r.passed is not True]
        if container_status == "missing":
            failing_checks = ["container_missing"]
        elif isinstance(service, VPNService) and container_status != "running":
            failing_checks = ["container_not_running"]
        return HealthAssessment(
            service_name=adapter.identity.name,
            profile_name=service.profile,
            assessed_at=assessed_at,
            container_status=container_status,
            health_score=observation.health_score,
            health_class=observation.health_class,
            failing_checks=failing_checks,
            results=observation.results,
            control_api_reachable=control_api_reachable,
            current_egress_ip=observation.current_egress_ip,
            direct_ip=direct_ip,
            source=adapter.identity.source,
            capabilities=adapter.capabilities,
            available=observation.available,
            restart_ready=observation.restart_ready,
            authentication=observation.authentication,
            connectivity=observation.connectivity,
            latency_ms=observation.latency_ms,
            peer_evidence=self._peer_evidence(service, peer_assessments),
        )

    async def assess_services(
        self,
        services: list[VPNService | ExternalProxyEndpoint],
        *,
        lines: int = 20,
        timeout: int | None = None,
        progress_callback: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> dict[str, HealthAssessment]:
        """Assess a batch of services and enrich each result with peer evidence."""

        assessments: dict[str, HealthAssessment] = {}

        async def _assess(
            service: VPNService | ExternalProxyEndpoint,
        ) -> tuple[
            VPNService | ExternalProxyEndpoint,
            HealthAssessment | None,
            Exception | None,
        ]:
            with logging_context(service_name=service.name, provider=service.provider):
                try:
                    assessment = await self.assess_service(
                        service,
                        lines=lines,
                        timeout=timeout,
                    )
                    return service, assessment, None
                except Exception as exc:
                    logger.exception("health_assessment_failed", error=str(exc))
                    return service, None, exc

        assessment_tasks = [
            asyncio.create_task(_assess(service)) for service in services
        ]

        try:
            for task in asyncio.as_completed(assessment_tasks):
                service, assessment, error = await task
                if assessment is not None:
                    assessments[assessment.service_name] = assessment
                else:
                    assessments[service.name] = HealthAssessment(
                        service_name=service.name,
                        profile_name=service.profile,
                        assessed_at=datetime.now(timezone.utc),
                        container_status="unknown",
                        health_score=0,
                        health_class="assessment_failed",
                        failing_checks=["assessment_error"],
                        control_api_reachable=False
                        if isinstance(service, VPNService)
                        else None,
                        source="gluetun"
                        if isinstance(service, VPNService)
                        else "external_proxy",
                        capabilities=GLUETUN_CAPABILITIES
                        if isinstance(service, VPNService)
                        else EgressCapabilities(),
                    )

                if progress_callback is not None:
                    callback_result = progress_callback(service.name)
                    if isawaitable(callback_result):
                        await callback_result
        finally:
            for task in assessment_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*assessment_tasks, return_exceptions=True)

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

    def _peer_evidence(
        self,
        service: VPNService | ExternalProxyEndpoint,
        peer_assessments: dict[str, HealthAssessment] | None,
    ) -> PeerEvidence:
        if not peer_assessments or service.profile is None:
            return PeerEvidence()

        evidence = PeerEvidence()
        for candidate_name, assessment in peer_assessments.items():
            if candidate_name == service.name:
                continue
            if assessment.profile_name != service.profile:
                continue
            is_healthy = (
                assessment.available is True
                and assessment.health_score >= self.threshold
            )
            if is_healthy:
                evidence.healthy.append(candidate_name)
                continue
            if any(
                result.check in {"auth_failure", "config_error"} and not result.passed
                for result in assessment.results
            ):
                evidence.auth_config.append(candidate_name)
            else:
                evidence.other_unhealthy.append(candidate_name)

        for names in (
            evidence.healthy,
            evidence.auth_config,
            evidence.other_unhealthy,
            evidence.probe_failed,
        ):
            names.sort()
        return evidence

    def _peer_evidence_from_map(
        self,
        services: list[VPNService | ExternalProxyEndpoint],
        peer_assessments: dict[str, HealthAssessment],
        service_name: str,
    ) -> PeerEvidence:
        current = next(
            (service for service in services if service.name == service_name), None
        )
        if current is None or current.profile is None:
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
                assessment.available is True
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

"""Deterministic watchdog recovery decisions over supplied observations and time."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from proxy2vpn.agent.models import ActionRecord, AgentIncident, ServiceSnapshot
from proxy2vpn.core.egress import EgressCapabilities, GLUETUN_CAPABILITIES
from proxy2vpn.core.services.diagnostics import DiagnosticResult
from proxy2vpn.core.services.health_assessment import HealthAssessment

RecoveryPurpose = Literal["repair_connectivity", "request_different_exit_ip"]


@dataclass(frozen=True)
class ServiceIdentity:
    """Compose-resolved scope, supplied without a compose manager or file path."""

    name: str
    profile: str
    provider: str
    country: str
    capabilities: EgressCapabilities = field(
        default_factory=lambda: GLUETUN_CAPABILITIES
    )


@dataclass(frozen=True)
class RecoverySettings:
    """Explicit policy values; constructing these never reads environment settings."""

    health_threshold: int
    recheck_delay_seconds: int
    rotation_grace_period_seconds: int
    restore_cooldown_seconds: int
    incident_cooldown_seconds: int


@dataclass(frozen=True)
class RecoveryProgress:
    """Bounded progress within this cycle, separate from persisted action history."""

    phase: Literal["observed", "restarted", "restored"] = "observed"
    route_restore_attempted: bool = False


@dataclass(frozen=True)
class RecoveryContext:
    """All evidence needed to choose the next step, with no live dependencies."""

    service: ServiceIdentity
    assessment: HealthAssessment
    snapshot: ServiceSnapshot
    results: Sequence[DiagnosticResult]
    actions: Sequence[ActionRecord]
    incidents: Sequence[AgentIncident]
    services: Mapping[str, ServiceIdentity]
    assessments: Mapping[str, HealthAssessment]
    progress: RecoveryProgress = field(default_factory=RecoveryProgress)


@dataclass(frozen=True)
class FollowupObservation:
    """Required evidence after execution; an accepted request is not recovery."""

    kind: Literal["health", "connectivity", "exit_ip_change"]
    owner: Literal["watchdog", "fleet"]
    delay_seconds: int = 0

    def satisfied(
        self,
        *,
        healthy: bool,
        previous_ip: str | None = None,
        current_ip: str | None = None,
    ) -> bool:
        if self.kind == "exit_ip_change":
            return bool(
                healthy and previous_ip and current_ip and previous_ip != current_ip
            )
        return healthy


@dataclass(frozen=True)
class RecoveryDecision:
    """One supported action or terminal outcome, its reason, and required evidence."""

    action: Literal[
        "resolve", "restart_tunnel", "restore", "rotate", "incident", "wait"
    ]
    reason: str
    trigger: str = "automatic_remediation"
    purpose: RecoveryPurpose = "repair_connectivity"
    observation: FollowupObservation | None = None
    next_progress: RecoveryProgress = field(default_factory=RecoveryProgress)
    incident_type: str | None = None
    suppressed: bool = False


def action_matches_service(action: ActionRecord, service_name: str) -> bool:
    return service_name in {
        action.service_name,
        action.details.get("requested_service_name"),
        action.details.get("final_service_name"),
    }


def persistent_auth_or_config_failure(results: Sequence[DiagnosticResult]) -> bool:
    # Preserve the diagnostic ordering used by the existing watchdog.
    for result in results:
        if result.check in {"auth_failure", "config_error"} and not result.passed:
            return bool(result.persistent or result.check == "config_error")
    return False


def failed_check(results: Sequence[DiagnosticResult], check: str) -> bool:
    return any(result.check == check and not result.passed for result in results)


def persistent_route_failure(results: Sequence[DiagnosticResult]) -> bool:
    route = next(
        (r for r in results if r.check == "route_error" and not r.passed), None
    )
    return bool(route and route.persistent and failed_check(results, "connectivity"))


def recently_dismissed(
    incidents: Sequence[AgentIncident],
    service_name: str,
    incident_type: str,
    now: datetime,
    cooldown_seconds: int,
) -> bool:
    return any(
        i.service_name == service_name
        and i.type == incident_type
        and i.status == "dismissed"
        and i.updated_at >= now - timedelta(seconds=cooldown_seconds)
        for i in incidents
    )


# @lat: [[lat.md/recovery-policy#Recovery Policy]]
@dataclass(frozen=True)
class RecoveryPolicy:
    """Choose recovery steps without I/O, mutation, delays, or a clock."""

    settings: RecoverySettings

    def snapshot(
        self,
        service_name: str,
        assessment: HealthAssessment,
        previous: ServiceSnapshot | None,
        now: datetime,
    ) -> ServiceSnapshot:
        if previous is not None and previous.source != assessment.source:
            previous = None
        healthy = assessment.health_score >= self.settings.health_threshold
        return ServiceSnapshot(
            service_name=service_name,
            container_status=assessment.container_status,
            health_score=assessment.health_score,
            consecutive_failures=0
            if healthy
            else (previous.consecutive_failures + 1 if previous else 1),
            degraded_since=None
            if healthy
            else (
                previous.degraded_since if previous and previous.degraded_since else now
            ),
            last_check_at=now,
            source=assessment.source,
            capabilities=assessment.capabilities,
            health_class=assessment.health_class,
            failing_checks=assessment.failing_checks,
            current_egress_ip=assessment.current_egress_ip,
            connectivity=assessment.connectivity,
            authentication=assessment.authentication,
            latency_ms=assessment.latency_ms,
            last_action=previous.last_action if previous else None,
            last_action_result=previous.last_action_result if previous else None,
        )

    def decide(self, context: RecoveryContext, now: datetime) -> RecoveryDecision:
        snapshot, assessment = context.snapshot, context.assessment
        if snapshot.health_score >= self.settings.health_threshold:
            return RecoveryDecision("resolve", "Connectivity is healthy.")

        capabilities = context.service.capabilities
        if not (
            capabilities.restart_tunnel
            or capabilities.restore
            or capabilities.replace_endpoint
        ):
            return self._incident(
                context,
                "endpoint_unhealthy",
                "Endpoint requires operator investigation; automated recovery operations are unsupported.",
                now,
            )

        if persistent_auth_or_config_failure(context.results):
            if context.progress.phase == "observed" and self._isolated_auth_restart(
                context
            ):
                return self._restart("isolated_auth_failure")
            return self._incident(
                context,
                "auth_config_failure",
                "Persistent authentication or configuration failure detected.",
                now,
            )

        if (
            context.progress.phase == "observed"
            and assessment.available is True
            and capabilities.restart_tunnel
            and snapshot.consecutive_failures == 1
            and assessment.restart_ready
        ):
            return self._restart("first_unhealthy_cycle")

        route_failure = persistent_route_failure(context.results)
        force_rotation = (
            context.progress.phase == "restarted"
            and failed_check(context.results, "tls_error")
        ) or (
            context.progress.phase != "restored"
            and route_failure
            and self._restored_since_degraded(context)
        )
        if (
            capabilities.restore
            and context.progress.phase != "restored"
            and not force_rotation
            and self.can_restore(context.service.name, context.actions, now)
        ):
            return RecoveryDecision(
                "restore",
                "Recreate the service before considering rotation.",
                observation=FollowupObservation(
                    "health", "watchdog", self.settings.recheck_delay_seconds
                ),
                next_progress=RecoveryProgress("restored", route_failure),
            )
        if context.progress.route_restore_attempted and route_failure:
            return RecoveryDecision(
                "wait", "Recheck persistent route failure on the next cycle."
            )
        if not force_rotation and not self.grace_elapsed(snapshot, now):
            return RecoveryDecision("wait", "Rotation grace period has not elapsed.")

        block = self.rotation_block(context, now)
        if block:
            reason, incident_type = block
            return self._incident(context, incident_type, reason, now)
        if not capabilities.replace_endpoint:
            return self._incident(
                context,
                "endpoint_unhealthy",
                "Endpoint replacement is unsupported; investigate the endpoint.",
                now,
            )
        return RecoveryDecision(
            "rotate",
            "Recovery remains unhealthy after bounded remediation.",
            observation=FollowupObservation("connectivity", "fleet"),
        )

    def manual_rotation(
        self,
        incident: AgentIncident,
        *,
        approved: bool,
        purpose: RecoveryPurpose = "repair_connectivity",
    ) -> RecoveryDecision:
        """Validate the existing explicit approval path, which may override budgets."""
        if incident.status in {"resolved", "dismissed", "failed"}:
            raise RuntimeError(f"Incident '{incident.id}' is already closed")
        if incident.recommended_action != "rotate" and incident.type not in {
            "rotation_exhausted",
            "provider_outage_suspected",
            "rotation_required",
        }:
            raise RuntimeError(f"Incident '{incident.id}' is not a rotation incident")
        if not approved:
            return RecoveryDecision(
                "wait", "Manual rotation requires explicit approval.", purpose=purpose
            )
        return RecoveryDecision(
            "rotate",
            "Operator approved a rotation.",
            trigger="manual_approval",
            purpose=purpose,
            observation=FollowupObservation(
                "exit_ip_change"
                if purpose == "request_different_exit_ip"
                else "connectivity",
                "fleet",
            ),
        )

    def can_restore(
        self, service_name: str, actions: Sequence[ActionRecord], now: datetime
    ) -> bool:
        cutoff = now - timedelta(seconds=self.settings.restore_cooldown_seconds)
        for action in reversed(actions):
            if action.action == "restore" and action_matches_service(
                action, service_name
            ):
                return action.ts < cutoff
        return True

    def grace_elapsed(self, snapshot: ServiceSnapshot, now: datetime) -> bool:
        return (
            snapshot.degraded_since is not None
            and now - snapshot.degraded_since
            >= timedelta(seconds=self.settings.rotation_grace_period_seconds)
        )

    def rotation_budget_exhausted(
        self, service_name: str, actions: Sequence[ActionRecord], now: datetime
    ) -> bool:
        rotations = [
            a
            for a in actions
            if a.action == "rotate"
            and a.result == "success"
            and a.trigger in {"automatic_remediation", "auto_rotation"}
            and action_matches_service(a, service_name)
        ]
        return (
            any(a.ts >= now - timedelta(minutes=30) for a in rotations)
            or sum(a.ts >= now - timedelta(hours=6) for a in rotations) >= 2
        )

    def profile_breaker(self, context: RecoveryContext, now: datetime) -> bool:
        matching = [
            a
            for a in context.assessments.values()
            if a.health_class == "auth_config"
            and (identity := context.services.get(a.service_name)) is not None
            and identity.profile == context.service.profile
        ]
        return len(matching) >= 2 or self._scope_incident(
            context, "profile_auth_config_failure", 3600, now
        )

    def provider_breaker(self, context: RecoveryContext, now: datetime) -> bool:
        matching = [
            a
            for a in context.assessments.values()
            if a.service_name != context.service.name
            and a.health_score < self.settings.health_threshold
            and a.health_class != "auth_config"
            and (identity := context.services.get(a.service_name)) is not None
            and (identity.provider, identity.country)
            == (context.service.provider, context.service.country)
        ]
        return len(matching) >= 2 or self._scope_incident(
            context, "provider_outage_suspected", 1800, now
        )

    def rotation_block(
        self, context: RecoveryContext, now: datetime
    ) -> tuple[str, str] | None:
        # Scope breakers use batch peers; service failures use the latest recheck.
        if persistent_auth_or_config_failure(context.results):
            return "Service still shows auth/config failure.", "rotation_exhausted"
        if self.profile_breaker(context, now):
            return (
                "Profile auth/config breaker is active.",
                "profile_auth_config_failure",
            )
        if self.provider_breaker(context, now):
            return (
                "Provider/country degradation breaker is active.",
                "provider_outage_suspected",
            )
        if self.rotation_budget_exhausted(context.service.name, context.actions, now):
            return "Rotation budget exhausted.", "rotation_exhausted"
        return None

    def _restart(self, trigger: str) -> RecoveryDecision:
        return RecoveryDecision(
            "restart_tunnel",
            "Restart the tunnel and observe connectivity.",
            trigger=trigger,
            observation=FollowupObservation(
                "health", "watchdog", self.settings.recheck_delay_seconds
            ),
            next_progress=RecoveryProgress("restarted"),
        )

    def _incident(
        self, context: RecoveryContext, incident_type: str, reason: str, now: datetime
    ) -> RecoveryDecision:
        scope_active = (
            incident_type == "profile_auth_config_failure"
            and self._scope_incident(context, incident_type, 3600, now)
        ) or (
            incident_type == "provider_outage_suspected"
            and self._scope_incident(context, incident_type, 1800, now)
        )
        return RecoveryDecision(
            "incident",
            reason,
            incident_type=incident_type,
            suppressed=not scope_active
            and recently_dismissed(
                context.incidents,
                context.service.name,
                incident_type,
                now,
                self.settings.incident_cooldown_seconds,
            ),
        )

    def _isolated_auth_restart(self, context: RecoveryContext) -> bool:
        assessment = context.assessment
        # An interrupted attempt still consumes the single isolated restart for
        # this degradation episode, even if dismissal suppresses its incident.
        if context.snapshot.degraded_since is not None and any(
            action.action == "restart_tunnel"
            and action.trigger == "isolated_auth_failure"
            and action_matches_service(action, context.service.name)
            and action.ts >= context.snapshot.degraded_since
            and (
                action.details.get("cancelled") == "true"
                or action.details.get("observation")
                in {"pending", "interrupted", "failed"}
            )
            for action in context.actions
        ):
            return False
        return bool(
            context.service.capabilities.restart_tunnel
            and assessment.restart_ready
            and not any(
                i.service_name == context.service.name
                and i.type == "auth_config_failure"
                and i.status not in {"resolved", "dismissed", "failed"}
                for i in context.incidents
            )
            and not failed_check(assessment.results, "config_error")
            and failed_check(assessment.results, "auth_failure")
            and assessment.peer_evidence.healthy
        )

    def _restored_since_degraded(self, context: RecoveryContext) -> bool:
        if context.snapshot.degraded_since is None:
            return False
        for action in reversed(context.actions):
            if action.ts < context.snapshot.degraded_since:
                break
            if action.action == "restore" and action_matches_service(
                action, context.service.name
            ):
                return True
        return False

    def _scope_incident(
        self, context: RecoveryContext, incident_type: str, seconds: int, now: datetime
    ) -> bool:
        for incident in context.incidents:
            if (
                incident.type != incident_type
                or incident.status in {"resolved", "dismissed", "failed"}
                or incident.updated_at < now - timedelta(seconds=seconds)
            ):
                continue
            identity = context.services.get(incident.service_name)
            if identity is None:
                continue
            if (
                incident_type == "profile_auth_config_failure"
                and identity.profile == context.service.profile
            ):
                return True
            if incident_type == "provider_outage_suspected" and (
                identity.provider,
                identity.country,
            ) == (context.service.provider, context.service.country):
                return True
        return False

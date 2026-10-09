"""Rule-first watchdog runtime for proxy2vpn services."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.llm import (
    IncidentContext,
    InvestigationContext,
    InvestigationPlan,
    OpenAIIncidentEnricher,
    OpenAIIncidentInvestigator,
)
from proxy2vpn.agent.models import (
    ActionRecord,
    AgentIncident,
    IncidentSeverity,
    IncidentInvestigation,
    AgentState,
    AgentStatus,
    DaemonMode,
    IncidentStatus,
    ServiceSnapshot,
)
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.agent.recovery_policy import (
    RecoveryContext,
    RecoveryPolicy,
    RecoverySettings,
    ServiceIdentity,
    action_matches_service,
    persistent_auth_or_config_failure,
    recently_dismissed,
)
from proxy2vpn.adapters.external_proxy import load_external_endpoints
from proxy2vpn.core.external_proxy import ExternalProxyEndpoint
from proxy2vpn.core.egress import (
    EgressCapabilities,
    GLUETUN_CAPABILITIES,
    require_supported_operation,
)
from proxy2vpn.adapters.docker_ops import _load_env_file
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.fleet_state_manager import (
    FleetStateManager,
    OperationConfig,
    OperationResult,
    RotationCriteria,
    RotationProgressCallback,
)
from proxy2vpn.adapters.gluetun_runtime import GluetunRuntime, GluetunRuntimeInterface
from proxy2vpn.adapters.logging_utils import (
    get_event_logger,
    get_logger,
    logging_context,
)
from proxy2vpn.core.models import VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer, DiagnosticResult
from proxy2vpn.core.services.health_assessment import (
    HealthAssessment,
    HealthAssessmentService,
)

logger = get_logger(__name__)
events = get_event_logger(__name__)


class HealthEvaluation(TypedDict, total=False):
    container_status: str
    health_score: int
    results: list[DiagnosticResult]
    assessment: HealthAssessment


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


# @lat: [[lat.md/agent#Watchdog Cycle]]
class AgentWatchdog:
    """Watch one compose root and apply limited remediation policies."""

    def __init__(
        self,
        compose_file: Path,
        interval_seconds: int | None = None,
        llm_mode: str | None = None,
        store: AgentStateStore | None = None,
        settings: AgentSettings | None = None,
        runtime: GluetunRuntimeInterface | None = None,
    ) -> None:
        self.compose_file = compose_file.expanduser().resolve()
        self.settings = settings or (
            store.settings if store is not None else AgentSettings()
        )
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else self.settings.interval_seconds
        )
        self.llm_mode = (llm_mode or self.settings.llm_mode).strip() or "disabled"
        self.store = store or AgentStateStore(self.compose_file, settings=self.settings)
        self._runtime = (
            runtime
            if runtime is not None
            else GluetunRuntime(
                probe_timeout=self.settings.probe_timeout_seconds,
                control_api_timeout=self.settings.control_api_timeout_seconds,
                control_api_retry_attempts=self.settings.control_api_retry_attempts,
            )
        )
        self._health_assessor = HealthAssessmentService(
            self.settings.health_threshold,
            probe_timeout=self.settings.probe_timeout_seconds,
            control_api_timeout=self.settings.control_api_timeout_seconds,
            control_api_retry_attempts=self.settings.control_api_retry_attempts,
            runtime=self._runtime,
        )
        self._recovery_policy = RecoveryPolicy(
            RecoverySettings(
                health_threshold=self.settings.health_threshold,
                recheck_delay_seconds=self.settings.recheck_delay_seconds,
                rotation_grace_period_seconds=self.settings.rotation_grace_period_seconds,
                restore_cooldown_seconds=self.settings.restore_cooldown_seconds,
                incident_cooldown_seconds=self.settings.incident_cooldown_seconds,
            )
        )
        self._incident_enricher = self._build_incident_enricher()
        self._incident_investigator = self._build_incident_investigator()
        self._llm_warning_emitted = False
        self._inflight_service_names: dict[str, str] = {}

    def empty_state(self) -> AgentState:
        """Return a zeroed state for status output before the agent has run."""

        return AgentState(
            status=AgentStatus(
                compose_path=str(self.compose_file),
                daemon_mode="inactive",
                interval_seconds=self.interval_seconds,
                llm_mode=self.llm_mode,
            )
        )

    async def run_forever(self, daemon_mode: DaemonMode = "foreground") -> AgentState:
        """Run until interrupted."""

        state = self._load_state(daemon_mode, refresh_started_at=True)
        self.store.write_state(state)
        while True:
            state = await self.run_cycle(state)
            await asyncio.sleep(self.interval_seconds)

    async def run_once(self) -> AgentState:
        """Execute one cycle and exit."""

        return await self.run_cycle(self._load_state("once", refresh_started_at=True))

    async def run_cycle(self, state: AgentState) -> AgentState:
        """Execute one monitoring and remediation cycle."""

        with logging_context(
            clear=True, cycle_id=uuid4().hex, compose_path=str(self.compose_file)
        ):
            events.info("agent_cycle_started")
            try:
                result = await self._run_cycle(state)
            except Exception as exc:
                events.exception("agent_cycle_failed", error=str(exc))
                raise
            events.info(
                "agent_cycle_completed",
                service_count=result.status.service_count,
                unhealthy_count=result.status.unhealthy_count,
            )
            return result

    async def _run_cycle(self, state: AgentState) -> AgentState:
        """Perform a cycle inside the operation's logging context."""

        manager = None
        self._inflight_service_names.clear()
        progress_at = utc_now()
        state.status.compose_path = str(self.compose_file)
        state.status.interval_seconds = self.interval_seconds
        state.status.llm_mode = self.llm_mode
        state.status.last_error = None
        state.status.active_cycle_started_at = progress_at
        state.status.active_cycle_phase = "cleanup"
        state.status.active_cycle_service_name = None
        state.status.last_progress_at = progress_at
        self.store.write_state(state)
        updated_snapshots: list[ServiceSnapshot] = list(state.services)
        cycle_error: Exception | None = None
        try:
            manager, services = self._inventory()
            # External-only workspaces require no Docker daemon.
            cleanup = None
            if manager is not None and (
                not any(isinstance(item, ExternalProxyEndpoint) for item in services)
                or any(isinstance(item, VPNService) for item in services)
            ):
                cleanup = await self._runtime.cleanup_orphans(manager)
            if cleanup is not None and cleanup.error is not None:
                raise RuntimeError(cleanup.error)
            orphaned = cleanup.removed if cleanup is not None else []
            if orphaned:
                logger.warning(
                    "agent_orphaned_containers_removed",
                    extra={
                        "compose_path": str(self.compose_file),
                        "container_names": orphaned,
                        "count": len(orphaned),
                    },
                )
            state.status.service_count = len(services)
            state.status.active_cycle_phase = "assessing_services"
            self.store.write_state(state)
            assessments = await self._health_assessor.assess_services(
                services,
                progress_callback=lambda service_name: self._persist_cycle_progress(
                    state,
                    service_name=service_name,
                ),
            )
            snapshots_by_name = {
                snapshot.service_name: snapshot for snapshot in state.services
            }
            incidents = self.store.load_incidents()
            updated_snapshots = []
            state.status.active_cycle_phase = "processing_services"
            state.status.last_progress_at = utc_now()
            self.store.write_state(state)
            for service in services:
                with logging_context(
                    service_name=service.name, provider=service.provider
                ):
                    self._persist_cycle_progress(state, service_name=service.name)
                    snapshot = await self._process_service(
                        manager=manager,
                        service=service,
                        previous=snapshots_by_name.get(service.name),
                        state=state,
                        incidents=incidents,
                        assessment=assessments.get(service.name),
                        assessment_map=assessments,
                    )
                updated_snapshots.append(snapshot)
                self._merge_persisted_snapshot(
                    state=state,
                    previous_service_name=service.name,
                    snapshot=snapshot,
                )
                state.status.unhealthy_count = sum(
                    1
                    for persisted_snapshot in state.services
                    if persisted_snapshot.health_score < self.settings.health_threshold
                )
                state.status.last_progress_at = utc_now()
                self.store.write_state(state)
        except asyncio.CancelledError:
            state.status.last_error = "Cycle cancelled"
            updated_snapshots = list(state.services)
            raise
        except Exception as exc:
            state.status.last_error = str(exc)
            updated_snapshots = list(state.services)
            cycle_error = exc
        finally:
            final_progress_at = utc_now()
            state.status.last_loop_at = final_progress_at
            state.status.last_progress_at = final_progress_at
            state.status.active_cycle_started_at = None
            state.status.active_cycle_phase = None
            state.status.active_cycle_service_name = None
            state.services = updated_snapshots
            state.status.unhealthy_count = sum(
                1
                for snapshot in updated_snapshots
                if snapshot.health_score < self.settings.health_threshold
            )
            self.store.write_state(state)
        if cycle_error is not None:
            raise cycle_error
        return state

    def _persist_cycle_progress(
        self,
        state: AgentState,
        *,
        service_name: str | None = None,
        step: str | None = None,
        detail: str | None = None,
        current_live_service_name: str | None = None,
    ) -> None:
        active_service_name = current_live_service_name or service_name
        state.status.active_cycle_service_name = active_service_name
        if service_name and current_live_service_name:
            self._sync_inflight_service_name(
                state,
                requested_service_name=service_name,
                current_live_service_name=current_live_service_name,
            )
        state.status.last_progress_at = utc_now()
        if step:
            logger.debug(
                "agent_cycle_progress",
                extra={
                    "compose_path": str(self.compose_file),
                    "requested_service_name": service_name,
                    "current_live_service_name": active_service_name,
                    "step": step,
                    "detail": detail,
                },
            )
        self.store.write_state(state)

    def _sync_inflight_service_name(
        self,
        state: AgentState,
        *,
        requested_service_name: str,
        current_live_service_name: str,
    ) -> None:
        previous_live_name = self._inflight_service_names.get(
            requested_service_name, requested_service_name
        )
        self._inflight_service_names[requested_service_name] = current_live_service_name
        if previous_live_name == current_live_service_name:
            return
        for snapshot in state.services:
            if snapshot.service_name not in {
                requested_service_name,
                previous_live_name,
                current_live_service_name,
            }:
                continue
            snapshot.service_name = current_live_service_name
            break

    async def approve_incident(self, incident_id: str) -> AgentIncident:
        """Deprecated compatibility path for manually triggering a rotation."""

        incidents = self.store.load_incidents()
        incident = next((item for item in incidents if item.id == incident_id), None)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found")
        if incident.source == "external_proxy" or self._is_external_endpoint(
            incident.service_name
        ):
            require_supported_operation(EgressCapabilities(), "replace_endpoint")
        decision = self._recovery_policy.manual_rotation(incident, approved=True)
        state = self.store.read_state() or self.empty_state()
        self._inflight_service_names.pop(incident.service_name, None)
        try:
            result = await self._rotate_service_via_fleet(
                incident.service_name, state=state
            )
        except asyncio.CancelledError:
            self._record_interrupted_rotation(
                state, incident.service_name, incident.id, cancelled=True
            )
            raise
        except Exception as exc:
            self._record_interrupted_rotation(
                state, incident.service_name, incident.id, error=str(exc)
            )
            raise
        finally:
            if state.status.active_cycle_started_at is None:
                state.status.active_cycle_service_name = None
                self.store.write_state(state)

        action_result = "success" if result.success else "failed"
        action_details = self._build_rotation_action_details(
            incident_id=incident.id,
            requested_service_name=incident.service_name,
            result=result,
        )
        action_service_name = action_details.get(
            "final_service_name", incident.service_name
        )
        self._append_action(
            state,
            service_name=action_service_name,
            action="rotate",
            trigger=decision.trigger,
            result=action_result,
            details=action_details,
        )
        self._update_snapshot_action(
            state,
            incident.service_name,
            last_action="rotate",
            last_action_result=action_result,
            new_service_name=action_details.get("final_service_name"),
        )
        self.store.write_state(state)
        final_service_name = action_details.get("final_service_name")
        if final_service_name not in {None, incident.service_name}:
            self._migrate_active_incidents(
                old_service_name=incident.service_name,
                new_service_name=final_service_name,
                exclude_incident_ids={incident.id},
            )

        terminal_status: IncidentStatus = "resolved" if result.success else "failed"
        summary_text = (
            incident.summary
            if result.success
            else f"{incident.summary} Rotation failed: {' | '.join(result.errors)}"
        )
        summary, human_explanation = self._enrich_summary(
            IncidentContext(
                service_name=incident.service_name,
                fallback_summary=summary_text,
                recommended_action=incident.recommended_action,
                failure_count=incident.failure_count,
                issues=[],
                recent_actions=[
                    action
                    for action in self._recent_actions_for_service(
                        state.actions,
                        incident.service_name,
                    )
                ],
            )
        )
        terminal = incident.model_copy(
            update={
                "status": terminal_status,
                "resolved_at": utc_now() if result.success else None,
                "updated_at": utc_now(),
                "summary": summary,
                "human_explanation": human_explanation or incident.human_explanation,
            }
        )
        self.store.append_incident(terminal)
        return terminal

    def dismiss_incident(self, incident_id: str) -> AgentIncident:
        """Dismiss one incident and suppress re-opening it for a cooldown window."""

        incidents = self.store.load_incidents()
        incident = next((item for item in incidents if item.id == incident_id), None)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found")
        if incident.status in {"resolved", "dismissed"}:
            raise RuntimeError(f"Incident '{incident_id}' is already closed")

        dismissed = incident.model_copy(
            update={"status": "dismissed", "updated_at": utc_now()}
        )
        self.store.append_incident(dismissed)
        return dismissed

    async def investigate_incident(self, incident_id: str) -> AgentIncident:
        """Investigate one incident and persist a concrete operator action plan."""

        incidents = self.store.load_incidents()
        incident = next((item for item in incidents if item.id == incident_id), None)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found")
        if incident.status in {"resolved", "dismissed", "failed"}:
            raise RuntimeError(f"Incident '{incident_id}' is already closed")

        context = self.store.sanitizer().model(
            await self._build_investigation_context(incident)
        )
        investigation = self._investigate_context(context)
        updated = incident.model_copy(
            update={
                "investigation": IncidentInvestigation(
                    summary=investigation.summary.strip() or incident.summary,
                    findings=[
                        item.strip()
                        for item in investigation.findings
                        if item and item.strip()
                    ],
                    log_evidence=[
                        item.strip() for item in context.log_evidence if item.strip()
                    ],
                    action_plan=[
                        item.strip()
                        for item in investigation.action_plan
                        if item and item.strip()
                    ],
                    investigated_at=utc_now(),
                ),
                "updated_at": utc_now(),
            }
        )
        updated = self.store.sanitizer().model(updated)
        self.store.append_incident(updated)
        return updated

    def _load_state(
        self, daemon_mode: DaemonMode, refresh_started_at: bool
    ) -> AgentState:
        state = self.store.read_state() or self.empty_state()
        state.status.compose_path = str(self.compose_file)
        state.status.daemon_mode = daemon_mode
        state.status.interval_seconds = self.interval_seconds
        state.status.llm_mode = self.llm_mode
        if refresh_started_at:
            state.status.started_at = utc_now()
        return state

    def _external_endpoints(self) -> list[ExternalProxyEndpoint]:
        path = Path(self.settings.external_proxies_file).expanduser()
        if not path.is_absolute():
            path = self.compose_file.parent / path
        return load_external_endpoints(path)

    def _is_external_endpoint(self, service_name: str) -> bool:
        if any(
            endpoint.name == service_name for endpoint in self._external_endpoints()
        ):
            return True
        # Current Compose identity takes precedence over another source's history.
        # Approval of an old external incident is guarded by its own source above.
        if self.compose_file.exists() and any(
            service.name == service_name
            for service in ComposeManager(self.compose_file).list_services()
        ):
            return False
        if any(
            incident.service_name == service_name
            and incident.source == "external_proxy"
            for incident in self.store.load_incidents()
        ):
            return True
        state = self.store.read_state()
        return bool(
            state
            and any(
                snapshot.service_name == service_name
                and snapshot.source == "external_proxy"
                for snapshot in state.services
            )
        )

    def _inventory(
        self,
    ) -> tuple[ComposeManager | None, list[VPNService | ExternalProxyEndpoint]]:
        external = self._external_endpoints()
        manager = (
            ComposeManager(self.compose_file)
            if self.compose_file.exists() or not external
            else None
        )
        services: list[VPNService | ExternalProxyEndpoint] = (
            list(manager.list_services()) if manager is not None else []
        )
        names = {service.name for service in services}
        if any(endpoint.name in names for endpoint in external):
            raise ValueError(
                "External endpoint ids must not collide with compose service names"
            )
        services.extend(external)
        return manager, services

    def _recovery_context(
        self,
        manager: ComposeManager | None,
        service: VPNService | ExternalProxyEndpoint,
        assessment: HealthAssessment,
        snapshot: ServiceSnapshot,
        state: AgentState,
        incidents: list[AgentIncident],
        assessments: dict[str, HealthAssessment],
    ) -> RecoveryContext:
        # Resolve desired-state scope in orchestration before evaluating pure policy.
        identities = {
            candidate.name: ServiceIdentity(
                candidate.name,
                candidate.profile,
                candidate.provider,
                self._service_country(candidate),
                GLUETUN_CAPABILITIES,
            )
            for candidate in (manager.list_services() if manager is not None else [])
        }
        identity = ServiceIdentity(
            service.name,
            service.profile or "",
            service.provider,
            self._service_country(service) if isinstance(service, VPNService) else "",
            assessment.capabilities,
        )
        identities[identity.name] = identity
        return RecoveryContext(
            service=identity,
            assessment=assessment,
            snapshot=snapshot,
            results=assessment.results,
            actions=state.actions,
            incidents=incidents,
            services=identities,
            assessments=assessments,
        )

    async def _process_service(
        self,
        manager: ComposeManager | None,
        service: VPNService | ExternalProxyEndpoint,
        previous: ServiceSnapshot | None,
        state: AgentState,
        incidents: list[AgentIncident],
        assessment: HealthAssessment | None,
        assessment_map: dict[str, HealthAssessment] | None,
    ) -> ServiceSnapshot:
        if assessment is None:
            assessment = await self._health_assessor.assess_service(
                service, peer_assessments=assessment_map
            )
        snapshot = self._recovery_policy.snapshot(
            service.name, assessment, previous, utc_now()
        )
        failure_count, degraded_since = (
            snapshot.consecutive_failures,
            snapshot.degraded_since,
        )
        context = self._recovery_context(
            manager,
            service,
            assessment,
            snapshot,
            state,
            incidents,
            assessment_map or {},
        )
        # Keep the current observation available if an action is interrupted.
        self._merge_persisted_snapshot(state, service.name, snapshot)

        # At most restart, restore, and rotation; each recheck yields a new decision.
        for _ in range(4):
            context = replace(context, actions=state.actions)
            decision = self._recovery_policy.decide(context, utc_now())
            if decision.action == "resolve":
                self._resolve_active_incidents(
                    snapshot.service_name, incidents, source=assessment.source
                )
                return snapshot
            if decision.action == "wait":
                logger.info(
                    "agent_rotation_deferred",
                    extra={"service_name": service.name, "reason": decision.reason},
                )
                return snapshot
            if decision.action == "incident":
                if not decision.suppressed:
                    self._record_recovery_incident(
                        service,
                        snapshot,
                        context.results,
                        state,
                        incidents,
                        decision.incident_type or "rotation_exhausted",
                        decision.reason,
                    )
                return snapshot
            if decision.action == "rotate":
                require_supported_operation(assessment.capabilities, "replace_endpoint")
                assert isinstance(service, VPNService)
                return await self._execute_recovery_rotation(
                    service, snapshot, list(context.results), state, incidents
                )

            require_supported_operation(assessment.capabilities, decision.action)
            assert isinstance(service, VPNService) and manager is not None
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step=decision.action,
                detail=f"starting {decision.action}: {decision.reason}",
            )
            if decision.action == "restart_tunnel":
                previous_action = state.actions[-1] if state.actions else None
                result = await self._restart_tunnel(
                    service, state, trigger=decision.trigger
                )
                request_action = (
                    state.actions[-1]
                    if state.actions and state.actions[-1] is not previous_action
                    else None
                )
                snapshot.last_action = "restart_tunnel"
                snapshot.last_action_result = result
                self._persist_cycle_progress(
                    state,
                    service_name=service.name,
                    step="restart_tunnel_recheck",
                    detail="waiting before post-restart health check",
                )
                assert decision.observation is not None
                try:
                    await asyncio.sleep(decision.observation.delay_seconds)
                    health = await self._evaluate_health(service)
                except (asyncio.CancelledError, Exception) as exc:
                    self._record_restart_recheck_failure(
                        state,
                        service,
                        decision.trigger,
                        result,
                        request_action,
                        exc,
                    )
                    # Persist any investigation required by the last known evidence.
                    # This also prevents another isolated-auth restart after interruption.
                    interrupted = self._recovery_policy.decide(
                        replace(
                            context,
                            actions=state.actions,
                            progress=decision.next_progress,
                        ),
                        utc_now(),
                    )
                    if (
                        interrupted.incident_type == "auth_config_failure"
                        and not interrupted.suppressed
                    ):
                        self._upsert_incident(
                            incidents,
                            service.name,
                            interrupted.incident_type or "auth_config_failure",
                            "high",
                            interrupted.reason,
                            None,
                            "investigate",
                            snapshot.consecutive_failures,
                        )
                    raise
                self._persist_cycle_progress(
                    state,
                    service_name=service.name,
                    step="restart_tunnel_recheck",
                    detail="post-restart health check completed",
                )
            else:
                result, health = await self._restore_service(
                    manager=manager, service=service, state=state
                )
            snapshot.last_action = decision.action
            snapshot.last_action_result = result
            snapshot.container_status = health["container_status"]
            snapshot.health_score = health["health_score"]
            snapshot.last_check_at = utc_now()
            observed = health.get("assessment")
            if observed is not None:
                snapshot.health_class = observed.health_class
                snapshot.failing_checks = observed.failing_checks
                snapshot.current_egress_ip = observed.current_egress_ip
                snapshot.authentication = observed.authentication
                snapshot.connectivity = observed.connectivity
                snapshot.latency_ms = observed.latency_ms
                context = replace(context, assessment=observed)
            recovered = snapshot.health_score >= self.settings.health_threshold
            snapshot.consecutive_failures = 0 if recovered else failure_count
            snapshot.degraded_since = None if recovered else degraded_since
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step=decision.action,
                detail=f"{decision.action} completed with result={result}",
            )
            context = replace(
                context, results=health["results"], progress=decision.next_progress
            )
        raise RuntimeError("Recovery policy exceeded the bounded action sequence")

    def _record_restart_recheck_failure(
        self,
        state: AgentState,
        service: VPNService,
        trigger: str,
        request_result: str,
        request_action: ActionRecord | None,
        error: BaseException,
    ) -> None:
        cancelled = isinstance(error, asyncio.CancelledError)
        details = {
            "runtime_request_result": request_result,
            "observation": "interrupted" if cancelled else "failed",
        }
        if cancelled:
            details["cancelled"] = "true"
        else:
            details["observation_error"] = str(error)
        self._update_snapshot_action(state, service.name, "restart_tunnel", "failed")
        if request_action is not None:
            request_action.result = "failed"
            request_action.details.update(details)
            state.status.last_progress_at = utc_now()
            self.store.write_state(state)
        else:
            self._append_action(
                state, service.name, "restart_tunnel", trigger, "failed", details
            )
        events.warning(
            "agent_recovery_observation_failed",
            service_name=service.name,
            action="restart_tunnel",
            result="failed",
            details=details,
        )

    def _record_recovery_incident(
        self,
        service: VPNService | ExternalProxyEndpoint,
        snapshot: ServiceSnapshot,
        results: Sequence[DiagnosticResult],
        state: AgentState,
        incidents: list[AgentIncident],
        incident_type: str,
        reason: str,
    ) -> None:
        recommended_action = (
            "rotate" if incident_type == "rotation_exhausted" else "investigate"
        )
        summary, human_explanation = self._format_issue_summary(
            service.name,
            list(results),
            fallback=reason,
            recommended_action=recommended_action,
            recent_actions=state.actions if snapshot.source == "gluetun" else [],
            failure_count=snapshot.consecutive_failures,
        )
        severity: IncidentSeverity = (
            "medium" if incident_type == "rotation_exhausted" else "high"
        )
        if incident_type in {"auth_config_failure", "endpoint_unhealthy"}:
            incident = self._upsert_incident(
                incidents,
                service.name,
                incident_type,
                severity,
                summary,
                human_explanation,
                recommended_action,
                snapshot.consecutive_failures,
                source=snapshot.source,
            )
        else:
            assert isinstance(service, VPNService)
            incident = self._upsert_scope_incident(
                incidents,
                service,
                incident_type,
                severity,
                summary,
                human_explanation,
                recommended_action,
                snapshot.consecutive_failures,
            )
        if incident is not None and incident_type == "auth_config_failure":
            logger.warning(
                "agent_auth_config_failure",
                extra={"service_name": service.name, "incident_id": incident.id},
            )

    async def _execute_recovery_rotation(
        self,
        service: VPNService,
        snapshot: ServiceSnapshot,
        results: list[DiagnosticResult],
        state: AgentState,
        incidents: list[AgentIncident],
    ) -> ServiceSnapshot:
        failure_count = snapshot.consecutive_failures
        self._persist_cycle_progress(
            state,
            service_name=service.name,
            step="rotate",
            detail="starting fleet rotation",
        )
        try:
            rotate_result = await self._rotate_service_via_fleet(
                service.name, state=state
            )
        except asyncio.CancelledError:
            self._record_interrupted_rotation(state, service.name, cancelled=True)
            raise
        except Exception as exc:
            self._record_interrupted_rotation(state, service.name, error=str(exc))
            raise
        snapshot.last_action = "rotate"
        snapshot.last_action_result = "success" if rotate_result.success else "failed"
        action_details = self._build_rotation_action_details_from_result(
            service.name, rotate_result
        )
        self._persist_cycle_progress(
            state,
            service_name=service.name,
            step="rotate",
            detail=(
                "fleet rotation completed"
                if rotate_result.success
                else "fleet rotation exhausted candidates"
            ),
            current_live_service_name=action_details.get("final_service_name"),
        )
        self._append_action(
            state,
            service_name=action_details.get("final_service_name", service.name),
            action="rotate",
            trigger="automatic_remediation",
            result="success" if rotate_result.success else "failed",
            details=action_details,
        )
        final_service_name = action_details.get("final_service_name", service.name)
        snapshot.service_name = final_service_name
        if rotate_result.rotation_changes and rotate_result.success:
            snapshot.container_status = "running"
            snapshot.health_score = self.settings.health_threshold
            snapshot.consecutive_failures = 0
            snapshot.degraded_since = None
        if final_service_name != service.name:
            self._migrate_active_incidents(service.name, final_service_name)
            for incident in incidents:
                if (
                    incident.source == "gluetun"
                    and incident.service_name == service.name
                    and incident.status
                    not in {
                        "resolved",
                        "dismissed",
                        "failed",
                    }
                ):
                    incident.service_name = final_service_name
        if rotate_result.success:
            self._resolve_active_incidents(snapshot.service_name, incidents)
            return snapshot

        summary, human_explanation = self._format_issue_summary(
            snapshot.service_name,
            results,
            fallback="Automatic rotation failed after the grace period.",
            recommended_action="rotate",
            recent_actions=state.actions,
            failure_count=failure_count,
        )
        self._upsert_incident(
            incidents=incidents,
            service_name=snapshot.service_name,
            incident_type="rotation_exhausted",
            severity="medium",
            summary=summary,
            human_explanation=human_explanation,
            recommended_action="rotate",
            failure_count=failure_count,
        )
        return snapshot

    def _record_interrupted_rotation(
        self,
        state: AgentState,
        requested_service_name: str,
        incident_id: str | None = None,
        *,
        cancelled: bool = False,
        error: str | None = None,
    ) -> None:
        live_name = self._inflight_service_names.get(
            requested_service_name, requested_service_name
        )
        details = {
            "requested_service_name": requested_service_name,
            "final_service_name": live_name,
            "exit_ip_changed": "unknown",
        }
        if cancelled:
            details["cancelled"] = "true"
        if error is not None:
            details["error"] = error
        if incident_id is not None:
            details["incident_id"] = incident_id
        self._update_snapshot_action(
            state,
            requested_service_name,
            "rotate",
            "failed",
            new_service_name=live_name,
        )
        if live_name != requested_service_name:
            self._migrate_active_incidents(requested_service_name, live_name)
        self._append_action(
            state,
            service_name=live_name,
            action="rotate",
            trigger="manual_approval" if incident_id else "automatic_remediation",
            result="failed",
            details=details,
        )

    async def _control_api_reachable(self, service: VPNService) -> bool:
        return (await self._runtime.control_status(service)).success

    async def _restart_tunnel(
        self,
        service: VPNService,
        state: AgentState,
        trigger: str = "first_unhealthy_cycle",
    ) -> str:
        self._persist_cycle_progress(
            state,
            service_name=service.name,
            step="restart_tunnel",
            detail="contacting control API for tunnel restart",
        )
        try:
            restart = await self._runtime.restart_tunnel(service)
            if not restart.success:
                raise RuntimeError(restart.error or "Tunnel restart failed")
            self._append_action(
                state,
                service_name=service.name,
                action="restart_tunnel",
                trigger=trigger,
                result="success",
                details={"control_port": str(service.control_port)},
            )
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step="restart_tunnel",
                detail="control API tunnel restart completed",
            )
            return "success"
        except asyncio.CancelledError:
            self._update_snapshot_action(
                state, service.name, "restart_tunnel", "failed"
            )
            self._append_action(
                state,
                service_name=service.name,
                action="restart_tunnel",
                trigger=trigger,
                result="failed",
                details={"cancelled": "true"},
            )
            raise
        except Exception as exc:
            self._append_action(
                state,
                service_name=service.name,
                action="restart_tunnel",
                trigger=trigger,
                result="failed",
                details={"error": str(exc)},
            )
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step="restart_tunnel",
                detail="control API tunnel restart failed",
            )
            return "failed"

    async def _restore_service(
        self,
        manager: ComposeManager,
        service: VPNService,
        state: AgentState,
    ) -> tuple[str, HealthEvaluation]:
        try:
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step="restore",
                detail="recreating VPN service",
            )
            profile = manager.get_profile(service.profile)
            restore = await self._runtime.restore(service, profile)
            if not restore.success:
                raise RuntimeError(restore.error or "Service recreation failed")
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step="restore",
                detail="service recreate completed",
            )
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step="restore_recheck",
                detail="waiting before restore health check",
            )
            await asyncio.sleep(self.settings.recheck_delay_seconds)
            health = await self._evaluate_health(service)
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step="restore_recheck",
                detail="restore health check completed",
            )
            result = (
                "success"
                if health["health_score"] >= self.settings.health_threshold
                else "failed"
            )
            self._append_action(
                state,
                service_name=service.name,
                action="restore",
                trigger="automatic_remediation",
                result=result,
                details={"profile": service.profile},
            )
            return result, health
        except asyncio.CancelledError:
            self._update_snapshot_action(state, service.name, "restore", "failed")
            self._append_action(
                state,
                service_name=service.name,
                action="restore",
                trigger="automatic_remediation",
                result="failed",
                details={"cancelled": "true", "profile": service.profile},
            )
            raise
        except Exception as exc:
            self._append_action(
                state,
                service_name=service.name,
                action="restore",
                trigger="automatic_remediation",
                result="failed",
                details={"error": str(exc), "profile": service.profile},
            )
            self._persist_cycle_progress(
                state,
                service_name=service.name,
                step="restore",
                detail="service restore failed",
            )
            return "failed", {
                "container_status": "missing",
                "health_score": 0,
                "results": [],
            }

    async def _evaluate_health(self, service: VPNService) -> HealthEvaluation:
        assessment = await self._health_assessor.assess_service(service)
        return {
            "container_status": assessment.container_status,
            "health_score": assessment.health_score,
            "results": assessment.results,
            "assessment": assessment,
        }

    def _select_log_evidence(
        self,
        log_lines: list[str],
        issues: list[DiagnosticResult] | None = None,
        max_lines: int = 6,
    ) -> list[str]:
        normalized = [line.strip() for line in log_lines if str(line).strip()]
        if not normalized:
            return []

        generic_keywords = (
            "error",
            "warn",
            "fail",
            "auth",
            "route",
            "rtnetlink",
            "unreachable",
            "certificate",
            "tls",
            "dns",
            "tun0",
            "proxy",
        )
        failing_checks = {
            issue.check for issue in (issues or []) if not issue.passed and issue.check
        }
        priority_keywords: tuple[str, ...] = ()
        if "route_error" in failing_checks:
            priority_keywords = (
                "rtnetlink answers: file exists",
                "linux route add command failed",
                "route installation may fail",
                "network unreachable",
                "route",
                "tun0",
            )
        elif "auth_failure" in failing_checks:
            priority_keywords = (
                "auth_failed",
                "authentication failure",
                "auth",
                "credentials",
            )
        elif "config_error" in failing_checks:
            priority_keywords = (
                "config",
                "configuration",
                "invalid",
                "missing",
            )
        elif "tls_error" in failing_checks:
            priority_keywords = ("tls", "certificate", "ssl")
        elif "dns_error" in failing_checks:
            priority_keywords = ("dns",)
        elif "connectivity" in failing_checks:
            priority_keywords = ("proxy", "connect", "port", "timeout")

        selected = self._matching_log_lines(
            normalized,
            priority_keywords,
            max_lines=max_lines,
        )
        if not selected:
            selected = self._matching_log_lines(
                normalized,
                generic_keywords,
                max_lines=max_lines,
            )
        return selected or normalized[-max_lines:]

    def _matching_log_lines(
        self,
        log_lines: list[str],
        keywords: tuple[str, ...],
        max_lines: int,
    ) -> list[str]:
        if not keywords:
            return []

        matches: list[str] = []
        seen: set[str] = set()
        for line in log_lines:
            line_lower = line.lower()
            if not any(token in line_lower for token in keywords):
                continue
            if line in seen:
                continue
            seen.add(line)
            matches.append(line)
            if len(matches) >= max_lines:
                break
        return matches

    def _append_action(
        self,
        state: AgentState,
        service_name: str,
        action: str,
        trigger: str,
        result: str,
        details: dict[str, str] | None = None,
    ) -> None:
        state.actions.append(
            ActionRecord(
                ts=utc_now(),
                service_name=service_name,
                action=action,
                trigger=trigger,
                result=result,
                details=details or {},
            )
        )
        state.actions = state.actions[-self.settings.action_history_limit :]
        state.status.last_progress_at = utc_now()
        self.store.write_state(state)
        events.info(
            "agent_action_completed",
            service_name=service_name,
            action=action,
            trigger=trigger,
            result=result,
            details=details or {},
        )

    def _merge_persisted_snapshot(
        self,
        state: AgentState,
        previous_service_name: str,
        snapshot: ServiceSnapshot,
    ) -> None:
        merged: list[ServiceSnapshot] = []
        replaced = False
        for existing_snapshot in state.services:
            if existing_snapshot.service_name in {
                previous_service_name,
                snapshot.service_name,
            }:
                if not replaced:
                    merged.append(snapshot)
                    replaced = True
                continue
            merged.append(existing_snapshot)

        if not replaced:
            merged.append(snapshot)
        state.services = merged

    def _update_snapshot_action(
        self,
        state: AgentState,
        service_name: str,
        last_action: str,
        last_action_result: str,
        new_service_name: str | None = None,
    ) -> None:
        for snapshot in state.services:
            if snapshot.service_name not in {service_name, new_service_name}:
                continue
            if new_service_name:
                snapshot.service_name = new_service_name
            snapshot.last_action = last_action
            snapshot.last_action_result = last_action_result
            snapshot.last_check_at = utc_now()
            break

    def _build_rotation_action_details(
        self,
        incident_id: str,
        requested_service_name: str,
        result: Any,
    ) -> dict[str, str]:
        details = self._build_rotation_action_details_from_result(
            requested_service_name, result
        )
        details["incident_id"] = incident_id
        return details

    def _build_rotation_action_details_from_result(
        self, requested_service_name: str, result: Any
    ) -> dict[str, str]:
        details = {
            "requested_service_name": requested_service_name,
            "purpose": "repair_connectivity",
            "exit_ip_changed": "unknown",
        }
        errors = [
            error.strip()
            for error in getattr(result, "errors", [])
            if isinstance(error, str) and error.strip()
        ]
        if errors:
            details["errors"] = " | ".join(errors)

        rotation_changes = getattr(result, "rotation_changes", []) or []
        if not rotation_changes:
            details["final_service_name"] = self._inflight_service_names.get(
                requested_service_name, requested_service_name
            )
            return details

        change = rotation_changes[0]
        details["final_service_name"] = getattr(
            change,
            "final_service_name",
            requested_service_name,
        )
        details["old_location"] = getattr(change, "old_location", "")
        details["new_location"] = getattr(change, "new_location", "")
        candidate_locations = getattr(change, "candidate_locations", []) or []
        attempted_locations = getattr(change, "attempted_locations", []) or []
        if candidate_locations:
            details["candidate_locations"] = ", ".join(candidate_locations)
        if attempted_locations:
            details["attempted_locations"] = ", ".join(attempted_locations)
        return details

    def _action_matches_service(
        self,
        action: ActionRecord,
        service_name: str,
    ) -> bool:
        return action_matches_service(action, service_name)

    def _serialize_action(self, action: ActionRecord) -> dict[str, str]:
        payload = {
            "action": action.action,
            "result": action.result,
            "trigger": action.trigger,
            "service_name": action.service_name,
            "ts": action.ts.isoformat(),
        }
        payload.update(
            {
                key: value
                for key, value in action.details.items()
                if value and key not in payload
            }
        )
        return payload

    def _recent_actions_for_service(
        self,
        actions: list[ActionRecord],
        service_name: str,
        limit: int = 5,
    ) -> list[dict[str, str]]:
        matching_actions = [
            action
            for action in actions
            if self._action_matches_service(action, service_name)
        ]
        return [self._serialize_action(action) for action in matching_actions[-limit:]]

    def _describe_recent_action(self, action: dict[str, str]) -> str:
        parts = [f"{action['action']} [{action['result']}] via {action['trigger']}."]
        requested_service_name = action.get("requested_service_name")
        final_service_name = action.get("final_service_name")
        if (
            requested_service_name
            and final_service_name
            and requested_service_name != final_service_name
        ):
            parts.append(
                f"Service renamed {requested_service_name} -> {final_service_name}."
            )
        old_location = action.get("old_location")
        new_location = action.get("new_location")
        if old_location and new_location and old_location != new_location:
            parts.append(f"Location changed {old_location} -> {new_location}.")
        attempted_locations = action.get("attempted_locations")
        if attempted_locations:
            parts.append(f"Attempted locations: {attempted_locations}.")
        return " ".join(parts)

    def _rotation_grace_elapsed(self, snapshot: ServiceSnapshot) -> bool:
        return self._recovery_policy.grace_elapsed(snapshot, utc_now())

    def _upsert_scope_incident(
        self,
        incidents: list[AgentIncident],
        service: VPNService,
        incident_type: str,
        severity: IncidentSeverity,
        summary: str,
        human_explanation: str | None,
        recommended_action: str,
        failure_count: int,
    ) -> AgentIncident | None:
        now = utc_now()
        if incident_type == "profile_auth_config_failure":
            existing = next(
                (
                    item
                    for item in incidents
                    if item.type == incident_type
                    and item.status not in {"resolved", "dismissed", "failed"}
                    and self._same_profile(service, item.service_name)
                    and item.updated_at >= now - timedelta(hours=1)
                ),
                None,
            )
        elif incident_type == "provider_outage_suspected":
            existing = next(
                (
                    item
                    for item in incidents
                    if item.type == incident_type
                    and item.status not in {"resolved", "dismissed", "failed"}
                    and self._same_provider_country(service, item.service_name)
                    and item.updated_at >= now - timedelta(minutes=30)
                ),
                None,
            )
        else:
            existing = None

        if existing is not None:
            updated = existing.model_copy(
                update={
                    "severity": severity,
                    "summary": summary,
                    "human_explanation": human_explanation,
                    "recommended_action": recommended_action,
                    "failure_count": failure_count,
                    "updated_at": now,
                }
            )
            self.store.append_incident(updated)
            incidents[:] = [item for item in incidents if item.id != updated.id]
            incidents.insert(0, updated)
            events.bind(incident_id=updated.id, service_name=service.name).info(
                "agent_incident_updated", incident_type=incident_type
            )
            return updated

        return self._upsert_incident(
            incidents=incidents,
            service_name=service.name,
            incident_type=incident_type,
            severity=severity,
            summary=summary,
            human_explanation=human_explanation,
            recommended_action=recommended_action,
            failure_count=failure_count,
        )

    def _service_rotation_budget_exhausted(
        self, service_name: str, actions: list[ActionRecord]
    ) -> bool:
        return self._recovery_policy.rotation_budget_exhausted(
            service_name, actions, utc_now()
        )

    def _breaker_context(
        self,
        service: VPNService,
        assessments: dict[str, HealthAssessment],
        incidents: list[AgentIncident],
    ) -> RecoveryContext:
        assessment = assessments[service.name]
        snapshot = self._recovery_policy.snapshot(
            service.name, assessment, None, utc_now()
        )
        return self._recovery_context(
            ComposeManager(self.compose_file),
            service,
            assessment,
            snapshot,
            self.empty_state(),
            incidents,
            assessments,
        )

    def _profile_auth_config_breaker_active(
        self,
        service: VPNService,
        assessment_map: dict[str, HealthAssessment],
        incidents: list[AgentIncident],
    ) -> bool:
        return self._recovery_policy.profile_breaker(
            self._breaker_context(service, assessment_map, incidents), utc_now()
        )

    def _provider_country_breaker_active(
        self,
        service: VPNService,
        assessment_map: dict[str, HealthAssessment],
        incidents: list[AgentIncident],
    ) -> bool:
        return self._recovery_policy.provider_breaker(
            self._breaker_context(service, assessment_map, incidents), utc_now()
        )

    def _same_profile(self, service: VPNService, other_service_name: str) -> bool:
        manager = ComposeManager(self.compose_file)
        try:
            other_service = manager.get_service(other_service_name)
        except Exception:
            return False
        return other_service.profile == service.profile

    def _same_provider_country(
        self, service: VPNService, other_service_name: str
    ) -> bool:
        manager = ComposeManager(self.compose_file)
        try:
            other_service = manager.get_service(other_service_name)
        except Exception:
            return False
        return other_service.provider == service.provider and self._service_country(
            service
        ) == self._service_country(other_service)

    def _service_country(self, service: VPNService) -> str:
        country = service.environment.get("SERVER_COUNTRIES", "").strip()
        if country:
            return country.replace("-", " ").title()
        label_country = (
            service.labels.get("vpn.country", "").strip()
            if hasattr(service, "labels")
            else ""
        )
        if label_country:
            return label_country.replace("-", " ").title()

        provider = (
            service.provider.replace(" ", "-").lower() if service.provider else ""
        )
        location = (
            service.location.replace(" ", "-").lower() if service.location else ""
        )
        name = service.name.lower()

        if provider and name.startswith(provider + "-"):
            name = name[len(provider) + 1 :]

        if name.rsplit("-", 1)[-1].isdigit():
            name = name.rsplit("-", 1)[0]

        if location and name.endswith("-" + location):
            name = name[: -(len(location) + 1)]

        normalized = name.replace("-", " ").strip()
        return normalized.title() if normalized else service.location

    async def _rotate_service_via_fleet(
        self,
        service_name: str,
        *,
        state: AgentState | None = None,
    ) -> OperationResult:
        if self._is_external_endpoint(service_name):
            require_supported_operation(EgressCapabilities(), "replace_endpoint")
        fallback_countries: list[str] = []
        try:
            service = ComposeManager(self.compose_file).get_service(service_name)
        except Exception:
            service = None
        if service is not None:
            fallback_countries = self.settings.fallback_countries_by_provider.get(
                service.provider.casefold(),
                [],
            )
        fleet_manager = FleetStateManager(self.compose_file)
        try:
            progress_callback: RotationProgressCallback | None = None
            if state is not None:
                watchdog = self

                class _ProgressCallback:
                    def __call__(
                        self,
                        requested_service_name: str,
                        step: str,
                        detail: str | None = None,
                        *,
                        current_live_service_name: str | None = None,
                    ) -> None:
                        watchdog._persist_cycle_progress(
                            state,
                            service_name=requested_service_name,
                            step=f"rotate:{step}",
                            detail=detail,
                            current_live_service_name=current_live_service_name,
                        )

                progress_callback = _ProgressCallback()

            result = await fleet_manager.rotate_service(
                service_name,
                OperationConfig(
                    dry_run=False,
                    criteria=RotationCriteria.PERFORMANCE,
                    rollback_on_failure=True,
                    health_check_timeout=self.settings.probe_timeout_seconds,
                    fallback_countries=list(fallback_countries),
                    require_unique_egress_ip=True,
                ),
                progress_callback=progress_callback,
            )
        finally:
            await fleet_manager.close()
        return result

    async def build_remediation_overview(self, state: AgentState) -> dict[str, Any]:
        """Return live remediation status for the current compose root."""

        manager, services = self._inventory()
        assessments = await self._health_assessor.assess_services(services)
        incidents = self.store.load_incidents()
        snapshots_by_name = {
            snapshot.service_name: snapshot for snapshot in state.services
        }
        entries: list[dict[str, Any]] = []

        for service in services:
            assessment = assessments[service.name]
            blocks: list[dict[str, str]] = []
            if assessment.health_score >= self.settings.health_threshold:
                entries.append(
                    {
                        "service_name": service.name,
                        "healthy": True,
                        "suppressed": False,
                        "blocks": [],
                    }
                )
                continue

            if isinstance(service, ExternalProxyEndpoint):
                entries.append(
                    {
                        "service_name": service.name,
                        "healthy": False,
                        "suppressed": True,
                        "health_class": assessment.health_class,
                        "health_score": assessment.health_score,
                        "blocks": [
                            {
                                "type": "unsupported_operation",
                                "reason": "External endpoint recovery requires its operator.",
                            }
                        ],
                    }
                )
                continue

            if self._has_persistent_auth_or_config_failure(assessment.results):
                blocks.append(
                    {
                        "type": "manual",
                        "reason": "auth/config failure requires investigation",
                    }
                )
            snapshot = snapshots_by_name.get(service.name)
            if snapshot is not None and not self._rotation_grace_elapsed(snapshot):
                blocks.append(
                    {
                        "type": "cooldown",
                        "reason": "rotation grace period has not elapsed",
                    }
                )
            if self._service_rotation_budget_exhausted(service.name, state.actions):
                blocks.append(
                    {
                        "type": "service_budget",
                        "reason": "rotation budget exhausted",
                    }
                )
            if self._profile_auth_config_breaker_active(
                service, assessments, incidents
            ):
                blocks.append(
                    {
                        "type": "profile_breaker",
                        "reason": "profile auth/config breaker is active",
                    }
                )
            if self._provider_country_breaker_active(service, assessments, incidents):
                blocks.append(
                    {
                        "type": "provider_country_breaker",
                        "reason": "provider/country degradation breaker is active",
                    }
                )

            entries.append(
                {
                    "service_name": service.name,
                    "healthy": False,
                    "suppressed": bool(blocks),
                    "blocks": blocks,
                    "health_class": assessment.health_class,
                    "health_score": assessment.health_score,
                }
            )

        return {
            "blocked": any(entry.get("suppressed") for entry in entries),
            "services": entries,
        }

    def _has_persistent_auth_or_config_failure(
        self, results: list[DiagnosticResult]
    ) -> bool:
        return persistent_auth_or_config_failure(results)

    def _format_issue_summary(
        self,
        service_name: str,
        results: list[DiagnosticResult],
        fallback: str,
        recommended_action: str,
        recent_actions: list[ActionRecord],
        failure_count: int,
    ) -> tuple[str, str | None]:
        messages = [result.message for result in results if not result.passed]
        recommendations = [
            result.recommendation
            for result in results
            if not result.passed and result.recommendation
        ]
        recent_service_actions = self._recent_actions_for_service(
            recent_actions,
            service_name,
        )
        if not messages:
            return self._enrich_summary(
                IncidentContext(
                    service_name=service_name,
                    fallback_summary=f"{service_name}: {fallback}",
                    recommended_action=recommended_action,
                    failure_count=failure_count,
                    issues=[],
                    recent_actions=recent_service_actions,
                )
            )
        summary = f"{service_name}: {'; '.join(messages[:2])}"
        return self._enrich_summary(
            IncidentContext(
                service_name=service_name,
                fallback_summary=summary,
                recommended_action=recommended_action,
                failure_count=failure_count,
                issues=[
                    {
                        "check": result.check,
                        "message": result.message,
                        "recommendation": result.recommendation,
                        "persistent": result.persistent,
                    }
                    for result in results
                    if not result.passed
                ]
                or [
                    {
                        "check": "unknown",
                        "message": summary,
                        "recommendation": recommendations[0] if recommendations else "",
                        "persistent": False,
                    }
                ],
                recent_actions=recent_service_actions,
            )
        )

    def _enrich_summary(self, context: IncidentContext) -> tuple[str, str | None]:
        sanitizer = self.store.sanitizer()
        context = sanitizer.model(context)
        if self._incident_enricher is None:
            return context.fallback_summary, None
        try:
            self._incident_enricher.sanitizer = sanitizer
            enrichment = sanitizer.model(self._incident_enricher.enrich(context))
            summary = enrichment.summary.strip() or context.fallback_summary
            human_explanation = enrichment.human_explanation.strip() or None
            return summary, human_explanation
        except Exception as exc:
            if not self._llm_warning_emitted:
                logger.warning(
                    "agent_llm_unavailable",
                    extra={
                        "llm_mode": self.llm_mode,
                        "error": sanitizer.text(str(exc)),
                    },
                )
                self._llm_warning_emitted = True
            return context.fallback_summary, None

    def _build_incident_enricher(self) -> OpenAIIncidentEnricher | None:
        if self.llm_mode == "disabled":
            return None
        if self.llm_mode != "openai":
            logger.warning(
                "agent_llm_mode_unsupported",
                extra={"llm_mode": self.llm_mode},
            )
            return None
        return OpenAIIncidentEnricher(settings=self.settings)

    def _build_incident_investigator(self) -> OpenAIIncidentInvestigator | None:
        if self.llm_mode == "disabled":
            return None
        if self.llm_mode != "openai":
            return None
        return OpenAIIncidentInvestigator(settings=self.settings)

    async def _build_investigation_context(
        self, incident: AgentIncident
    ) -> InvestigationContext:
        state = self.store.read_state() or self.empty_state()
        snapshot = next(
            (
                item
                for item in state.services
                if item.service_name == incident.service_name
                and item.source == incident.source
            ),
            None,
        )
        recent_actions = (
            self._recent_actions_for_service(state.actions, incident.service_name)
            if incident.source == "gluetun"
            else []
        )

        if incident.source == "external_proxy":
            external = next(
                (
                    endpoint
                    for endpoint in self._external_endpoints()
                    if endpoint.name == incident.service_name
                ),
                None,
            )
            assessment = (
                await self._health_assessor.assess_service(external)
                if external is not None
                else None
            )
            return InvestigationContext(
                source=incident.source,
                incident_id=incident.id,
                incident_type=incident.type,
                severity=incident.severity,
                status=incident.status,
                service_name=incident.service_name,
                incident_summary=incident.summary,
                recommended_action=incident.recommended_action,
                failure_count=incident.failure_count,
                provider="external_proxy",
                container_status="not_applicable",
                health_score=assessment.health_score
                if assessment is not None
                else snapshot.health_score
                if snapshot is not None and snapshot.source == "external_proxy"
                else None,
                control_api_reachable=None,
                profile_validation_errors=[],
                issues=self._diagnostic_payload(assessment.results)
                if assessment is not None
                else [
                    {
                        "check": "configuration",
                        "message": "External endpoint is no longer configured.",
                        "recommendation": "Restore its configuration or dismiss this incident.",
                    }
                ],
                recent_actions=recent_actions,
                human_explanation=incident.human_explanation,
            )

        manager: ComposeManager | None = None
        service: VPNService | None = None
        profile = None
        try:
            manager = ComposeManager(self.compose_file)
            try:
                service, profile = manager.get_service_with_profile(
                    incident.service_name
                )
            except KeyError:
                service = manager.get_service(incident.service_name)
        except KeyError:
            service = None
        except Exception:
            service = None

        evidence = await self._runtime.collect_evidence(incident.service_name)
        container_status = evidence.container_status or (
            snapshot.container_status if snapshot is not None else "missing"
        )
        results = evidence.results
        log_evidence = self._select_log_evidence(evidence.log_lines, issues=results)
        analyzer = DiagnosticAnalyzer()

        health_score = (
            analyzer.health_score(results)
            if results
            else (snapshot.health_score if snapshot is not None else None)
        )
        control_api_reachable = None
        if service is not None:
            try:
                control_api_reachable = await self._control_api_reachable(service)
            except Exception:
                control_api_reachable = False

        profile_name = service.profile if service is not None else None
        profile_env_file = None
        profile_validation_errors: list[str] = []
        healthy_shared_profile_peers: list[str] = []
        auth_config_shared_profile_peers: list[str] = []
        other_unhealthy_shared_profile_peers: list[str] = []
        shared_profile_peer_probe_failures: list[str] = []
        if profile is not None:
            profile_env_file = str(profile._resolve_env_path())
            profile_validation_errors = self._validate_profile_for_investigation(
                profile=profile,
                service=service,
            )
        if manager is not None and service is not None:
            peer_evidence = await self._collect_shared_profile_peer_evidence(
                manager=manager,
                state=state,
                service=service,
            )
            healthy_shared_profile_peers = peer_evidence["healthy"]
            auth_config_shared_profile_peers = peer_evidence["auth_config"]
            other_unhealthy_shared_profile_peers = peer_evidence["other_unhealthy"]
            shared_profile_peer_probe_failures = peer_evidence["probe_failed"]

        return InvestigationContext(
            source=incident.source,
            incident_id=incident.id,
            incident_type=incident.type,
            severity=incident.severity,
            status=incident.status,
            service_name=incident.service_name,
            incident_summary=incident.summary,
            recommended_action=incident.recommended_action,
            failure_count=incident.failure_count,
            provider=service.provider if service is not None else None,
            location=service.location if service is not None else None,
            profile_name=profile_name,
            profile_env_file=profile_env_file,
            container_status=container_status,
            health_score=health_score,
            control_api_reachable=control_api_reachable,
            profile_validation_errors=profile_validation_errors,
            healthy_shared_profile_peers=healthy_shared_profile_peers,
            auth_config_shared_profile_peers=auth_config_shared_profile_peers,
            other_unhealthy_shared_profile_peers=other_unhealthy_shared_profile_peers,
            shared_profile_peer_probe_failures=shared_profile_peer_probe_failures,
            issues=self._diagnostic_payload(results),
            log_evidence=log_evidence,
            recent_actions=recent_actions,
            human_explanation=incident.human_explanation,
        )

    def _diagnostic_payload(
        self, results: list[DiagnosticResult]
    ) -> list[dict[str, Any]]:
        return [
            {
                "check": result.check,
                "message": result.message,
                "recommendation": result.recommendation,
                "persistent": result.persistent,
            }
            for result in results
            if not result.passed
        ]

    def _validate_profile_for_investigation(
        self,
        profile,
        service: VPNService | None,
    ) -> list[str]:
        env_path = profile._resolve_env_path()
        env_vars = _load_env_file(str(env_path))
        errors: list[str] = []

        if not env_path.is_file():
            return [f"Profile env file '{env_path}' does not exist."]

        provider = env_vars.get("VPN_SERVICE_PROVIDER", "").strip()
        if not provider:
            errors.append("VPN_SERVICE_PROVIDER is missing from the profile env file.")
        elif service is not None and provider.lower() != service.provider.lower():
            errors.append(
                "VPN_SERVICE_PROVIDER in the profile env file does not match the service provider."
            )

        vpn_type = env_vars.get("VPN_TYPE", "openvpn").strip().lower() or "openvpn"
        if vpn_type not in {"openvpn", "wireguard"}:
            errors.append("VPN_TYPE must be either 'openvpn' or 'wireguard'.")

        if vpn_type == "openvpn":
            if not env_vars.get("OPENVPN_USER"):
                errors.append("OPENVPN_USER is missing from the profile env file.")
            if not env_vars.get("OPENVPN_PASSWORD"):
                errors.append("OPENVPN_PASSWORD is missing from the profile env file.")

        effective_http_proxy = env_vars.get("HTTPPROXY", "")
        effective_proxy_user = env_vars.get("HTTPPROXY_USER")
        effective_proxy_password = env_vars.get("HTTPPROXY_PASSWORD")

        if service is not None:
            effective_http_proxy = service.environment.get(
                "HTTPPROXY", effective_http_proxy
            )
            effective_proxy_user = service.environment.get(
                "HTTPPROXY_USER", effective_proxy_user
            )
            effective_proxy_password = service.environment.get(
                "HTTPPROXY_PASSWORD", effective_proxy_password
            )
            if service.credentials is not None:
                effective_proxy_user = (
                    service.credentials.httpproxy_user or effective_proxy_user
                )
                effective_proxy_password = (
                    service.credentials.httpproxy_password or effective_proxy_password
                )

        if effective_http_proxy.strip().lower() in {"on", "true", "1"}:
            if not effective_proxy_user:
                errors.append("HTTPPROXY_USER is required when HTTPPROXY=on.")
            if not effective_proxy_password:
                errors.append("HTTPPROXY_PASSWORD is required when HTTPPROXY=on.")

        return errors

    async def _collect_shared_profile_peer_evidence(
        self,
        manager: ComposeManager,
        state: AgentState,
        service: VPNService,
    ) -> dict[str, list[str]]:
        """Return classified evidence for peer services that share the same profile."""

        snapshots_by_name = {
            snapshot.service_name: snapshot for snapshot in state.services
        }
        evidence = {
            "healthy": [],
            "auth_config": [],
            "other_unhealthy": [],
            "probe_failed": [],
        }

        for candidate in manager.list_services():
            if candidate.name == service.name or candidate.profile != service.profile:
                continue

            snapshot = snapshots_by_name.get(candidate.name)
            if snapshot is not None and (
                snapshot.container_status == "running"
                and snapshot.health_score >= self.settings.health_threshold
            ):
                evidence["healthy"].append(candidate.name)
                continue

            try:
                peer_health = await self._evaluate_health(candidate)
            except Exception as exc:
                logger.warning(
                    "agent_shared_profile_peer_probe_failed",
                    extra={
                        "service_name": service.name,
                        "peer_service_name": candidate.name,
                        "error": str(exc),
                    },
                )
                evidence["probe_failed"].append(candidate.name)
                continue

            is_healthy = (
                peer_health["container_status"] == "running"
                and peer_health["health_score"] >= self.settings.health_threshold
            )
            if is_healthy:
                evidence["healthy"].append(candidate.name)
                continue

            peer_results = peer_health["results"]
            has_auth_or_config_issue = any(
                isinstance(result, DiagnosticResult)
                and not result.passed
                and result.check in {"auth_failure", "config_error"}
                for result in peer_results
            )
            if has_auth_or_config_issue:
                evidence["auth_config"].append(candidate.name)
            else:
                evidence["other_unhealthy"].append(candidate.name)

        for names in evidence.values():
            names.sort()
        return evidence

    def _investigate_context(self, context: InvestigationContext) -> InvestigationPlan:
        sanitizer = self.store.sanitizer()
        context = sanitizer.model(context)
        fallback = sanitizer.model(self._fallback_investigation(context))
        if self._incident_investigator is None:
            return fallback

        try:
            self._incident_investigator.sanitizer = sanitizer
            plan = sanitizer.model(self._incident_investigator.investigate(context))
        except Exception as exc:
            if not self._llm_warning_emitted:
                logger.warning(
                    "agent_llm_unavailable",
                    extra={
                        "llm_mode": self.llm_mode,
                        "error": sanitizer.text(str(exc)),
                    },
                )
                self._llm_warning_emitted = True
            return fallback

        return InvestigationPlan(
            summary=plan.summary.strip() or fallback.summary,
            findings=[item.strip() for item in plan.findings if item and item.strip()]
            or fallback.findings,
            action_plan=[
                item.strip() for item in plan.action_plan if item and item.strip()
            ]
            or fallback.action_plan,
        )

    def _fallback_investigation(
        self, context: InvestigationContext
    ) -> InvestigationPlan:
        if context.source == "external_proxy":
            return InvestigationPlan(
                summary=f"Investigate external endpoint '{context.service_name}'.",
                findings=[f"Current health score: {context.health_score}/100."]
                + [issue["message"] for issue in context.issues],
                action_plan=[
                    "Verify the configured proxy address and credential environment variables.",
                    "Check upstream availability and any configured exit-IP allowlist with the endpoint operator.",
                    "Reassess the endpoint after repair. Restart, replacement, session renewal, and exit-IP change are unsupported by this adapter.",
                ],
            )
        findings: list[str] = []
        issues_by_check = {issue["check"]: issue for issue in context.issues}

        self._append_unique(findings, f"Incident summary: {context.incident_summary}")
        self._append_unique(
            findings, f"Current container status: {context.container_status}."
        )
        if context.health_score is not None:
            self._append_unique(
                findings, f"Current health score: {context.health_score}/100."
            )
        self._append_unique(
            findings, f"Failure count observed by watchdog: {context.failure_count}."
        )
        if context.profile_env_file:
            profile_label = context.profile_name or "unknown"
            self._append_unique(
                findings,
                f"Profile '{profile_label}' resolves to env file '{context.profile_env_file}'.",
            )
        if context.healthy_shared_profile_peers:
            peers = self._format_service_names(context.healthy_shared_profile_peers)
            profile_label = context.profile_name or "unknown"
            self._append_unique(
                findings,
                f"Profile '{profile_label}' is also healthy in other containers: {peers}. "
                "That weakens suspicion of a profile-wide or account-wide provider "
                "issue and points to a service-specific or endpoint-specific issue.",
            )
        if context.auth_config_shared_profile_peers:
            peers = self._format_service_names(context.auth_config_shared_profile_peers)
            profile_label = context.profile_name or "unknown"
            self._append_unique(
                findings,
                f"Other containers sharing profile '{profile_label}' also show auth/config "
                f"problems: {peers}. That supports an account/profile-wide issue such as "
                "bad credentials, provider-side limits, suspension, or other provider-side "
                "account issues.",
            )
        if context.other_unhealthy_shared_profile_peers:
            peers = self._format_service_names(
                context.other_unhealthy_shared_profile_peers
            )
            profile_label = context.profile_name or "unknown"
            self._append_unique(
                findings,
                f"Other containers sharing profile '{profile_label}' are unhealthy: {peers}. "
                "Current peer evidence does not show the same auth/config failure there, "
                "so this alone does not prove an account/profile-wide credential issue.",
            )
        if context.shared_profile_peer_probe_failures:
            peers = self._format_service_names(
                context.shared_profile_peer_probe_failures
            )
            self._append_unique(
                findings,
                f"Could not fully inspect some same-profile peers: {peers}. Peer evidence is incomplete.",
            )
        if context.control_api_reachable is False:
            self._append_unique(
                findings,
                "The Gluetun control API is not reachable on the configured control port.",
            )
        if context.log_evidence:
            self._append_unique(
                findings,
                "Recent log evidence is attached below and should drive the next operator action.",
            )
        if context.human_explanation:
            self._append_unique(findings, context.human_explanation)
        for error in context.profile_validation_errors[:4]:
            self._append_unique(findings, error)
        for issue in context.issues[:4]:
            message = issue.get("message", "").strip()
            recommendation = issue.get("recommendation", "").strip()
            if message:
                detail = message
                if recommendation:
                    detail = f"{detail} Recommendation: {recommendation}"
                self._append_unique(findings, detail)
        for action in context.recent_actions[-3:]:
            self._append_unique(
                findings,
                f"Recent action: {self._describe_recent_action(action)}",
            )

        if context.incident_type in {
            "auth_config_failure",
            "profile_auth_config_failure",
        }:
            if context.profile_validation_errors or "config_error" in issues_by_check:
                summary = (
                    f"{context.service_name}: configuration for the VPN profile or "
                    "service definition is incomplete or inconsistent."
                )
                action_plan = self._auth_config_action_plan(context)
            elif (
                "auth_failure" in issues_by_check
                and context.healthy_shared_profile_peers
            ):
                summary = (
                    f"{context.service_name}: auth-like failures look isolated to this "
                    f"service because profile '{context.profile_name or 'unknown'}' is "
                    f"healthy in {len(context.healthy_shared_profile_peers)} other "
                    "container(s)."
                )
                action_plan = self._isolated_service_action_plan(context)
            elif context.auth_config_shared_profile_peers:
                summary = (
                    f"{context.service_name}: multiple containers sharing profile "
                    f"'{context.profile_name or 'unknown'}' show auth/config problems, "
                    "which is consistent with an account/profile-wide issue."
                )
                action_plan = self._auth_config_action_plan(context)
            elif "auth_failure" in issues_by_check:
                summary = (
                    f"{context.service_name}: the VPN provider is rejecting the "
                    "configured authentication details."
                )
                action_plan = self._auth_config_action_plan(context)
            else:
                summary = (
                    f"{context.service_name}: authentication or configuration needs "
                    "manual review before more automation."
                )
                action_plan = self._auth_config_action_plan(context)
        elif context.incident_type in {
            "rotation_required",
            "rotation_exhausted",
            "provider_outage_suspected",
        }:
            if "route_error" in issues_by_check:
                summary = (
                    f"{context.service_name}: recent logs show OpenVPN route setup "
                    "errors, so review the local route state before rotating endpoints."
                )
                action_plan = self._generic_action_plan(context)
            else:
                summary = (
                    f"{context.service_name}: automatic remediation did not recover the "
                    "service, so a supervised rotation is the next step."
                )
                action_plan = self._rotation_action_plan(context)
        else:
            if "route_error" in issues_by_check:
                summary = (
                    f"{context.service_name}: recent logs show OpenVPN route setup "
                    "errors that need manual review before more automation."
                )
            else:
                summary = (
                    f"{context.service_name}: review the current incident evidence and "
                    "apply a manual fix before re-running health checks."
                )
            action_plan = self._generic_action_plan(context)

        return InvestigationPlan(
            summary=summary,
            findings=findings,
            action_plan=action_plan,
        )

    def _auth_config_action_plan(self, context: InvestigationContext) -> list[str]:
        service_name = context.service_name
        plan: list[str] = []
        if context.profile_env_file:
            plan.append(
                "Inspect the profile env file at "
                f"'{context.profile_env_file}' and verify "
                "`VPN_SERVICE_PROVIDER`, `VPN_TYPE`, and the required auth fields "
                "for the selected VPN type."
            )
        else:
            plan.append(
                "Inspect the service profile configuration and verify the required "
                "provider and authentication fields are present."
            )
        plan.append(
            "Re-enter or rotate the VPN provider credentials for this profile "
            "without exposing the secret values in logs or shell history."
        )
        if context.provider or context.location:
            provider_text = context.provider or "the configured provider"
            location_text = context.location or "the configured location"
            plan.append(
                f"Confirm the compose service targets {provider_text} / "
                f"{location_text} and that the location fields still match an "
                "available endpoint."
            )
        plan.append(
            f"Recreate the container with `proxy2vpn vpn update {service_name}` "
            "after the profile is corrected."
        )
        plan.append(
            f"Validate recovery with `proxy2vpn vpn test {service_name}` and then "
            "`proxy2vpn agent run --once` to confirm the incident closes cleanly."
        )
        return plan

    def _rotation_action_plan(self, context: InvestigationContext) -> list[str]:
        service_name = context.service_name
        return [
            "Review the recent findings and automatic remediation attempts to confirm "
            "the failure is not caused by bad credentials or broken local config.",
            f"Approve supervised rotation with `proxy2vpn agent approve {context.incident_id}`.",
            f"Verify the replacement endpoint with `proxy2vpn vpn test {service_name}`.",
            "Run `proxy2vpn agent run --once` to refresh watchdog state and confirm "
            "the incident resolves.",
        ]

    def _generic_action_plan(self, context: InvestigationContext) -> list[str]:
        service_name = context.service_name
        issue_checks = {issue.get("check") for issue in context.issues}
        if "route_error" in issue_checks:
            return [
                "Review the attached route-related log evidence and confirm the same lines with "
                f"`proxy2vpn vpn logs {service_name} --lines 100`.",
                "Inspect duplicate or stale routes, IPv6 route injection, and other local "
                "network state around `tun0` before changing credentials or rotating providers.",
                f"Recreate the service with `proxy2vpn vpn update {service_name}` if the route "
                "state looks stale or inconsistent.",
                f"Validate the tunnel with `proxy2vpn vpn test {service_name}` and "
                "`proxy2vpn agent run --once`.",
            ]
        return [
            f"Review recent logs with `proxy2vpn vpn logs {service_name} --lines 100`.",
            "Inspect the service profile and compose configuration for drift or "
            "missing settings.",
            f"Recreate the service with `proxy2vpn vpn update {service_name}` once "
            "the configuration issue is corrected.",
            f"Validate the tunnel with `proxy2vpn vpn test {service_name}` and "
            "`proxy2vpn agent run --once`.",
        ]

    def _isolated_service_action_plan(self, context: InvestigationContext) -> list[str]:
        service_name = context.service_name
        profile_label = context.profile_name or "unknown"
        peers = self._format_service_names(context.healthy_shared_profile_peers)
        plan = [
            f"Compare `{service_name}` against healthy containers sharing profile "
            f"'{profile_label}' ({peers}) and look for service-specific drift in "
            "location, env overrides, port mappings, or recreate history.",
            "Do not rotate the shared profile credentials yet; first inspect "
            "service-specific drift, endpoint selection, and port/control-path issues.",
            f"Review recent logs with `proxy2vpn vpn logs {service_name} --lines 100` "
            "and focus on endpoint-specific failures.",
        ]
        if context.provider or context.location:
            provider_text = context.provider or "the configured provider"
            location_text = context.location or "the configured location"
            plan.append(
                f"Validate the target endpoint for {provider_text} / {location_text}; "
                "if needed, rotate or adjust only this service rather than the whole "
                "profile."
            )
        if context.control_api_reachable is False:
            plan.append(
                "The control API is currently unreachable, so a manual tunnel restart "
                "is unlikely to succeed."
            )
            plan.append(
                f"Recreate only this service with `proxy2vpn vpn update {service_name}` "
                f"and validate recovery with `proxy2vpn vpn test {service_name}`."
            )
        else:
            plan.append(
                f"Request a tunnel restart with `proxy2vpn vpn restart-tunnel {service_name}` "
                f"and retest with `proxy2vpn vpn test {service_name}` before recreating the container."
            )
            plan.append(
                f"If the service remains unhealthy after the tunnel restart, recreate only "
                f"this service with `proxy2vpn vpn update {service_name}`."
            )
        plan.append(
            "Run `proxy2vpn agent run --once` to refresh watchdog state and confirm "
            "whether the isolated incident closes."
        )
        return plan

    def _append_unique(self, items: list[str], value: str) -> None:
        clean = value.strip()
        if clean and clean not in items:
            items.append(clean)

    def _format_service_names(self, names: list[str], limit: int = 3) -> str:
        if not names:
            return "none"
        visible = names[:limit]
        if len(names) <= limit:
            return ", ".join(visible)
        return f"{', '.join(visible)} (+{len(names) - limit} more)"

    def _upsert_incident(
        self,
        incidents: list[AgentIncident],
        service_name: str,
        incident_type: str,
        severity: IncidentSeverity,
        summary: str,
        human_explanation: str | None,
        recommended_action: str,
        failure_count: int,
        *,
        source: str = "gluetun",
    ) -> AgentIncident | None:
        now = utc_now()
        if self._is_recently_dismissed(incidents, service_name, incident_type, now):
            return None

        existing = self._find_active_incident(incidents, service_name, incident_type)
        if existing is not None:
            updated = existing.model_copy(
                update={
                    "source": source,
                    "severity": severity,
                    "summary": summary,
                    "human_explanation": human_explanation,
                    "recommended_action": recommended_action,
                    "approval_required": False,
                    "failure_count": failure_count,
                    "updated_at": now,
                }
            )
            self.store.append_incident(updated)
            incidents[:] = [item for item in incidents if item.id != updated.id]
            incidents.insert(0, updated)
            events.bind(incident_id=updated.id, service_name=service_name).info(
                "agent_incident_updated", incident_type=incident_type
            )
            return updated

        incident = AgentIncident(
            source=source,
            id=uuid4().hex[:12],
            service_name=service_name,
            type=incident_type,
            severity=severity,
            status="open",
            created_at=now,
            updated_at=now,
            failure_count=failure_count,
            summary=summary,
            human_explanation=human_explanation,
            recommended_action=recommended_action,
            approval_required=False,
        )
        self.store.append_incident(incident)
        incidents.insert(0, incident)
        events.bind(incident_id=incident.id, service_name=service_name).warning(
            "agent_incident_opened", incident_type=incident_type, severity=severity
        )
        return incident

    def _resolve_active_incidents(
        self,
        service_name: str,
        incidents: list[AgentIncident],
        *,
        source: str = "gluetun",
    ) -> None:
        now = utc_now()
        for incident in list(incidents):
            if incident.service_name != service_name or incident.source != source:
                continue
            if incident.status in {"resolved", "dismissed"}:
                continue
            resolved = incident.model_copy(
                update={"status": "resolved", "resolved_at": now, "updated_at": now}
            )
            self.store.append_incident(resolved)
            incidents.remove(incident)
            incidents.insert(0, resolved)
            events.bind(incident_id=incident.id, service_name=service_name).info(
                "agent_incident_resolved", incident_type=incident.type
            )

    def _migrate_active_incidents(
        self,
        old_service_name: str,
        new_service_name: str,
        exclude_incident_ids: set[str] | None = None,
    ) -> None:
        exclude_ids = exclude_incident_ids or set()
        now = utc_now()
        incidents = self.store.load_incidents()
        for incident in incidents:
            if incident.id in exclude_ids:
                continue
            if (
                incident.service_name != old_service_name
                or incident.source != "gluetun"
            ):
                continue
            if incident.status in {"resolved", "dismissed", "failed"}:
                continue
            migrated = incident.model_copy(
                update={"service_name": new_service_name, "updated_at": now}
            )
            self.store.append_incident(migrated)

    def _find_active_incident(
        self,
        incidents: list[AgentIncident],
        service_name: str,
        incident_type: str,
    ) -> AgentIncident | None:
        for incident in incidents:
            if incident.service_name != service_name or incident.type != incident_type:
                continue
            if incident.status not in {"resolved", "dismissed", "failed"}:
                return incident
        return None

    def _is_recently_dismissed(
        self,
        incidents: list[AgentIncident],
        service_name: str,
        incident_type: str,
        now: datetime,
    ) -> bool:
        return recently_dismissed(
            incidents,
            service_name,
            incident_type,
            now,
            self.settings.incident_cooldown_seconds,
        )

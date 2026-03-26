"""Rule-first watchdog runtime for proxy2vpn services."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
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
    IncidentInvestigation,
    AgentState,
    AgentStatus,
    DaemonMode,
    IncidentStatus,
    ServiceSnapshot,
)
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.adapters import docker_ops
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.fleet_state_manager import (
    FleetStateManager,
    OperationConfig,
    RotationCriteria,
)
from proxy2vpn.adapters.http_client import GluetunControlClient
from proxy2vpn.adapters.logging_utils import get_logger
from proxy2vpn.core.models import VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer, DiagnosticResult

logger = get_logger(__name__)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


class AgentWatchdog:
    """Watch one compose root and apply limited remediation policies."""

    def __init__(
        self,
        compose_file: Path,
        interval_seconds: int | None = None,
        llm_mode: str | None = None,
        store: AgentStateStore | None = None,
        settings: AgentSettings | None = None,
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
        self._incident_enricher = self._build_incident_enricher()
        self._incident_investigator = self._build_incident_investigator()
        self._llm_warning_emitted = False

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
        while True:
            state = await self.run_cycle(state)
            await asyncio.sleep(self.interval_seconds)

    async def run_once(self) -> AgentState:
        """Execute one cycle and exit."""

        return await self.run_cycle(self._load_state("once", refresh_started_at=True))

    async def run_cycle(self, state: AgentState) -> AgentState:
        """Execute one monitoring and remediation cycle."""

        manager = ComposeManager(self.compose_file)
        services = manager.list_services()
        snapshots_by_name = {
            snapshot.service_name: snapshot for snapshot in state.services
        }
        incidents = self.store.load_incidents()

        state.status.compose_path = str(self.compose_file)
        state.status.interval_seconds = self.interval_seconds
        state.status.llm_mode = self.llm_mode
        state.status.last_error = None
        state.status.service_count = len(services)

        updated_snapshots: list[ServiceSnapshot] = []
        cycle_error: Exception | None = None
        try:
            for service in services:
                snapshot = await self._process_service(
                    manager=manager,
                    service=service,
                    previous=snapshots_by_name.get(service.name),
                    state=state,
                    incidents=incidents,
                )
                updated_snapshots.append(snapshot)
        except Exception as exc:
            state.status.last_error = str(exc)
            logger.error("agent_cycle_failed", extra={"error": str(exc)})
            cycle_error = exc
        finally:
            state.status.last_loop_at = utc_now()

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

    async def approve_incident(self, incident_id: str) -> AgentIncident:
        """Approve one incident and execute its pending escalation exactly once."""

        incidents = self.store.load_incidents()
        incident = next((item for item in incidents if item.id == incident_id), None)
        if incident is None:
            raise KeyError(f"Incident '{incident_id}' not found")
        if incident.status in {"resolved", "dismissed", "failed"}:
            raise RuntimeError(f"Incident '{incident_id}' is already closed")
        if not incident.approval_required:
            raise RuntimeError(f"Incident '{incident_id}' does not require approval")
        if incident.approved_at is not None:
            raise RuntimeError(f"Incident '{incident_id}' was already approved")

        approved = incident.model_copy(
            update={
                "status": "approved",
                "approved_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        self.store.append_incident(approved)

        fleet_manager = FleetStateManager(self.compose_file)
        try:
            result = await fleet_manager.rotate_service(
                approved.service_name,
                OperationConfig(
                    dry_run=False,
                    criteria=RotationCriteria.PERFORMANCE,
                    rollback_on_failure=True,
                ),
            )
        finally:
            await fleet_manager.close()

        state = self.store.read_state() or self.empty_state()
        action_result = "success" if result.success else "failed"
        self._append_action(
            state,
            service_name=approved.service_name,
            action="rotate",
            trigger="manual_approval",
            result=action_result,
            details={"incident_id": approved.id, "errors": " | ".join(result.errors)},
        )
        self._update_snapshot_action(
            state,
            approved.service_name,
            last_action="rotate",
            last_action_result=action_result,
        )
        self.store.write_state(state)

        terminal_status: IncidentStatus = "resolved" if result.success else "failed"
        summary_text = (
            approved.summary
            if result.success
            else f"{approved.summary} Rotation failed: {' | '.join(result.errors)}"
        )
        summary, human_explanation = self._enrich_summary(
            IncidentContext(
                service_name=approved.service_name,
                fallback_summary=summary_text,
                recommended_action=approved.recommended_action,
                failure_count=approved.failure_count,
                issues=[],
                recent_actions=[
                    {
                        "action": action.action,
                        "result": action.result,
                        "trigger": action.trigger,
                    }
                    for action in state.actions[-5:]
                    if action.service_name == approved.service_name
                ],
            )
        )
        terminal = approved.model_copy(
            update={
                "status": terminal_status,
                "resolved_at": utc_now() if result.success else None,
                "updated_at": utc_now(),
                "summary": summary,
                "human_explanation": human_explanation or approved.human_explanation,
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

        context = await self._build_investigation_context(incident)
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

    async def _process_service(
        self,
        manager: ComposeManager,
        service: VPNService,
        previous: ServiceSnapshot | None,
        state: AgentState,
        incidents: list[AgentIncident],
    ) -> ServiceSnapshot:
        container = docker_ops.get_container_by_service_name(service.name)
        container_status = "missing"
        results: list[DiagnosticResult] = []
        health_score = 0
        control_api_reachable = False

        if container is not None:
            try:
                container.reload()
            except Exception:
                pass
            container_status = getattr(container, "status", "unknown") or "unknown"
            if container_status == "running":
                analyzer = DiagnosticAnalyzer()
                results = await self._analyze_service_logs(service.name, analyzer)
                health_score = analyzer.health_score(results)
                control_api_reachable = await self._control_api_reachable(service)

        failure_count = (
            0
            if health_score >= self.settings.health_threshold
            else (previous.consecutive_failures + 1 if previous else 1)
        )
        snapshot = ServiceSnapshot(
            service_name=service.name,
            container_status=container_status,
            health_score=health_score,
            consecutive_failures=failure_count,
            last_check_at=utc_now(),
            last_action=previous.last_action if previous else None,
            last_action_result=previous.last_action_result if previous else None,
        )

        if health_score >= self.settings.health_threshold:
            self._resolve_active_incidents(service.name, incidents)
            return snapshot

        if self._has_persistent_auth_or_config_failure(results):
            auth_restart = await self._attempt_isolated_auth_restart(
                manager=manager,
                state=state,
                incidents=incidents,
                service=service,
                results=results,
                control_api_reachable=control_api_reachable,
            )
            if auth_restart is not None:
                restart_result, post_restart = auth_restart
                snapshot.last_action = "restart_tunnel"
                snapshot.last_action_result = restart_result
                snapshot.container_status = post_restart["container_status"]
                snapshot.health_score = post_restart["health_score"]
                snapshot.last_check_at = utc_now()
                snapshot.consecutive_failures = (
                    0
                    if post_restart["health_score"] >= self.settings.health_threshold
                    else failure_count
                )
                if post_restart["health_score"] >= self.settings.health_threshold:
                    self._resolve_active_incidents(service.name, incidents)
                    return snapshot
                results = post_restart["results"]

            summary, human_explanation = self._format_issue_summary(
                service.name,
                results,
                fallback="Persistent authentication or configuration failure detected.",
                recommended_action="investigate",
                recent_actions=state.actions,
                failure_count=failure_count,
            )
            incident = self._upsert_incident(
                incidents=incidents,
                service_name=service.name,
                incident_type="auth_config_failure",
                severity="high",
                summary=summary,
                human_explanation=human_explanation,
                recommended_action="investigate",
                approval_required=False,
                failure_count=failure_count,
            )
            if incident is not None:
                logger.warning(
                    "agent_auth_config_failure",
                    extra={"service_name": service.name, "incident_id": incident.id},
                )
            return snapshot

        if (
            container_status == "running"
            and failure_count == 1
            and control_api_reachable
        ):
            restart_result = await self._restart_tunnel(service, state)
            snapshot.last_action = "restart_tunnel"
            snapshot.last_action_result = restart_result
            await asyncio.sleep(self.settings.recheck_delay_seconds)

            post_restart = await self._evaluate_health(service)
            snapshot.container_status = post_restart["container_status"]
            snapshot.health_score = post_restart["health_score"]
            snapshot.last_check_at = utc_now()
            snapshot.consecutive_failures = (
                0
                if post_restart["health_score"] >= self.settings.health_threshold
                else failure_count
            )

            if post_restart["health_score"] >= self.settings.health_threshold:
                self._resolve_active_incidents(service.name, incidents)
                return snapshot

            results = post_restart["results"]

        if self._can_restore(service.name, state.actions):
            restore_result, post_restore = await self._restore_service(
                manager=manager,
                service=service,
                state=state,
            )
            snapshot.last_action = "restore"
            snapshot.last_action_result = restore_result
            snapshot.health_score = post_restore["health_score"]
            snapshot.last_check_at = utc_now()
            snapshot.container_status = post_restore["container_status"]
            snapshot.consecutive_failures = (
                0
                if post_restore["health_score"] >= self.settings.health_threshold
                else failure_count
            )
            if post_restore["health_score"] >= self.settings.health_threshold:
                self._resolve_active_incidents(service.name, incidents)
                return snapshot

        summary, human_explanation = self._format_issue_summary(
            service.name,
            results,
            fallback="Service remains unhealthy after automatic remediation. Rotation approval is required.",
            recommended_action="rotate",
            recent_actions=state.actions,
            failure_count=failure_count,
        )
        self._upsert_incident(
            incidents=incidents,
            service_name=service.name,
            incident_type="rotation_required",
            severity="medium",
            summary=summary,
            human_explanation=human_explanation,
            recommended_action="rotate",
            approval_required=True,
            failure_count=failure_count,
        )
        return snapshot

    async def _control_api_reachable(self, service: VPNService) -> bool:
        base_url = f"http://localhost:{service.control_port}/v1"
        try:
            async with GluetunControlClient(base_url) as client:
                await client.status()
            return True
        except Exception:
            return False

    async def _restart_tunnel(
        self,
        service: VPNService,
        state: AgentState,
        trigger: str = "first_unhealthy_cycle",
    ) -> str:
        base_url = f"http://localhost:{service.control_port}/v1"
        try:
            async with GluetunControlClient(base_url) as client:
                await client.restart_tunnel()
            self._append_action(
                state,
                service_name=service.name,
                action="restart_tunnel",
                trigger=trigger,
                result="success",
                details={"control_port": str(service.control_port)},
            )
            return "success"
        except Exception as exc:
            self._append_action(
                state,
                service_name=service.name,
                action="restart_tunnel",
                trigger=trigger,
                result="failed",
                details={"error": str(exc)},
            )
            return "failed"

    async def _restore_service(
        self,
        manager: ComposeManager,
        service: VPNService,
        state: AgentState,
    ) -> tuple[str, dict[str, object]]:
        try:
            profile = manager.get_profile(service.profile)
            await asyncio.to_thread(
                docker_ops.start_vpn_service, service, profile, True
            )
            await asyncio.sleep(self.settings.recheck_delay_seconds)
            health = await self._evaluate_health(service)
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
        except Exception as exc:
            self._append_action(
                state,
                service_name=service.name,
                action="restore",
                trigger="automatic_remediation",
                result="failed",
                details={"error": str(exc), "profile": service.profile},
            )
            return "failed", {
                "container_status": "missing",
                "health_score": 0,
                "results": [],
            }

    async def _evaluate_health(self, service: VPNService) -> dict[str, object]:
        container = docker_ops.get_container_by_service_name(service.name)
        if container is None:
            return {"container_status": "missing", "health_score": 0, "results": []}
        try:
            container.reload()
        except Exception:
            pass
        container_status = getattr(container, "status", "unknown") or "unknown"
        if container_status != "running":
            return {
                "container_status": container_status,
                "health_score": 0,
                "results": [],
            }

        analyzer = DiagnosticAnalyzer()
        results = await self._analyze_service_logs(service.name, analyzer)
        return {
            "container_status": container_status,
            "health_score": analyzer.health_score(results),
            "results": results,
        }

    async def _analyze_service_logs(
        self,
        service_name: str,
        analyzer: DiagnosticAnalyzer,
        lines: int = 20,
        timeout: int = 5,
    ) -> list[DiagnosticResult]:
        # Keep synchronous diagnostics off the watchdog event loop because
        # connectivity checks use sync IP helpers that manage their own loop.
        return await asyncio.to_thread(
            docker_ops.analyze_container_logs,
            service_name,
            lines,
            analyzer,
            timeout,
        )

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

    def _update_snapshot_action(
        self,
        state: AgentState,
        service_name: str,
        last_action: str,
        last_action_result: str,
    ) -> None:
        for snapshot in state.services:
            if snapshot.service_name == service_name:
                snapshot.last_action = last_action
                snapshot.last_action_result = last_action_result
                snapshot.last_check_at = utc_now()
                break

    def _can_restore(self, service_name: str, actions: list[ActionRecord]) -> bool:
        cutoff = utc_now() - timedelta(seconds=self.settings.restore_cooldown_seconds)
        for action in reversed(actions):
            if action.service_name == service_name and action.action == "restore":
                return action.ts < cutoff
        return True

    async def _attempt_isolated_auth_restart(
        self,
        manager: ComposeManager,
        state: AgentState,
        incidents: list[AgentIncident],
        service: VPNService,
        results: list[DiagnosticResult],
        control_api_reachable: bool,
    ) -> tuple[str, dict[str, object]] | None:
        if not control_api_reachable:
            return None
        if self._find_active_incident(incidents, service.name, "auth_config_failure"):
            return None
        if any(
            result.check == "config_error" and not result.passed for result in results
        ):
            return None
        if not any(
            result.check == "auth_failure" and not result.passed for result in results
        ):
            return None

        peer_evidence = await self._collect_shared_profile_peer_evidence(
            manager=manager,
            state=state,
            service=service,
        )
        if not peer_evidence["healthy"]:
            return None

        restart_result = await self._restart_tunnel(
            service,
            state,
            trigger="isolated_auth_failure",
        )
        await asyncio.sleep(self.settings.recheck_delay_seconds)
        post_restart = await self._evaluate_health(service)
        return restart_result, post_restart

    def _has_persistent_auth_or_config_failure(
        self, results: list[DiagnosticResult]
    ) -> bool:
        for result in results:
            if result.check in {"auth_failure", "config_error"} and not result.passed:
                return bool(result.persistent or result.check == "config_error")
        return False

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
        recent_service_actions = [
            {
                "action": action.action,
                "result": action.result,
                "trigger": action.trigger,
            }
            for action in recent_actions[-5:]
            if action.service_name == service_name
        ]
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
        if self._incident_enricher is None:
            return context.fallback_summary, None
        try:
            enrichment = self._incident_enricher.enrich(context)
            summary = enrichment.summary.strip() or context.fallback_summary
            human_explanation = enrichment.human_explanation.strip() or None
            return summary, human_explanation
        except Exception as exc:
            if not self._llm_warning_emitted:
                logger.warning(
                    "agent_llm_unavailable",
                    extra={"llm_mode": self.llm_mode, "error": str(exc)},
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
            ),
            None,
        )
        recent_actions = [
            {
                "action": action.action,
                "result": action.result,
                "trigger": action.trigger,
            }
            for action in state.actions[-5:]
            if action.service_name == incident.service_name
        ]

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

        container = docker_ops.get_container_by_service_name(incident.service_name)
        container_status = (
            snapshot.container_status if snapshot is not None else "missing"
        )
        if container is not None:
            try:
                container.reload()
            except Exception:
                pass
            container_status = (
                getattr(container, "status", container_status) or "unknown"
            )

        results: list[DiagnosticResult] = []
        analyzer = DiagnosticAnalyzer()
        if container_status == "running":
            try:
                results = await self._analyze_service_logs(
                    incident.service_name, analyzer
                )
            except Exception:
                results = []

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
        env_vars = docker_ops._load_env_file(str(env_path))
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

            peer_results = peer_health.get("results", [])
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
        fallback = self._fallback_investigation(context)
        if self._incident_investigator is None:
            return fallback

        try:
            plan = self._incident_investigator.investigate(context)
        except Exception as exc:
            if not self._llm_warning_emitted:
                logger.warning(
                    "agent_llm_unavailable",
                    extra={"llm_mode": self.llm_mode, "error": str(exc)},
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
                "Recent action: "
                f"{action['action']} [{action['result']}] via {action['trigger']}.",
            )

        if context.incident_type == "auth_config_failure":
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
        elif context.incident_type == "rotation_required":
            summary = (
                f"{context.service_name}: automatic remediation did not recover the "
                "service, so a supervised rotation is the next step."
            )
            action_plan = self._rotation_action_plan(context)
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
        severity: str,
        summary: str,
        human_explanation: str | None,
        recommended_action: str,
        approval_required: bool,
        failure_count: int,
    ) -> AgentIncident | None:
        now = utc_now()
        if self._is_recently_dismissed(incidents, service_name, incident_type, now):
            return None

        existing = self._find_active_incident(incidents, service_name, incident_type)
        if existing is not None:
            updated = existing.model_copy(
                update={
                    "severity": severity,
                    "summary": summary,
                    "human_explanation": human_explanation,
                    "recommended_action": recommended_action,
                    "approval_required": approval_required,
                    "failure_count": failure_count,
                    "updated_at": now,
                }
            )
            self.store.append_incident(updated)
            incidents[:] = [item for item in incidents if item.id != updated.id]
            incidents.insert(0, updated)
            return updated

        incident = AgentIncident(
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
            approval_required=approval_required,
        )
        self.store.append_incident(incident)
        incidents.insert(0, incident)
        return incident

    def _resolve_active_incidents(
        self, service_name: str, incidents: list[AgentIncident]
    ) -> None:
        now = utc_now()
        for incident in list(incidents):
            if incident.service_name != service_name:
                continue
            if incident.status in {"resolved", "dismissed"}:
                continue
            resolved = incident.model_copy(
                update={"status": "resolved", "resolved_at": now, "updated_at": now}
            )
            self.store.append_incident(resolved)
            incidents.remove(incident)
            incidents.insert(0, resolved)

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
        cooldown = timedelta(seconds=self.settings.incident_cooldown_seconds)
        for incident in incidents:
            if incident.service_name != service_name or incident.type != incident_type:
                continue
            if incident.status == "dismissed" and incident.updated_at >= now - cooldown:
                return True
        return False

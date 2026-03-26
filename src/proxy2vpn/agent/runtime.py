"""Rule-first watchdog runtime for proxy2vpn services."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import (
    ActionRecord,
    AgentIncident,
    AgentState,
    AgentStatus,
    DaemonMode,
    IncidentStatus,
    ServiceSnapshot,
)
from proxy2vpn.agent.llm import IncidentContext, OpenAIIncidentEnricher
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
                    criteria=RotationCriteria.RANDOM,
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

    async def _restart_tunnel(self, service: VPNService, state: AgentState) -> str:
        base_url = f"http://localhost:{service.control_port}/v1"
        try:
            async with GluetunControlClient(base_url) as client:
                await client.restart_tunnel()
            self._append_action(
                state,
                service_name=service.name,
                action="restart_tunnel",
                trigger="first_unhealthy_cycle",
                result="success",
                details={"control_port": str(service.control_port)},
            )
            return "success"
        except Exception as exc:
            self._append_action(
                state,
                service_name=service.name,
                action="restart_tunnel",
                trigger="first_unhealthy_cycle",
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

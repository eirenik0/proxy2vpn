"""Project Gluetun-specific runtime evidence onto the shared egress seam."""

from proxy2vpn.adapters.gluetun_runtime import (
    GluetunRuntimeInterface,
    RuntimeInspection,
)
from proxy2vpn.core.egress import (
    EndpointIdentity,
    EgressObservation,
    GLUETUN_CAPABILITIES,
)
from proxy2vpn.core.models import VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer


# @lat: [[lat.md/egress#Gluetun Adapter]]
class GluetunEgressAdapter:
    """Keep container/control evidence here, while preserving existing scoring."""

    capabilities = GLUETUN_CAPABILITIES

    def __init__(
        self, service: VPNService, runtime: GluetunRuntimeInterface, threshold: int = 60
    ):
        self.service = service
        self.runtime = runtime
        self.threshold = threshold
        self.inspection: RuntimeInspection | None = None
        self.identity = EndpointIdentity(service.name, "gluetun")

    async def observe(
        self, *, lines: int = 20, timeout: int | None = None
    ) -> EgressObservation:
        inspection = await self.runtime.inspect(
            self.service, lines=lines, timeout=timeout
        )
        self.inspection = inspection
        if inspection.failure is not None:
            raise RuntimeError(inspection.failure)
        results = inspection.results
        status = inspection.container_status
        if status != "running":
            return EgressObservation(
                0,
                "missing" if status == "missing" else "container_stopped",
                results,
                available=False,
            )
        score = DiagnosticAnalyzer().health_score(results)
        failed = {r.check for r in results if not r.passed}
        health_class = (
            "healthy"
            if score >= self.threshold
            else (
                "auth_config"
                if failed & {"auth_failure", "config_error"}
                else "connectivity"
                if "connectivity" in failed
                else "degraded"
            )
        )
        connectivity = next(
            (r.passed for r in results if r.check == "connectivity"), None
        )
        return EgressObservation(
            score,
            health_class,
            results,
            available=True,
            restart_ready=inspection.control_api_reachable,
            authentication=False if "auth_failure" in failed else None,
            connectivity=connectivity,
            current_egress_ip=inspection.current_egress_ip,
        )

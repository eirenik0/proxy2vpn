"""Source-neutral identity, evidence, and supported egress operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from proxy2vpn.core.services.diagnostics import DiagnosticResult

EgressOperation = Literal[
    "restart_tunnel",
    "restore",
    "replace_endpoint",
    "replace_session",
    "request_different_exit_ip",
]


@dataclass(frozen=True)
class EndpointIdentity:
    """Stable configured identity, independent of an observed exit IP."""

    name: str
    source: Literal["gluetun", "external_proxy"]


class EgressCapabilities(BaseModel):
    """Supported requests; none implies a verified health or exit-IP outcome."""

    restart_tunnel: bool = False
    restore: bool = False
    replace_endpoint: bool = False
    replace_session: bool = False
    request_different_exit_ip: bool = False

    model_config = ConfigDict(frozen=True, extra="forbid")


GLUETUN_CAPABILITIES = EgressCapabilities(
    restart_tunnel=True, restore=True, replace_endpoint=True
)


@dataclass
class EgressObservation:
    """Normalized health with explicitly unknown unavailable evidence."""

    health_score: int
    health_class: str
    results: list[DiagnosticResult] = field(default_factory=list)
    available: bool | None = None
    restart_ready: bool = False
    authentication: bool | None = None
    connectivity: bool | None = None
    latency_ms: float | None = None
    current_egress_ip: str | None = None


# @lat: [[lat.md/egress#Shared Egress Interface]]
class EgressAdapter(Protocol):
    """The common health seam, derived from the two concrete adapters."""

    @property
    def identity(self) -> EndpointIdentity: ...

    @property
    def capabilities(self) -> EgressCapabilities: ...

    async def observe(
        self, *, lines: int = 20, timeout: int | None = None
    ) -> EgressObservation: ...


class UnsupportedEgressOperation(RuntimeError):
    """A request the endpoint cannot perform, never a backend fallback."""


def require_supported_operation(
    capabilities: EgressCapabilities, operation: EgressOperation
) -> None:
    if not getattr(capabilities, operation):
        raise UnsupportedEgressOperation(f"Endpoint does not support {operation}")

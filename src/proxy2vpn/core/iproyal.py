"""Narrow manual dedicated-mobile contract with secret references only."""

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RequestOutcome = Literal["not_dispatched", "acknowledged", "rejected", "unknown"]
ExitChange = Literal["changed", "unchanged", "unknown"]
OperationReason = Literal[
    "reserved",
    "dispatch_intent",
    "acknowledged",
    "auth_rejected",
    "rate_limited",
    "http_rejected",
    "expired",
    "timeout",
    "cancelled",
    "transport_error",
    "malformed_response",
    "redirect",
    "configuration_changed",
    "verification_failed",
    "cooldown",
    "unsupported",
    "credentials_missing",
    "no_baseline",
]


class IPRoyalMobileConfig(BaseModel):
    """One provider, one manual operation; no account or purchase API configuration."""

    provider: Literal["iproyal_mobile"] = "iproyal_mobile"
    access_token_env: str
    rotation_key_env: str
    cooldown_seconds: int = Field(default=360, ge=360, le=86400)
    expires_at: datetime | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("access_token_env", "rotation_key_env")
    @classmethod
    def environment_reference(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("Provider credentials require environment references")
        return value


class ProviderOperation(BaseModel):
    """Private typed ledger facts, independent of monitoring generation."""

    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    service_name: str
    source: Literal["external_proxy"] = "external_proxy"
    provider: Literal["iproyal_mobile"] = "iproyal_mobile"
    resource_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    cooldown_until: datetime
    operation_phase: Literal["reserved", "dispatched", "completed", "unknown"] = (
        "reserved"
    )
    request_outcome: RequestOutcome = "not_dispatched"
    exit_change: ExitChange = "unknown"
    session_replaced: bool | None = None
    authentication: bool | None = None
    connectivity: bool | None = None
    connectivity_restored: bool | None = None
    completed_at: datetime | None = None
    reason_code: OperationReason = "reserved"
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)
    audit_recorded: bool = False
    verification_state: Literal[
        "pending", "completed", "configuration_changed", "failed"
    ] = "pending"

    model_config = ConfigDict(validate_assignment=True, extra="forbid")


class ControlResult(BaseModel):
    """HTTP evidence only, without provider response bodies or request URLs."""

    request_outcome: RequestOutcome
    reason_code: OperationReason
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)


def valid_rotation_key(value: str) -> bool:
    """Confine an opaque key to one path component, including after URL normalization."""
    return (
        bool(value)
        and value not in {".", ".."}
        and len(value) <= 1024
        and all(
            character not in "/\\" and 32 <= ord(character) < 127 for character in value
        )
    )

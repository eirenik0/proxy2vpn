"""Environment-backed settings for HTTP client integrations."""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GluetunControlSettings(BaseSettings):
    """Single source of truth for Gluetun control client env settings."""

    control_auth: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GLUETUN_CONTROL_AUTH"),
    )

    model_config = SettingsConfigDict(
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    def auth_tuple(self) -> tuple[str, str] | None:
        """Return parsed control auth credentials when configured."""

        if not self.control_auth:
            return None
        username, sep, password = self.control_auth.partition(":")
        if not sep:
            raise ValueError("GLUETUN_CONTROL_AUTH must be in 'user:pass' format")
        return username, password

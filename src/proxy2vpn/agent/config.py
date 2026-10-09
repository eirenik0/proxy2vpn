"""Configuration for the local proxy2vpn agent."""

from __future__ import annotations

from typing import Any
import unicodedata

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Single source of truth for watchdog and enrichment settings."""

    external_proxies_file: str = "external-proxies.json"
    state_dirname: str = ".proxy2vpn-agent"
    state_file: str = "state.json"
    incidents_file: str = "incidents.jsonl"
    runtime_lock_file: str = "runtime.lock"
    daemon_pid_file: str = "daemon.pid"
    daemon_log_file: str = "daemon.log"
    interval_seconds: int = 30
    health_threshold: int = 60
    recheck_delay_seconds: int = 15
    rotation_grace_period_seconds: int = 300
    restore_cooldown_seconds: int = 600
    incident_cooldown_seconds: int = 1800
    probe_timeout_seconds: int = 3
    control_api_timeout_seconds: float = 3.0
    control_api_retry_attempts: int = 0
    action_history_limit: int = 100
    fallback_countries_by_provider: dict[str, list[str]] = Field(default_factory=dict)
    llm_mode: str = "disabled"
    openai_model: str = "gpt-5-nano"
    openai_timeout_seconds: float = 10.0
    openai_max_output_tokens: int = 220
    openai_reasoning_effort: str = "minimal"

    @model_validator(mode="after")
    def _safe_storage_names(self) -> "AgentSettings":
        names = [
            self.state_dirname,
            self.state_file,
            self.incidents_file,
            self.runtime_lock_file,
            self.daemon_pid_file,
            self.daemon_log_file,
        ]
        if any(
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or name != name.rstrip(". ")
            for name in names
        ):
            raise ValueError("Agent storage names must be single path components")
        files = names[1:] + [
            "evidence.lock",
            "identity.lock",
            "identity.key",
            "transaction.json",
        ]
        if len(
            {unicodedata.normalize("NFC", name).casefold() for name in files}
        ) != len(files):
            raise ValueError("Agent storage filenames must be distinct")
        return self

    @field_validator("fallback_countries_by_provider", mode="before")
    @classmethod
    def _normalize_fallback_countries(cls, value: Any) -> dict[str, list[str]] | Any:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            return value

        normalized: dict[str, list[str]] = {}
        for provider, countries in value.items():
            if provider is None:
                continue
            key = str(provider).strip().casefold()
            if not key:
                continue

            if isinstance(countries, str):
                country_list = [part.strip() for part in countries.split(",")]
            elif isinstance(countries, list):
                country_list = [str(part).strip() for part in countries]
            else:
                raise TypeError(
                    "fallback_countries_by_provider values must be strings or lists"
                )

            normalized[key] = [country for country in country_list if country]
        return normalized

    model_config = SettingsConfigDict(
        env_prefix="PROXY2VPN_AGENT_",
        extra="ignore",
        frozen=True,
    )

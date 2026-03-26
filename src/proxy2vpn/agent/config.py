"""Configuration for the local proxy2vpn agent."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Single source of truth for watchdog and enrichment settings."""

    state_dirname: str = ".proxy2vpn-agent"
    state_file: str = "state.json"
    incidents_file: str = "incidents.jsonl"
    runtime_lock_file: str = "runtime.lock"
    interval_seconds: int = 30
    health_threshold: int = 60
    recheck_delay_seconds: int = 15
    restore_cooldown_seconds: int = 600
    incident_cooldown_seconds: int = 1800
    action_history_limit: int = 100
    llm_mode: str = "disabled"
    openai_model: str = "gpt-5-nano"
    openai_timeout_seconds: float = 10.0
    openai_max_output_tokens: int = 220
    openai_reasoning_effort: str = "minimal"

    model_config = SettingsConfigDict(
        env_prefix="PROXY2VPN_AGENT_",
        extra="ignore",
        frozen=True,
    )

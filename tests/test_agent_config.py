from proxy2vpn.agent.config import AgentSettings


def test_agent_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("PROXY2VPN_AGENT_INTERVAL_SECONDS", "45")
    monkeypatch.setenv("PROXY2VPN_AGENT_LLM_MODE", "openai")
    monkeypatch.setenv("PROXY2VPN_AGENT_OPENAI_MODEL", "gpt-5-nano")
    monkeypatch.setenv("PROXY2VPN_AGENT_OPENAI_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("PROXY2VPN_AGENT_OPENAI_MAX_OUTPUT_TOKENS", "300")
    monkeypatch.setenv("PROXY2VPN_AGENT_OPENAI_REASONING_EFFORT", "minimal")
    monkeypatch.setenv("PROXY2VPN_AGENT_ROTATION_GRACE_PERIOD_SECONDS", "600")
    monkeypatch.setenv("PROXY2VPN_AGENT_PROBE_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("PROXY2VPN_AGENT_CONTROL_API_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("PROXY2VPN_AGENT_CONTROL_API_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv(
        "PROXY2VPN_AGENT_FALLBACK_COUNTRIES_BY_PROVIDER",
        '{"protonvpn":["Canada","Netherlands"],"NordVPN":"United Kingdom, Germany"}',
    )

    settings = AgentSettings()

    assert settings.interval_seconds == 45
    assert settings.llm_mode == "openai"
    assert settings.openai_model == "gpt-5-nano"
    assert settings.openai_timeout_seconds == 12.5
    assert settings.openai_max_output_tokens == 300
    assert settings.openai_reasoning_effort == "minimal"
    assert settings.rotation_grace_period_seconds == 600
    assert settings.probe_timeout_seconds == 4
    assert settings.control_api_timeout_seconds == 2.5
    assert settings.control_api_retry_attempts == 1
    assert settings.fallback_countries_by_provider == {
        "protonvpn": ["Canada", "Netherlands"],
        "nordvpn": ["United Kingdom", "Germany"],
    }

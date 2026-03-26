from proxy2vpn.agent.config import AgentSettings


def test_agent_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("PROXY2VPN_AGENT_INTERVAL_SECONDS", "45")
    monkeypatch.setenv("PROXY2VPN_AGENT_LLM_MODE", "openai")
    monkeypatch.setenv("PROXY2VPN_AGENT_OPENAI_MODEL", "gpt-5-nano")
    monkeypatch.setenv("PROXY2VPN_AGENT_OPENAI_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("PROXY2VPN_AGENT_OPENAI_MAX_OUTPUT_TOKENS", "300")
    monkeypatch.setenv("PROXY2VPN_AGENT_OPENAI_REASONING_EFFORT", "minimal")

    settings = AgentSettings()

    assert settings.interval_seconds == 45
    assert settings.llm_mode == "openai"
    assert settings.openai_model == "gpt-5-nano"
    assert settings.openai_timeout_seconds == 12.5
    assert settings.openai_max_output_tokens == 300
    assert settings.openai_reasoning_effort == "minimal"

from types import SimpleNamespace

from proxy2vpn.agent.llm import (
    IncidentContext,
    IncidentEnrichment,
    OpenAIIncidentEnricher,
)


def test_openai_incident_enricher_uses_structured_output():
    captured = {}

    class DummyResponses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_parsed=IncidentEnrichment(
                    summary="vpn-a: auth failure",
                    human_explanation="The service credentials appear invalid.",
                )
            )

    class DummyClient:
        def __init__(self):
            self.responses = DummyResponses()

    enricher = OpenAIIncidentEnricher(client=DummyClient(), model="gpt-5-nano")
    result = enricher.enrich(
        IncidentContext(
            service_name="vpn-a",
            fallback_summary="vpn-a: fallback",
            recommended_action="investigate",
            failure_count=2,
            issues=[
                {
                    "check": "auth_failure",
                    "message": "Recent authentication failure detected",
                    "recommendation": "Verify credentials",
                    "persistent": True,
                }
            ],
            recent_actions=[
                {
                    "action": "restore",
                    "result": "failed",
                    "trigger": "automatic_remediation",
                }
            ],
        )
    )

    assert result.summary == "vpn-a: auth failure"
    assert result.human_explanation == "The service credentials appear invalid."
    assert captured["model"] == "gpt-5-nano"
    assert captured["text_format"] is IncidentEnrichment
    assert captured["reasoning"] == {"effort": "minimal"}

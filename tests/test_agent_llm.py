from types import SimpleNamespace

from proxy2vpn.agent.llm import (
    IncidentContext,
    IncidentEnrichment,
    InvestigationContext,
    InvestigationPlan,
    OpenAIIncidentEnricher,
    OpenAIIncidentInvestigator,
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


def test_openai_incident_investigator_uses_structured_output():
    captured = {}

    class DummyResponses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_parsed=InvestigationPlan(
                    summary="vpn-a needs credential validation",
                    findings=[
                        "Recent diagnostics show repeated authentication failure."
                    ],
                    action_plan=[
                        "Check the profile env file credentials.",
                        "Recreate the service after correcting the profile.",
                    ],
                )
            )

    class DummyClient:
        def __init__(self):
            self.responses = DummyResponses()

    investigator = OpenAIIncidentInvestigator(client=DummyClient(), model="gpt-5-nano")
    result = investigator.investigate(
        InvestigationContext(
            incident_id="incident123",
            incident_type="auth_config_failure",
            severity="high",
            status="open",
            service_name="vpn-a",
            incident_summary="vpn-a: config issue",
            recommended_action="investigate",
            failure_count=3,
            provider="protonvpn",
            location="Toronto",
            profile_name="prod",
            profile_env_file="/tmp/prod.env",
            container_status="running",
            health_score=0,
            control_api_reachable=False,
            profile_validation_errors=["OPENVPN_PASSWORD is missing."],
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
            human_explanation="Provider rejected the current credentials.",
        )
    )

    assert result.summary == "vpn-a needs credential validation"
    assert result.action_plan[0] == "Check the profile env file credentials."
    assert captured["model"] == "gpt-5-nano"
    assert captured["text_format"] is InvestigationPlan
    assert captured["reasoning"] == {"effort": "minimal"}

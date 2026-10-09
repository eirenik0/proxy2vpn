"""Secrecy regressions at agent storage and LLM boundaries."""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import pytest

from proxy2vpn.agent.evidence import EvidenceSanitizer
from proxy2vpn.agent.llm import (
    IncidentContext,
    IncidentEnrichment,
    InvestigationContext,
    InvestigationPlan,
    OpenAIIncidentEnricher,
    OpenAIIncidentInvestigator,
)
from proxy2vpn.agent.models import (
    AgentIncident,
    AgentState,
    AgentStatus,
    ActionRecord,
    IncidentInvestigation,
)
from proxy2vpn.agent.runtime import AgentWatchdog
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.agent.runtime import utc_now


SECRET = "Seeded/p@ss+word-138"
ENCODINGS = [
    SECRET,
    quote(SECRET, safe=""),
    quote_plus(SECRET),
    base64.b64encode(SECRET.encode()).decode(),
    base64.urlsafe_b64encode(SECRET.encode()).decode(),
]


def context():
    return IncidentContext(
        service_name="vpn-a",
        fallback_summary="auth failure " + " ".join(ENCODINGS),
        recommended_action="investigate",
        failure_count=2,
        issues=[
            {
                "check": "auth_failure",
                "message": "unlabelled unknown payload",
                "persistent": True,
                "nested": {"password": SECRET},
            }
        ],
        recent_actions=[
            {
                "action": "restore",
                "result": "failed",
                "trigger": "manual",
                "error": SECRET,
            }
        ],
    )


def investigation_context():
    return InvestigationContext(
        incident_id="incident138",
        incident_type="auth_config_failure",
        severity="high",
        status="open",
        service_name="vpn-a",
        incident_summary="auth failure " + SECRET,
        recommended_action="investigate",
        failure_count=2,
        container_status="running",
        profile_validation_errors=["password=" + SECRET],
        issues=context().issues,
        recent_actions=context().recent_actions,
        log_evidence=[
            "AUTH_FAILED opaque-unlabelled-secret " + SECRET,
            "raw unknown text",
        ],
    )


def assert_clean(value):
    text = json.dumps(value, default=str)
    for secret in ENCODINGS:
        assert secret not in text
    assert "opaque-unlabelled-secret" not in text
    assert "unlabelled unknown payload" not in text


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Bounded Copied Diagnostics]]
def test_sanitizer_is_bounded_idempotent_and_preserves_classifications():
    sanitizer = EvidenceSanitizer([SECRET, "RED", "ACT"])
    original = investigation_context().model_dump()
    safe = sanitizer.sanitize(original)
    assert_clean(safe)
    assert safe == sanitizer.sanitize(safe)
    assert safe["issues"][0]["check"] == "auth_failure"
    assert safe["issues"][0]["persistent"] is True
    assert safe["log_evidence"] == ["AUTH_FAILED"]
    assert SECRET in original["incident_summary"]
    assert sanitizer.sanitize({"details": {"untrusted": SECRET}}) == {"details": {}}
    cycle = []
    cycle.append(cycle)
    assert "[TRUNCATED]" in str(sanitizer.sanitize(cycle))
    assert len(sanitizer.text("x" * 5000)) == 2048
    history = {
        "services": [{"health_score": 100}] * 80,
        "actions": [{"result": "success"}] * 80,
    }
    safe_history = sanitizer.sanitize(history)
    assert len(safe_history["services"]) == len(safe_history["actions"]) == 80
    assert sanitizer.sanitize(object()) == "[OMITTED]"
    labelled = "Bearer opaque-value Basic another-value password=third-value"
    safe_text = sanitizer.text(labelled)
    assert safe_text == sanitizer.text(safe_text)
    assert (
        sanitizer.sanitize({"issues": [{"check": {"password": SECRET}}]})["issues"][0][
            "check"
        ]
        == "unknown"
    )
    assert SECRET not in sanitizer.text(
        "-----BEGIN PRIVATE KEY-----\n" + SECRET + "\n-----END PRIVATE KEY-----"
    )


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Both LLM Boundaries]]
@pytest.mark.parametrize("investigate", [False, True])
def test_both_llm_requests_and_results_are_sanitized(investigate):
    captured = {}
    result = (
        InvestigationPlan(summary=SECRET, findings=ENCODINGS, action_plan=["restart"])
        if investigate
        else IncidentEnrichment(summary=SECRET, human_explanation=" ".join(ENCODINGS))
    )

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=result)

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    responder = (OpenAIIncidentInvestigator if investigate else OpenAIIncidentEnricher)(
        client=client, sanitizer=EvidenceSanitizer([SECRET])
    )
    original = investigation_context() if investigate else context()
    safe = (
        responder.investigate(original) if investigate else responder.enrich(original)
    )
    assert_clean(captured["input"])
    assert_clean(safe.model_dump())
    assert SECRET in original.model_dump_json()


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Configured Values And Historical Migration]]
def test_storage_scrubs_all_history_and_state_without_mutating_inputs(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  vpn-a:\n    env_file: env.custom\n")
    (tmp_path / "env.custom").write_text("OPENVPN_PASSWORD=" + SECRET + "\n")
    store = AgentStateStore(compose)
    now = utc_now()
    state = AgentState(
        status=AgentStatus(
            compose_path=str(compose), interval_seconds=60, last_error=SECRET
        ),
        actions=[
            ActionRecord(
                ts=now,
                service_name="vpn-a",
                action="restore",
                trigger="manual",
                result="failed",
                details={"error": SECRET, "profile": SECRET},
            )
        ],
    )
    incident = AgentIncident(
        id="incident138",
        service_name="vpn-a",
        type="auth_config_failure",
        severity="high",
        created_at=now,
        updated_at=now,
        summary=" ".join(ENCODINGS),
        recommended_action="investigate",
        investigation=IncidentInvestigation(
            summary=SECRET,
            findings=ENCODINGS,
            action_plan=ENCODINGS,
            log_evidence=["AUTH_FAILED " + SECRET],
            investigated_at=now,
        ),
    )
    store.ensure_dir()
    store.state_file.write_text(state.model_dump_json())
    store.incidents_file.write_text(
        incident.model_dump_json() + "\n" + incident.model_dump_json() + "\n"
    )
    assert_clean(store.read_state().model_dump())
    assert_clean([record.model_dump() for record in store.load_incidents()])
    assert len(store.incidents_file.read_text().splitlines()) == 2
    assert_clean(store.state_file.read_text())
    assert_clean(store.incidents_file.read_text())
    store.write_state(state)
    store.append_incident(incident)
    assert SECRET in state.model_dump_json() and SECRET in incident.model_dump_json()
    assert SECRET in (tmp_path / "env.custom").read_text()
    assert_clean(store.state_file.read_text())
    assert_clean(store.incidents_file.read_text())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: store.append_incident(incident), range(12)))
    assert len(store.incidents_file.read_text().splitlines()) == 15
    assert not list(store.agent_dir.glob("*.tmp"))
    safe_history = store.incidents_file.read_text()
    store.incidents_file.write_text(safe_history + "{malformed\n")
    with pytest.raises(json.JSONDecodeError):
        store.load_incidents()
    assert store.incidents_file.read_text() == safe_history + "{malformed\n"
    assert not list(store.agent_dir.glob("*.tmp"))


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Disabled And Failing LLM Fallbacks]]
@pytest.mark.parametrize("fails", [False, True])
def test_runtime_disabled_and_failed_paths_are_sanitized(tmp_path, fails):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    (tmp_path / ".env").write_text("OPENVPN_PASSWORD=" + SECRET + "\n")
    watchdog = AgentWatchdog(compose, llm_mode="disabled")
    if fails:

        def fail(_):
            raise RuntimeError(SECRET)

        watchdog._incident_enricher = SimpleNamespace(enrich=fail)
        watchdog._incident_investigator = SimpleNamespace(investigate=fail)
    summary, explanation = watchdog._enrich_summary(context())
    assert_clean([summary, explanation])
    assert_clean(watchdog._investigate_context(investigation_context()).model_dump())


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Configuration Sources And Encodings]]
def test_config_sources_and_pattern_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "x-vpn-base-prod:\n  env_file: env.prod\nservices:\n  vpn-a:\n    environment:\n      OPENVPN_PASSWORD: "
        + SECRET
        + "\n"
    )
    (tmp_path / "external-proxies.json").write_text(
        json.dumps({"url": "http://user138:" + quote(SECRET, safe="") + "@host:8080"})
    )
    (tmp_path / "env.prod").write_text("OPENVPN_PASSWORD=anchor-credential-138\n")
    (tmp_path / "control-client-auth.json").write_text(
        json.dumps({"monitor": {"password": "monitor-secret-138"}})
    )
    (tmp_path / "control-server-auth.toml").write_text(
        'password = "operator-secret-138"\n'
    )
    sanitizer = EvidenceSanitizer.from_compose(compose)
    assert "monitor-secret-138" not in sanitizer.text("monitor-secret-138")
    assert "operator-secret-138" not in sanitizer.text("operator-secret-138")
    assert "anchor-credential-138" not in sanitizer.text("anchor-credential-138")
    assert_clean(sanitizer.sanitize({"summary": " ".join(ENCODINGS)}))
    assert "user138" not in sanitizer.text("user138")
    text = sanitizer.text(
        "Bearer opaque-value password=another-value http://unknown:credential@host"
    )
    assert (
        "opaque-value" not in text
        and "another-value" not in text
        and "credential@" not in text
    )

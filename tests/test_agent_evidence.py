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


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Marker-containing Credentials]]
@pytest.mark.parametrize(
    "secret",
    ["foo[REDACTED]bar", "[REDACTED]bar", "foo[REDACTED]", "[REDACTED][REDACTED]"],
)
def test_marker_credentials_are_redacted_at_all_boundaries(tmp_path, secret):
    encoded = [
        secret,
        quote(secret, safe=""),
        quote_plus(secret),
        base64.b64encode(secret.encode()).decode(),
        base64.urlsafe_b64encode(secret.encode()).decode(),
        json.dumps(secret, ensure_ascii=True)[1:-1],
    ]
    narrative = " ".join(encoded)
    sanitizer = EvidenceSanitizer([secret, "RED", "ACT", "["])
    safe_text = sanitizer.text(narrative)
    for credential in encoded:
        assert credential not in safe_text
    assert safe_text == sanitizer.text(safe_text)
    assert sanitizer.text("[REDACTED]") == "[REDACTED]"

    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    (tmp_path / ".env").write_text("OPENVPN_PASSWORD=" + secret + "\n")
    store = AgentStateStore(compose)
    now = utc_now()
    state = AgentState(
        status=AgentStatus(compose_path=str(compose), interval_seconds=60),
        actions=[
            ActionRecord(
                ts=now,
                service_name="vpn-a",
                action="restore",
                trigger="manual",
                result="failed",
                details={"profile": narrative},
            )
        ],
    )
    incident = AgentIncident(
        id="marker138",
        service_name="vpn-a",
        type="auth_config_failure",
        severity="high",
        created_at=now,
        updated_at=now,
        summary=narrative,
        recommended_action="investigate",
        investigation=IncidentInvestigation(
            summary=narrative,
            findings=[narrative],
            action_plan=[narrative],
            investigated_at=now,
        ),
    )
    # Seed legacy files, then exercise scrubbing and normal writes.
    store.ensure_dir()
    store.state_file.write_text(state.model_dump_json())
    store.incidents_file.write_text(incident.model_dump_json() + "\n")
    store.read_state()
    store.load_incidents()
    store.write_state(state)
    store.append_incident(incident)
    captured = []

    def parse(**kwargs):
        captured.append(kwargs["input"])
        output_model = kwargs["text_format"]
        result = (
            IncidentEnrichment(summary=narrative, human_explanation=narrative)
            if output_model is IncidentEnrichment
            else InvestigationPlan(
                summary=narrative, findings=[narrative], action_plan=[narrative]
            )
        )
        return SimpleNamespace(output_parsed=result)

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    enrichment = OpenAIIncidentEnricher(
        client=client, sanitizer=store.sanitizer()
    ).enrich(
        context().model_copy(
            update={
                "fallback_summary": narrative,
                "issues": [],
                "service_name": "vpn-a",
            }
        )
    )
    plan = OpenAIIncidentInvestigator(
        client=client, sanitizer=store.sanitizer()
    ).investigate(
        investigation_context().model_copy(
            update={
                "incident_summary": narrative,
                "profile_validation_errors": [narrative],
            }
        )
    )
    outputs = (
        json.dumps(captured) + enrichment.model_dump_json() + plan.model_dump_json()
    )
    outputs += store.state_file.read_text() + store.incidents_file.read_text()
    for credential in encoded:
        assert credential not in outputs
    assert incident.summary == narrative


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Replacement Convergence]]
def test_redaction_converges_when_replacements_form_a_configured_secret():
    sanitizer = EvidenceSanitizer(["foo", "[REDACTED]bar"])
    safe = sanitizer.text("foobar")
    assert "[REDACTED]bar" not in safe
    assert safe == sanitizer.text(safe)


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


# @lat: [[agent-evidence-tests#Agent Evidence Tests#External Credential References]]
def test_arbitrary_external_environment_references_are_scrubbed(tmp_path, monkeypatch):
    username, password = "external-login-138", "external-key-138"
    monkeypatch.setenv("LOGIN", username)
    monkeypatch.setenv("KEY", password)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    (tmp_path / "external-proxies.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "id": "office",
                        "connection": {"host": "127.0.0.1", "port": 8080},
                        "credentials": {"username_env": "LOGIN", "password_env": "KEY"},
                    }
                ],
            }
        )
    )
    secrets = [username, password]
    encodings = [
        value
        for secret in secrets
        for value in (
            secret,
            quote(secret, safe=""),
            quote_plus(secret),
            base64.b64encode(secret.encode()).decode(),
            base64.urlsafe_b64encode(secret.encode()).decode(),
        )
    ]
    narrative = " ".join(encodings)
    store = AgentStateStore(compose)
    now = utc_now()
    incident = AgentIncident(
        id="external138",
        service_name="office",
        type="endpoint_unhealthy",
        severity="high",
        source="external_proxy",
        created_at=now,
        updated_at=now,
        summary=narrative,
        recommended_action="investigate",
    )
    state = AgentState(
        status=AgentStatus(compose_path=str(compose), interval_seconds=60),
        actions=[
            ActionRecord(
                ts=now,
                service_name="office",
                action="restore",
                trigger="manual",
                result="failed",
                details={"profile": narrative},
            )
        ],
    )
    store.ensure_dir()
    store.state_file.write_text(state.model_dump_json())
    store.incidents_file.write_text(incident.model_dump_json() + "\n")
    store.read_state()
    store.load_incidents()
    store.write_state(state)
    store.append_incident(incident)
    captured = []

    def parse(**kwargs):
        captured.append(kwargs["input"])
        return SimpleNamespace(
            output_parsed=(
                IncidentEnrichment(summary=narrative, human_explanation=narrative)
                if kwargs["text_format"] is IncidentEnrichment
                else InvestigationPlan(
                    summary=narrative, findings=[narrative], action_plan=[narrative]
                )
            )
        )

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    sanitizer = store.sanitizer()
    assert sanitizer.text("LOGIN KEY") == "LOGIN KEY"
    enrichment = OpenAIIncidentEnricher(client=client, sanitizer=sanitizer).enrich(
        context().model_copy(update={"fallback_summary": narrative, "issues": []})
    )
    plan = OpenAIIncidentInvestigator(client=client, sanitizer=sanitizer).investigate(
        investigation_context().model_copy(update={"incident_summary": narrative})
    )
    output = store.state_file.read_text() + store.incidents_file.read_text()
    output += (
        json.dumps(captured) + enrichment.model_dump_json() + plan.model_dump_json()
    )
    for secret in encodings:
        assert secret not in output
    assert incident.summary == narrative


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Typed Control Facts]]
@pytest.mark.parametrize(
    "secret",
    [
        "open",
        "high",
        "once",
        "external_proxy",
        "running",
        "rotate",
        "success",
        "auth_failure",
        "2026",
    ],
)
def test_credential_collisions_preserve_typed_control_facts(tmp_path, secret):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    (tmp_path / ".env").write_text("OPENVPN_PASSWORD=" + secret + "\n")
    store = AgentStateStore(compose)
    now = utc_now().replace(year=2026)
    incident = AgentIncident(
        id="control138",
        service_name="vpn-a",
        type="endpoint_unhealthy",
        severity="high",
        source="external_proxy",
        status="open",
        created_at=now,
        updated_at=now,
        summary="Unlabelled diagnostic contains " + secret,
        recommended_action="rotate",
    )
    state = AgentState(
        status=AgentStatus(
            compose_path=str(compose),
            daemon_mode="once",
            started_at=now,
            interval_seconds=60,
        ),
        actions=[
            ActionRecord(
                ts=now,
                service_name="vpn-a",
                action="rotate",
                trigger="manual",
                result="success",
            )
        ],
    )
    store.write_state(state)
    store.append_incident(incident)
    loaded_state, loaded_incident = store.read_state(), store.load_incidents()[0]
    assert loaded_state.status.daemon_mode == "once"
    assert loaded_state.status.started_at == now
    assert loaded_state.actions[0].action == "rotate"
    assert loaded_state.actions[0].result == "success"
    assert loaded_incident.status == "open"
    assert loaded_incident.severity == "high"
    assert loaded_incident.source == "external_proxy"
    assert loaded_incident.recommended_action == "rotate"
    assert loaded_incident.created_at == now
    assert secret not in loaded_incident.summary
    sanitizer = store.sanitizer()
    diagnostic = {
        "container_status": "running",
        "failing_checks": ["auth_failure"],
        "source": "external_proxy",
    }
    assert sanitizer.sanitize(diagnostic) == diagnostic
    assert (
        sanitizer.sanitize({"status": "unexpected-" + secret})["status"]
        != "unexpected-" + secret
    )
    plan_context = investigation_context().model_copy(
        update={"status": "open", "severity": "high", "incident_summary": secret}
    )
    safe_context = sanitizer.model(plan_context)
    assert safe_context.status == "open" and safe_context.severity == "high"
    assert secret not in safe_context.incident_summary


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Scalar Compose Credentials]]
@pytest.mark.parametrize("value", [123456, True, False, 0, 12.5])
def test_scalar_compose_credentials_use_runtime_string_representation(tmp_path, value):
    from proxy2vpn.adapters.compose_utils import parse_env_with_issues

    compose = tmp_path / "compose.yml"
    compose.write_text(
        f"services:\n  vpn-a:\n    environment:\n      HTTPPROXY_PASSWORD: {value}\n"
    )
    runtime_value = parse_env_with_issues({"HTTPPROXY_PASSWORD": value})[0][
        "HTTPPROXY_PASSWORD"
    ]
    encodings = [
        runtime_value,
        quote(runtime_value, safe=""),
        quote_plus(runtime_value),
        base64.b64encode(runtime_value.encode()).decode(),
        base64.urlsafe_b64encode(runtime_value.encode()).decode(),
    ]
    narrative = " ".join(encodings)
    store = AgentStateStore(compose)
    now = utc_now()
    incident = AgentIncident(
        id="scalar-value",
        service_name="vpn-a",
        type="auth_config_failure",
        severity="high",
        created_at=now,
        updated_at=now,
        summary=narrative,
        recommended_action="investigate",
    )
    state = AgentState(
        status=AgentStatus(compose_path=str(compose), interval_seconds=60),
        actions=[
            ActionRecord(
                ts=now,
                service_name="vpn-a",
                action="restore",
                trigger="manual",
                result="failed",
                details={"profile": narrative},
            )
        ],
    )
    store.ensure_dir()
    store.state_file.write_text(state.model_dump_json())
    store.incidents_file.write_text(incident.model_dump_json() + "\n")
    assert runtime_value not in store.load_incidents()[0].summary
    assert runtime_value not in store.read_state().actions[0].details["profile"]
    store.write_state(state)
    store.append_incident(incident)
    assert (
        runtime_value
        not in json.loads(store.incidents_file.read_text().splitlines()[-1])["summary"]
    )
    captured = []

    def parse(**kwargs):
        captured.append(kwargs["input"])
        return SimpleNamespace(
            output_parsed=(
                IncidentEnrichment(summary=narrative, human_explanation=narrative)
                if kwargs["text_format"] is IncidentEnrichment
                else InvestigationPlan(
                    summary=narrative, findings=[narrative], action_plan=[narrative]
                )
            )
        )

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    enrichment = OpenAIIncidentEnricher(
        client=client, sanitizer=store.sanitizer()
    ).enrich(context().model_copy(update={"fallback_summary": narrative, "issues": []}))
    plan = OpenAIIncidentInvestigator(
        client=client, sanitizer=store.sanitizer()
    ).investigate(
        investigation_context().model_copy(update={"incident_summary": narrative})
    )
    payload = json.loads(captured[0][1]["content"].split("\n", 1)[1])
    investigator_payload = json.loads(captured[1][1]["content"].split("\n", 1)[1])
    for secret in encodings:
        for text in [
            payload["fallback_summary"],
            investigator_payload["incident_summary"],
            enrichment.summary,
            plan.summary,
        ]:
            assert secret not in text


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Stable Service Identity Aliases]]
def test_aliases_preserve_service_identity_across_cycles(tmp_path, monkeypatch):
    import asyncio
    from proxy2vpn.adapters.external_proxy import ExternalProxyAdapter
    from proxy2vpn.core.egress import EgressObservation
    from proxy2vpn.core.services.diagnostics import DiagnosticResult

    monkeypatch.setenv("LOGIN", "office")
    monkeypatch.setenv("KEY", "distinct-password-138")
    (tmp_path / "external-proxies.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "id": "office",
                        "connection": {"host": "127.0.0.1", "port": 8080},
                        "credentials": {"username_env": "LOGIN", "password_env": "KEY"},
                    }
                ],
            }
        )
    )
    healthy = False

    async def observe(self, **kwargs):
        return EgressObservation(
            100 if healthy else 0,
            "healthy" if healthy else "connectivity",
            results=[
                DiagnosticResult(
                    check="connectivity",
                    passed=healthy,
                    message="endpoint probe",
                    recommendation="",
                )
            ],
            connectivity=healthy,
        )

    monkeypatch.setattr(ExternalProxyAdapter, "observe", observe)
    watchdog = AgentWatchdog(tmp_path / "compose.yml", llm_mode="disabled")
    first = asyncio.run(watchdog.run_once())
    incident_id = watchdog.store.load_incidents()[0].id
    second = asyncio.run(watchdog.run_once())
    assert first.services[0].consecutive_failures == 1
    assert second.services[0].consecutive_failures == 2
    assert len(watchdog.store.load_incidents()) == 1
    assert watchdog.store.load_incidents()[0].id == incident_id
    assert watchdog.store.load_incidents()[0].service_name == "office"
    assert "office" not in watchdog.store.state_file.read_text()
    assert "office" not in watchdog.store.incidents_file.read_text()
    safe = watchdog.store.sanitizer().sanitize(
        {
            "service_name": "office",
            "recent_actions": [
                {"requested_service_name": "office", "final_service_name": "office"}
            ],
        }
    )
    assert safe == watchdog.store.sanitizer().sanitize(safe)
    restored = watchdog.store.sanitizer().restore_identities(safe)
    assert (
        restored["service_name"]
        == restored["recent_actions"][0]["final_service_name"]
        == "office"
    )
    monkeypatch.setenv("LOGIN", "changed-username-138")
    assert watchdog.store.read_state().services[0].service_name == "office"
    monkeypatch.setenv("LOGIN", "office")
    healthy = True
    third = asyncio.run(watchdog.run_once())
    assert third.services[0].consecutive_failures == 0
    assert len(watchdog.store.load_incidents()) == 1
    assert watchdog.store.load_incidents()[0].status == "resolved"
    assert "office" not in watchdog.store.state_file.read_text()
    assert "office" not in watchdog.store.incidents_file.read_text()

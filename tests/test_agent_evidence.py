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
    sanitizer = EvidenceSanitizer([SECRET, "RED", "ACT", "pending"])
    original = investigation_context().model_dump()
    safe = sanitizer.sanitize(original)
    assert_clean(safe)
    assert safe == sanitizer.sanitize(safe)
    assert sanitizer.sanitize({"details": {"observation": "pending"}}) == {
        "details": {"observation": "pending"}
    }
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
    from proxy2vpn.core.private_storage import StorageError

    with pytest.raises(StorageError, match="Corrupt agent incident history"):
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


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Compose Credential Interpolation]]
@pytest.mark.parametrize(
    "expression,source,expected",
    [
        ("${LOGIN_KEY}", "shell", "shell-value-138"),
        ("$LOGIN_KEY", "dotenv", "dotenv-value-138"),
        ("prefix-${LOGIN_KEY}-suffix", "shell", "prefix-shell-value-138-suffix"),
        ("${LOGIN_KEY:-fallback-value-138}", "missing", "fallback-value-138"),
        ("${LOGIN_KEY-fallback-value-138}", "missing", "fallback-value-138"),
        ("${LOGIN_KEY:+alternative-value-138}", "shell", "alternative-value-138"),
        ("${LOGIN_KEY+alternative-value-138}", "shell", "alternative-value-138"),
        ("${LOGIN_KEY:?required}", "shell", "shell-value-138"),
        ("${LOGIN_KEY?required}", "shell", "shell-value-138"),
        ("${LOGIN_KEY:-${OTHER_KEY:-nested-value-138}}", "missing", "nested-value-138"),
        ("$$LOGIN_KEY", "shell", "$LOGIN_KEY"),
        ("${LOGIN_KEY}", "single_literal", "literal-$OTHER_KEY"),
        ("${LOGIN_KEY}", "shell_literal", "literal-${UNKNOWN?not-an-expression}"),
    ],
)
def test_interpolated_credentials_cover_storage_and_llm_boundaries(
    tmp_path, monkeypatch, expression, source, expected
):
    monkeypatch.delenv("LOGIN_KEY", raising=False)
    monkeypatch.delenv("OTHER_KEY", raising=False)
    if source.startswith("shell"):
        monkeypatch.setenv(
            "LOGIN_KEY", "shell-value-138" if source == "shell" else expected
        )
        (tmp_path / ".env").write_text("LOGIN_KEY=dotenv-value-138\n")
    elif source == "dotenv":
        (tmp_path / ".env").write_text('LOGIN_KEY="dotenv-value-138" # comment\n')
    elif source == "single_literal":
        (tmp_path / ".env").write_text("LOGIN_KEY='literal-$OTHER_KEY'\n")
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  vpn-a:\n    environment:\n      HTTPPROXY_PASSWORD: "
        + json.dumps(expression)
        + "\n"
    )
    store = AgentStateStore(compose)
    encodings = [
        expected,
        quote(expected, safe=""),
        quote_plus(expected),
        base64.b64encode(expected.encode()).decode(),
        base64.urlsafe_b64encode(expected.encode()).decode(),
    ]
    narrative = " ".join(encodings)
    now = utc_now()
    incident = AgentIncident(
        id="interpolation138",
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
    store.incidents_file.write_text(incident.model_dump_json() + "\n")
    store.state_file.write_text(state.model_dump_json())
    store.load_incidents()
    store.read_state()
    store.append_incident(incident)
    store.write_state(state)
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
    outputs = (
        store.state_file.read_text()
        + store.incidents_file.read_text()
        + json.dumps(captured)
        + enrichment.model_dump_json()
        + plan.model_dump_json()
    )
    for secret in encodings:
        assert secret not in outputs
    assert expected not in store.sanitizer().text(expected)
    assert expression in compose.read_text()


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Interpolation Source Semantics]]
def test_interpolation_sources_and_errors_fail_closed(tmp_path, monkeypatch):
    from proxy2vpn.agent.interpolation import dotenv_variables, interpolate

    path = tmp_path / ".env"
    path.write_text(
        "FIRST: \"dotenv-value-138\"\nSECOND=${FIRST}\nLITERAL='${FIRST}'\nMULTILINE='first\nsecond'\nESCAPED=\"first\\nsecond\"\n"
    )
    values = dotenv_variables(path, {"FIRST": "shell-value-138"})
    assert values["SECOND"] == "shell-value-138"
    assert values["LITERAL"] == "${FIRST}"
    assert values["MULTILINE"] == values["ESCAPED"] == "first\nsecond"
    assert interpolate("${KEY-default}", {"KEY": ""}) == ""
    assert interpolate("${KEY:-default}", {"KEY": ""}) == "default"
    assert interpolate("${KEY+present}", {"KEY": ""}) == "present"
    assert interpolate("${KEY:+present}", {"KEY": ""}) == ""
    assert interpolate("${KEY?required}", {"KEY": ""}) == ""
    for expression in (
        "${KEY:?must not expose this}",
        "${KEY?must not expose this}",
        "${KEY",
        "${KEY/unsupported}",
    ):
        with pytest.raises(ValueError) as error:
            interpolate(expression, {})
        assert "must not expose" not in str(error.value)
    path.unlink()
    monkeypatch.delenv("FIRST", raising=False)
    monkeypatch.setenv("FILE_KEY", "env.custom")
    (tmp_path / "env.custom").write_text(
        "HTTPPROXY_PASSWORD=${FIRST:-file-value-138}\n"
    )
    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  vpn-a:\n    env_file: ${FILE_KEY}\n")
    assert "file-value-138" not in EvidenceSanitizer.from_compose(compose).text(
        "file-value-138"
    )
    compose.write_text(
        "services:\n  vpn-a:\n    environment:\n      HTTPPROXY_PASSWORD: ${MISSING_REQUIRED:?sensitive error}\n"
    )
    monkeypatch.delenv("MISSING_REQUIRED", raising=False)
    with pytest.raises(ValueError, match="required credential interpolation"):
        EvidenceSanitizer.from_compose(compose)


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Literal And Raw Env Files]]
@pytest.mark.parametrize(
    "raw,delimiter,value,expected",
    [
        (False, ":", "'${OTHER}'", "${OTHER}"),
        (False, "=", "'${OTHER}'", "${OTHER}"),
        (False, ":", '"${OTHER:-default-value-138}"', "default-value-138"),
        (True, "=", "${MISSING:?literal}", "${MISSING:?literal}"),
        (True, "=", '"${MISSING:?literal}"', '"${MISSING:?literal}"'),
        (True, "=", "'$$literal # text'", "'$$literal # text'"),
    ],
)
def test_literal_and_raw_env_credentials_remain_available_and_redacted(
    tmp_path, monkeypatch, raw, delimiter, value, expected
):
    from proxy2vpn.agent.interpolation import dotenv_variables

    monkeypatch.delenv("OTHER", raising=False)
    monkeypatch.delenv("MISSING", raising=False)
    env_file = tmp_path / "credentials.env"
    env_file.write_text("OPENVPN_PASSWORD" + delimiter + " " + value + "\n")
    # Raw format includes whitespace after the delimiter; use canonical key=value.
    if raw:
        env_file.write_text("OPENVPN_PASSWORD=" + value + "\n")
    assert dotenv_variables(env_file, {}, raw=raw)["OPENVPN_PASSWORD"] == expected
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  vpn-a:\n    env_file:\n      - path: credentials.env\n"
        + ("        format: raw\n" if raw else "")
    )
    store = AgentStateStore(compose)
    now = utc_now()
    encodings = [
        expected,
        quote(expected, safe=""),
        quote_plus(expected),
        base64.b64encode(expected.encode()).decode(),
        base64.urlsafe_b64encode(expected.encode()).decode(),
    ]
    narrative = " ".join(encodings)
    incident = AgentIncident(
        id="literal138",
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
    store.write_state(state)
    store.append_incident(incident)
    assert store.read_state() is not None and len(store.load_incidents()) == 1
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
    enriched = OpenAIIncidentEnricher(
        client=client, sanitizer=store.sanitizer()
    ).enrich(context().model_copy(update={"fallback_summary": narrative, "issues": []}))
    investigated = OpenAIIncidentInvestigator(
        client=client, sanitizer=store.sanitizer()
    ).investigate(
        investigation_context().model_copy(update={"incident_summary": narrative})
    )
    output = (
        store.state_file.read_text()
        + store.incidents_file.read_text()
        + json.dumps(captured)
        + enriched.model_dump_json()
        + investigated.model_dump_json()
    )
    for secret in encodings:
        assert secret not in output


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Private Identity Keys]]
def test_identity_keys_are_private_stable_and_migrate_legacy_aliases(tmp_path):
    import hashlib
    import subprocess
    import sys

    root = tmp_path / "first"
    root.mkdir()
    compose = root / "compose.yml"
    compose.write_text(
        "services:\n  office:\n    environment:\n      OPENVPN_PASSWORD: office\n"
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        sanitizers = list(
            pool.map(lambda _: EvidenceSanitizer.from_compose(compose), range(8))
        )
    payload = {"service_name": "office"}
    alias = sanitizers[0].sanitize(payload)["service_name"]
    legacy = "[SERVICE:" + hashlib.sha256(b"office").hexdigest() + "]"
    assert alias != legacy
    assert alias.startswith("[SERVICE:v2:")
    assert all(s.sanitize(payload)["service_name"] == alias for s in sanitizers)
    assert all(
        s.restore_identities({"service_name": alias}) == payload for s in sanitizers
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from proxy2vpn.agent.evidence import EvidenceSanitizer; "
            "import sys; print(EvidenceSanitizer.from_compose(Path(sys.argv[1]))."
            "sanitize({'service_name': 'office'})['service_name'])",
            str(compose),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == alias
    other = tmp_path / "second"
    other.mkdir()
    other_compose = other / "compose.yml"
    other_compose.write_text(compose.read_text())
    assert (
        EvidenceSanitizer.from_compose(other_compose).sanitize(payload)["service_name"]
        != alias
    )
    key_file = root / ".proxy2vpn-agent" / "identity.key"
    assert key_file.stat().st_mode & 0o777 == 0o600
    assert len(key_file.read_bytes()) == 32
    sanitizer = sanitizers[0]
    assert sanitizer.sanitize({"service_name": legacy}) == {"service_name": alias}
    unknown = "[SERVICE:" + hashlib.sha256(b"removed").hexdigest() + "]"
    assert sanitizer.sanitize({"service_name": unknown}) == {
        "service_name": "[REDACTED]"
    }
    assert key_file.read_bytes().hex() not in json.dumps(sanitizer.sanitize(payload))
    assert base64.b64encode(key_file.read_bytes()).decode() not in json.dumps(
        sanitizer.sanitize(payload)
    )
    key_file.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="Invalid evidence identity key"):
        EvidenceSanitizer.from_compose(compose)


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Interpolated Env Formats And Live Investigation Identities]]
def test_interpolated_raw_format_and_investigation_live_identity(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.delenv("ENV_FORMAT", raising=False)
    (tmp_path / "credentials.env").write_text("OPENVPN_PASSWORD=${MISSING:?literal}\n")
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  office:\n    environment:\n      HTTPPROXY_PASSWORD: office\n"
        "    env_file:\n      - path: credentials.env\n        format: ${ENV_FORMAT:-raw}\n"
    )
    watchdog = AgentWatchdog(compose, llm_mode="disabled")
    sanitizer = watchdog.store.sanitizer()
    assert sanitizer.text("${MISSING:?literal}") == "[REDACTED]"
    now = utc_now()
    incident = AgentIncident(
        id="live138",
        service_name="office",
        type="auth_config_failure",
        severity="high",
        created_at=now,
        updated_at=now,
        summary="credential office",
        recommended_action="investigate",
    )
    watchdog.store.append_incident(incident)

    async def context(_):
        return investigation_context().model_copy(update={"service_name": "office"})

    monkeypatch.setattr(watchdog, "_build_investigation_context", context)
    monkeypatch.setattr(
        watchdog,
        "_investigate_context",
        lambda _: InvestigationPlan(
            summary="office", findings=["office"], action_plan=["office"]
        ),
    )
    result = asyncio.run(watchdog.investigate_incident(incident.id))
    assert result.service_name == "office"
    assert "office" not in result.investigation.model_dump_json()
    assert "office" not in watchdog.store.incidents_file.read_text()
    assert watchdog.store.load_incidents()[0].service_name == "office"


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Env Declaration Boundaries]]
def test_env_file_discovery_ignores_nested_data_keys(tmp_path):
    (tmp_path / "unrelated.data").write_text("this is not valid dotenv content\n")
    (tmp_path / "env.service").write_text("OPENVPN_PASSWORD=service-secret-138\n")
    (tmp_path / "env.profile").write_text("OPENVPN_PASSWORD=profile-secret-138\n")
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "x-arbitrary:\n  env_file: unrelated.data\n"
        "x-vpn-base-prod:\n  env_file: env.profile\n"
        "  environment:\n    env_file: unrelated.data\n"
        "services:\n  vpn-a:\n    env_file: env.service\n"
        "    environment:\n      env_file: unrelated.data\n"
        "    labels:\n      env_file: unrelated.data\n"
    )
    sanitizer = EvidenceSanitizer.from_compose(compose)
    assert (
        sanitizer.text("service-secret-138 profile-secret-138")
        == "[REDACTED] [REDACTED]"
    )
    store = AgentStateStore(compose)
    store.write_state(
        AgentState(status=AgentStatus(compose_path=str(compose), interval_seconds=60))
    )
    assert store.read_state() is not None


# @lat: [[agent-evidence-tests#Agent Evidence Tests#Exact Marker Credentials And Renamed Identities]]
@pytest.mark.parametrize("secret", ["[REDACTED]", "[TRUNCATED]"])
def test_exact_marker_encodings_and_renamed_incidents(tmp_path, secret):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  office:\n    environment:\n      OPENVPN_PASSWORD: office\n"
    )
    (tmp_path / ".env").write_text("HTTPPROXY_PASSWORD=" + secret + "\n")
    store = AgentStateStore(compose)
    sanitizer = store.sanitizer()
    variants = {
        quote(secret, safe=""),
        quote_plus(secret),
        base64.b64encode(secret.encode()).decode(),
        base64.urlsafe_b64encode(secret.encode()).decode(),
    }
    narrative = " ".join(variants)
    safe = sanitizer.text(narrative)
    assert safe == sanitizer.text(safe)
    assert sanitizer.text("[REDACTED]") == "[REDACTED]"
    assert all(value not in safe for value in variants)
    now = utc_now()
    for identifier, status in [
        ("active138", "open"),
        ("closed138", "resolved"),
        ("exclude138", "open"),
    ]:
        store.append_incident(
            AgentIncident(
                id=identifier,
                service_name="office",
                type="auth_config_failure",
                severity="high",
                status=status,
                created_at=now,
                updated_at=now,
                summary=narrative,
                recommended_action="investigate",
            )
        )
    # Compose is rewritten before rotation migrates other incidents.
    compose.write_text(
        "services:\n  new-office:\n    environment:\n      OPENVPN_PASSWORD: office\n"
    )
    watchdog = AgentWatchdog(compose, llm_mode="disabled")
    watchdog._migrate_active_incidents("office", "new-office", {"exclude138"})
    records = {record.id: record for record in store.load_incidents()}
    assert records["active138"].service_name == "new-office"
    assert records["closed138"].service_name == records["exclude138"].service_name
    assert records["closed138"].service_name.startswith("[SERVICE:v2:")
    assert all(value not in store.incidents_file.read_text() for value in variants)
    captured = []

    def parse(**kwargs):
        captured.append(kwargs["input"])
        return SimpleNamespace(
            output_parsed=kwargs["text_format"](
                summary=narrative,
                human_explanation=narrative,
                findings=[narrative],
                action_plan=[narrative],
            )
        )

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    OpenAIIncidentEnricher(client=client, sanitizer=sanitizer).enrich(
        context().model_copy(update={"fallback_summary": narrative})
    )
    OpenAIIncidentInvestigator(client=client, sanitizer=sanitizer).investigate(
        investigation_context().model_copy(update={"incident_summary": narrative})
    )
    assert all(value not in json.dumps(captured) for value in variants)

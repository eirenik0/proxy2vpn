"""Recovery decisions tested with plain evidence and an explicit clock."""

import asyncio
import builtins
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
import time

import pytest

from proxy2vpn.agent.models import ActionRecord, AgentIncident
from proxy2vpn.agent.recovery_policy import (
    RecoveryContext,
    RecoveryPolicy,
    RecoveryProgress,
    RecoverySettings,
    ServiceIdentity,
)
from proxy2vpn.core.services.diagnostics import DiagnosticResult
from proxy2vpn.core.services.health_assessment import HealthAssessment, PeerEvidence

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
POLICY = RecoveryPolicy(RecoverySettings(60, 15, 300, 600, 1800))
SERVICE = ServiceIdentity("vpn-new-york", "test", "protonvpn", "United States")


def check(name, *, persistent=False, passed=False):
    return DiagnosticResult(
        check=name,
        passed=passed,
        persistent=persistent,
        message=name,
        recommendation="",
    )


def assessment(
    name=SERVICE.name,
    *,
    results=None,
    score=0,
    status="running",
    control=False,
    health_class="connectivity",
    peers=None,
):
    return HealthAssessment(
        service_name=name,
        profile_name="test",
        assessed_at=NOW,
        container_status=status,
        health_score=score,
        health_class=health_class,
        results=results if results is not None else [check("connectivity")],
        control_api_reachable=control,
        peer_evidence=peers or PeerEvidence(),
    )


def context(*, observation=None, actions=(), incidents=(), progress=None):
    observation = observation or assessment()
    snapshot = POLICY.snapshot(SERVICE.name, observation, None, NOW)
    snapshot.consecutive_failures = 2
    snapshot.degraded_since = NOW - timedelta(minutes=10)
    return RecoveryContext(
        service=SERVICE,
        assessment=observation,
        snapshot=snapshot,
        results=observation.results,
        actions=actions,
        incidents=incidents,
        services={SERVICE.name: SERVICE},
        assessments={SERVICE.name: observation},
        progress=progress or RecoveryProgress(),
    )


def action(
    name="restore",
    *,
    minutes=1,
    result="success",
    trigger="automatic_remediation",
    service_name=SERVICE.name,
    details=None,
):
    return ActionRecord(
        ts=NOW - timedelta(minutes=minutes),
        service_name=service_name,
        action=name,
        trigger=trigger,
        result=result,
        details=details or {},
    )


def incident(
    kind="auth_config_failure",
    *,
    status="open",
    minutes=1,
    name=SERVICE.name,
    recommended_action="investigate",
):
    return AgentIncident(
        id=f"{name}-{kind}",
        service_name=name,
        type=kind,
        severity="high",
        status=status,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=minutes),
        summary="Needs recovery",
        recommended_action=recommended_action,
    )


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Pure Evaluation]]
def test_policy_has_no_io_sleep_clock_or_input_mutation(monkeypatch):
    ctx = context(actions=[action()], progress=RecoveryProgress("restored"))
    original = deepcopy(ctx)

    def forbidden(*args, **kwargs):
        raise AssertionError("Policy attempted an external effect")

    for obj, name in [
        (builtins, "open"),
        (Path, "open"),
        (Path, "read_text"),
        (os, "getenv"),
        (socket, "socket"),
        (time, "sleep"),
        (asyncio, "sleep"),
    ]:
        monkeypatch.setattr(obj, name, forbidden)
    import proxy2vpn.agent.recovery_policy as policy_module

    class NoClock(datetime):
        now = forbidden
        utcnow = forbidden

    monkeypatch.setattr(policy_module, "datetime", NoClock)
    first = POLICY.decide(ctx, NOW)
    assert first == POLICY.decide(ctx, NOW)
    assert first.action == "rotate"
    assert ctx == original


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Initial And Transient Recovery]]
@pytest.mark.parametrize(
    "status,control,score,expected",
    [
        ("missing", False, 0, "restore"),
        ("exited", False, 0, "restore"),
        ("running", True, 0, "restart_tunnel"),
        ("running", False, 0, "restore"),
        ("running", True, 100, "resolve"),
    ],
)
def test_initial_decisions_and_transient_recovery(status, control, score, expected):
    observation = assessment(status=status, control=control, score=score)
    ctx = context(observation=observation)
    ctx.snapshot.consecutive_failures = 1
    decision = POLICY.decide(ctx, NOW)
    assert decision.action == expected
    if expected == "restart_tunnel":
        assert decision.trigger == "first_unhealthy_cycle"
        assert decision.observation.delay_seconds == 15
        recovered = replace(
            ctx,
            snapshot=ctx.snapshot.model_copy(update={"health_score": 100}),
            progress=decision.next_progress,
        )
        assert POLICY.decide(recovered, NOW + timedelta(seconds=15)).action == "resolve"
    if expected == "restore":
        assert decision.observation.kind == "health"
        assert decision.next_progress.phase == "restored"


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Authentication And Configuration]]
@pytest.mark.parametrize(
    "failure,healthy_peer,active_incident,control,expected",
    [
        ("auth_failure", True, False, True, "restart_tunnel"),
        ("auth_failure", False, False, True, "incident"),
        ("auth_failure", True, True, True, "incident"),
        ("auth_failure", True, False, False, "incident"),
        ("config_error", True, False, True, "incident"),
    ],
)
def test_isolated_auth_restart_and_shared_or_config_failure(
    failure, healthy_peer, active_incident, control, expected
):
    observation = assessment(
        results=[check(failure, persistent=True)],
        control=control,
        health_class="auth_config",
        peers=PeerEvidence(
            healthy=["peer"] if healthy_peer else [],
            auth_config=[] if healthy_peer else ["peer"],
        ),
    )
    ctx = context(
        observation=observation, incidents=[incident()] if active_incident else []
    )
    decision = POLICY.decide(ctx, NOW)
    assert decision.action == expected
    if expected == "restart_tunnel":
        assert decision.trigger == "isolated_auth_failure"
        failed_recheck = replace(ctx, progress=decision.next_progress)
        assert POLICY.decide(failed_recheck, NOW).incident_type == "auth_config_failure"
    else:
        assert decision.incident_type == "auth_config_failure"


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Route And TLS Escalation]]
def test_route_restores_once_then_rotates_next_cycle_and_tls_skips_restore():
    route = [check("route_error", persistent=True), check("connectivity")]
    ctx = context(observation=assessment(results=route))
    first = POLICY.decide(ctx, NOW)
    assert first.action == "restore"
    post_restore = replace(ctx, progress=first.next_progress, actions=[action()])
    assert POLICY.decide(post_restore, NOW).action == "wait"
    next_cycle = replace(ctx, actions=[action()])
    assert POLICY.decide(next_cycle, NOW).action == "rotate"
    ctx.snapshot.degraded_since = NOW
    tls = replace(
        ctx, results=[check("tls_error")], progress=RecoveryProgress("restarted")
    )
    assert POLICY.decide(tls, NOW).action == "rotate"


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Time Windows And Budgets]]
@pytest.mark.parametrize(
    "offset,can_restore,grace_elapsed",
    [(-1, False, False), (0, False, True), (1, True, True)],
)
def test_cooldown_and_grace_boundaries_use_supplied_time(
    offset, can_restore, grace_elapsed
):
    restore = action(minutes=10)
    assert (
        POLICY.can_restore(SERVICE.name, [restore], NOW + timedelta(seconds=offset))
        is can_restore
    )
    ctx = context()
    ctx.snapshot.degraded_since = NOW - timedelta(seconds=300)
    assert (
        POLICY.grace_elapsed(ctx.snapshot, NOW + timedelta(seconds=offset))
        is grace_elapsed
    )


@pytest.mark.parametrize(
    "rotations,exhausted",
    [
        ([action("rotate", minutes=30)], True),
        ([action("rotate", minutes=31)], False),
        ([action("rotate", minutes=31), action("rotate", minutes=360)], True),
        ([action("rotate", minutes=31), action("rotate", minutes=361)], False),
        ([action("rotate", result="failed")], False),
        ([action("rotate", trigger="manual_approval")], False),
        (
            [
                action(
                    "rotate",
                    service_name="new-name",
                    details={
                        "requested_service_name": SERVICE.name,
                        "final_service_name": "new-name",
                    },
                )
            ],
            True,
        ),
    ],
)
def test_rotation_budget_preserves_success_windows_and_renamed_identity(
    rotations, exhausted
):
    ctx = context(actions=rotations, progress=RecoveryProgress("restored"))
    decision = POLICY.decide(ctx, NOW)
    assert decision.action == ("incident" if exhausted else "rotate")
    if exhausted:
        assert decision.incident_type == "rotation_exhausted"
        assert decision.reason == "Rotation budget exhausted."


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Scoped Circuit Breakers]]
@pytest.mark.parametrize(
    "scope,expected",
    [
        ("profile", "profile_auth_config_failure"),
        ("provider", "provider_outage_suspected"),
        ("other_provider", None),
        ("other_country", None),
    ],
)
def test_scope_evidence_blocks_only_matching_profile_or_provider_country(
    scope, expected
):
    ctx = context(progress=RecoveryProgress("restored"))
    identities, observations = dict(ctx.services), dict(ctx.assessments)
    for name in ["peer-a", "peer-b"]:
        identities[name] = ServiceIdentity(
            name,
            "test" if scope == "profile" else name,
            "other" if scope == "other_provider" else SERVICE.provider,
            "Canada" if scope == "other_country" else SERVICE.country,
        )
        observations[name] = assessment(
            name, health_class="auth_config" if scope == "profile" else "connectivity"
        )
    decision = POLICY.decide(
        replace(ctx, services=identities, assessments=observations), NOW
    )
    assert decision.incident_type == expected
    assert decision.action == ("incident" if expected else "rotate")


@pytest.mark.parametrize(
    "kind,minutes,blocked",
    [
        ("profile_auth_config_failure", 60, True),
        ("profile_auth_config_failure", 61, False),
        ("provider_outage_suspected", 30, True),
        ("provider_outage_suspected", 31, False),
    ],
)
def test_scope_incident_windows(kind, minutes, blocked):
    ctx = context(
        incidents=[incident(kind, minutes=minutes)],
        progress=RecoveryProgress("restored"),
    )
    decision = POLICY.decide(ctx, NOW)
    assert decision.action == ("incident" if blocked else "rotate")
    assert decision.incident_type == (kind if blocked else None)


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Dismissal And Approval]]
def test_dismissal_window_and_explicit_manual_approval():
    ctx = context(
        observation=assessment(results=[check("auth_failure", persistent=True)]),
        incidents=[incident(status="dismissed", minutes=30)],
    )
    assert POLICY.decide(ctx, NOW).suppressed
    assert not POLICY.decide(ctx, NOW + timedelta(seconds=1)).suppressed
    rotation = incident("rotation_exhausted", recommended_action="rotate")
    rotation.approval_required = True
    assert POLICY.manual_rotation(rotation, approved=False).action == "wait"
    approved = POLICY.manual_rotation(rotation, approved=True)
    assert approved.action == "rotate" and approved.trigger == "manual_approval"
    for status in ["resolved", "dismissed", "failed"]:
        with pytest.raises(RuntimeError, match="already closed"):
            POLICY.manual_rotation(
                rotation.model_copy(update={"status": status}), approved=True
            )
    with pytest.raises(RuntimeError, match="not a rotation incident"):
        POLICY.manual_rotation(incident(), approved=True)


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Connectivity Versus Exit IP]]
def test_rotation_is_not_evidence_of_a_different_exit_ip():
    rotation = incident("rotation_required", recommended_action="rotate")
    repair = POLICY.manual_rotation(rotation, approved=True)
    different_ip = POLICY.manual_rotation(
        rotation, approved=True, purpose="request_different_exit_ip"
    )
    assert repair.observation.satisfied(
        healthy=True, previous_ip="203.0.113.1", current_ip="203.0.113.1"
    )
    assert not different_ip.observation.satisfied(
        healthy=True, previous_ip="203.0.113.1", current_ip="203.0.113.1"
    )
    assert not different_ip.observation.satisfied(
        healthy=True, current_ip="203.0.113.2"
    )
    assert not different_ip.observation.satisfied(
        healthy=False, previous_ip="203.0.113.1", current_ip="203.0.113.2"
    )
    assert different_ip.observation.satisfied(
        healthy=True, previous_ip="203.0.113.1", current_ip="203.0.113.2"
    )


def test_route_failure_first_seen_after_restore_keeps_original_grace():
    ctx = context()
    ctx.snapshot.degraded_since = NOW
    restored = replace(
        ctx,
        actions=[action()],
        progress=RecoveryProgress("restored"),
        results=[check("route_error", persistent=True), check("connectivity")],
    )
    assert POLICY.decide(restored, NOW).action == "wait"
    assert POLICY.decide(restored, NOW + timedelta(seconds=300)).action == "rotate"


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Current Followup Diagnostics]]
@pytest.mark.parametrize("phase", ["restarted", "restored"])
@pytest.mark.parametrize("failure", ["auth_failure", "config_error"])
def test_new_persistent_auth_config_failure_immediately_requests_investigation(
    phase, failure
):
    ctx = context()
    followup = replace(
        ctx, progress=RecoveryProgress(phase), results=[check(failure, persistent=True)]
    )
    decision = POLICY.decide(followup, NOW)
    assert decision.action == "incident"
    assert decision.incident_type == "auth_config_failure"


def test_cleared_auth_does_not_override_current_connectivity_evidence():
    initial = assessment(
        results=[check("auth_failure", persistent=True)],
        control=True,
        peers=PeerEvidence(healthy=["peer"]),
    )
    ctx = context(observation=initial)
    restart = POLICY.decide(ctx, NOW)
    assert restart.trigger == "isolated_auth_failure"
    current = replace(
        ctx, progress=restart.next_progress, results=[check("connectivity")]
    )
    restore = POLICY.decide(current, NOW)
    assert restore.action == "restore"
    current = replace(current, progress=restore.next_progress)
    assert POLICY.decide(current, NOW).action == "rotate"


# @lat: [[lat.md/recovery-policy-tests#Recovery Policy Tests#Interrupted Auth Attempt Memory]]
@pytest.mark.parametrize(
    "details",
    [
        {"cancelled": "true"},
        {"observation": "pending"},
        {"observation": "interrupted"},
        {"observation": "failed"},
    ],
)
@pytest.mark.parametrize("dismissed", [False, True])
def test_interrupted_auth_attempt_is_bounded_even_when_incident_is_dismissed(
    details, dismissed
):
    observation = assessment(
        results=[check("auth_failure", persistent=True)],
        control=True,
        health_class="auth_config",
        peers=PeerEvidence(healthy=["peer"]),
    )
    previous_attempt = action(
        "restart_tunnel",
        trigger="isolated_auth_failure",
        result="success" if details.get("observation") == "pending" else "failed",
        details=details,
    )
    ctx = context(
        observation=observation,
        actions=[previous_attempt],
        incidents=[incident(status="dismissed")] if dismissed else [],
    )
    decision = POLICY.decide(ctx, NOW)
    assert (
        decision.action == "incident"
        and decision.incident_type == "auth_config_failure"
    )
    assert decision.suppressed is dismissed
    # A later degradation episode may recover with a fresh isolated restart.
    ctx.snapshot.degraded_since = NOW
    assert POLICY.decide(ctx, NOW).trigger == "isolated_auth_failure"

"""Durable provider claims must never rely on monitoring history or HTTP success."""

import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

from proxy2vpn.agent.provider import MobileOperations, ProviderOperationError
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.core.egress import EgressObservation
from proxy2vpn.core.iproyal import ControlResult, valid_rotation_key


@pytest.fixture
def mobile(tmp_path, monkeypatch):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    document = {
        "version": 1,
        "endpoints": [
            {
                "id": "mobile",
                "connection": {"host": "proxy.invalid", "port": 1234},
                "credentials": {"username_env": "M_USER", "password_env": "M_PASS"},
                "mobile_provider": {
                    "access_token_env": "M_TOKEN",
                    "rotation_key_env": "M_KEY",
                },
            }
        ],
    }
    (tmp_path / "external-proxies.json").write_text(json.dumps(document))
    for key, value in {
        "M_USER": "mobile-user-secret",
        "M_PASS": "mobile-password-secret",
        "M_TOKEN": "mobile-token-secret",
        "M_KEY": "mobile-key-secret",
    }.items():
        monkeypatch.setenv(key, value)
    return MobileOperations(AgentStateStore(compose)), document


def observer(ip="198.51.100.8"):
    return EgressObservation(
        100, "healthy", authentication=True, connectivity=True, current_egress_ip=ip
    )


@pytest.mark.parametrize("changed", [False, True])
# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Acknowledgment And Reset]]
def test_acknowledgement_is_independent_and_guard_survives_reset(
    mobile, monkeypatch, changed
):
    operations, _ = mobile
    calls = []
    count = 0

    async def observe(self, **kwargs):
        nonlocal count
        count += 1
        return observer("198.51.100.9" if changed and count > 1 else "198.51.100.8")

    async def request(self, token, key):
        calls.append((token, key))
        operations.store.reset_monitoring_state()
        return ControlResult(request_outcome="acknowledged", reason_code="acknowledged")

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )
    result = asyncio.run(operations.request("mobile"))
    assert result.request_outcome == "acknowledged"
    assert result.exit_change == ("changed" if changed else "unchanged")
    assert result.session_replaced is None
    assert len(calls) == 1
    state = operations.store.read_state()
    assert state.generation == 1
    assert state.actions[-1].source == "external_proxy"
    assert state.metrics.recovery_counters[-1].metric_count == 1
    operations.store.reset_monitoring_state()
    restarted = MobileOperations(AgentStateStore(operations.store.compose_file))
    with pytest.raises(ProviderOperationError, match="guarded"):
        asyncio.run(restarted.request("mobile"))
    persisted = operations.store.state_file.read_text()
    for secret in calls[0] + ("mobile-password-secret", "mobile-user-secret"):
        assert secret not in persisted


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Long Cooldown]]
def test_timeout_respects_longer_cooldown_and_reconciliation_never_replays(
    mobile, monkeypatch
):
    operations, document = mobile
    document["endpoints"][0]["mobile_provider"]["cooldown_seconds"] = 7200
    (operations.store.compose_root / "external-proxies.json").write_text(
        json.dumps(document)
    )
    calls = []

    async def observe(self, **kwargs):
        return observer()

    async def request(self, token, key):
        calls.append(key)
        return ControlResult(request_outcome="unknown", reason_code="timeout")

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )
    result = asyncio.run(operations.request("mobile"))
    assert result.cooldown_until >= result.completed_at + timedelta(seconds=7200)
    asyncio.run(operations.reconcile("mobile"))
    asyncio.run(operations.reconcile("mobile"))
    assert len(calls) == 1
    assert operations.store.read_state().metrics.recovery_counters[0].metric_count == 1


@pytest.mark.parametrize("key", [".", "..", "a/b", "a\\b", "a\n", "\ud800"])
# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Key Confinement]]
def test_invalid_rotation_key_has_no_claim_or_effect(mobile, monkeypatch, key):
    operations, _ = mobile
    assert not valid_rotation_key(key)
    # Surrogate cannot be represented as an environment value.
    if key == "\ud800":
        return
    monkeypatch.setenv("M_KEY", key)
    with pytest.raises(ProviderOperationError, match="credential"):
        asyncio.run(operations.request("mobile"))
    assert operations.store.read_state() is None


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Cancellation]]
def test_cancelled_dispatch_is_unknown_and_cannot_be_retried(mobile, monkeypatch):
    operations, _ = mobile

    async def observe(self, **kwargs):
        return observer()

    async def request(self, token, key):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(operations.request("mobile"))
    result = operations.status("mobile")[0]
    assert result.request_outcome == "unknown"
    assert result.audit_recorded
    with pytest.raises(ProviderOperationError, match="guarded"):
        asyncio.run(operations.request("mobile"))


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Alias Concurrency]]
def test_aliases_share_resource_claim(mobile, monkeypatch):
    operations, document = mobile
    alias = dict(document["endpoints"][0], id="alias")
    document["endpoints"].append(alias)
    (operations.store.compose_root / "external-proxies.json").write_text(
        json.dumps(document)
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def observe(self, **kwargs):
        return observer()

    async def request(self, token, key):
        entered.set()
        await release.wait()
        return ControlResult(request_outcome="acknowledged", reason_code="acknowledged")

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )

    async def scenario():
        first = asyncio.create_task(operations.request("mobile"))
        await entered.wait()
        with pytest.raises(ProviderOperationError, match="guarded"):
            await operations.request("alias")
        release.set()
        return await first

    assert asyncio.run(scenario()).audit_recorded


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Expiry]]
def test_expired_metadata_blocks_before_claim(mobile):
    operations, document = mobile
    document["endpoints"][0]["mobile_provider"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    (operations.store.compose_root / "external-proxies.json").write_text(
        json.dumps(document)
    )
    with pytest.raises(ProviderOperationError, match="expiry"):
        asyncio.run(operations.request("mobile"))
    assert operations.store.read_state() is None


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Lost Dispatch]]
def test_dispatch_death_requires_probe_only_reconciliation(mobile, monkeypatch):
    operations, _ = mobile

    async def observe(self, **kwargs):
        return observer()

    async def request(self, token, key):
        raise SystemExit("simulated process death")

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )
    with pytest.raises(SystemExit):
        asyncio.run(operations.request("mobile"))
    record = operations.status("mobile")[0]
    assert record.operation_phase == "dispatched"
    assert not record.audit_recorded
    operations.store.reset_monitoring_state()
    with pytest.raises(ProviderOperationError, match="guarded"):
        asyncio.run(operations.request("mobile"))
    with operations.store.provider_transaction():
        state = operations.store.read_state()
        state.provider_operations[0].cooldown_until = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
        operations.store.write_state(state)
    result = asyncio.run(operations.reconcile("mobile"))
    assert result.request_outcome == "unknown"
    assert result.audit_recorded
    assert operations.store.read_state().metrics.recovery_counters[0].metric_count == 1


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Failed Outcome Commit]]
def test_outcome_commit_failure_preserves_intent_without_duplicate_counter(
    mobile, monkeypatch
):
    from proxy2vpn.core.private_storage import StorageError

    operations, _ = mobile
    calls = []

    async def observe(self, **kwargs):
        return observer()

    async def request(self, token, key):
        calls.append(key)
        return ControlResult(request_outcome="acknowledged", reason_code="acknowledged")

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )
    original = operations.store._atomic_write

    def fail_result(path, text):
        if '"audit_recorded": true' in text:
            raise StorageError("simulated write failure")
        return original(path, text)

    monkeypatch.setattr(operations.store, "_atomic_write", fail_result)
    with pytest.raises(StorageError):
        asyncio.run(operations.request("mobile"))
    assert len(calls) == 1
    state = operations.store.read_state()
    assert state.provider_operations[0].request_outcome == "unknown"
    assert not state.metrics.recovery_counters
    monkeypatch.setattr(operations.store, "_atomic_write", original)
    with pytest.raises(ProviderOperationError, match="guarded"):
        asyncio.run(operations.request("mobile"))


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Changed Credentials]]
def test_changed_credentials_fence_verification_without_rewriting_ack(
    mobile, monkeypatch
):
    operations, _ = mobile

    async def observe(self, **kwargs):
        return observer()

    async def request(self, token, key):
        monkeypatch.setenv("M_PASS", "replacement-password")
        return ControlResult(request_outcome="acknowledged", reason_code="acknowledged")

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )
    result = asyncio.run(operations.request("mobile"))
    assert result.request_outcome == "acknowledged"
    assert result.reason_code == "acknowledged"
    assert result.verification_state == "configuration_changed"
    assert result.exit_change == "unknown"
    assert result.authentication is None


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Reset Generation]]
def test_provider_completion_survives_reset_but_stale_health_writer_does_not(
    mobile, monkeypatch
):
    from proxy2vpn.agent.state import StorageConflict

    operations, _ = mobile

    async def observe(self, **kwargs):
        return observer()

    async def request(self, token, key):
        operations.store.reset_monitoring_state()
        return ControlResult(request_outcome="acknowledged", reason_code="acknowledged")

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )
    with operations.store.observation_session(0):
        result = asyncio.run(operations.request("mobile"))
        assert result.audit_recorded
        with pytest.raises(StorageConflict):
            operations.store.write_state(operations.store.read_state())


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Unsupported Sources]]
def test_generic_and_gluetun_names_do_not_dispatch(mobile):
    operations, document = mobile
    del document["endpoints"][0]["mobile_provider"]
    (operations.store.compose_root / "external-proxies.json").write_text(
        json.dumps(document)
    )
    for name in ("mobile", "gluetun-service", "missing"):
        with pytest.raises(ProviderOperationError, match="does not support"):
            asyncio.run(operations.request(name))
    assert operations.store.read_state() is None


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Revoked Reservation]]
def test_reconciliation_revokes_suspended_baseline_dispatch(mobile, monkeypatch):
    operations, _ = mobile
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    first = True

    async def observe(self, **kwargs):
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()
        return observer()

    async def request(self, token, key):
        calls.append(key)
        return ControlResult(request_outcome="acknowledged", reason_code="acknowledged")

    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.observe", observe
    )
    monkeypatch.setattr(
        "proxy2vpn.adapters.iproyal.IPRoyalMobileAdapter.request_exit_ip", request
    )

    async def scenario():
        pending = asyncio.create_task(operations.request("mobile"))
        await entered.wait()
        with operations.store.provider_transaction():
            state = operations.store.read_state()
            state.provider_operations[0].cooldown_until = datetime.now(
                timezone.utc
            ) - timedelta(seconds=1)
            operations.store.write_state(state)
        reconciled = await operations.reconcile("mobile")
        assert reconciled.request_outcome == "not_dispatched"
        release.set()
        return await pending

    result = asyncio.run(scenario())
    assert not calls
    assert result.request_outcome == "not_dispatched"
    assert result.operation_phase == "completed"
    assert operations.store.read_state().metrics.recovery_counters[0].metric_count == 1


# @lat: [[lat.md/mobile-operation-tests#Mobile Operation Tests#Durable Dispatch Required]]
def test_capability_never_allows_uncoordinated_execute(mobile):
    from proxy2vpn.core.egress import UnsupportedEgressOperation

    adapter = mobile[0]._snapshot("mobile")[-1]
    assert adapter.capabilities.request_different_exit_ip
    for operation in (
        "request_different_exit_ip",
        "restart_tunnel",
        "restore",
        "replace_endpoint",
        "replace_session",
    ):
        with pytest.raises(UnsupportedEgressOperation, match="manual coordinator"):
            asyncio.run(adapter.execute(operation))

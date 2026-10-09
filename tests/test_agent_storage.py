"""Durability and process-concurrency contracts for local watchdog evidence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import multiprocessing
import os
from pathlib import Path

import pytest

from proxy2vpn.agent.models import ActionRecord, AgentIncident, AgentState, AgentStatus
from proxy2vpn.agent.runtime import AgentWatchdog
from proxy2vpn.agent.state import AgentStateStore, StorageConflict
from proxy2vpn.core.private_storage import StorageError


def _incident(identifier="012345abcdef"):
    now = datetime.now(timezone.utc)
    return AgentIncident(
        id=identifier,
        service_name="vpn",
        type="rotation_required",
        severity="high",
        created_at=now,
        updated_at=now,
        summary="Needs recovery",
        recommended_action="investigate",
    )


@pytest.fixture
def store(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    return AgentStateStore(compose)


def _state(store):
    return AgentState(
        status=AgentStatus(compose_path=str(store.compose_file), interval_seconds=30)
    )


def _writer(compose, index):
    store = AgentStateStore(Path(compose))
    for item in range(5):
        with store.transaction():
            state = store.read_state() or _state(store)
            state.status.service_count += 1
            store.write_state(state)
            store.append_incident(_incident(f"{index * 5 + item:012x}"))


# @lat: [[agent-storage-tests#Agent Storage Tests#Concurrent Transactions]]
def test_multiprocess_transactions_preserve_updates(store):
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_writer, args=(str(store.compose_file), index))
        for index in range(3)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0
    assert store.read_state().status.service_count == 15
    assert len(store.load_incidents()) == 15
    assert len(store.incidents_file.read_text().splitlines()) == 15


# @lat: [[agent-storage-tests#Agent Storage Tests#Stale Writes And Action Evidence]]
def test_stale_state_and_incidents_are_rejected_but_action_audit_survives(store):
    state = _state(store)
    store.write_state(state)
    stale = store.read_state()
    state.status.service_count = 9
    store.write_state(state)
    with pytest.raises(StorageConflict):
        store.write_state(stale)
    action = ActionRecord(
        ts=datetime.now(timezone.utc),
        service_name="vpn",
        action="rotate",
        trigger="manual",
        result="success",
    )
    stale.actions.append(action)
    store.append_action(stale, action)
    assert store.read_state().status.service_count == 9
    assert store.read_state().actions == [action]
    incident = _incident()
    store.append_incident(incident)
    obsolete = store.load_incidents()[0]
    incident.status = "dismissed"
    store.append_incident(incident)
    with pytest.raises(StorageConflict):
        store.append_incident(obsolete)
    assert store.load_incidents()[0].status == "dismissed"


# @lat: [[agent-storage-tests#Agent Storage Tests#Reset Fences Delayed Work]]
def test_reset_rejects_old_state_existing_and_new_incidents(store):
    state = _state(store)
    store.write_state(state)
    incident = _incident()
    store.append_incident(incident)
    with store.observation_session(state.generation):
        store.reset_monitoring_state()
        for mutation in [
            lambda: store.write_state(state),
            lambda: store.append_incident(incident),
            lambda: store.append_incident(_incident("abcdef012345")),
        ]:
            with pytest.raises(StorageConflict):
                mutation()
    assert store.read_state().generation == 1
    assert store.load_incidents() == []
    assert store.read_state().services == []
    # New work at the current generation remains usable after reset.
    fresh = store.read_state()
    with store.observation_session(fresh.generation):
        store.append_incident(_incident())
    assert store.load_incidents()[0].generation == 1


# @lat: [[agent-storage-tests#Agent Storage Tests#Reset Journal Recovery]]
def test_reset_interruption_recovers_both_files(store, monkeypatch):
    state = _state(store)
    state.status.service_count = 5
    store.write_state(state)
    store.append_incident(_incident())
    original = store._atomic_write

    def interrupt(path, text):
        if path == store.incidents_file:
            raise OSError("simulated power loss")
        original(path, text)

    monkeypatch.setattr(store, "_atomic_write", interrupt)
    with pytest.raises(OSError):
        store.reset_monitoring_state()
    assert store.journal_path.exists()
    recovered = AgentStateStore(store.compose_file)
    assert recovered.read_state().status.service_count == 0
    assert recovered.read_state().generation == 1
    assert recovered.load_incidents() == []
    assert not recovered.journal_path.exists()
    assert recovered.load_incidents() == []


# @lat: [[agent-storage-tests#Agent Storage Tests#Replacement Failure]]
def test_failed_replacement_preserves_previous_valid_state(store, monkeypatch):
    state = _state(store)
    store.write_state(state)
    previous = store.state_file.read_bytes()

    def fail(*args):
        raise OSError("replace interrupted")

    monkeypatch.setattr(os, "replace", fail)
    state.status.service_count = 10
    with pytest.raises(OSError):
        store.write_state(state)
    assert store.state_file.read_bytes() == previous
    assert not list(store.agent_dir.glob("*.tmp"))


# @lat: [[agent-storage-tests#Agent Storage Tests#Partial History And Corruption]]
def test_only_truncated_final_history_record_is_recovered(store):
    store.append_incident(_incident())
    valid = store.incidents_file.read_text()
    store.incidents_file.write_text(valid + '{"id":"unfinished')
    assert len(store.load_incidents()) == 1
    assert store.incidents_file.read_text() == valid
    corrupt = valid + '{"raw":"private-secret"}\n' + valid
    store.incidents_file.write_text(corrupt)
    with pytest.raises(StorageError) as error:
        store.load_incidents()
    assert "private-secret" not in str(error.value)
    assert store.incidents_file.read_text() == corrupt


# @lat: [[agent-storage-tests#Agent Storage Tests#Private Permissions]]
@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_permissive_umask_and_legacy_permission_repair(store):
    old = os.umask(0)
    try:
        store.write_state(_state(store))
        store.append_incident(_incident())
        store.write_daemon_pid(123)
        with store.runtime_lock():
            pass
        store.agent_dir.chmod(0o777)
        for path in store.agent_dir.iterdir():
            path.chmod(0o666)
        store.read_state()
        assert store.agent_dir.stat().st_mode & 0o777 == 0o700
        assert all(
            path.stat().st_mode & 0o777 == 0o600 for path in store.agent_dir.iterdir()
        )
    finally:
        os.umask(old)


# @lat: [[agent-storage-tests#Agent Storage Tests#Symlink Rejection]]
@pytest.mark.parametrize(
    "name",
    [
        "state.json",
        "incidents.jsonl",
        "daemon.pid",
        "daemon.log",
        "evidence.lock",
        "runtime.lock",
        "identity.key",
        "identity.lock",
        "transaction.json",
    ],
)
def test_symlink_storage_does_not_touch_target(store, tmp_path, name):
    target = tmp_path / "outside"
    target.write_text("untouched")
    target.chmod(0o644)
    link = store.agent_dir / name
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    with pytest.raises(StorageError):
        store.read_state()
    assert target.read_text() == "untouched"
    assert target.stat().st_mode & 0o777 == 0o644


# @lat: [[agent-storage-tests#Agent Storage Tests#Lock Timeout]]
def test_lock_timeout_is_safe_and_bounded(store):
    other = AgentStateStore(store.compose_file)
    other._lock.timeout = 0
    with store.transaction():
        with pytest.raises(StorageError, match="timed out"):
            other.read_state()
    assert other.read_state() is None


# @lat: [[agent-storage-tests#Agent Storage Tests#Investigation Interleaving]]
def test_investigation_does_not_lock_network_or_reopen_dismissal(store, monkeypatch):
    store.append_incident(_incident())
    watchdog = AgentWatchdog(store.compose_file, store=store)

    async def investigate_context(incident):
        # A second process/store can acquire the lock during suspended network work.
        other = AgentStateStore(store.compose_file)
        with other.transaction():
            dismissed = other.load_incidents()[0]
            dismissed.status = "dismissed"
            other.append_incident(dismissed)
        from proxy2vpn.agent.llm import InvestigationContext

        return InvestigationContext(
            incident_id=incident.id,
            service_name="vpn",
            incident_type=incident.type,
            severity=incident.severity,
            status=incident.status,
            incident_summary=incident.summary,
            recommended_action="investigate",
            failure_count=1,
            container_status="running",
            profile_validation_errors=[],
            issues=[],
            recent_actions=[],
        )

    monkeypatch.setattr(watchdog, "_build_investigation_context", investigate_context)
    with pytest.raises(StorageConflict):
        asyncio.run(watchdog.investigate_incident("012345abcdef"))
    assert store.load_incidents()[0].status == "dismissed"


# @lat: [[agent-storage-tests#Agent Storage Tests#Fresh Incident Decisions]]
def test_stale_incident_inventory_obeys_latest_dismissal(store):
    store.append_incident(_incident())
    stale = store.load_incidents()
    dismissed = store.load_incidents()[0]
    dismissed.status = "dismissed"
    store.append_incident(dismissed)
    watchdog = AgentWatchdog(store.compose_file, store=store)
    result = watchdog._upsert_incident(
        stale,
        "vpn",
        "rotation_required",
        "high",
        "Needs recovery",
        None,
        "investigate",
        2,
    )
    assert result is None
    assert len(store.load_incidents()) == 1
    assert store.load_incidents()[0].status == "dismissed"


# @lat: [[agent-storage-tests#Agent Storage Tests#Approval Audit Interleaving]]
def test_approval_records_completed_rotation_before_stale_finalization(
    store, monkeypatch
):
    from types import SimpleNamespace

    store.write_state(_state(store))
    store.append_incident(_incident())
    watchdog = AgentWatchdog(store.compose_file, store=store)

    async def rotate(name, *, state):
        other = AgentStateStore(store.compose_file)
        fresh = other.read_state()
        fresh.status.service_count = 12
        other.write_state(fresh)
        return SimpleNamespace(
            success=True,
            errors=[],
            rotation_changes=[],
            rotation_attempts=[],
            current_egress_ip=None,
        )

    monkeypatch.setattr(watchdog, "_rotate_service_via_fleet", rotate)
    with pytest.raises(StorageConflict):
        asyncio.run(watchdog.approve_incident("012345abcdef"))
    persisted = store.read_state()
    assert persisted.status.service_count == 12
    assert len(persisted.actions) == 1
    assert persisted.actions[0].action == "rotate"
    assert persisted.actions[0].result == "success"


# @lat: [[agent-storage-tests#Agent Storage Tests#Approval Reset Interleaving]]
def test_interrupted_approval_cannot_migrate_new_generation_incidents(
    store, monkeypatch
):
    store.write_state(_state(store))
    store.append_incident(_incident())
    watchdog = AgentWatchdog(store.compose_file, store=store)

    async def rotate(name, *, state):
        watchdog._inflight_service_names[name] = "vpn-new"
        other = AgentStateStore(store.compose_file)
        other.reset_monitoring_state()
        fresh_incident = _incident("abcdef012345")
        fresh_incident.generation = other.read_state().generation
        with other.observation_session(fresh_incident.generation):
            other.append_incident(fresh_incident)
        raise RuntimeError("interrupted rotation")

    monkeypatch.setattr(watchdog, "_rotate_service_via_fleet", rotate)
    with pytest.raises(StorageConflict):
        asyncio.run(watchdog.approve_incident("012345abcdef"))
    incidents = store.load_incidents()
    assert [(item.generation, item.service_name) for item in incidents] == [(1, "vpn")]
    assert store.read_state().actions == []


# @lat: [[agent-storage-tests#Agent Storage Tests#Unsafe Log Parents]]
def test_log_parent_symlink_is_rejected(tmp_path):
    from proxy2vpn.adapters.logging_utils import PrivateFileHandler

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "logdir"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        PrivateFileHandler(link / "daemon.log")
    assert not (outside / "daemon.log").exists()


# @lat: [[agent-storage-tests#Agent Storage Tests#Special File Rejection]]
@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO contract")
def test_fifo_is_rejected_without_blocking(store):
    os.mkfifo(store.state_file)
    with pytest.raises(StorageError):
        store.read_state()


# @lat: [[agent-storage-tests#Agent Storage Tests#Approval Entry Reset]]
def test_approval_cannot_adopt_reset_generation_after_initial_validation(
    store, monkeypatch
):
    from types import SimpleNamespace

    store.write_state(_state(store))
    store.append_incident(_incident())
    watchdog = AgentWatchdog(store.compose_file, store=store)

    def reset_after_check(name):
        AgentStateStore(store.compose_file).reset_monitoring_state()
        return False

    async def rotate(name, *, state):
        return SimpleNamespace(
            success=True, errors=[], rotation_changes=[], rotation_attempts=[]
        )

    monkeypatch.setattr(watchdog, "_is_external_endpoint", reset_after_check)
    monkeypatch.setattr(watchdog, "_rotate_service_via_fleet", rotate)
    with pytest.raises(StorageConflict):
        asyncio.run(watchdog.approve_incident("012345abcdef"))
    assert store.read_state().generation == 1
    assert store.read_state().actions == []
    assert store.load_incidents() == []


# @lat: [[agent-storage-tests#Agent Storage Tests#Portable Filename Isolation]]
@pytest.mark.parametrize(
    "overrides",
    [
        {"runtime_lock_file": "STATE.JSON"},
        {"state_file": "EVIDENCE.LOCK"},
        {"state_file": "../outside"},
        {"state_file": "state.json."},
        {"incidents_file": "/tmp/history"},
    ],
)
def test_storage_filename_aliases_are_rejected(overrides):
    from proxy2vpn.agent.config import AgentSettings

    with pytest.raises(ValueError):
        AgentSettings(**overrides)


def _approval_writer(compose, barrier, entered, release, counter, results):
    from types import SimpleNamespace

    watchdog = AgentWatchdog(Path(compose))

    def synchronize(name):
        barrier.wait(15)
        return False

    async def rotate(name, *, state):
        with counter.get_lock():
            counter.value += 1
        entered.set()
        await asyncio.to_thread(release.wait, 15)
        return SimpleNamespace(
            success=True, errors=[], rotation_changes=[], rotation_attempts=[]
        )

    watchdog._is_external_endpoint = synchronize
    watchdog._rotate_service_via_fleet = rotate
    try:
        asyncio.run(watchdog.approve_incident("012345abcdef"))
    except (StorageConflict, RuntimeError):
        results.put("rejected")
    else:
        results.put("completed")


# @lat: [[agent-storage-tests#Agent Storage Tests#Concurrent Approval Claims]]
def test_multiprocess_approvals_claim_before_network(store):
    store.write_state(_state(store))
    store.append_incident(_incident())
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    entered, release = context.Event(), context.Event()
    counter = context.Value("i", 0)
    results = context.Queue()
    processes = [
        context.Process(
            target=_approval_writer,
            args=(str(store.compose_file), barrier, entered, release, counter, results),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        assert entered.wait(15)
        # The winner's network operation is suspended, but storage stays available.
        with store.transaction():
            claimed = store.load_incidents()[0]
            assert claimed.approved_at is not None and claimed.status == "open"
        assert results.get(timeout=15) == "rejected"
        release.set()
        assert results.get(timeout=15) == "completed"
        assert counter.value == 1
    finally:
        release.set()
        for process in processes:
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join()
    assert all(process.exitcode == 0 for process in processes)
    assert store.load_incidents()[0].status == "resolved"


# @lat: [[agent-storage-tests#Agent Storage Tests#Interrupted Approval Claim]]
def test_cancelled_approval_keeps_claim_and_rejects_retry(store, monkeypatch):
    store.write_state(_state(store))
    store.append_incident(_incident())
    watchdog = AgentWatchdog(store.compose_file, store=store)
    calls = []

    async def rotate(name, *, state):
        calls.append(name)
        raise asyncio.CancelledError()

    monkeypatch.setattr(watchdog, "_rotate_service_via_fleet", rotate)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(watchdog.approve_incident("012345abcdef"))
    incident = store.load_incidents()[0]
    assert incident.status == "open" and incident.approved_at is not None
    with pytest.raises(RuntimeError, match="already claimed"):
        asyncio.run(watchdog.approve_incident(incident.id))
    assert calls == ["vpn"]


# @lat: [[agent-storage-tests#Agent Storage Tests#Approval Completion Merge]]
@pytest.mark.parametrize("concurrent_status", ["open", "dismissed", "resolved"])
def test_approval_completion_preserves_concurrent_incident_updates(
    store, monkeypatch, concurrent_status
):
    from types import SimpleNamespace

    store.write_state(_state(store))
    store.append_incident(_incident())
    watchdog = AgentWatchdog(store.compose_file, store=store)

    async def rotate(name, *, state):
        other = AgentStateStore(store.compose_file)
        fresh = other.load_incidents()[0]
        fresh.status = concurrent_status
        fresh.failure_count = 7
        fresh.human_explanation = "New operator evidence"
        other.append_incident(fresh)
        return SimpleNamespace(
            success=True, errors=[], rotation_changes=[], rotation_attempts=[]
        )

    monkeypatch.setattr(watchdog, "_rotate_service_via_fleet", rotate)
    result = asyncio.run(watchdog.approve_incident("012345abcdef"))
    assert result.status == (
        "resolved" if concurrent_status == "open" else concurrent_status
    )
    assert result.failure_count == 7
    assert result.human_explanation == "New operator evidence"
    assert result.approved_at is not None


# @lat: [[agent-storage-tests#Agent Storage Tests#Daemon Startup Conflict]]
def test_daemon_retries_initial_state_conflict(store, monkeypatch):
    store.write_state(_state(store))
    watchdog = AgentWatchdog(store.compose_file, store=store)
    original_write = store.write_state
    writes = 0
    cycles = []
    sleeps = []

    def conflict_once(state):
        nonlocal writes
        writes += 1
        if writes == 1:
            other = AgentStateStore(store.compose_file)
            current = other.read_state()
            current.status.service_count = 8
            other.write_state(current)
        original_write(state)

    async def cycle(state):
        cycles.append(state)
        assert state.status.service_count == 8
        assert state.status.daemon_mode == "foreground"
        return state

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(store, "write_state", conflict_once)
    monkeypatch.setattr(watchdog, "run_cycle", cycle)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(watchdog.run_forever())
    assert writes == 2 and len(cycles) == 1
    assert sleeps == [watchdog.interval_seconds] * 2


# @lat: [[agent-storage-tests#Agent Storage Tests#Unicode Filename Isolation]]
@pytest.mark.parametrize(
    "left,right", [("é.json", "e\u0301.json"), ("É.json", "e\u0301.JSON")]
)
def test_canonically_equivalent_storage_names_are_rejected(left, right):
    from proxy2vpn.agent.config import AgentSettings

    with pytest.raises(ValueError, match="distinct"):
        AgentSettings(state_file=left, incidents_file=right)

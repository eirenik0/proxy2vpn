"""Retention boundaries, preview purity and concurrent/durable compaction."""

from datetime import datetime, timedelta, timezone
import asyncio
import json
import multiprocessing
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import AgentIncident, AgentState, AgentStatus
from proxy2vpn.agent.retention import plan_compaction, preview_compaction
from proxy2vpn.agent.recovery_policy import recently_dismissed
from proxy2vpn.agent.runtime import AgentWatchdog
from proxy2vpn.agent.state import AgentStateStore, StorageConflict
from proxy2vpn.cli.main import app
from proxy2vpn.core.private_storage import StorageError

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def incident(index, status="open", age=0, **changes):
    stamp = NOW - timedelta(seconds=age)
    return AgentIncident(
        id=f"{index:012x}",
        service_name="vpn",
        source="external_proxy",
        type="endpoint_unhealthy",
        severity="high",
        status=status,
        created_at=stamp,
        updated_at=stamp,
        summary="Known failure",
        recommended_action="investigate",
        **changes,
    )


@pytest.fixture
def store(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    return AgentStateStore(
        compose,
        AgentSettings(incident_retention_seconds=100, incident_cooldown_seconds=200),
    )


# @lat: [[incident-retention-tests#Incident Retention Tests#Latest Evidence And Status Classification]]
def test_compaction_preserves_latest_active_claims_failed_and_source(store):
    records = [
        incident(1, "open", 1000, approved_at=NOW),
        incident(2, "approved", 1000),
        incident(3, "failed", 1000),
        incident(4, "resolved", 101, approved_at=NOW),
        incident(5, "dismissed", 201),
        incident(6, "resolved", 100),
        incident(7, "dismissed", 200),
    ]
    for item in records:
        store.append_incident(item)
    updated = records[0].model_copy(
        update={"failure_count": 9, "updated_at": NOW - timedelta(seconds=2000)}
    )
    store.append_incident(updated)
    before = AgentState(
        status=AgentStatus(compose_path=str(store.compose_file), interval_seconds=30)
    )
    store.write_state(before)
    state_bytes = store.state_file.read_bytes()
    report = store.compact_incidents(now=NOW)
    assert (
        report.records_before,
        report.records_after,
        report.superseded_versions,
        report.expired_incidents,
    ) == (8, 5, 1, 2)
    kept = {item.id: item for item in store.load_incidents()}
    assert set(kept) == {
        item.id for item in (updated, records[1], records[2], records[5], records[6])
    }
    assert kept[updated.id] == updated
    assert kept[updated.id].source == "external_proxy"
    assert kept[updated.id].approved_at == NOW
    assert kept[records[2].id].status == "failed"
    assert store.state_file.read_bytes() == state_bytes
    history_bytes = store.incidents_file.read_bytes()
    inode = store.incidents_file.stat().st_ino
    repeated = store.compact_incidents(now=NOW)
    assert repeated.expired_incidents == repeated.superseded_versions == 0
    assert store.incidents_file.read_bytes() == history_bytes
    assert store.incidents_file.stat().st_ino == inode


# @lat: [[incident-retention-tests#Incident Retention Tests#Clock And Expiry Boundaries]]
def test_mixed_clocks_future_and_unbounded_retention(store):
    naive = incident(1, "resolved", 100).model_copy(
        update={"updated_at": (NOW - timedelta(seconds=100)).replace(tzinfo=None)}
    )
    offset = incident(2, "resolved", 101).model_copy(
        update={
            "updated_at": (NOW - timedelta(seconds=101)).astimezone(
                timezone(timedelta(hours=5))
            )
        }
    )
    future = incident(3, "dismissed", -100)
    report, kept = plan_compaction(
        [naive, offset, future], store.settings, now=NOW.replace(tzinfo=None)
    )
    assert kept == [naive, future]
    assert report.expired_incidents == 1
    for item in (naive, offset, future):
        store.append_incident(item)
    assert len(store.load_incidents()) == 3  # Mixed timestamps can also be listed.
    huge = AgentSettings(incident_retention_seconds=10**100)
    assert len(plan_compaction([naive, offset, future], huge, now=NOW)[1]) == 3
    zero = AgentSettings(incident_retention_seconds=0, incident_cooldown_seconds=0)
    assert plan_compaction(
        [incident(4, "resolved"), incident(5, "resolved", 1)], zero, now=NOW
    )[1] == [incident(4, "resolved")]


# @lat: [[incident-retention-tests#Incident Retention Tests#Dismissal Suppression]]
def test_pruning_keeps_dismissal_until_inclusive_cooldown_boundary(store, monkeypatch):
    dismissed = incident(1, "dismissed", 200).model_copy(
        update={"updated_at": (NOW - timedelta(seconds=200)).replace(tzinfo=None)}
    )
    store.append_incident(dismissed)
    store.compact_incidents(now=NOW)
    assert recently_dismissed(
        store.load_incidents(), "vpn", "endpoint_unhealthy", NOW, 200
    )
    watchdog = AgentWatchdog(store.compose_file, store=store)
    monkeypatch.setattr("proxy2vpn.agent.runtime.utc_now", lambda: NOW)
    args = dict(
        incidents=[],
        service_name="vpn",
        incident_type="endpoint_unhealthy",
        severity="high",
        summary="Unhealthy",
        human_explanation=None,
        recommended_action="investigate",
        failure_count=1,
        source="external_proxy",
    )
    assert watchdog._upsert_incident(**args) is None
    store.compact_incidents(now=NOW + timedelta(seconds=1))
    monkeypatch.setattr(
        "proxy2vpn.agent.runtime.utc_now", lambda: NOW + timedelta(seconds=1)
    )
    assert watchdog._upsert_incident(**args) is not None


# @lat: [[incident-retention-tests#Incident Retention Tests#Preview Has No Evidence Side Effects]]
def test_preview_preserves_legacy_bytes_permissions_and_missing_storage(tmp_path):
    compose = tmp_path / "compose.yml"
    settings = AgentSettings(incident_retention_seconds=0, incident_cooldown_seconds=0)
    report = preview_compaction(compose, settings, now=NOW)
    assert report.dry_run and report.records_before == 0
    assert not (tmp_path / settings.state_dirname).exists()
    root = tmp_path / settings.state_dirname
    root.mkdir(mode=0o755)
    history = root / settings.incidents_file
    record = incident(1, "resolved", 100).model_dump(mode="json")
    record["summary"] = "password=preview-must-not-export-this"
    history.write_text(json.dumps(record) + "\n")
    history.chmod(0o644)
    before = (
        history.read_bytes(),
        history.stat().st_mode,
        root.stat().st_mode,
        sorted(root.iterdir()),
    )
    report = preview_compaction(compose, settings, now=NOW)
    assert report.expired_incidents == 1
    assert "preview-must-not-export-this" not in report.model_dump_json()
    assert before == (
        history.read_bytes(),
        history.stat().st_mode,
        root.stat().st_mode,
        sorted(root.iterdir()),
    )
    # Read-only validation still refuses unsafe artifact types.
    history.unlink()
    history.symlink_to(compose)
    with pytest.raises(StorageError):
        preview_compaction(compose, settings, now=NOW)


# @lat: [[incident-retention-tests#Incident Retention Tests#Preview Corruption And Pending Recovery]]
def test_preview_refuses_recovery_and_corruption_without_mutation(store):
    store.append_incident(incident(1))
    valid = store.incidents_file.read_text()
    store.incidents_file.write_text(valid + '{"summary":"private-corruption"}\n')
    before = store.incidents_file.read_bytes()
    with pytest.raises(StorageError) as exc:
        preview_compaction(store.compose_file, store.settings, now=NOW)
    assert "private-corruption" not in str(exc.value)
    assert store.incidents_file.read_bytes() == before
    store.incidents_file.write_text(valid + '{"id":"torn')
    assert (
        preview_compaction(store.compose_file, store.settings, now=NOW).records_before
        == 1
    )
    assert store.incidents_file.read_text().endswith("torn")
    store.compact_incidents(now=NOW)
    assert store.incidents_file.read_text() == valid
    store.journal_path.write_text('{"pending":"do-not-replay"}')
    with pytest.raises(StorageError, match="Pending agent storage recovery"):
        preview_compaction(store.compose_file, store.settings, now=NOW)
    assert store.journal_path.read_text() == '{"pending":"do-not-replay"}'


# @lat: [[incident-retention-tests#Incident Retention Tests#Atomic Interruption And Redaction]]
@pytest.mark.parametrize("after_replace", [False, True])
def test_interrupted_compaction_has_old_or_new_valid_redacted_history(
    store, monkeypatch, after_replace
):
    first = incident(1)
    store.append_incident(first)
    store.append_incident(first)
    original = os.replace
    before = store.incidents_file.read_bytes()

    def interrupt(source, destination):
        if Path(destination) == store.incidents_file:
            if after_replace:
                original(source, destination)
            raise OSError("simulated interruption")
        return original(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "replace", interrupt)
        with pytest.raises(OSError):
            store.compact_incidents(now=NOW)
    assert len(store.load_incidents()) == 1
    assert (
        store.incidents_file.read_bytes() == before
        if not after_replace
        else len(store.incidents_file.read_text().splitlines()) == 1
    )
    assert not list(store.agent_dir.glob("*.tmp"))
    store.compact_incidents(now=NOW)
    assert len(store.incidents_file.read_text().splitlines()) == 1
    legacy = incident(2).model_dump(mode="json")
    legacy["summary"] = "password=retained-secret-value"
    store.incidents_file.write_text(
        store.incidents_file.read_text() + json.dumps(legacy) + "\n"
    )
    store.compact_incidents(now=NOW)
    assert "retained-secret-value" not in store.incidents_file.read_text()


# @lat: [[incident-retention-tests#Incident Retention Tests#Stale Updates And Reset Fences]]
def test_compaction_preserves_revisions_rejects_pruned_writers_and_reset_sessions(
    store,
):
    active = incident(1)
    expired = incident(2, "resolved", 1000)
    store.append_incident(active)
    store.append_incident(expired)
    store.compact_incidents(now=NOW)
    active.failure_count = 8
    store.append_incident(active)  # Compaction did not change this revision.
    with pytest.raises(StorageConflict, match="no longer exists"):
        store.append_incident(expired)
    with store.observation_session(0):
        store.reset_monitoring_state()
        with pytest.raises(StorageConflict, match="reset"):
            store.compact_incidents(now=NOW)
    assert store.load_incidents() == []


def _concurrent_writer(compose, ready, proceed):
    store = AgentStateStore(Path(compose))
    with store.transaction():
        item = store.load_incidents()[0]
        item.failure_count = 13
        ready.set()
        if not proceed.wait(10):
            raise RuntimeError("parent did not release writer")
        store.append_incident(item)


# @lat: [[incident-retention-tests#Incident Retention Tests#Concurrent Writer Preservation]]
def test_compaction_reloads_a_writer_committed_under_the_same_lock(store):
    item = incident(1)
    store.append_incident(item)
    ctx = multiprocessing.get_context("spawn")
    ready, proceed = ctx.Event(), ctx.Event()
    process = ctx.Process(
        target=_concurrent_writer, args=(str(store.compose_file), ready, proceed)
    )
    process.start()
    try:
        assert ready.wait(10)
        proceed.set()
        store.compact_incidents(now=NOW)
        process.join(10)
        assert process.exitcode == 0
        assert store.load_incidents()[0].failure_count == 13
        assert len(store.incidents_file.read_text().splitlines()) == 1
    finally:
        if process.is_alive():
            process.terminate()
        process.join()


# @lat: [[incident-retention-tests#Incident Retention Tests#Automatic Cycle Compaction]]
def test_watchdog_compacts_before_network_work_and_clears_failed_progress(
    store, monkeypatch
):
    item = incident(1)
    store.append_incident(item)
    store.append_incident(item)
    watchdog = AgentWatchdog(store.compose_file, store=store)

    def inventory():
        assert len(store.incidents_file.read_text().splitlines()) == 1
        return None, []

    monkeypatch.setattr(watchdog, "_inventory", inventory)
    asyncio.run(watchdog.run_once())
    assert store.read_state().status.active_cycle_phase is None
    monkeypatch.setattr(
        store,
        "compact_incidents",
        lambda **kwargs: (_ for _ in ()).throw(StorageError("compaction failed")),
    )
    with pytest.raises(StorageError, match="compaction failed"):
        asyncio.run(watchdog.run_once())
    assert store.read_state().status.active_cycle_phase is None


# @lat: [[incident-retention-tests#Incident Retention Tests#Operator CLI And Configuration]]
def test_cli_preview_apply_and_environment_validation(store, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr("proxy2vpn.agent.retention.datetime", Clock)
    monkeypatch.setenv("PROXY2VPN_AGENT_INCIDENT_RETENTION_SECONDS", "0")
    monkeypatch.setenv("PROXY2VPN_AGENT_INCIDENT_COOLDOWN_SECONDS", "0")
    store.append_incident(incident(1, "resolved", 1000))
    before = store.incidents_file.read_bytes()
    runner = CliRunner()
    args = [
        "--compose-file",
        str(store.compose_file),
        "agent",
        "compact-incidents",
        "--json",
    ]
    result = runner.invoke(app, args + ["--dry-run"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["expired_incidents"] == 1
    assert store.incidents_file.read_bytes() == before
    result = runner.invoke(app, args + ["--retention-days", "1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is False
    assert json.loads(result.output)["records_after"] == 1
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["records_after"] == 0
    result = runner.invoke(app, args + ["--retention-days", "-1"])
    assert result.exit_code != 0
    monkeypatch.setenv("PROXY2VPN_AGENT_INCIDENT_RETENTION_SECONDS", "-1")
    with pytest.raises(ValidationError):
        AgentSettings()


# @lat: [[incident-retention-tests#Incident Retention Tests#Legacy Revision Migration]]
def test_legacy_reader_cannot_reopen_after_dismissal_and_pruning(store):
    legacy = incident(1).model_dump(mode="json")
    legacy.pop("revision")
    store.incidents_file.write_text(json.dumps(legacy) + "\n")
    # Preview does not silently migrate persisted metadata.
    before = store.incidents_file.read_bytes()
    preview_compaction(store.compose_file, store.settings, now=NOW)
    assert store.incidents_file.read_bytes() == before
    stale = store.load_incidents()[0]
    assert stale.revision == 1
    assert json.loads(store.incidents_file.read_text())["revision"] == 1
    assert store.load_incidents()[0].revision == 1
    dismissed = stale.model_copy(update={"status": "dismissed"})
    store.append_incident(dismissed)
    store.compact_incidents(now=NOW + timedelta(seconds=201))
    assert store.load_incidents() == []
    with pytest.raises(StorageConflict, match="no longer exists"):
        store.append_incident(stale)
    assert store.load_incidents() == []

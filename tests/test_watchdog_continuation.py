"""Continuous monitoring retries operational errors without weakening storage."""

import asyncio

import pytest

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.agent.runtime import AgentWatchdog
from proxy2vpn.core.private_storage import StorageError


def watchdog(tmp_path):
    path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(path)
    return AgentWatchdog(path, interval_seconds=1)


# @lat: [[reliability-tests#Reliability Tests#Watchdog Failure Then Recovery]]
@pytest.mark.parametrize("reset_during_backoff", [False, True])
def test_transient_inventory_failure_is_persisted_then_monitoring_recovers(
    tmp_path, monkeypatch, reset_during_backoff
):
    agent = watchdog(tmp_path)
    attempts = 0
    delays = []

    def inventory():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary inventory failure")
        return None, []

    async def sleep(delay):
        delays.append(delay)
        state = agent.store.read_state()
        if len(delays) == 1:
            assert state.metrics.cycle_outcome == "failed"
            assert state.status.last_error == "Diagnostic error (raw text omitted)"
            assert state.status.active_cycle_phase is None
            if reset_during_backoff:
                agent.store.reset_monitoring_state()
        else:
            raise asyncio.CancelledError()

    monkeypatch.setattr(agent, "_inventory", inventory)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run_forever())
    assert attempts == 2
    state = agent.store.read_state()
    assert state.metrics.cycles_attempted == 2
    assert state.metrics.monitoring_resets == (1 if reset_during_backoff else 0)
    assert state.generation == (1 if reset_during_backoff else 0)
    assert state.metrics.cycle_outcome == "success"
    assert state.status.last_error is None
    assert delays == [1, 1]


# @lat: [[reliability-tests#Reliability Tests#Watchdog Bounded Backoff]]
def test_retry_backoff_is_capped_and_resets_after_success(tmp_path, monkeypatch):
    agent = watchdog(tmp_path)
    attempts = 0
    delays = []

    def inventory():
        nonlocal attempts
        attempts += 1
        if attempts != 12:
            raise RuntimeError("Docker unavailable")
        return None, []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 13:
            raise asyncio.CancelledError()

    monkeypatch.setattr(agent, "_inventory", inventory)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run_forever())
    assert delays == [1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300, 1, 1]


# @lat: [[reliability-tests#Reliability Tests#Watchdog Fatal Failures]]
@pytest.mark.parametrize(
    "error",
    [
        StorageError("unsafe storage"),
        ValueError("invalid configuration"),
        OSError("disk failure"),
    ],
)
def test_storage_and_configuration_errors_are_not_retried(tmp_path, monkeypatch, error):
    agent = watchdog(tmp_path)
    delays = []

    def inventory():
        raise error

    async def sleep(delay):
        delays.append(delay)
        raise AssertionError("fatal error was retried")

    monkeypatch.setattr(agent, "_inventory", inventory)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(type(error), match=str(error)):
        asyncio.run(agent.run_forever())
    assert delays == []


# @lat: [[reliability-tests#Reliability Tests#Watchdog Retry Cancellation]]
def test_cancellation_during_retry_preserves_failed_cycle(tmp_path, monkeypatch):
    agent = watchdog(tmp_path)

    def inventory():
        raise ConnectionError("temporary connection failure")

    async def sleep(delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(agent, "_inventory", inventory)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run_forever())
    state = agent.store.read_state()
    assert state.metrics.cycles_attempted == 1
    assert state.metrics.cycle_outcome == "failed"
    assert state.status.active_cycle_started_at is None


# @lat: [[reliability-tests#Reliability Tests#One Shot Failure]]
def test_one_shot_failure_still_propagates(tmp_path, monkeypatch):
    agent = watchdog(tmp_path)

    def inventory():
        raise RuntimeError("temporary inventory failure")

    monkeypatch.setattr(agent, "_inventory", inventory)
    with pytest.raises(RuntimeError, match="temporary inventory failure"):
        asyncio.run(agent.run_once())

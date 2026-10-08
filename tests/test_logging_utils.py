from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path

import pytest
import structlog

from proxy2vpn.adapters.logging_utils import (
    REDACTED,
    configure_logging,
    get_event_logger,
    get_logger,
    logging_context,
    set_log_level,
)

pytestmark = pytest.mark.usefixtures("isolated_logging")


# @lat: [[lat.md/logging-tests#Logging Tests#JSON Schema And Compatibility]]
def test_configure_logging_writes_to_file(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    configure_logging(log_file=log_file)
    logger = get_logger("test")
    logger.info("hello", extra={"foo": "bar"})
    data = json.loads(log_file.read_text().strip())
    assert data["message"] == "hello"
    assert data["event"] == "hello"
    assert data["foo"] == "bar"
    assert data["level"] == "INFO"
    assert data["logger"] == "test"
    assert data["timestamp"].endswith("Z")
    assert (
        datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")).tzinfo
        == timezone.utc
    )

    native = get_event_logger("native").bind(provider="protonvpn")
    native.info("checked %s", "vpn", service_name="vpn")
    logging.getLogger("third-party").warning(
        "received %s", "reply", extra={"status": 503}
    )
    records = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert len(records) == 3
    assert records[1]["message"] == "checked vpn"
    assert records[1]["provider"] == "protonvpn"
    assert records[1]["service_name"] == "vpn"
    assert records[2]["message"] == "received reply"
    assert records[2]["status"] == 503
    assert records[2]["level"] == "WARNING"
    for record in records:
        assert {"timestamp", "level", "logger", "event", "message"} <= record.keys()
        assert "_record" not in record
        assert "_from_structlog" not in record


# @lat: [[lat.md/logging-tests#Logging Tests#Quiet Defaults]]
def test_configure_logging_suppresses_logs(capfd) -> None:
    configure_logging()
    logger = get_logger("test")
    logger.info("quiet")
    logger.error("also quiet")
    get_event_logger("native").error("quiet_structured", service_name="vpn")
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# @lat: [[lat.md/logging-tests#Logging Tests#Secret Redaction]]
@pytest.mark.parametrize("native", [False, True])
def test_secrets_are_redacted_in_fields_messages_and_exceptions(
    tmp_path, capfd, native
):
    log_file = tmp_path / "secrets.log"
    configure_logging(log_file=log_file)
    secrets = [f"sensitive-{index}-value" for index in range(12)]
    url = f"socks5h://{secrets[0]}:{secrets[1]}@proxy.example:1080/path?api_key={secrets[2]}"
    message = f"Failed {url} password='{secrets[3]} with spaces' token={secrets[4]}"
    error = (
        f"Request {url} failed: Authorization: Bearer {secrets[5]} API_KEY={secrets[6]}"
    )

    class Detail:
        def __str__(self):
            return f"access_token={secrets[7]}"

        def __structlog__(self):
            return secrets[8]

    fields = {
        "password": {"value": secrets[9]},
        "nested": [{"apiKey": secrets[10]}, {"ACCESS_TOKEN": secrets[11]}],
        "url": url,
        "detail": Detail(),
        "payload": (f"token={secrets[4]}".encode(),),
        "safe": {"port": 1080, "reachable": False},
    }
    logger = get_event_logger("native") if native else get_logger("legacy")
    try:
        raise RuntimeError(error)
    except RuntimeError:
        if native:
            logger.exception(message, **fields)
        else:
            logger.exception(message, extra=fields)
    output = log_file.read_text()
    captured = capfd.readouterr()
    for secret in secrets:
        assert secret not in output + captured.out + captured.err
    record = json.loads(output)
    assert record["password"] == REDACTED
    assert record["nested"] == [{"apiKey": REDACTED}, {"ACCESS_TOKEN": REDACTED}]
    assert "proxy.example:1080" in record["url"]
    assert record["safe"] == {"port": 1080, "reachable": False}
    assert "RuntimeError" in record["exception"]
    assert record["exc_info"] == record["exception"]
    assert record["message"] == record["event"]


# @lat: [[lat.md/logging-tests#Logging Tests#Dynamic Levels And Reconfiguration]]
def test_dynamic_levels_and_reconfiguration(tmp_path):
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    native = get_event_logger("native")
    legacy = get_logger("legacy")
    configure_logging(log_file=first)
    old_handler = logging.getLogger().handlers[0]
    native.debug("hidden")
    set_log_level(logging.DEBUG)
    native.debug("debug_native")
    legacy.debug("debug_legacy")
    set_log_level(logging.ERROR)
    native.warning("hidden")
    legacy.warning("hidden")
    native.error("error_native")
    configure_logging(log_file=second)
    assert old_handler.stream is None
    legacy.info("one_legacy_record")
    native.info("one_native_record")
    assert [json.loads(line)["message"] for line in first.read_text().splitlines()] == [
        "debug_native",
        "debug_legacy",
        "error_native",
    ]
    assert [
        json.loads(line)["message"] for line in second.read_text().splitlines()
    ] == ["one_legacy_record", "one_native_record"]
    assert len(logging.getLogger().handlers) == 1


# @lat: [[lat.md/logging-tests#Logging Tests#Context Cleanup]]
def test_context_boundaries_restore_after_failure_and_cancellation(tmp_path):
    configure_logging(log_file=tmp_path / "context.log")
    structlog.contextvars.bind_contextvars(incident_id="stale")
    with pytest.raises(RuntimeError):
        with logging_context(clear=True, cycle_id="new"):
            assert structlog.contextvars.get_contextvars() == {"cycle_id": "new"}
            with logging_context(service_name="vpn", provider="protonvpn"):
                structlog.contextvars.bind_contextvars(temporary="discard")
                raise RuntimeError("failed")
    assert structlog.contextvars.get_contextvars() == {"incident_id": "stale"}

    async def cancelled_check():
        with pytest.raises(asyncio.CancelledError):
            with logging_context(service_name="cancelled"):
                raise asyncio.CancelledError()
        assert structlog.contextvars.get_contextvars() == {"incident_id": "stale"}

    asyncio.run(cancelled_check())
    configure_logging()
    assert structlog.contextvars.get_contextvars() == {}

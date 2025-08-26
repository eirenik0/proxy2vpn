from __future__ import annotations

import json
from pathlib import Path

from proxy2vpn.adapters.logging_utils import configure_logging, get_logger


def test_configure_logging_writes_to_file(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    configure_logging(log_file=log_file)
    logger = get_logger("test")
    logger.info("hello", extra={"foo": "bar"})
    data = json.loads(log_file.read_text().strip())
    assert data["message"] == "hello"
    assert data["foo"] == "bar"


def test_configure_logging_suppresses_logs(capfd) -> None:
    configure_logging()
    logger = get_logger("test")
    logger.info("quiet")
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""

"""Structured logging utilities for proxy2vpn."""

import json
import logging
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format logs as single line JSON."""

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - trivial
        log_record: dict[str, Any] = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        # Include extra fields passed to the logger
        for key, value in record.__dict__.items():
            if key not in {
                "levelname",
                "msg",
                "args",
                "name",
                "exc_info",
                "exc_text",
                "stack_info",
                "levelno",
                "pathname",
                "filename",
                "module",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
            }:
                log_record[key] = value
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_record, ensure_ascii=False)


def configure_logging(
    level: int = logging.INFO, log_file: str | Path | None = None
) -> None:
    """Configure root logger with JSON formatter.

    If ``log_file`` is provided, logs are written to that file. Otherwise, logs
    are suppressed so they do not interfere with console output.
    """

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    if log_file:
        handler: logging.Handler = logging.FileHandler(log_file)
        handler.setFormatter(JsonFormatter())
    else:
        handler = logging.NullHandler()
    root.addHandler(handler)


def set_log_level(level: int) -> None:
    """Dynamically change the logging level for the root logger."""

    root = logging.getLogger()
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a module level logger."""

    return logging.getLogger(name)

"""Structured logging utilities for proxy2vpn."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    """Format logs as single line JSON."""

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - trivial
        log_record: Dict[str, Any] = {
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


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with JSON formatter."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a module level logger."""

    return logging.getLogger(name)

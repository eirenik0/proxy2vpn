"""One redacted JSON pipeline for structlog and standard-library records."""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
import logging
from pathlib import Path
import re
from typing import Any

import structlog
from structlog.types import EventDict, Processor


REDACTED = "[REDACTED]"
_SECRET_NAMES = (
    "password",
    "passwd",
    "passphrase",
    "token",
    "apikey",
    "secret",
    "credential",
    "authorization",
    "privatekey",
    "accesskey",
)
_URL_CREDENTIALS = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s?#]+@")
_AUTHORIZATION = re.compile(r"(?i)(\b(?:bearer|basic)\s+)[^\s,;\"'}\]]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?[\w.-]*(?:password|passwd|passphrase|token|api[_-]?key|secret|"
    r"credential|authorization|private[_-]?key|access[_-]?key|\bpwd\b|\bpass\b)"
    r"[\w.-]*[\"']?\s*[:=]\s*)"
    r"(?:\[REDACTED\]|\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)"
)
_owned_handler: logging.Handler | None = None


def _secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in {"pass", "pwd"} or any(
        name in normalized for name in _SECRET_NAMES
    )


def _redact_text(value: str) -> str:
    value = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    value = _AUTHORIZATION.sub(r"\1[REDACTED]", value)
    return _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)


def _redact_value(value: Any, *, depth: int = 0) -> Any:
    # Normalize before JSONRenderer so custom objects cannot serialize secrets
    # through a later __structlog__ or repr fallback. Bound depth handles cycles.
    if depth > 20:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        return {
            _redact_text(str(key)): REDACTED
            if _secret_key(str(key))
            else _redact_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_value(item, depth=depth + 1) for item in value]
    if isinstance(value, bytes):
        return _redact_text(value.decode("utf-8", errors="replace"))
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


# @lat: [[lat.md/logging#Operational Logging#Secret Redaction]]
def redact_secrets(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """Redact nested fields and rendered text before JSON serialization."""
    return _redact_value(event_dict)


def _event_schema(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    # Preserve the legacy message and uppercase level contracts, while exposing
    # structlog's event and exception fields for new consumers.
    event_dict["level"] = event_dict["level"].upper()
    event_dict["message"] = event_dict["event"]
    if "exception" in event_dict:
        event_dict["exc_info"] = event_dict["exception"]
    return event_dict


# @lat: [[lat.md/logging#Operational Logging#Context Boundaries]]
@contextmanager
def logging_context(
    *, clear: bool = False, **fields: Any
) -> Generator[None, None, None]:
    """Scope task-local context and restore it even on failure or cancellation."""
    previous = structlog.contextvars.get_contextvars()
    if clear:
        structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(**fields)
    try:
        yield
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(**previous)


# @lat: [[lat.md/logging#Operational Logging#Processor Pipeline]]
def configure_logging(
    level: int = logging.INFO, log_file: str | Path | None = None
) -> None:
    """Configure shared JSON file logs; keep console command output quiet."""
    global _owned_handler
    structlog.contextvars.clear_contextvars()
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *shared],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _event_schema,
            redact_secrets,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    handler: logging.Handler
    if log_file:
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatter)
    else:
        handler = logging.NullHandler()
    root = logging.getLogger()
    for old_handler in root.handlers[:]:
        root.removeHandler(old_handler)
    if _owned_handler is not None:
        _owned_handler.close()
    _owned_handler = handler
    root.setLevel(level)
    root.addHandler(handler)


def set_log_level(level: int) -> None:
    """Change the level for both logging APIs, including existing loggers."""
    logging.getLogger().setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Keep the stdlib API for existing callers, including extra= fields."""
    return logging.getLogger(name)


def get_event_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger for events with bound or keyword context."""
    return structlog.stdlib.get_logger(name)

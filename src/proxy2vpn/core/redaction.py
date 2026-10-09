"""Shared pattern redaction for logs and agent evidence."""

from collections.abc import Mapping
import re
from typing import Any

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
_AUTHORIZATION = re.compile(
    r"(?i)(\b(?:bearer|basic)\s+)(?:\[REDACTED\]|[^\s,;\"'}\]]+)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?[\w.-]*(?:password|passwd|passphrase|token|api[_-]?key|secret|"
    r"credential|authorization|private[_-]?key|access[_-]?key|\bpwd\b|\bpass\b)"
    r"[\w.-]*[\"']?\s*[:=]\s*)"
    r"(?:\[REDACTED\]|\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)"
)


def _secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in {"pass", "pwd"} or any(
        name in normalized for name in _SECRET_NAMES
    )


_PRIVATE_KEY = re.compile(
    r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----.*?-----END \1-----", re.DOTALL
)


def _redact_text(value: str) -> str:
    value = _PRIVATE_KEY.sub(REDACTED, value)
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

"""Bounded, copied diagnostic representations for storage and transmission."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import datetime
import json
import hashlib
import hmac
import tempfile
import os
import re
from pathlib import Path
from typing import Any, get_args
from urllib.parse import quote, quote_plus, unquote, urlsplit

from filelock import FileLock
from ruamel.yaml import YAML

from proxy2vpn.agent.interpolation import dotenv_variables, interpolate
from proxy2vpn.agent.models import DaemonMode, IncidentSeverity, IncidentStatus
from proxy2vpn.core import config
from proxy2vpn.core.security import CLIENT_AUTH_FILE
from proxy2vpn.core.redaction import REDACTED, _redact_text, _secret_key

# Raw log lines are never retained. Only these literal diagnostic facts survive.
_LOG_FACTS = (
    "AUTH_FAILED",
    "authentication failure",
    "TLS handshake failed",
    "RTNETLINK answers: File exists",
    "Linux route add command failed",
    "Network unreachable",
    "DNS resolution failed",
    "connection refused",
    "connection timed out",
    "certificate verification failed",
)
_CHECKS = frozenset(
    (
        "auth_failure",
        "config_error",
        "tls_error",
        "route_error",
        "dns_error",
        "connectivity",
        "control_api",
        "dns_status",
        "updater_status",
        "port_forward",
        "logs",
        "authentication",
        "latency",
        "container",
        "egress_ip",
        "egress_identity",
        "configuration",
        "unknown",
    )
)
# Only recognized typed/control facts bypass credential substring matching.
_CLASSIFICATIONS = {
    "status": frozenset(get_args(IncidentStatus)),
    "severity": frozenset(get_args(IncidentSeverity)),
    "daemon_mode": frozenset(get_args(DaemonMode)),
    "source": frozenset({"gluetun", "external_proxy"}),
    "llm_mode": frozenset({"disabled", "openai"}),
    "container_status": frozenset(
        {
            "created",
            "running",
            "paused",
            "restarting",
            "removing",
            "exited",
            "dead",
            "missing",
            "unknown",
            "not_applicable",
        }
    ),
    "health_class": frozenset(
        {
            "healthy",
            "auth_config",
            "connectivity",
            "container_stopped",
            "degraded",
            "assessment_failed",
        }
    ),
    "failing_checks": _CHECKS,
    "check": _CHECKS,
    "active_cycle_phase": frozenset(
        {"cleanup", "assessing_services", "processing_services"}
    ),
}
_ACTIONS = frozenset(
    {
        "restart_tunnel",
        "restore",
        "rotate",
        "investigate",
        "replace_endpoint",
        "replace_session",
        "request_different_exit_ip",
    }
)
_RESULTS = frozenset(
    {
        "success",
        "failed",
        "cancelled",
        "interrupted",
        "accepted",
        "rejected",
        "unsupported",
        "unknown",
    }
)
for _field in ("action", "last_action", "recommended_action"):
    _CLASSIFICATIONS[_field] = _ACTIONS
for _field in ("result", "last_action_result", "runtime_request_result", "observation"):
    _CLASSIFICATIONS[_field] = _RESULTS
_INCIDENT_TYPES = frozenset(
    {
        "auth_config_failure",
        "endpoint_unhealthy",
        "rotation_exhausted",
        "rotation_required",
        "profile_auth_config_failure",
        "provider_outage_suspected",
    }
)
for _field in ("type", "incident_type"):
    _CLASSIFICATIONS[_field] = _INCIDENT_TYPES
_CLASSIFICATIONS["trigger"] = frozenset(
    {
        "manual",
        "manual_approval",
        "automatic_remediation",
        "auto_rotation",
        "first_unhealthy_cycle",
        "isolated_auth_failure",
    }
)
_CLASSIFICATIONS["purpose"] = frozenset(
    {"repair_connectivity", "request_different_exit_ip"}
)
_IDENTITY_FIELDS = frozenset(
    "service_name requested_service_name final_service_name active_cycle_service_name healthy_shared_profile_peers auth_config_shared_profile_peers other_unhealthy_shared_profile_peers shared_profile_peer_probe_failures".split()
)
_IDENTITY_ALIAS = re.compile(r"\[SERVICE:v2:[0-9a-f]{64}\]")
_LEGACY_IDENTITY_ALIAS = re.compile(r"\[SERVICE:[0-9a-f]{64}\]")

_TIMESTAMPS = frozenset(
    "started_at active_cycle_started_at last_loop_at last_progress_at degraded_since last_check_at ts created_at updated_at approved_at resolved_at investigated_at".split()
)

# Unknown payload keys are omitted rather than trusting their serialized value.
_FIELDS = frozenset(
    """status services actions compose_path daemon_mode started_at
active_cycle_started_at active_cycle_phase active_cycle_service_name last_loop_at
last_progress_at interval_seconds service_count unhealthy_count last_error llm_mode
service_name container_status health_score consecutive_failures degraded_since
last_check_at source capabilities health_class failing_checks current_egress_ip
authentication connectivity latency_ms last_action last_action_result ts action
trigger result details id type severity created_at updated_at failure_count summary
recommended_action approval_required approved_at resolved_at human_explanation
investigation findings log_evidence action_plan investigated_at fallback_summary
issues recent_actions incident_id incident_type incident_summary provider location
profile_name profile_env_file control_api_reachable profile_validation_errors
healthy_shared_profile_peers auth_config_shared_profile_peers
other_unhealthy_shared_profile_peers shared_profile_peer_probe_failures check passed
persistent message recommendation inspect_runtime collect_logs restart_tunnel
restore replace_endpoint replace_session request_different_exit_ip
recreate_service rotate_location requested_service_name final_service_name
old_location new_location candidate_locations attempted_locations errors error
cancelled incident_id control_port profile runtime_request_result observation
observation_error exit_ip_changed previous_exit_ip current_exit_ip old_exit_ip new_exit_ip request_result
rotation_result reason message_count purpose""".split()
)


def _env_mappings(value: Any, depth: int = 0):
    """Discover env-file references in services and shared Compose profile anchors."""
    if depth > 20:
        return
    if isinstance(value, Mapping):
        if "env_file" in value:
            yield value
        for item in value.values():
            yield from _env_mappings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _env_mappings(item, depth + 1)


def _identity_key(root: Path) -> bytes:
    """Keep alias HMAC keys local, private, and stable across processes."""
    directory = root / ".proxy2vpn-agent"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "identity.key"
    with FileLock(str(directory / "identity.lock")):
        if path.exists():
            os.chmod(path, 0o600)
            key = path.read_bytes()
            if len(key) != 32:
                raise ValueError("Invalid evidence identity key")
            return key
        key = os.urandom(32)
        fd, temporary = tempfile.mkstemp(prefix="identity.", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).replace(path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return key


class EvidenceSanitizer:
    """Mask configured credentials and project untrusted evidence onto safe fields."""

    def __init__(
        self, secrets: list[str] | None = None, *, identity_key: bytes | None = None
    ) -> None:
        self._values: set[str] = set()
        self._identity_names: dict[str, str] = {}
        self._legacy_identity_names: dict[str, str] = {}
        self._identity_key = (
            identity_key if identity_key is not None else os.urandom(32)
        )
        self._interpolation_variables: dict[str, str] = dict(os.environ)
        for secret in secrets or []:
            self.add_secret(secret)

    def add_secret(self, secret: str) -> None:
        if not secret or secret in {REDACTED, "[TRUNCATED]"}:
            return
        # Explicitly supported one-layer encodings; no heuristic decoding of text.
        raw = secret.encode()
        self._values.update(
            (
                secret,
                quote(secret, safe=""),
                quote_plus(secret),
                base64.b64encode(raw).decode(),
                base64.urlsafe_b64encode(raw).decode(),
                json.dumps(secret, ensure_ascii=True)[1:-1],
            )
        )

    def text(self, value: str) -> str:
        secrets = sorted(self._values, key=len, reverse=True)
        # Full marker-containing credentials must win over a standalone marker.
        # Other credentials (including marker fragments) cannot consume markers.
        alternatives = (
            [re.escape(secret) for secret in secrets if REDACTED in secret]
            + [re.escape(REDACTED)]
            + [re.escape(secret) for secret in secrets if REDACTED not in secret]
        )
        matcher = re.compile("|".join(alternatives))
        while True:
            result = _redact_text(matcher.sub(lambda _: REDACTED, value))
            if len(result) > 2048:
                result = result[:2048]
                for length in range(1, len(REDACTED)):
                    if result.endswith(REDACTED[:length]):
                        result = result[:-length]
                        break
            if result == value:
                return result
            # Replacement can create a marker-containing configured credential.
            # Further passes consume surrounding text or collapse multiple markers;
            # standalone markers remain intact, so bounded output converges.
            value = result

    def collect_environment(self) -> None:
        # Shell identity/location variables are metadata, not credential aliases.
        self.collect(
            {
                key: value
                for key, value in os.environ.items()
                if key.upper() not in {"USER", "USERNAME", "PWD", "OLDPWD"}
            }
        )

    def collect(
        self, value: Any, *, depth: int = 0, interpolate_values: bool = False
    ) -> None:
        if depth > 20:
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in {"username_env", "password_env"} and isinstance(item, str):
                    credential = os.environ.get(item)
                    if credential:
                        self.add_secret(credential)
                    continue
                if (
                    _secret_key(str(key))
                    or str(key).lower() in {"username", "user"}
                    or str(key).upper().endswith(("_USER", "_USERNAME"))
                ) and isinstance(item, (str, int, float, bool)):
                    self.add_secret(str(item))
                    if interpolate_values and isinstance(item, str):
                        self.add_secret(
                            interpolate(item, self._interpolation_variables)
                        )
                self.collect(
                    item, depth=depth + 1, interpolate_values=interpolate_values
                )
        elif isinstance(value, list):
            for item in value:
                self.collect(
                    item, depth=depth + 1, interpolate_values=interpolate_values
                )
        elif isinstance(value, str):
            if interpolate_values:
                value = interpolate(value, self._interpolation_variables)
            if "=" in value:
                key, item = value.split("=", 1)
                if (
                    _secret_key(key)
                    or key.lower() in {"username", "user"}
                    or key.upper().endswith(("_USER", "_USERNAME"))
                ):
                    self.add_secret(item)
                    self.add_secret(item.strip().strip("\"'"))

            if "://" in value:
                try:
                    url = urlsplit(value)
                    for part in (url.username, url.password):
                        if part:
                            self.add_secret(unquote(part))
                    if url.username and url.password:
                        self.add_secret(
                            f"{unquote(url.username)}:{unquote(url.password)}"
                        )
                except ValueError:
                    pass

    @classmethod
    def from_compose(
        cls, compose_file: Path, inventory_file: str = "external-proxies.json"
    ) -> EvidenceSanitizer:
        root = compose_file.parent
        sanitizer = cls(identity_key=_identity_key(root))
        sanitizer.collect_environment()
        root_variables = dotenv_variables(root / ".env", os.environ)
        sanitizer._interpolation_variables = {**root_variables, **os.environ}
        sanitizer.collect(root_variables)
        inventory_path = Path(inventory_file).expanduser()
        if not inventory_path.is_absolute():
            inventory_path = root / inventory_path
        json_paths = {inventory_path, root / CLIENT_AUTH_FILE}
        paths = {
            compose_file,
            root / ".env",
            root / config.CONTROL_AUTH_CONFIG_FILE,
            *json_paths,
        }
        paths.update((root / "profiles").glob("*.env"))
        raw_paths: set[Path] = set()
        standard_paths: set[Path] = set()
        if compose_file.exists():
            data = YAML(typ="safe").load(compose_file.read_text()) or {}
            sanitizer.collect(data, interpolate_values=True)
            for name in data.get("services") or {}:
                if isinstance(name, str):
                    sanitizer.register_identity(name)
            for service in _env_mappings(data):
                env_files = service.get("env_file", [])
                if isinstance(env_files, (str, dict)):
                    env_files = [env_files]
                for item in env_files:
                    path = item.get("path") if isinstance(item, dict) else item
                    if path:
                        resolved = root / interpolate(
                            path, sanitizer._interpolation_variables
                        )
                        paths.add(resolved)
                        if (
                            isinstance(item, dict)
                            and interpolate(
                                str(item.get("format", "")),
                                sanitizer._interpolation_variables,
                            )
                            == "raw"
                        ):
                            raw_paths.add(resolved)
                        else:
                            standard_paths.add(resolved)
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text()
            if path == compose_file:
                continue
            if path in json_paths:
                data = json.loads(text)
                sanitizer.collect(data)
                if path == inventory_path and isinstance(data, dict):
                    for endpoint in data.get("endpoints", []):
                        if isinstance(endpoint, dict) and isinstance(
                            endpoint.get("id"), str
                        ):
                            sanitizer.register_identity(endpoint["id"])
            else:
                sanitizer.collect(text.splitlines())
                if path != root / config.CONTROL_AUTH_CONFIG_FILE:
                    if path in raw_paths:
                        sanitizer.collect(
                            dotenv_variables(
                                path, sanitizer._interpolation_variables, raw=True
                            )
                        )
                    if path not in raw_paths or path in standard_paths:
                        sanitizer.collect(
                            dotenv_variables(path, sanitizer._interpolation_variables)
                        )
        return sanitizer

    def identity_alias(self, name: str) -> str:
        digest = hmac.new(self._identity_key, name.encode(), hashlib.sha256).hexdigest()
        return "[SERVICE:v2:" + digest + "]"

    def register_identity(self, name: str) -> None:
        self._identity_names[self.identity_alias(name)] = name
        legacy = "[SERVICE:" + hashlib.sha256(name.encode()).hexdigest() + "]"
        self._legacy_identity_names[legacy] = name

    def restore_identities(self, value: Any, *, key: str = "") -> Any:
        """Restore only configured identities for in-memory recovery decisions."""
        if isinstance(value, Mapping):
            return {
                name: self.restore_identities(item, key=name)
                for name, item in value.items()
            }
        if isinstance(value, list):
            return [self.restore_identities(item, key=key) for item in value]
        if isinstance(value, str) and key in _IDENTITY_FIELDS:
            return self._identity_names.get(value, value)
        return value

    # @lat: [[agent#Evidence Secrecy Contract]]
    def sanitize(self, value: Any, *, key: str = "", depth: int = 0) -> Any:
        if depth > 20:
            return "[TRUNCATED]"
        if _secret_key(key):
            return REDACTED
        if isinstance(value, Mapping):
            if key == "issues":
                check = value.get("check", "unknown")
                check = (
                    check if isinstance(check, str) and check in _CHECKS else "unknown"
                )
                return {
                    "check": check,
                    "passed": value.get("passed")
                    if isinstance(value.get("passed"), bool)
                    else None,
                    "persistent": bool(value.get("persistent", False)),
                    "message": "External endpoint is no longer configured."
                    if check == "configuration"
                    and value.get("message")
                    == "External endpoint is no longer configured."
                    else f"Diagnostic check: {check}",
                    "recommendation": "Review diagnostic classification",
                }
            return {
                name: self.sanitize(item, key=name, depth=depth + 1)
                for name, item in value.items()
                if isinstance(name, str) and name in _FIELDS
            }
        if isinstance(value, (list, tuple)):
            if key == "log_evidence":
                facts = [
                    fact
                    for fact in _LOG_FACTS
                    if any(fact.lower() in str(line).lower() for line in value)
                ]
                return [fact for fact in facts if self.text(fact) == fact][:6]
            return [
                self.sanitize(item, key=key, depth=depth + 1)
                for item in (value if key in {"services", "actions"} else value[:64])
            ]
        if isinstance(value, str):
            if key in _IDENTITY_FIELDS:
                if _LEGACY_IDENTITY_ALIAS.fullmatch(value):
                    name = self._legacy_identity_names.get(value)
                    return self.identity_alias(name) if name is not None else REDACTED
                if _IDENTITY_ALIAS.fullmatch(value) or self.text(value) == value:
                    return value
                return self.identity_alias(value)
            if key in {"id", "incident_id"} and re.fullmatch(r"[0-9a-f]{12}", value):
                return value
            if value in _CLASSIFICATIONS.get(key, ()):
                return value
            if key in _TIMESTAMPS:
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    pass
                else:
                    return value
            if key in {"error", "errors", "observation_error", "last_error"}:
                return (
                    value
                    if value == "Cycle cancelled"
                    else "Diagnostic error (raw text omitted)"
                )
            return self.text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return "[OMITTED]"

    def model(self, value: Any) -> Any:
        data = self.sanitize(value.model_dump(mode="json"))
        if "fallback_summary" in data and data.get("issues"):
            checks = ", ".join(issue["check"] for issue in data["issues"])
            data["fallback_summary"] = self.text(
                f"{data['service_name']}: diagnostic checks: {checks}"
            )
        return type(value).model_validate(data)

"""Read-only Prometheus exporter for producer-owned monitoring evidence."""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import AgentState
from proxy2vpn.agent.retention import as_utc, parse_history
from proxy2vpn.core import config
from proxy2vpn.core.private_storage import managed_directory, private_file


class Exposition:
    def __init__(self):
        self.lines = []
        self.families = set()

    def emit(
        self,
        name,
        value,
        *,
        kind="gauge",
        help="Persisted watchdog evidence.",
        **labels,
    ):
        name = "proxy2vpn_" + name
        if name not in self.families:
            self.lines.extend([f"# HELP {name} {help}", f"# TYPE {name} {kind}"])
            self.families.add(name)
        suffix = (
            "{"
            + ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
            + "}"
            if labels
            else ""
        )
        self.lines.append(f"{name}{suffix} {float(value)}")

    def text(self):
        return "\n".join(self.lines) + "\n"


# @lat: [[metrics#Watchdog Metrics#Read Only Collection]]
def collect_metrics(
    compose_file: Path, settings: AgentSettings, *, now=None, freshness_seconds=120
):
    """Contain parsing and rendering failures without exposing partial success."""
    try:
        return _collect_metrics(
            compose_file, settings, now=now, freshness_seconds=freshness_seconds
        )
    except Exception:
        output = Exposition()
        output.emit("exporter_collection_success", 0)
        return output.text()


def _collect_metrics(
    compose_file: Path, settings: AgentSettings, *, now=None, freshness_seconds=120
):
    """Collect an atomic validated snapshot, without constructing a writable store."""
    output = Exposition()
    now = as_utc(now or datetime.now(timezone.utc))
    root = config.resolve_compose_root(compose_file.expanduser().resolve())
    directory = root / settings.state_dirname
    try:
        managed_directory(directory, settings.storage_artifact_names, repair=False)
        journal = directory / "transaction.json"
        state_path = directory / settings.state_file
        history_path = directory / settings.incidents_file
        if journal.exists():
            raise ValueError("pending transaction")
        private_file(state_path, repair=False)
        private_file(history_path, repair=False)
        if not state_path.exists():
            output.emit("state_present", 0)
            output.emit("exporter_collection_success", 1)
            return output.text()
        raw = state_path.read_bytes()
        state = AgentState.model_validate_json(raw)
        records = parse_history(
            history_path.read_text() if history_path.exists() else ""
        )
        if journal.exists() or state_path.read_bytes() != raw:
            raise ValueError("concurrent state change")
        metrics = state.metrics
        endpoint_ids = [row.endpoint_id for row in metrics.endpoint_observations]
        recovery_keys = [
            (row.source, row.recovery_action, row.recovery_result)
            for row in metrics.recovery_counters
        ]
        cycle_keys = [row.cycle_outcome for row in metrics.cycle_counters]
        if (
            len(set(endpoint_ids)) != len(endpoint_ids)
            or len(set(recovery_keys)) != len(recovery_keys)
            or len(set(cycle_keys)) != len(cycle_keys)
        ):
            raise ValueError("Duplicate metric series")
        if len(metrics.endpoint_observations) > 1000:
            raise ValueError("inventory bound exceeded")
    except Exception:
        output.emit("exporter_collection_success", 0)
        return output.text()
    output.emit("exporter_collection_success", 1)
    output.emit("state_present", 1)
    initialized = (
        metrics.initialized_at is not None and metrics.deployment_id is not None
    )
    output.emit("metrics_initialized", initialized)
    if not initialized:
        return output.text()
    labels = {"deployment": metrics.deployment_id}
    output.emit(
        "cycle_attempts_total", metrics.cycles_attempted, kind="counter", **labels
    )
    output.emit(
        "monitoring_resets_total", metrics.monitoring_resets, kind="counter", **labels
    )
    output.emit("cycle_in_progress", metrics.cycle_run_id is not None, **labels)
    fresh_cycle = (
        metrics.cycle_run_id is None
        and metrics.cycle_outcome == "success"
        and metrics.last_success_at is not None
        and 0
        <= (now - as_utc(metrics.last_success_at)).total_seconds()
        <= freshness_seconds
    )
    output.emit("cycle_success_fresh", fresh_cycle, **labels)
    for field in ("last_attempt_at", "last_success_at"):
        stamp = getattr(metrics, field)
        if stamp is not None:
            output.emit(
                "cycle_" + field.removesuffix("_at") + "_timestamp_seconds",
                as_utc(stamp).timestamp(),
                **labels,
            )
    for counter in metrics.cycle_counters:
        output.emit(
            "cycle_outcomes_total",
            counter.metric_count,
            kind="counter",
            outcome=counter.cycle_outcome,
            **labels,
        )
    for counter in metrics.recovery_counters:
        output.emit(
            "recovery_action_outcomes_total",
            counter.metric_count,
            kind="counter",
            help="Committed recovery request/audit outcomes; not physical attempts or recovered endpoints.",
            source=counter.source,
            action=counter.recovery_action,
            result=counter.recovery_result,
            **labels,
        )
    for row in metrics.endpoint_observations:
        endpoint_labels = {**labels, "endpoint": row.endpoint_id, "source": row.source}
        output.emit(
            "endpoint_observation_known", row.observed_at is not None, **endpoint_labels
        )
        output.emit(
            "endpoint_observation_complete", row.observation_complete, **endpoint_labels
        )
        fresh = (
            fresh_cycle
            and row.observation_complete
            and row.observed_at is not None
            and 0
            <= (now - as_utc(row.observed_at)).total_seconds()
            <= freshness_seconds
        )
        output.emit("endpoint_observation_fresh", bool(fresh), **endpoint_labels)
        if row.observed_at is not None:
            output.emit(
                "endpoint_observation_timestamp_seconds",
                as_utc(row.observed_at).timestamp(),
                **endpoint_labels,
            )
            for field in (
                "available",
                "authentication",
                "connectivity",
                "health_ok",
                "duration_seconds",
                "reported_latency_seconds",
            ):
                value = getattr(row, field)
                output.emit(
                    "endpoint_" + field + "_known", value is not None, **endpoint_labels
                )
                if value is not None:
                    output.emit("endpoint_" + field, value, **endpoint_labels)
    latest = {item.id: item for item in records}
    for source in ("gluetun", "external_proxy"):
        output.emit(
            "open_incidents",
            sum(
                item.status == "open" and item.source == source
                for item in latest.values()
            ),
            source=source,
            **labels,
        )
        output.emit(
            "recovery_blocked_incidents",
            sum(
                item.status in {"open", "approved"}
                and item.type == "rotation_exhausted"
                and item.source == source
                for item in latest.values()
            ),
            source=source,
            **labels,
        )
    return output.text()


def serve_metrics(
    compose_file,
    settings,
    *,
    bind_address="127.0.0.1",
    port=9109,
    freshness_seconds=120,
):
    address = ipaddress.ip_address(bind_address)
    if (
        address.version != 4
        or address.is_unspecified
        or not (address.is_loopback or address.is_private)
    ):
        raise ValueError(
            "Metrics must bind to an explicit loopback or private IPv4 address"
        )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = collect_metrics(
                compose_file, settings, freshness_seconds=freshness_seconds
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer((bind_address, port), Handler) as server:
        server.serve_forever()

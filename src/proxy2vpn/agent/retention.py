"""Incident retention planning and a counts-only, nonmutating preview."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import AgentIncident
from proxy2vpn.core import config
from proxy2vpn.core.private_storage import StorageError, managed_directory

if TYPE_CHECKING:
    from proxy2vpn.agent.evidence import EvidenceSanitizer


def as_utc(value: datetime) -> datetime:
    """Legacy naive timestamps represent UTC, not the machine's local timezone."""
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def incident_age(incident: AgentIncident, now: datetime) -> float:
    return (as_utc(now) - as_utc(incident.updated_at)).total_seconds()


def parse_history(
    text: str, sanitizer: EvidenceSanitizer | None = None
) -> list[AgentIncident]:
    """Reject corruption except an invalid, unterminated final JSON fragment."""
    records = []
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not line.endswith("\n"):
                break
            raise StorageError(
                f"Corrupt agent incident history at record {index + 1}; repair is required"
            ) from None
        try:
            records.append(
                AgentIncident.model_validate(
                    sanitizer.sanitize(data) if sanitizer is not None else data
                )
            )
        except ValueError:
            raise StorageError(
                f"Invalid agent incident history at record {index + 1}; repair is required"
            ) from None
    return records


class CompactionReport(BaseModel):
    """Safe operator counts; never include incident narratives or identities."""

    dry_run: bool
    evaluated_at: datetime
    retention_seconds: int
    records_before: int
    records_after: int
    superseded_versions: int
    expired_incidents: int
    retained_by_status: dict[str, int]


def plan_compaction(
    records: list[AgentIncident],
    settings: AgentSettings,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> tuple[CompactionReport, list[AgentIncident]]:
    now = as_utc(now or datetime.now(timezone.utc))
    # Append order is authoritative even if the wall clock moves backward.
    latest = {item.id: item for item in records}
    kept = []
    counts = {
        status: 0 for status in ("open", "approved", "failed", "resolved", "dismissed")
    }
    for item in latest.values():
        age = incident_age(item, now)
        expired = (
            item.status in {"resolved", "dismissed"}
            and age > settings.incident_retention_seconds
            and (
                item.status != "dismissed"
                or age > max(0, settings.incident_cooldown_seconds)
            )
        )
        if not expired:
            kept.append(item)
            counts[item.status] += 1
    return CompactionReport(
        dry_run=dry_run,
        evaluated_at=now,
        retention_seconds=settings.incident_retention_seconds,
        records_before=len(records),
        records_after=len(kept),
        superseded_versions=len(records) - len(latest),
        expired_incidents=len(latest) - len(kept),
        retained_by_status=counts,
    ), kept


def preview_compaction(
    compose_file: Path, settings: AgentSettings, *, now: datetime | None = None
) -> CompactionReport:
    """Estimate from an atomic snapshot without creating storage, keys or locks."""
    root = config.resolve_compose_root(compose_file.expanduser().resolve())
    directory = root / settings.state_dirname
    managed_directory(directory, settings.storage_artifact_names, repair=False)
    journal = directory / "transaction.json"
    history = directory / settings.incidents_file
    if journal.exists():
        raise StorageError(
            "Pending agent storage recovery; run a normal storage operation first"
        )
    text = history.read_text() if history.exists() else ""
    if journal.exists():
        raise StorageError(
            "Pending agent storage recovery; run a normal storage operation first"
        )
    report, _kept = plan_compaction(
        parse_history(text), settings, now=now, dry_run=True
    )
    return report

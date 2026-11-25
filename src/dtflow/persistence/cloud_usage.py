"""Read-only cloud usage evidence and the remembered reset timezone; both credential-free."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dtflow.core.models import ValidationError
from dtflow.persistence.gallery import reader
from dtflow.persistence.store import PersistenceError
from dtflow.services.artifacts import atomic_write
from dtflow.services.cloud_usage import CLOUD_BACKEND_ID, DEFAULT_TIMEZONE, UsageRecord

FILENAME = "usage_preferences.json"
SCHEMA_VERSION = 1
MAX_BYTES = 4096

_QUERY = """
SELECT a.updated_at_utc AS saved, json_array_length(a.artifacts_json) - 1 AS outputs,
       u.credential_id AS credential_id, u.key_hint AS hint
FROM attempts a LEFT JOIN cloud_usage u ON u.attempt_id = a.attempt_id
WHERE a.backend_id = ? AND a.state = 'succeeded' AND a.remote_outcome = 'succeeded'
  AND a.updated_at_utc >= ? AND a.updated_at_utc < ? AND json_valid(a.artifacts_json)
"""
_LEGACY_QUERY = (
    _QUERY.replace("u.credential_id", "NULL")
    .replace("u.key_hint", "NULL")
    .replace("LEFT JOIN cloud_usage u ON u.attempt_id = a.attempt_id", "")
)


def read_usage_records(state: Path, now: datetime, *, days: int = 3) -> tuple[UsageRecord, ...]:
    """Saved cloud attempts around ``now`` (a margin of whole UTC days covers any timezone's local day).

    Dates are compared as ISO text, so the window is padded and the exact boundary is applied by the caller.
    """
    since = (now - timedelta(days=days)).astimezone(UTC).strftime("%Y-%m-%dT00:00:00")
    until = (now + timedelta(days=days)).astimezone(UTC).strftime("%Y-%m-%dT00:00:00")
    stage = "opening saved history"
    try:
        # The advisory counter runs off the Qt thread and may scan a legacy, unindexed store.
        # Keep this startup read bounded without relaxing the gallery's short budget.
        with reader(state, include_recycle=False, read_budget_seconds=15) as db:
            if db is None:
                return ()
            stage = "checking key attribution"
            has_usage = db.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_usage'").fetchone() is not None
            stage = "reading completed outputs"
            rows = db.execute(_QUERY if has_usage else _LEGACY_QUERY, (CLOUD_BACKEND_ID, since, until)).fetchall()
    except PersistenceError as exc:
        raise PersistenceError(f"Local usage failed while {stage}: {exc}") from exc
    records = []
    for row in rows:
        try:
            saved = datetime.fromisoformat(row["saved"])
        except (TypeError, ValueError):
            continue
        if saved.tzinfo is None:
            saved = saved.replace(tzinfo=UTC)
        if type(row["outputs"]) is int and row["outputs"] > 0:
            records.append(UsageRecord(saved.astimezone(UTC), row["outputs"], row["credential_id"], row["hint"]))
    return tuple(records)


def _read_preferences(path: Path) -> dict | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_BYTES:
        raise ValidationError("Usage preferences exceed the size limit.")
    value = json.loads(raw)
    version = value.get("schema_version") if isinstance(value, dict) else None
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValidationError("Unsupported usage preferences schema.")
    return value


def load_timezone(state: Path) -> str:
    try:
        value = _read_preferences(Path(state) / FILENAME) or {}
    except (OSError, ValueError, RecursionError):
        return DEFAULT_TIMEZONE
    zone = value.get("timezone")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError):
        return DEFAULT_TIMEZONE
    return zone


def save_timezone(state: Path, zone: str) -> None:
    """Remember the reset timezone; an unreadable or newer file is left unchanged."""
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError):
        raise ValidationError("Unknown timezone.") from None
    path = Path(state) / FILENAME
    _read_preferences(path)
    Path(state).mkdir(parents=True, exist_ok=True)
    atomic_write(
        path, (json.dumps({"schema_version": SCHEMA_VERSION, "timezone": zone}, indent=2) + "\n").encode("utf-8")
    )

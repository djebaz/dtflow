"""Read-only cloud usage evidence and the remembered reset timezone; both credential-free."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dtflow.core.models import ValidationError
from dtflow.persistence.gallery import reader
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
    with reader(state) as db:
        if db is None:
            return ()
        has_usage = db.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_usage'").fetchone() is not None
        rows = db.execute(_QUERY if has_usage else _LEGACY_QUERY, (CLOUD_BACKEND_ID, since, until)).fetchall()
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


def load_timezone(state: Path) -> str:
    try:
        raw = (Path(state) / FILENAME).read_bytes()[: MAX_BYTES + 1]
        value = json.loads(raw) if len(raw) <= MAX_BYTES else None
    except FileNotFoundError:
        return DEFAULT_TIMEZONE
    except (OSError, ValueError):
        return DEFAULT_TIMEZONE
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
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
    if path.exists():
        try:
            existing = json.loads(path.read_bytes()[: MAX_BYTES + 1])
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict) and type(existing.get("schema_version")) is int:
            if existing["schema_version"] > SCHEMA_VERSION:
                raise ValidationError("Newer usage preferences; file left unchanged.")
    Path(state).mkdir(parents=True, exist_ok=True)
    atomic_write(
        path, (json.dumps({"schema_version": SCHEMA_VERSION, "timezone": zone}, indent=2) + "\n").encode("utf-8")
    )

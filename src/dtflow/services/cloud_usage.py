"""Local DT+ completed-image counts per key and calendar day: an estimate from saved outputs, never provider usage.

Every count derives from durable attempt evidence (UTC saved time, output count, opaque key identity), so a different
reset timezone is a pure recomputation: no provider call, no generation and no rewrite of saved records.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

DEFAULT_TIMEZONE = "Europe/Paris"
AMBER_FROM = 401  # owner working assumption (400/500), visual guidance only, never a block
RED_FROM = 500
HINT_LENGTH = 4
CLOUD_BACKEND_ID = "drawthings-cloud"

# The real DT+ key format is not documented in this repository. A hint is therefore derived conservatively: an optional
# short alphabetic prefix ending in "-" or "_" is skipped, and the remainder must be a long purely alphanumeric run.
# Anything else gets no excerpt and the caller shows a neutral numbered label instead.
_PREFIX = re.compile(r"[A-Za-z]{2,10}[-_]")
_VARIABLE = re.compile(r"[A-Za-z0-9]{12,}")


def key_identity(secret: str) -> tuple[str, str | None]:
    """Opaque stable identity plus an optional four-character hint; neither reveals more than the hint.

    The identity is a domain-separated SHA-256 digest of the stripped key. Hints are not unique identities.
    """
    value = secret.strip()
    digest = hashlib.sha256(b"dtflow.cloud-key.v1\0" + value.encode("utf-8")).hexdigest()[:32]
    match = _PREFIX.match(value)
    variable = value[match.end() :] if match else value
    hint = variable[:HINT_LENGTH] if _VARIABLE.fullmatch(variable) else None
    return digest, hint


def timezone_choices() -> tuple[tuple[str, str], ...]:
    """(label, IANA id) pairs: the named zones plus fixed UTC±N offsets (Etc/GMT signs are inverted by convention)."""
    names = sorted(
        name for name in available_timezones() if "/" in name and not name.startswith(("Etc/", "posix", "right"))
    )
    fixed = [("UTC", "UTC")]
    for hours in range(1, 15):
        fixed.append((f"UTC+{hours} (Etc/GMT-{hours})", f"Etc/GMT-{hours}"))
    for hours in range(1, 13):
        fixed.append((f"UTC−{hours} (Etc/GMT+{hours})", f"Etc/GMT+{hours}"))
    available = set(available_timezones())
    return tuple((label, zone) for label, zone in (*((n, n) for n in names), *fixed) if zone in available)


def zone_or_default(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")


def day_bounds(now: datetime, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC instants of the two consecutive local midnights around ``now`` (DST-aware: a day may be 23 or 25 hours)."""
    local = now.astimezone(zone)
    start_date = local.date()
    end_date = start_date + timedelta(days=1)
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=zone)
    end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One saved cloud attempt: UTC completion time, final-image count and opaque key attribution (or ``None``)."""

    saved_at: datetime
    outputs: int
    credential_id: str | None
    hint: str | None


@dataclass(frozen=True, slots=True)
class KeyCount:
    credential_id: str
    label: str
    count: int
    active: bool
    level: str  # "ok" | "warn" | "over"


@dataclass(frozen=True, slots=True)
class DayCounts:
    start: datetime
    end: datetime
    keys: tuple[KeyCount, ...]
    unattributed: int  # saved before key attribution existed or without a resolvable key


def level_for(count: int) -> str:
    return "over" if count >= RED_FROM else "warn" if count >= AMBER_FROM else "ok"


def count_day(
    records: tuple[UsageRecord, ...], now: datetime, zone: ZoneInfo, *, active_credential: str | None = None
) -> DayCounts:
    start, end = day_bounds(now, zone)
    totals: dict[str, int] = {}
    hints: dict[str, str | None] = {}
    unattributed = 0
    for record in sorted(records, key=lambda r: r.saved_at):
        if not start <= record.saved_at < end:
            continue
        if record.credential_id is None:
            unattributed += record.outputs
            continue
        totals[record.credential_id] = totals.get(record.credential_id, 0) + record.outputs
        hints.setdefault(record.credential_id, record.hint)
    labels = key_labels(hints)
    keys = tuple(
        KeyCount(cid, labels[cid], totals[cid], cid == active_credential, level_for(totals[cid]))
        for cid in sorted(totals, key=lambda c: (c != active_credential, -totals[c], c))
    )
    return DayCounts(start, end, keys, unattributed)


def key_labels(hints: dict[str, str | None]) -> dict[str, str]:
    """``Key 4F8K••••`` rows; shared hints and hint-less keys get a harmless ordinal, never more key characters."""
    shared: dict[str, int] = {}
    for hint in hints.values():
        if hint:
            shared[hint] = shared.get(hint, 0) + 1
    seen: dict[str | None, int] = {}
    labels = {}
    for cid in sorted(hints):
        hint = hints[cid]
        seen[hint] = seen.get(hint, 0) + 1
        ordinal = seen[hint]
        if hint:
            base = f"Key {hint}••••"
            labels[cid] = base if shared[hint] == 1 else f"{base} ({ordinal})"
        else:
            labels[cid] = f"Key (unidentified {ordinal})"
    return labels

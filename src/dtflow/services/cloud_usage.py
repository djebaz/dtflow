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

# Owner-reported DT+ key format (2026-11-04): ``dk_`` followed by 25 alphanumeric characters. A hint is taken only
# from a key of exactly that shape; any other shape gets no excerpt and a neutral numbered label, never guessed
# characters.
_KEY = re.compile(r"dk_([A-Za-z0-9]{25})")


def key_identity(secret: str) -> tuple[str, str | None]:
    """Opaque stable identity plus an optional four-character hint; neither reveals more than the hint.

    The identity is a domain-separated SHA-256 digest of the stripped key. Hints are not unique identities.
    """
    value = secret.strip()
    digest = hashlib.sha256(b"dtflow.cloud-key.v1\0" + value.encode("utf-8")).hexdigest()[:32]
    match = _KEY.fullmatch(value)
    hint = match[1][:HINT_LENGTH] if match else None
    return digest, hint


# Most populous city whose year-round or standard-time offset is that whole hour; a presentation hint only.
_BIGGEST_CITY = {
    -12: "Baker Island", -11: "Pago Pago", -10: "Honolulu", -9: "Anchorage", -8: "Los Angeles", -7: "Phoenix",
    -6: "Mexico City", -5: "Lima", -4: "Santiago", -3: "S\u00e3o Paulo", -2: "South Georgia", -1: "Praia",
    0: "London", 1: "Lagos", 2: "Cairo", 3: "Istanbul", 4: "Dubai", 5: "Karachi", 6: "Dhaka", 7: "Jakarta",
    8: "Shanghai", 9: "Tokyo", 10: "Sydney", 11: "Noum\u00e9a", 12: "Auckland", 13: "Nuku\u2019alofa",
    14: "Kiritimati",
}  # fmt: skip


def timezone_choices(now: datetime | None = None) -> tuple[tuple[str, str], ...]:
    """One (label, IANA id) per whole-hour offset from GMT-12 to GMT+14, each with its current local time.

    Fixed offsets use ``Etc/GMT`` zones (their sign is inverted by convention). The default Europe/Paris takes the
    entry matching its current offset (and adds its name) so its daylight-saving boundaries stay correct.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    available = available_timezones()
    paris = ZoneInfo(DEFAULT_TIMEZONE) if DEFAULT_TIMEZONE in available else None
    paris_hours = int(now.astimezone(paris).utcoffset().total_seconds() // 3600) if paris else None
    choices = []
    for hours in range(-12, 15):
        zone = "UTC" if hours == 0 else f"Etc/GMT{-hours:+d}"
        city = _BIGGEST_CITY[hours]
        if hours == paris_hours:
            zone, city = DEFAULT_TIMEZONE, f"{city}, Paris"
        if zone not in available:
            continue
        local = now.astimezone(ZoneInfo(zone))
        choices.append((f"GMT{hours:+03d} \u00b7 {local:%H:%M} \u00b7 {city}", zone))
    return tuple(choices)


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

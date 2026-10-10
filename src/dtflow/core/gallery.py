"""Immutable, provider-neutral saved-output queries and view data."""

from dataclasses import dataclass
from datetime import UTC, datetime

from dtflow.core.models import ValidationError

SORTS = ("newest", "oldest", "model", "size", "loras")


def utc_boundary(value: str | None) -> str | None:
    """Normalize an ISO date (UTC midnight) or offset-aware timestamp; reject ambiguous times."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if len(value) == 10:
            parsed = parsed.replace(tzinfo=UTC)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC).isoformat()
    except ValueError as exc:
        raise ValidationError("Date must be YYYY-MM-DD or an ISO timestamp with a timezone.") from exc


# Longer-side pixel bounds [low, high) per size class.
SIZE_CLASSES = {"": None, "small": (0, 400), "medium": (400, 600), "large": (600, 1200), "big": (1200, None)}


@dataclass(frozen=True, slots=True)
class GalleryQuery:
    prompt: str = ""
    negative: str = ""
    model: str = ""
    lora: str = ""
    since: str | None = None
    until: str | None = None
    ratio: str = ""
    width: int | None = None
    height: int | None = None
    size_class: str = ""
    sort: str = "newest"
    offset: int = 0
    limit: int = 100

    def __post_init__(self):
        for field in ("prompt", "negative", "model", "lora", "ratio"):
            if not isinstance(getattr(self, field), str) or len(getattr(self, field)) > 4096:
                raise ValidationError("Gallery filters must be text of at most 4096 characters.")
        if self.size_class not in SIZE_CLASSES:
            raise ValidationError("Unknown gallery size class.")
        if self.sort not in SORTS:
            raise ValidationError("Unknown gallery sort.")
        if type(self.limit) is not int or not 1 <= self.limit <= 200:
            raise ValidationError("Gallery page size must be in 1..200.")
        if type(self.offset) is not int or not 0 <= self.offset <= 2**31 - 1:
            raise ValidationError("Gallery offset must be a nonnegative integer.")
        for value in (self.width, self.height):
            if value is not None and (type(value) is not int or not 1 <= value <= 32768):
                raise ValidationError("Gallery dimensions must be integers in 1..32768.")
        if self.ratio not in ("", "square", "portrait", "landscape"):
            try:
                a, b = self.ratio.split(":")
                if not 1 <= int(a) <= 32768 or not 1 <= int(b) <= 32768:
                    raise ValueError
            except ValueError as exc:
                raise ValidationError("Ratio must be square, portrait, landscape or W:H.") from exc
        object.__setattr__(self, "since", utc_boundary(self.since))
        object.__setattr__(self, "until", utc_boundary(self.until))
        if self.since and self.until and self.since >= self.until:
            raise ValidationError("Date range end must follow its start (end is exclusive).")


@dataclass(frozen=True, slots=True)
class GalleryItem:
    identity: str
    attempt_id: str
    request_id: str
    path: str
    metadata_path: str
    saved_at_utc: str
    backend_id: str
    model: str
    prompt: str
    negative: str
    width: int | None
    height: int | None
    loras: tuple[tuple[str, float], ...]
    request_json: str
    file_size: int | None
    issue: str = ""


@dataclass(frozen=True, slots=True)
class GalleryPage:
    items: tuple[GalleryItem, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class GalleryDetail:
    item: GalleryItem
    metadata_json: str
    issue: str = ""

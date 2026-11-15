"""Live Civitai gallery listing and in-memory thumbnails (#466, epic #327).

Qt-free and blocking: call from a worker thread. Design: ``devdocs/plans/2026-11-15-civitai-downloads-design.md``.

- The listing is ``GET /api/v1/models`` with ``types=LORA`` and ``baseModels`` set to the Civitai aliases of the
  global supported-families file, so unsupported entries are not requested; every result is checked against the same
  file again. Paging uses the cursor (``page`` is not used). NSFW is always requested. No token is sent for listing.
- A card keeps only bounded fields the API returned. Thumbnails live in a small in-memory LRU and are never written to
  disk; only ``https`` addresses on ``civitai.com`` and its subdomains are fetched, without credentials.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

import httpx

from dtflow.services import civitai
from dtflow.services.lora_convert.registry import family_for_label, provider_labels, supported_families

PAGE_SIZE = 20  # small cursor batches for multi-version metadata; the view appends them as you scroll
SORTS = ("Most Downloaded", "Highest Rated", "Newest")  # the values the API documents for ``sort``
PERIODS = ("AllTime", "Year", "Month", "Week", "Day")  # the values the API documents for ``period``
MAX_QUERY = 100
THUMB_WIDTH = 320
MAX_THUMB_BYTES = 1024 * 1024
MAX_THUMB_ENTRIES = 300
MAX_THUMB_TOTAL = 48 * 1024 * 1024
THUMB_HOPS = 3
WITHHELD_MIN_CARDS = 12  # a page this large with only the lowest cover level suggests withheld mature content

# Civitai's ``nsfwLevel`` is a bit flag; the lowest value is general-audience content.
NSFW_LABELS = {2: "Soft", 4: "Mature", 8: "X", 16: "XXX"}

_WIDTH = re.compile(r"/(?:width=\d+|original=true)(?=/)")
_THUMBS = threading.Semaphore(4)


@dataclass(frozen=True, slots=True)
class VersionOption:
    """One downloadable version of a card's model."""

    version: civitai.CivitaiVersion
    family: str  # the converter family label

    @property
    def key(self) -> tuple[int, int | None]:
        """Exact Civitai version/file identity."""
        return (self.version.version_id, self.version.file.id)

    @property
    def label(self) -> str:
        size = self.version.file.size_bytes
        text = f"{size / 1024**2:.0f} MB" if size else ""
        return " · ".join(
            part for part in (
                self.version.version_name or "Version",
                self.version.base_model or "Unknown base",
                self.family,
                f"ID {self.version.version_id}",
                self.version.file.name,
                text,
            ) if part
        )


@dataclass(frozen=True, slots=True)
class GalleryCard:
    """One model with all known supported families and explicitly downloadable version/file choices."""

    model_id: int
    version_id: int
    name: str
    version_name: str
    creator: str | None
    downloads: int | None
    thumbs_up: int | None
    thumbs_down: int | None
    size_bytes: int | None
    base_model: str | None
    family: str  # family of the representative version
    families: tuple[str, ...]  # all supported families across listed versions, in first-seen order
    nsfw_level: int  # the cover image's level (1 = general audience)
    availability: str | None
    trained_words: tuple[str, ...]
    tags: tuple[str, ...]
    published: str | None  # the date part of ``publishedAt``
    cover_url: str | None
    version: civitai.CivitaiVersion | None  # None: not downloadable here, see ``problem``
    problem: str | None = None
    options: tuple[VersionOption, ...] = ()

    @property
    def key(self) -> tuple[int, int | None]:
        """The downloads list key of this card's file: (version id, file id)."""
        return (self.version_id, self.version.file.id if self.version else None)

    @property
    def downloadable(self) -> bool:
        return bool(self.options)

    @property
    def nsfw_label(self) -> str | None:
        """A badge text when the cover is above the lowest level, else None."""
        if self.nsfw_level <= 1:
            return None
        top = max((flag for flag in NSFW_LABELS if self.nsfw_level & flag), default=0)
        return NSFW_LABELS.get(top, "Mature")

    def tooltip(self) -> str:
        lines = [f"{self.name} · {self.version_name}" if self.version_name else self.name]
        if self.creator:
            lines.append(f"by {self.creator}")
        if self.trained_words:
            lines.append("Trigger words: " + ", ".join(self.trained_words[:8]))
        if self.tags:
            lines.append("Tags: " + ", ".join(self.tags[:10]))
        if self.thumbs_up is not None or self.thumbs_down is not None:
            lines.append(f"👍 {self.thumbs_up or 0}   👎 {self.thumbs_down or 0}")
        if self.published:
            lines.append(f"Published {self.published}")
        if self.options:
            lines.append(f"{len(self.options)} supported version/file choices: Download lets you choose.")
        if self.families:
            lines.append("Supported families: " + ", ".join(self.families))
        if self.problem:
            lines.append(self.problem)
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class GalleryPage:
    cards: tuple[GalleryCard, ...]
    next_cursor: str | None
    skipped: int  # results left out (a family not supported, or no usable file)
    withheld_note: bool  # mature content looks withheld for this region


def family_choices() -> tuple[tuple[str, str], ...]:
    """(key, label) of every family that has Civitai labels, in file order, for the gallery's family filter."""
    return tuple((spec.key, spec.label) for spec in supported_families() if spec.aliases.get("civitai"))


def listing_query(
    query: str,
    sort: str,
    cursor: str | None,
    limit: int = PAGE_SIZE,
    *,
    family: str | None = None,
    period: str | None = None,
) -> str:
    """Request supported LoRA models with full file choices and cursor paging."""
    labels = provider_labels("civitai")
    if family:
        chosen = [spec for spec in supported_families() if spec.key == family]
        if chosen and chosen[0].aliases.get("civitai"):
            labels = tuple(chosen[0].aliases["civitai"])
    sort = sort if sort in SORTS else SORTS[0]
    parts = [("types", "LORA"), ("nsfw", "true"), ("limit", str(max(1, min(limit, 100))))]
    parts.append(("sort", sort))
    if period in PERIODS and period != PERIODS[0]:
        parts.append(("period", period))
    parts.extend(("baseModels", label) for label in labels)
    text = " ".join((query or "").split())[:MAX_QUERY]
    if text:
        parts.append(("query", text))
    if cursor:
        parts.append(("cursor", cursor))
    return "&".join(f"{key}={quote(value, safe='')}" for key, value in parts)


def thumbnail_url(url: str | None) -> str | None:
    """The cover's ``width=320`` rendition of an ``image.civitai.com`` address, or None for anything else."""
    if not isinstance(url, str) or not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or not _civitai_host(parts.hostname) or parts.username or parts.password:
        return None
    resized = _WIDTH.sub(f"/width={THUMB_WIDTH}", url, count=1)
    return resized


def _civitai_host(host: str | None) -> bool:
    return bool(host) and (host == civitai.HOST or host.endswith("." + civitai.HOST))


def _cover(version: dict) -> tuple[str | None, int]:
    """First still image (a video cover falls back to the next still image): its address and level."""
    for image in version.get("images") or []:
        if isinstance(image, dict) and image.get("type") == "image":
            level = image.get("nsfwLevel")
            return thumbnail_url(image.get("url")), level if isinstance(level, int) and level > 0 else 1
    return None, 1


def _stat(stats, key: str) -> int | None:
    value = stats.get(key) if isinstance(stats, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _resolve(model: dict, version: dict, model_id: int, version_id: int) -> civitai.CivitaiVersion:
    primary = [
        f.get("id")
        for f in version.get("files") or []
        if isinstance(f, dict) and f.get("primary") is True and str(f.get("name", "")).lower().endswith(".safetensors")
    ]
    return civitai.normalise(model, version, model_id, version_id, primary[0] if len(primary) == 1 else None)


def _card(model: dict, family: str | None = None) -> GalleryCard | None:
    """One Civitai model, with explicit options for every listed supported version and safetensors file."""
    versions = [v for v in model.get("modelVersions") or [] if isinstance(v, dict)]
    model_id = model.get("id")
    if not versions or not isinstance(model_id, int) or model.get("type") != civitai.LORA_TYPE:
        return None
    shown: tuple[dict, int, object] | None = None
    options: list[VersionOption] = []
    families: list[str] = []
    family_keys: set[str] = set()
    problem: str | None = None
    for candidate in versions:
        candidate_id = candidate.get("id")
        candidate_spec = family_for_label("civitai", candidate.get("baseModel"))
        if candidate_spec is None or not isinstance(candidate_id, int):
            continue
        family_keys.add(candidate_spec.key)
        label = candidate_spec.label + (" (experimental)" if candidate_spec.experimental else "")
        if label not in families:
            families.append(label)
        if shown is None:
            shown = (candidate, candidate_id, candidate_spec)
        safetensors = [
            f for f in (candidate.get("files") or [])
            if isinstance(f, dict) and isinstance(f.get("id"), int)
            and str(f.get("name", "")).lower().endswith(".safetensors")
        ]
        if not safetensors:
            problem = problem or "A supported version has no .safetensors file."
        for file in safetensors:
            try:
                resolved = civitai.normalise(model, candidate, model_id, candidate_id, file["id"])
                options.append(VersionOption(resolved, label))
            except civitai.CivitaiError as exc:
                problem = problem or str(exc)
    if shown is None or (family and family not in family_keys):
        return None
    version, version_id, spec = shown
    if options:
        version_id = options[0].version.version_id
        version = next(v for v in versions if v.get("id") == version_id)
        matched_spec = family_for_label("civitai", version.get("baseModel"))
        if matched_spec is not None:
            spec = matched_spec
        problem = None
    resolved = options[0].version if options else None
    cover, level = _cover(version)
    stats = version.get("stats")
    creator = model.get("creator")
    published = version.get("publishedAt")
    size = resolved.file.size_bytes if resolved else None
    words = version.get("trainedWords")
    tags = model.get("tags")
    return GalleryCard(
        model_id=model_id,
        version_id=version_id,
        name=civitai._text(model.get("name")) or f"Model {model_id}",
        version_name=civitai._text(version.get("name")) or "",
        creator=civitai._text(creator.get("username"), 80) if isinstance(creator, dict) else None,
        downloads=_stat(stats, "downloadCount"),
        thumbs_up=_stat(stats, "thumbsUpCount"),
        thumbs_down=_stat(stats, "thumbsDownCount"),
        size_bytes=size,
        base_model=civitai._text(version.get("baseModel"), 80),
        family=spec.label + (" (experimental)" if spec.experimental else ""),
        families=tuple(families),
        nsfw_level=level,
        availability=civitai._text(version.get("availability"), 40),
        trained_words=tuple(w[:60] for w in (words if isinstance(words, list) else []) if isinstance(w, str) and w)[:16],
        tags=tuple(t[:40] for t in (tags if isinstance(tags, list) else []) if isinstance(t, str))[:24],
        published=published[:10] if isinstance(published, str) else None,
        cover_url=cover,
        version=resolved,
        problem=problem,
        options=tuple(options),
    )

def list_page(
    client: httpx.Client,
    query: str = "",
    sort: str = SORTS[0],
    cursor: str | None = None,
    limit: int = PAGE_SIZE,
    *,
    family: str | None = None,
    period: str | None = None,
) -> GalleryPage:
    """One page of the gallery (blocking). Raises ``CivitaiError`` with Civitai's own wording on failure."""
    body = civitai.get_listing(client, listing_query(query, sort, cursor, limit, family=family, period=period))
    items = [item for item in body.get("items") or [] if isinstance(item, dict)]
    cards = tuple(card for card in (_card(item, family) for item in items) if card is not None)
    meta = body.get("metadata")
    nxt = meta.get("nextCursor") if isinstance(meta, dict) else None
    # Mature content is requested on every call; Civitai ignores that in some regions. There is no flag for it, so a
    # full page whose covers are all the lowest level is reported as a likely withholding, never as a certainty.
    withheld = len(cards) >= WITHHELD_MIN_CARDS and all(card.nsfw_level <= 1 for card in cards)
    return GalleryPage(cards, nxt if isinstance(nxt, str) and nxt else None, len(items) - len(cards), withheld)


# ------------------------------------------------------------------------------------------------------ thumbnails


class ThumbnailCache:
    """Bounded in-memory LRU of encoded cover bytes. Nothing is written to disk."""

    def __init__(self, entries: int = MAX_THUMB_ENTRIES, total: int = MAX_THUMB_TOTAL):
        self._entries, self._total = entries, total
        self._data: OrderedDict[str, bytes] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, url: str) -> bytes | None:
        with self._lock:
            data = self._data.get(url)
            if data is not None:
                self._data.move_to_end(url)
            return data

    def put(self, url: str, data: bytes) -> None:
        with self._lock:
            old = self._data.pop(url, None)
            if old is not None:
                self._size -= len(old)
            self._data[url] = data
            self._size += len(data)
            while self._data and (len(self._data) > self._entries or self._size > self._total):
                _key, dropped = self._data.popitem(last=False)
                self._size -= len(dropped)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._size = 0


def fetch_thumbnail(client: httpx.Client, url: str, cache: ThumbnailCache) -> bytes | None:
    """The cover's bytes from the cache or from Civitai (blocking); None when it cannot be had. No credentials are
    sent, redirects stay on civitai.com hosts over https, and the body is bounded."""
    cached = cache.get(url)
    if cached is not None:
        return cached
    with _THUMBS:
        current = url
        for _ in range(THUMB_HOPS + 1):
            parts = urlsplit(current)
            if parts.scheme != "https" or not _civitai_host(parts.hostname) or parts.username or parts.password:
                return None
            try:
                with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            return None
                        current = urljoin(current, location)
                        continue
                    kind = response.headers.get("content-type", "")
                    if response.status_code != 200 or not kind.startswith("image/"):
                        return None
                    raw = b""
                    for chunk in response.iter_bytes():
                        raw += chunk
                        if len(raw) > MAX_THUMB_BYTES:
                            return None
            except httpx.HTTPError:
                return None
            cache.put(url, raw)
            return raw
    return None

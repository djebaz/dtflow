"""State and workflow behind the Downloads view (#465, epic #327).

Qt-free. ``resolve_text`` turns pasted links and version ids into reviewed entries (no download happens), and
``run_item`` carries one entry through download and conversion while writing progress onto a ``DownloadItem``. The view
polls the items; nothing here touches widgets. Every download is an explicit call, nothing uploads to DT+.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.persistence.assets import AssetRegistry
from dtflow.services import civitai
from dtflow.services.lora_convert.errors import ConversionCancelled
from dtflow.services.lora_convert.registry import FamilySpec
from dtflow.services.lora_download import convert_downloaded
from dtflow.services.lora_sources import list_sources, remove_source
from dtflow.services.lora_store import LoraStore
from dtflow.services.lora_naming import LoraPrepareMetadata

log = logging.getLogger(__name__)

MAX_LINES = 20
MAX_OPTIONS = 8  # versions or files listed when a link does not name one

QUEUED, DOWNLOADING, CONVERTING = "queued", "downloading", "converting"
CONVERTED, NOT_CONVERTIBLE, FAILED, CANCELLED, INTERRUPTED = (
    "converted",
    "not_convertible",
    "failed",
    "cancelled",
    "interrupted",
)
ACTIVE = (QUEUED, DOWNLOADING, CONVERTING)

PREPARE_NEEDS_CLOUD = "Prepare needs DT+ Cloud"
QUEUED_DETAIL = "At most 3 transfers run at once"
SPEED_WINDOW = 0.5  # seconds between two speed samples


# -------------------------------------------------------------------------------------------------- resolving links


@dataclass(frozen=True, slots=True)
class ResolvedEntry:
    """One reviewed line. ``version`` and ``family`` both set and no ``problem``: Download is allowed."""

    label: str  # shown when there is no version (a line that failed), never the pasted text
    version: civitai.CivitaiVersion | None = None
    family: FamilySpec | None = None
    problem: str | None = None

    @property
    def downloadable(self) -> bool:
        return self.version is not None and self.family is not None and self.problem is None


def canonical_line(ref: civitai.CivitaiRef) -> str | None:
    """A token-free spelling of a reference: a version id, or the model link with ``?modelVersionId=``."""
    if ref.model_id is not None and ref.version_id is not None:
        return f"https://civitai.com/models/{ref.model_id}?modelVersionId={ref.version_id}"
    if ref.model_id is not None:
        return f"https://civitai.com/models/{ref.model_id}"
    return str(ref.version_id) if ref.version_id is not None else None


def entry_for(version: civitai.CivitaiVersion) -> ResolvedEntry:
    label = f"{version.model_name} · {version.version_name}".strip(" ·") or f"Version {version.version_id}"
    try:
        family = civitai.supported_family(version)
    except civitai.CivitaiUnsupportedFamily as exc:
        return ResolvedEntry(label, version, None, str(exc))
    return ResolvedEntry(label, version, family)


def _resolve_ref(
    ref: civitai.CivitaiRef,
    file_id: int | None,
    token: str | None,
    client,
    line: int,
    depth: int = 0,
) -> list[ResolvedEntry]:
    fallback = f"Version {ref.version_id}" if ref.version_id else f"Model {ref.model_id}"
    try:
        return [entry_for(civitai.resolve(ref, token, file_id=file_id, client=client))]
    except civitai.CivitaiChoice as choice:
        if depth >= 2 or not choice.options:
            return [ResolvedEntry(fallback, problem=str(choice))]
        entries: list[ResolvedEntry] = []
        for option in choice.options[:MAX_OPTIONS]:
            if "version_id" in option:
                sub = civitai.CivitaiRef(model_id=ref.model_id, version_id=option["version_id"])
                entries.extend(_resolve_ref(sub, None, token, client, line, depth + 1))
            elif option.get("file_id") is not None:
                entries.extend(_resolve_ref(ref, option["file_id"], token, client, line, depth + 1))
        if len(choice.options) > MAX_OPTIONS:
            entries.append(ResolvedEntry(fallback, problem=f"Only the first {MAX_OPTIONS} choices are listed."))
        return entries or [ResolvedEntry(fallback, problem=str(choice))]
    except civitai.CivitaiError as exc:
        return [ResolvedEntry(fallback, problem=str(exc))]


def resolve_text(text: str, token: str | None) -> tuple[str, list[ResolvedEntry]]:
    """Resolve pasted lines (blocking: run on a worker). Returns the sanitised text (token-free canonical spellings,
    unparseable lines dropped) and the entries. A model link without a version, or a version with several files,
    lists every choice; nothing resolves to "latest"."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    entries: list[ResolvedEntry] = []
    clean: list[str] = []
    seen: set[str] = set()
    client = civitai.new_client()
    try:
        for number, raw in enumerate(lines[:MAX_LINES], 1):
            try:
                ref = civitai.parse_reference(raw)
            except civitai.CivitaiError as exc:
                entries.append(ResolvedEntry(f"Line {number}", problem=str(exc)))
                continue
            canonical = canonical_line(ref)
            if canonical is None or canonical in seen:
                continue
            seen.add(canonical)
            clean.append(canonical)
            entries.extend(_resolve_ref(ref, None, token, client, number))
        if len(lines) > MAX_LINES:
            entries.append(ResolvedEntry("Too many lines", problem=f"Only the first {MAX_LINES} lines were resolved."))
    finally:
        client.close()
    return "\n".join(clean), entries


# ------------------------------------------------------------------------------------------------------ the list


@dataclass(eq=False)
class DownloadItem:
    """One row of the downloads list. Worker threads write the progress fields; the view reads them on a timer."""

    name: str
    key: tuple[int | None, int | None]  # (version id, file id)
    family: str = ""
    version: civitai.CivitaiVersion | None = None  # None for a row restored from the source registry
    state: str = QUEUED
    done: int = 0
    total: int | None = None
    detail: str = ""
    source_sha: str | None = None
    converted_sha: str | None = None
    converted_size: int | None = None
    prepare_metadata: LoraPrepareMetadata | None = None  # exact metadata chosen during successful conversion
    auto_prepare_status: str = ""  # presentation-only detail for opted-in DT+ Prepare
    auto_prepare_phase: str = ""  # "", "preparing", or "prepared"; conversion itself remains durable
    version_id: int | None = None
    model_id: int | None = None
    speed: float = 0.0  # bytes per second while downloading (smoothed)
    cancel: threading.Event = field(default_factory=threading.Event)
    handle: civitai.DownloadHandle | None = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    def request_cancel(self) -> None:
        self.cancel.set()
        if self.handle is not None:
            self.handle.cancel.set()


def item_for(entry: ResolvedEntry) -> DownloadItem:
    version, family = entry.version, entry.family
    assert version is not None and family is not None
    return DownloadItem(
        entry.label,
        (version.version_id, version.file.id),
        family.label + (", experimental" if family.experimental else ""),
        version,
        version_id=version.version_id,
        model_id=version.model_id,
    )


def _from_source(row: dict) -> DownloadItem:
    state = {"converted": CONVERTED, "refused": NOT_CONVERTIBLE, "failed": NOT_CONVERTIBLE}.get(
        row["state"], INTERRUPTED
    )
    name = f"{row.get('model_name') or ''} · {row.get('version_name') or ''}".strip(" ·") or "Downloaded LoRA"
    detail = ""
    if state == CONVERTED:
        detail = "Source file deleted" if row.get("file_state") == "deleted" else "Converted"
    elif state == NOT_CONVERTIBLE:
        detail = (row.get("state_detail") or "The converter could not use this file.") + " File kept."
    else:
        detail = "Downloaded, conversion did not finish. File kept."
    family = row.get("family") or ""
    from dtflow.services.lora_convert.registry import supported_families

    labels = {spec.key: spec.label for spec in supported_families()}
    return DownloadItem(
        name,
        (row.get("version_id"), row.get("file_id")),
        labels.get(family, family),
        None,
        state,
        detail=detail,
        source_sha=row["sha256"],
        converted_sha=row.get("converted_sha256"),
        converted_size=row.get("converted_size"),
        version_id=row.get("version_id"),
        model_id=row.get("model_id"),
    )


async def prepared_contents_for_key(state: Path, credential_id: str) -> frozenset[tuple[str, int]]:
    """Read existing confirmed DT+ receipts for this key, not remote account inventory.

    Matching exact converted content, never just a filename, avoids a stale or another
    account's receipt falsely marking a download as Prepared.
    """
    registry = AssetRegistry(state)
    await registry.open()
    try:
        return await registry.confirmed_lora_contents(credential_id)
    finally:
        await registry.aclose()


async def load_items(state: Path) -> list[DownloadItem]:
    """The durable rows: every recorded source, newest first."""
    try:
        rows = await list_sources(state)
    except (ValidationError, OSError):
        return []
    return [_from_source(row) for row in reversed(rows) if row.get("provider") == "civitai"]


def reresolve(item: DownloadItem, token: str | None) -> ResolvedEntry:
    """Blocking: fetch a row's version again (a Retry of a restored row, or of one whose metadata is gone)."""
    client = civitai.new_client()
    try:
        entries = _resolve_ref(civitai.CivitaiRef(version_id=item.version_id), item.key[1], token, client, 0)
    finally:
        client.close()
    return entries[0]


async def remove_item(state: Path, item: DownloadItem) -> bool:
    """Remove a kept (not convertible or interrupted) source: its file and its row, permanently."""
    if item.source_sha:
        return await remove_source(state, item.source_sha)
    return True


async def retained_path(state: Path, item: DownloadItem) -> Path | None:
    """The converted ``.ckpt`` of a converted row, for Prepare (a blocking index read, off the GUI thread)."""
    if not item.converted_sha or item.converted_size is None:
        return None
    found = await asyncio.to_thread(LoraStore(state).find, item.converted_sha, item.converted_size)
    return None if found is None else found.path


# ------------------------------------------------------------------------------------------------------- running


def _mebibytes(value: int) -> str:
    return f"{value / 1048576:.0f}"


async def run_item(
    state: Path,
    item: DownloadItem,
    manager: civitai.DownloadManager,
    read_token: Callable[[], str | None],
) -> None:
    """Download, verify, convert and retain one item; the outcome lands on ``item`` (never raises for a failed file)."""
    version = item.version
    assert version is not None
    item.cancel = threading.Event()
    item.handle = None
    item.state, item.done, item.total, item.detail = QUEUED, 0, None, QUEUED_DETAIL

    mark = [time.monotonic(), 0]  # (time, bytes) of the last speed sample

    def on_download(progress: civitai.DownloadProgress) -> None:
        now = time.monotonic()
        if progress.done < mark[1]:  # the one automatic retry starts again from zero
            mark[0], mark[1], item.speed = now, progress.done, 0.0
        elif now - mark[0] >= SPEED_WINDOW:
            rate = (progress.done - mark[1]) / (now - mark[0])
            item.speed = rate if item.speed <= 0 else 0.6 * item.speed + 0.4 * rate
            mark[0], mark[1] = now, progress.done
        item.state, item.done, item.total = DOWNLOADING, progress.done, progress.total
        item.detail = ""

    def on_convert(progress) -> None:
        item.done, item.total = progress.done, progress.total or None

    try:
        try:
            token = await asyncio.to_thread(read_token)
        except Exception:  # no secure storage: try without a token, Civitai's answer explains the rest
            token = None
        handle = manager.submit(version, token, on_download)
        item.handle = handle
        if item.cancel.is_set():
            handle.cancel.set()
        downloaded = await asyncio.wrap_future(handle.future)
        if item.cancel.is_set():
            raise civitai.CivitaiCancelled("Cancelled.")
        item.speed = 0.0
        item.state, item.done, item.total, item.detail = CONVERTING, 0, None, item.family
        item.source_sha = downloaded.sha256
        result = await convert_downloaded(state, version, downloaded, progress=on_convert, cancel=item.cancel)
    except (civitai.CivitaiCancelled, ConversionCancelled, asyncio.CancelledError):
        item.state, item.detail = CANCELLED, "Partial file discarded"
        if item.source_sha:
            item.detail = "Stopped. The downloaded file is kept; Retry converts it."
        return
    except ValidationError as exc:
        item.state, item.detail = FAILED, str(exc) or "The download failed."
        return
    except Exception as exc:  # a bug must show as a failed row, not vanish; details go to the log
        log.exception("download item failed")
        item.state, item.detail = FAILED, f"Unexpected error ({type(exc).__name__}). See the Logs workspace."
        return
    finally:
        item.handle = None
    if result.state == "converted" and result.prepared is not None:
        retained = result.prepared.retained
        item.state, item.converted_sha, item.converted_size = CONVERTED, retained.sha256, retained.size
        item.prepare_metadata = result.prepared.metadata
        item.detail = "Source file deleted" if result.source_deleted else "Converted"
        if result.warnings:
            item.detail += ". " + " ".join(result.warnings)
        if result.detector_note:
            item.detail += ". " + result.detector_note
    else:
        item.state = NOT_CONVERTIBLE
        item.detail = (result.reason or "The converter could not use this file.") + " File kept."

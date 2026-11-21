"""Production saved-output controller for the existing Grid/List/Detail Images page."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sys
from collections import deque
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path, PureWindowsPath
from stat import S_ISREG
from time import monotonic, thread_time

from PySide6.QtCore import (
    QByteArray,
    QDataStream,
    QEvent,
    QIODevice,
    QSignalBlocker,
    QObject,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
    qVersion,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dtflow import gallery_limits
from dtflow.core.gallery import GalleryItem, GalleryQuery
from dtflow.core.gallery_selection import MAX_CHECKED_IMAGES, GallerySelection
from dtflow.core.models import ValidationError
from dtflow.core.video import clip_summary, duration_text
from dtflow.services.video_artifacts import video_path_for
from dtflow.services.lora_naming import display_label
from dtflow.diagnostics import event as diagnostic_event
from dtflow.diagnostics import measurement
from dtflow.logging_setup import perf_enabled
from dtflow.persistence import gallery as gallery_storage
from dtflow.persistence import thumbnail_cache
from dtflow.persistence.original_io import OriginalImagesBusyError, OriginalImagesUnavailableError, original_images_lock
from dtflow.persistence.store import PersistenceError
from dtflow.persistence.viewer import load_viewer, save_viewer
from dtflow.services.artifact_paths import ArtifactReadPaths, foreign_windows_path
from dtflow.services.gallery import (
    MISSING_ISSUE,
    GalleryEditSource,
    copy_output_paths,
    filter_choices,
    gallery_file_signature,
    inspect_output,
    load_detail,
    locate_output,
    query_outputs,
    query_snapshot,
    resolve_item_read,
)
from dtflow.ui.performance import PerfBatchStats
from dtflow.ui.scroll_step import keep_own_wheel
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.button_focus import KeyboardFocusGuard
from dtflow.ui.desktop_open import open_local_path
from dtflow.ui.gallery_hover_card import GalleryHoverCard
from dtflow.ui.gallery_slideshow import GallerySlideshow
from dtflow.ui.gallery_detail_preview import (
    ASSETS,
    CoverPainter,
    RowHoverDelegate,
    apply_gallery,
    toggle_image_focus,
)
from dtflow.ui.gallery_loading import GalleryCanvasLoading
from dtflow.ui.gallery_pager import GalleryPager
from dtflow.ui.video_player import VideoPlayer
from dtflow.ui.gallery_selection_check import GallerySelectionCheck
from dtflow.ui.gallery_preparation import GalleryPreparation, GalleryRetirement
from dtflow.ui.inline_feedback import feedback
from dtflow.ui.live_control_fx import LiveFollowFx, live_dot_icon
from dtflow.ui.prompt_tags import PROMPT_RUNS_ROLE, PromptTagHighlighter, request_runs, stored_runs, tagged_html
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.theme import load_tokens

log = logging.getLogger(__name__)

LIVE_FOLLOW_TIPS = {
    "off": "Turn on Live and Follow",
    "live": "Follow: scroll to the newest image as it arrives.",
    "following": "Following the newest image. Click to stop.",
    "paused": "Follow paused because you scrolled away. Click, or scroll back to the top, to resume.",
    "newest-only": "Follow works with Newest first. Switch the sort to use it.",
}

PAGE_SIZE = gallery_limits.DEFAULT_BATCH
FILENAME_COLUMN = 2
PROMPT_COLUMN = 8  # the list view's prompt cell, see ``_entry_fields``
MAX_MOUNTED_IMAGES = gallery_limits.DEFAULT_CARDS  # five default batches; bounded buffered Qt cards
MAX_QUERY_ITEMS = gallery_limits.MAX_READ  # GalleryQuery retains its existing per-read safety cap
# One fixed, bounded decode size per saved output for Grid, List, and Detail filmstrip.
# The zoom slider changes widget geometry only; it never creates new thumbnail sizes.
THUMBNAIL_SIDE = 600
THUMBNAIL_CONCURRENCY = 6
AUTO_REFRESH_MS = 1000  # file-metadata poll only; unchanged polls never rebuild the page
PUSH_DEBOUNCE_MS = 250  # coalesce in-process change hints before the metadata check
LIVE_RECOVERY_INTERVAL_S = 5  # full live reads during snapshot/failure episodes keep their original spacing
SNAPSHOT_RECOVERY_GRACE_S = 5  # presentation-only stability grace before a recovered live read hides the warning
LOOKAHEAD_SCREENS = 3  # loaded beyond the viewport: at least one batch or this many screens
AUTO_REFRESH_FULL_INTERVAL_S = 30  # a full read periodically guards against missed file metadata changes
STANDARD_THUMBNAIL_PX = 320
MIN_THUMBNAIL_PERCENT = 25
MAX_THUMBNAIL_PERCENT = 250

SORT_LABELS = {
    "Newest first": "newest",
    "Oldest first": "oldest",
    "Model": "model",
    "Pixel size": "size",
    "LoRAs": "loras",
}
SORT_NAMES = {value: key for key, value in SORT_LABELS.items()}
DIMENSIONS = re.compile(r"^\s*(\d+)\s*[x×]\s*(\d+)\s*$")
SIZE_CLASS_LABELS = {
    "Small": "small",
    "Medium": "medium",
    "Large": "large",
    "Big": "big",
}
DATE_RANGE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*\.\.\s*(\d{4}-\d{2}-\d{2})\s*$")


def _filename(path: str) -> str:
    return PureWindowsPath(path).name if foreign_windows_path(path) else Path(path).name


# Originals larger than this are decoded from the file under the lease instead of from memory.
_MAX_BUFFERED_ORIGINAL = 256 * 1024 * 1024


def _image(
    path: str,
    max_side: int,
    state: Path | None = None,
    *,
    item: GalleryItem | None = None,
    cache: bool = False,
    stats: PerfBatchStats | None = None,
) -> tuple[QImage | None, str]:
    """Read one original under the shared lease, then decode it outside the lease.

    The lease keeps the bytes consistent with removal; decoding from memory lets
    other thumbnails, saves and removals take the lease meanwhile. With ``cache``,
    a small on-disk thumbnail keyed by the original's identity, size and mtime
    replaces the original read and full decode; the stat happens under the same
    lease, so a removed or replaced original never serves a stale entry.
    """

    def access() -> str:
        return (
            resolve_item_read(item, state, display=True)[0]
            if item is not None and state is not None
            else ArtifactReadPaths.discover().resolve(path)
            if foreign_windows_path(path)
            else path
        )

    def decode(source: str | QIODevice) -> tuple[QImage | None, str]:
        if stats is None or not perf_enabled():
            return decode_image(source)
        started, cpu_started = monotonic(), thread_time()
        success = False
        try:
            value, issue = decode_image(source)
            success = value is not None
            return value, issue
        finally:
            stats.observe(
                "thumbnail_decode",
                (monotonic() - started) * 1000,
                cpu_ms=(thread_time() - cpu_started) * 1000,
                success=success,
            )

    def decode_image(source: str | QIODevice) -> tuple[QImage | None, str]:
        reader = QImageReader(source)
        reader.setAutoTransform(True)
        if max_side:
            size = reader.size()
            if size.isValid() and (size.width() > max_side or size.height() > max_side):
                reader.setScaledSize(size.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio))
        value = reader.read()
        if value.isNull():
            return None, reader.errorString() or "Image could not be decoded."
        return value, ""

    def unreadable(exc: OSError) -> tuple[None, str]:
        return None, str(exc) if item is not None else "Windows output drive is unavailable or the path is unsupported."

    if state is None:
        try:
            return decode(access())
        except OSError as exc:
            return unreadable(exc)
    entry: Path | None = None
    cached = False
    try:
        requested = monotonic()
        with original_images_lock(state, timeout=12, background=True):
            leased = monotonic()
            try:
                source = access()
                resolved = monotonic()
                with open(source, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if cache and item is not None:
                        entry = thumbnail_cache.entry_path(state, item.identity, info, max_side)
                    if entry is not None:
                        lookup = monotonic()
                        data = thumbnail_cache.read(entry)
                        cached = data is not None
                        if stats is not None and perf_enabled():
                            stats.observe(
                                "thumbnail_cache_hit" if cached else "thumbnail_cache_miss",
                                (monotonic() - lookup) * 1000,
                            )
                    if not cached:
                        # Size first: read(huge) would allocate the whole cap per thumbnail.
                        if info.st_size > _MAX_BUFFERED_ORIGINAL:
                            return decode(source)
                        data = stream.read()
            except OSError as exc:
                return unreadable(exc)
            held = monotonic() - leased
            if held >= 0.5:
                diagnostic_event(
                    log,
                    "gallery.thumbnail_read_slow",
                    level=logging.DEBUG,
                    data={
                        "lease_wait_ms": (leased - requested) * 1000,
                        "resolve_ms": (resolved - leased) * 1000,
                        "read_ms": (monotonic() - resolved) * 1000,
                        "bytes": len(data),
                    },
                )
    except OriginalImagesUnavailableError as exc:
        # Preserve the full sanitized prerequisite for the tooltip, not the thumbnail canvas.
        return None, str(exc)
    except OriginalImagesBusyError:
        return None, "Original temporarily busy"
    # QDataStream owns a native C++ QBuffer here; no stream deserialization is
    # performed. A Python-created QBuffer has virtual I/O callbacks which can
    # re-enter Python while Qt holds its image-plugin mutex. A concurrent GUI
    # paint/save can then invert that mutex with the GIL. Keep this owner alive
    # until decoding finishes, preserving the immutable bytes and scaled reads.
    encoded = QDataStream(QByteArray(data))
    image, issue = decode(encoded.device())
    if cached and image is None:
        # A corrupt entry: drop it and rebuild from the original once.
        thumbnail_cache.discard(state, entry)
        return _image(path, max_side, state, item=item, cache=False, stats=stats)
    if entry is not None and not cached and image is not None:
        # Show the thumbnail now; a background writer encodes a private copy.
        copy = image.copy()

        def written(stored: bool, elapsed: float) -> None:
            if stats is not None and perf_enabled():
                stats.observe("thumbnail_cache_write", elapsed * 1000, success=stored)

        if entry.suffix == ".jpg" and copy.hasAlphaChannel():
            entry = entry.with_suffix(".webp")  # Preserve transparency instead of flattening it for JPEG.
        encoding, quality = ("JPEG", 85) if entry.suffix == ".jpg" else ("WEBP", 80)
        thumbnail_cache.store_later(state, entry, lambda scratch: copy.save(str(scratch), encoding, quality), written)
    return image, issue


def _missing_icon() -> QIcon:
    return QIcon(str(ASSETS / "file-x.svg"))


def _display_output_path(path: str) -> str:
    """Show a saved image path from the output directory onward."""
    parts = Path(path).parts
    for index, part in enumerate(parts):
        if part.lower() in {"output", "outputs"}:
            return str(Path(*parts[index:]))
    return path


def _human_size(value: int | None) -> str:
    if value is None:
        return "Unknown"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.0f} {unit}" if unit in ("B", "KB") else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _local_timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%b %d, %Y\n%H:%M:%S")
    except (TypeError, ValueError):
        return "Not recorded"


def _local_midnight(day: date) -> str:
    # A naive local wall time lets the OS apply the date-specific DST rule before normalization to UTC.
    return datetime.combine(day, time.min).astimezone().astimezone(UTC).isoformat()


def configure_limits(*, batch: int | None = None, cards: int | None = None) -> None:
    """Apply the -ml/-mc launch limits before any GalleryPage is built."""
    global PAGE_SIZE, MAX_MOUNTED_IMAGES
    PAGE_SIZE, MAX_MOUNTED_IMAGES = gallery_limits.resolve(batch, cards)
    diagnostic_event(
        log,
        "gallery.limits",
        level=logging.INFO,
        data={
            "batch": PAGE_SIZE,
            "cards": MAX_MOUNTED_IMAGES,
        },
    )


def _read_limit() -> int:
    # One GalleryQuery read is capped; larger -ml batches extend in chunks.
    return min(PAGE_SIZE, MAX_QUERY_ITEMS)


async def _extend_page(current: GalleryQuery, page: GalleryPage, read, target_count: int) -> GalleryPage:
    """Fill up to target_count contiguous records using bounded reads (MAX_QUERY_ITEMS rows each)."""
    if len(page.items) != current.limit or target_count <= current.limit:
        return page
    while True:
        remaining = min(target_count - len(page.items), max(0, page.total - current.offset - len(page.items)))
        if remaining <= 0:
            return page
        next_query = replace(current, offset=current.offset + len(page.items), limit=min(MAX_QUERY_ITEMS, remaining))
        extension_started = monotonic()
        extension = await read(next_query)
        if perf_enabled():
            measurement(
                log,
                "gallery.query_extension",
                elapsed_ms=(monotonic() - extension_started) * 1000,
                offset=next_query.offset,
                returned=len(extension.items),
            )
        if (
            not extension.items
            or extension.total != page.total
            or extension.snapshot_at != page.snapshot_at
            or {item.identity for item in page.items} & {item.identity for item in extension.items}
        ):
            # Concurrent gallery changes must never produce an inconsistent
            # mounted sequence. A subsequent poll can refill the window.
            return page
        page = replace(page, items=(*page.items, *extension.items), limit=len(page.items) + len(extension.items))


def _date_bounds(text: str) -> tuple[str | None, str | None]:
    value = text.strip()
    if not value or value.casefold() == "all time":
        return None, None
    today = datetime.now().astimezone().date()
    lowered = value.casefold()
    if lowered == "today":
        return _local_midnight(today), _local_midnight(today + timedelta(days=1))
    if lowered == "last 7 days":
        return _local_midnight(today - timedelta(days=6)), _local_midnight(today + timedelta(days=1))
    if lowered == "last 30 days":
        return _local_midnight(today - timedelta(days=29)), _local_midnight(today + timedelta(days=1))
    match = DATE_RANGE.match(value)
    try:
        if match:
            first = date.fromisoformat(match.group(1))
            last = date.fromisoformat(match.group(2))
            if last < first:
                raise ValueError
            return _local_midnight(first), _local_midnight(last + timedelta(days=1))
        single = date.fromisoformat(value)
        return _local_midnight(single), _local_midnight(single + timedelta(days=1))
    except ValueError as exc:
        raise ValidationError(
            "Date filter must be All time, Today, Last 7 days, Last 30 days, YYYY-MM-DD, or START..END."
        ) from exc


def _timed_thumbnail_image(path: str, max_side: int, state: Path, *, item: GalleryItem, stats: PerfBatchStats):
    """Aggregate worker CPU and elapsed time without changing thumbnail results."""
    wall_started = monotonic()
    cpu_started = thread_time()
    try:
        return _image(path, max_side, state, item=item, cache=max_side == THUMBNAIL_SIDE, stats=stats)
    finally:
        if perf_enabled():
            stats.observe(
                "thumbnail_workers",
                (monotonic() - wall_started) * 1000,
                cpu_ms=(thread_time() - cpu_started) * 1000,
            )


class GalleryPage(QObject):
    """Bind saved-output services to the existing Designer gallery without owning generation."""

    grid_position_changed = Signal(int, int)
    _facet_models: tuple[str, ...] = ()

    def __init__(
        self,
        page: QWidget,
        *,
        runner: AsyncRunner,
        state_dir: Path,
        reuse_callback=None,
        regenerate_callback=None,
        edit_callback=None,
        export_callback=None,
        recycle_callback=None,
    ):
        super().__init__(page)
        self.page = page
        self.runner = runner
        self.state_dir = Path(state_dir)
        self._reuse_callback = reuse_callback
        self._regenerate_callback = regenerate_callback
        self._edit_callback = edit_callback
        self.recycle_scope_revision = 0
        self._export_callback = export_callback
        self._recycle_callback = recycle_callback
        self._permanent_callback = None
        self.removal_mode = "trash"
        self.removal_ready = True
        self._recycle_busy = False
        self.selection = GallerySelection()
        self._tokens = load_tokens()
        self._hidden_checks = 0
        self._all_filtered_checked = False
        self._matching_checked: set[str] = set()
        self._hidden_query: GalleryQuery | None = None
        self._selection_generation = 0
        self._selection_busy = False
        self._selection_query: GalleryQuery | None = None
        self._syncing_selection = False
        self._check_boxes: dict[str, QCheckBox] = {}
        self._active = False
        self._loading = False
        self._adjacent_loading = False
        self._adjacent_failed_offset: int | None = None
        self._suppress_batch_scroll = False
        self._drag_scroll_positions: dict[str, int] = {}
        self._pending_refresh: tuple[bool, bool, str] | None = None
        self._auto_signature: tuple | None = None
        self._auto_checking = False
        self._auto_last_full = 0.0
        self._auto_unchanged_polls = 0
        self._auto_skip_reasons: list[str] = []
        self._auto_skip_at = 0.0
        self._refresh_failing = False
        self._snapshot_at = None
        self._snapshot_warning_dismissed = False
        self._snapshot_warning_episode = False
        self._snapshot_live_since = None
        self._restoring = True
        self._query_generation = 0
        self._query_started = 0.0
        self._image_generation = 0
        self._image_pending = False
        self._slideshow_position: int | None = None
        self._detail_generation = 0
        self._presented_item: GalleryItem | None = None
        self._thumb_generation = 0
        self._thumb_versions: dict[str, int] = {}
        self._thumb_active = 0
        self._thumb_waiting: deque[tuple[int, str, str]] = deque()
        self._save_running = False
        self._save_dirty = False
        self._save_future = None
        self._pending_page_selection: str | None = None
        self._page_scroll_target: str | None = None
        self._prefetch_zone_hint: int | None = None
        self._pending_page_jump: int | None = None
        self._clear_on_refresh = False
        # Grid cells use absolute rows; an adjacent load holds this anchor
        # (identity, viewport offset) while cards are removed or inserted.
        self._grid_slots: dict[str, tuple[int, int]] = {}
        self._held_anchor: tuple[str, int] | None = None
        self._held_scroll_delta = 0
        self._scroll_direction = 1  # last user scroll direction; forward first
        self.items: list[GalleryItem] = []
        self._rendered_page: tuple[int, tuple[GalleryItem, ...]] | None = None
        self.total = 0
        self.offset = 0
        self.selected_id: str | None = None
        self._open_request: str | None = None  # Queues "Open in Images": show this output in Detail once located
        self._open_generation: int | None = None
        self._open_attempts = 0
        self._open_started: float | None = None
        self._open_waits = 0
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.setInterval(250)
        self._open_timer.timeout.connect(self._continue_open_output)
        self.runner.shutdown_started.connect(self._cancel_open_output)
        self._origin_identity: str | None = None
        self._grid_buttons: dict[str, QPushButton] = {}
        self._grid_painters: dict[str, CoverPainter] = {}
        self._grid_labels: dict[str, tuple[QLabel, QLabel]] = {}
        self._grid_empty: QLabel | None = None
        self._grid_layout_keys: tuple[str, ...] = ()
        self._grid_layout_columns = 0
        self._grid_mount_keys: tuple[str, ...] | None = None
        self._grid_mount_columns = 0
        self._grid_mount_cursor = 0
        self._grid_mount_retained = 0
        self._grid_mount_added = 0
        self._grid_mount_moved = 0
        self._grid_mount_started = 0.0
        self._grid_mount_budget_ms = 4.0
        self._grid_mount_work_ms = 0.0
        self._grid_mount_max_slice_ms = 0.0
        self._grid_mount_slices = 0
        self._grid_mount_callbacks: list = []
        self._applied_thumbnail_percent: int | None = None
        # Grid, List and Detail (filmstrip) each remember their own slider value.
        self._thumbnail_percents = {"Grid": 100, "List": 100, "Detail": 100}
        self._density_anchor: tuple[str, str, int] | None = None
        self._film_buttons: list[QPushButton] = []
        self._film_painters: list[CoverPainter] = []
        self._film_entries: dict[str, tuple[QPushButton, CoverPainter]] = {}
        self._prepared_entries: dict[str, dict] = {}
        self._prepared_sizes: dict[str, int] = {}
        self._preparation_icons = None
        self._perf_stats = PerfBatchStats(log)
        self.retirement = GalleryRetirement(self)
        self.preparation = GalleryPreparation(
            self,
            dispose=self._dispose_prepared_entry,
            observe=self._observe_preparation,
        )
        self.runner.shutdown_started.connect(self._cancel_preparation)

        self._presentation_painters = apply_gallery(page, sample=False)
        self.surface_painter = self._presentation_painters[0]
        self.surface_painter.pixmap = QPixmap()
        self.surface = self._widget("galleryImageSurface", QLabel)
        self.video_player = VideoPlayer(
            self.surface,
            lambda text: self._selection_notice(text),
            self._video_playback_changed,
            on_navigate=self.step,
            navigation_allowed=lambda: self._active and self.mode == "Detail" and not self.slideshow.active,
            on_autoplay_changed=self._schedule_save,
        )
        self.video_player.play_button.clicked.connect(self._play_video)
        self.hover_card = GalleryHoverCard(self.surface)
        self.slideshow = GallerySlideshow(self)
        self._widget("galleryFixtureOverlay").hide()

        self.stack = self._widget("galleryContentStack")
        self.canvas_loading = GalleryCanvasLoading(
            self.stack, on_retry=lambda: self.refresh(preserve=True, trigger="retry")
        )
        self.grid_scroll = self._widget("galleryGridScroll")
        self.grid_cards = self._widget("galleryGridCards")
        self.cards_layout = self.grid_cards.layout()
        # Sliced first mounting is visible before its final callback. Reserve
        # the same top/left origin from the first row, not only at completion.
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.table = self._widget("galleryListTable", QTableWidget)
        keep_own_wheel(self.grid_scroll)  # benchmarked wheel scrolling (gallery.scroll_intent) keeps its own step
        keep_own_wheel(self.table)
        self.grid_viewport = self.grid_scroll.viewport()
        self.table_viewport = self.table.viewport()
        self.slider = self._widget("galleryThumbnailSizeSlider")
        self.film_scroll = self._widget("galleryFilmstripScroll")
        self._scroll = {"grid": 0, "list": 0, "filmstrip": 0}

        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(300)
        self.filter_timer.timeout.connect(self._apply_filter_change)
        self.reflow_timer = QTimer(self)
        self.reflow_timer.setSingleShot(True)
        self.reflow_timer.timeout.connect(self._start_grid_mount)
        self._grid_mount_timer = QTimer(self)
        self._grid_mount_timer.setSingleShot(True)
        self._grid_mount_timer.setInterval(1)
        self._grid_mount_timer.timeout.connect(self._mount_grid_slice)
        # Keep the immediate preceding/following 100-image batches resident
        # around the currently visible pager zone, without polling on each scroll event.
        self._neighbor_prefetch_timer = QTimer(self)
        self._neighbor_prefetch_timer.setSingleShot(True)
        self._neighbor_prefetch_timer.timeout.connect(self._prefetch_neighbors)
        self._pending_jump_timer = QTimer(self)
        self._pending_jump_timer.setSingleShot(True)
        self._pending_jump_timer.setInterval(40)
        self._pending_jump_timer.timeout.connect(self._resume_pending_page_jump)
        self.density_anchor_timer = QTimer(self)
        self.density_anchor_timer.setSingleShot(True)
        self.density_anchor_timer.timeout.connect(self._restore_density_anchor)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(250)
        self.save_timer.timeout.connect(self._flush_save)
        # Producers in this process (generation, queue items) push a change hint; the debounce
        # coalesces bursts and the metadata check still decides whether a read is needed.
        self.push_timer = QTimer(self)
        self.push_timer.setSingleShot(True)
        self.push_timer.setInterval(PUSH_DEBOUNCE_MS)
        self.push_timer.timeout.connect(self._auto_refresh_tick)
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(AUTO_REFRESH_MS)
        self.auto_timer.timeout.connect(self._auto_refresh_tick)
        if perf_enabled():
            screen = QApplication.primaryScreen()
            measurement(
                log,
                "gallery.runtime",
                platform=sys.platform,
                qt=qVersion(),
                dpr=screen.devicePixelRatio() if screen is not None else 1.0,
                dpi=screen.logicalDotsPerInch() if screen is not None else 0.0,
            )

        self._setup_filters()
        self._setup_views()
        self._setup_filmstrip()
        self._setup_actions()
        self.pager = GalleryPager(self.page, self._jump_to_page)
        self.pager.present(total=0, visible_start=0, visible_end=0, loaded=0, active_page=0)
        self._setup_selection_actions()
        self._restore_preferences()
        self._clear_detail()
        self.show_view(self.mode, persist=False)
        self._sync_auto_refresh()
        self._restoring = False

    def _widget(self, name: str, cls=QWidget):
        value = self.page.findChild(cls, name)
        if value is None:
            raise RuntimeError(f"Gallery form is missing {name}.")
        return value

    def _setup_filters(self) -> None:
        self.prompt = self._widget("galleryPromptSearch", QLineEdit)
        self.model = self._widget("galleryModelFilter", QComboBox)
        self.lora = self._widget("galleryLoRAFilter", QComboBox)
        self.date_filter = self._widget("galleryDateFilter", QComboBox)
        self.dimensions = self._widget("galleryDimensionsFilter", QComboBox)
        self.ratio = self._widget("galleryRatioFilter", QComboBox)
        self.sort = self._widget("gallerySortFilter", QComboBox)

        for control in (
            self.prompt,
            self.model,
            self.lora,
            self.date_filter,
            self.dimensions,
            self.ratio,
            self.sort,
        ):
            control.setEnabled(True)

        for combo in (self.model, self.lora, self.date_filter, self.dimensions, self.ratio):
            combo.setEditable(True)

        self.model.clear()
        self.model.addItem("All models")
        self.lora.clear()
        self.lora.addItem("All LoRAs")
        self.date_filter.clear()
        self.date_filter.addItems(["All time", "Today", "Last 7 days", "Last 30 days"])
        self.dimensions.clear()
        self.dimensions.addItem("Any size")
        self.ratio.clear()
        self.ratio.addItems(["Any ratio", "Square", "Portrait", "Landscape", "1:1", "4:3", "3:2", "16:9"])
        self.sort.clear()
        self.sort.addItems(SORT_LABELS)

        self.prompt.setToolTip("Case-insensitive literal search in recorded positive prompts.")
        self.model.setToolTip("Exact recorded model identifier; the list is a bounded convenience facet.")
        self.lora.setToolTip(
            "Pick a LoRA by its short name, or type a recorded filename; the list is a bounded convenience facet."
        )
        self.date_filter.setToolTip("Local calendar date or range. Examples: 2026-10-06 or 2026-10-01..2026-10-06.")
        self.dimensions.setToolTip(
            "Small under 400 px, Medium under 600, Large under 1200, Big 1200 and up (longer side); typing exact dimensions such as 1024 × 1024 also works."
        )
        self.ratio.setToolTip("Orientation or exact W:H aspect ratio.")
        self.sort.setToolTip("Deterministic saved-output ordering.")

        self.prompt.textChanged.connect(self._filter_edited)
        for combo in (self.model, self.lora, self.date_filter, self.dimensions, self.ratio, self.sort):
            combo.currentTextChanged.connect(self._filter_edited)

    def _setup_views(self) -> None:
        self.modes: dict[str, QWidget] = {}
        self.mode_buttons = QButtonGroup(self)
        self.mode_buttons.setExclusive(True)
        for mode in ("Grid", "List", "Detail"):
            button = self._widget(f"gallery{mode}Button", QPushButton)
            self.mode_buttons.addButton(button)
            button.clicked.connect(lambda checked=False, name=mode: self.show_view(name))
            self.modes[mode] = self._widget(f"gallery{mode}Content")

        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            [
                "",
                "Image",
                "Filename",
                "Model",
                "LoRAs / weights / status",
                "Dimensions",
                "File size",
                "Date / time",
                "Positive prompt",
            ]
        )
        self.table.verticalHeader().hide()
        self.table.setProperty("hoverRow", -1)
        self.table.setItemDelegate(RowHoverDelegate(self.table))
        self.table.setWordWrap(False)  # the prompt cell's tags are painted on one elided line
        self.table.setIconSize(QSize(64, 48))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((36, 80, 160, 180, 300, 120, 90, 145, 360)):
            self.table.setColumnWidth(column, width)
        self.table.setColumnHidden(FILENAME_COLUMN, True)  # the name stays in the cell data; the list shows no Filename
        self.table.itemClicked.connect(self._table_clicked)
        self.table.itemActivated.connect(self._table_activated)
        self.table.itemChanged.connect(self._table_item_changed)
        self._header_check = QCheckBox(self.table.horizontalHeader())
        self._header_check.setTristate(True)
        self._header_check.setToolTip("Select or unselect the currently displayed rows.")
        self._header_check.setAccessibleName("Select currently displayed rows")
        self._header_check.setGeometry(10, 6, 22, 22)
        self._header_check.clicked.connect(self._toggle_visible_rows)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._position_header_check())
        self.table.horizontalHeader().sectionMoved.connect(lambda *_: self._position_header_check())
        self.table.horizontalScrollBar().valueChanged.connect(lambda *_: self._position_header_check())

        self.table_viewport.setMouseTracking(True)
        self.table_viewport.installEventFilter(self)
        self.grid_viewport.installEventFilter(self)
        self.page.installEventFilter(self)
        self.slider.valueChanged.connect(self._density_changed)
        self.grid_scroll.verticalScrollBar().valueChanged.connect(lambda value: self._scroll_changed("grid", value))
        self.table.verticalScrollBar().valueChanged.connect(lambda value: self._scroll_changed("list", value))
        self.film_scroll.verticalScrollBar().valueChanged.connect(
            lambda value: self._scroll_changed("filmstrip", value)
        )
        # Qt emits valueChanged for programmatic anchor restore and reflow.
        # Prefetch only on user scrollbar actions; viewport Wheel is handled
        # separately in eventFilter.
        for name, bar in (
            ("grid", self.grid_scroll.verticalScrollBar()),
            ("list", self.table.verticalScrollBar()),
        ):
            bar.actionTriggered.connect(lambda action, mode=name: self._scrollbar_action(mode, action))
            bar.sliderPressed.connect(
                lambda mode=name, scrollbar=bar: self._drag_scroll_positions.__setitem__(mode, scrollbar.value())
            )
            bar.sliderMoved.connect(lambda position, mode=name: self._scrollbar_drag(mode, position))
            bar.sliderReleased.connect(lambda mode=name: self._drag_scroll_positions.pop(mode, None))

        for key, callback in (
            ("Left", lambda: self.step(-1)),
            ("Right", lambda: self.step(1)),
            ("Ctrl+PgUp", lambda: self._change_page(-1)),
            ("Ctrl+PgDown", lambda: self._change_page(1)),
        ):
            shortcut = QShortcut(QKeySequence(key), self.page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)

    def _setup_filmstrip(self) -> None:
        buttons = [self._widget(f"galleryThumbnail{i}", QPushButton) for i in range(1, 7)]
        self._designer_film_buttons = set(buttons)
        self._film_spares = list(zip(buttons, self._presentation_painters[1:7], strict=True))
        layout = self._widget("galleryFilmstripScrollContents").layout()
        for button in buttons:
            button.clicked.connect(lambda checked=False, source=button: self.select(str(source.property("identity"))))
            button.hide()
            layout.removeWidget(button)
            button.setAutoExclusive(False)
            button.setChecked(False)
            button.setAutoExclusive(True)

    def _setup_actions(self) -> None:
        self.refresh_button = self._widget("galleryRefreshButton", QPushButton)
        self.auto_button = self._widget("galleryAutoRefreshButton", QPushButton)
        self.refresh_button.setEnabled(True)
        self.refresh_button.setToolTip("Refresh saved outputs while keeping the current browsing anchor when possible.")
        self.refresh_button.clicked.connect(lambda: self.refresh(preserve=True, trigger="manual"))
        self.auto_button.setEnabled(True)
        self.auto_button.setCheckable(True)
        self.auto_button.installEventFilter(KeyboardFocusGuard(self.auto_button))
        # Live is two attached buttons: Live (toggle, checked = refreshing) and Follow (implies Live).
        # `_live_mode` stays 0 Off, 1 Auto, 2 Follow; `_live_state` is the drawn state (see _live_visual_state).
        self.follow_button = self._widget("galleryFollowButton", QPushButton)
        self.follow_button.setEnabled(True)
        self.follow_button.installEventFilter(KeyboardFocusGuard(self.follow_button))
        self.follow_button.setIconSize(QSize(16, 16))
        self.auto_button.setIconSize(QSize(10, 10))
        self._follow_icons = {
            False: QIcon(str(ASSETS / "chevrons-up-dim.svg")),
            True: QIcon(str(ASSETS / "chevrons-up.svg")),
        }
        self._live_mode = 0
        self._live_state = None
        self._syncing_live = False
        self._live_fx = LiveFollowFx(self.follow_button, QColor(self._tokens["color.focus"]))
        self.auto_button.toggled.connect(self._auto_refresh_changed)
        self.follow_button.clicked.connect(self._follow_clicked)
        self._sync_live_text()

        self.previous_button = self._widget("galleryPreviousButton", QPushButton)
        self.next_button = self._widget("galleryNextButton", QPushButton)
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.use_settings_button = self._widget("galleryUseSettingsButton", QPushButton)
        self.regenerate_button = self._widget("galleryRegenerateButton", QPushButton)
        self.use_settings_button.clicked.connect(self._use_settings)
        self.edit_button = self._widget("galleryEditImageButton", QPushButton)
        self._edit_tip = self.edit_button.toolTip()
        self.edit_button.clicked.connect(self._edit_image)
        self.regenerate_button.clicked.connect(self._regenerate)
        self.copy_button = self._widget("galleryCopyPathButton", QPushButton)
        self.folder_button = self._widget("galleryOpenFolderButton", QPushButton)
        self.external_button = self._widget("galleryExternalViewerButton", QPushButton)
        self.copy_button.clicked.connect(self._copy_path)
        self.folder_button.clicked.connect(self._open_folder)
        self.external_button.clicked.connect(self._open_external)

    def _setup_selection_actions(self) -> None:
        """Wire the canonical single-row contextual toolbar and keyboard affordances."""
        self.selection_bar = self._widget("gallerySelectionBar", QFrame)
        self.selection_count = self._widget("gallerySelectedCountLabel", QLabel)
        self.hidden_count_label = self._widget("galleryHiddenCountLabel", QLabel)
        self.select_filtered_button = self._widget("gallerySelectFilteredButton", QPushButton)
        self.clear_selection_button = self._widget("galleryUnselectButton", QPushButton)
        self.copy_paths_button = self._widget("galleryCopyPathsButton", QPushButton)
        self.more_button = self._widget("galleryMoreButton", QPushButton)
        self.export_button = self._widget("galleryExportButton", QPushButton)
        self.delete_button = self._widget("galleryDeleteButton", QPushButton)
        self.detail_check = self._widget("galleryDetailSelectionCheck", QCheckBox)

        self.select_filtered_button.clicked.connect(self._toggle_all_filtered)
        self.copy_paths_button.clicked.connect(self._copy_checked_paths)
        self.export_button.clicked.connect(self._open_checked_export)
        self.delete_button.clicked.connect(self._request_recycle)
        self.detail_check.toggled.connect(self._detail_check_toggled)
        for control, icon in (
            (self.select_filtered_button, "layers"),
            (self.copy_paths_button, "copy"),
            (self.more_button, "more"),
            (self.export_button, "export"),
            (self.delete_button, "delete"),
        ):
            control.setIcon(QIcon(str(ASSETS / f"{icon}.svg")))
            control.setIconSize(QSize(16, 16))

        clear_menu = QMenu(self.clear_selection_button)
        clear_menu.addAction("Unselect hidden", self._unselect_hidden)
        clear_menu.addAction("Unselect all", self._unselect_all)
        self.clear_selection_button.setText("Unselect")
        self.clear_selection_button.setMenu(clear_menu)
        more_menu = QMenu(self.more_button)
        more_menu.addAction("Copy paths", self._copy_checked_paths)
        more_menu.addSeparator()
        more_menu.addAction("Unselect hidden", self._unselect_hidden)
        more_menu.addAction("Unselect all", self._unselect_all)
        self.more_button.setText("More")
        self.more_button.setMenu(more_menu)
        self.more_button.hide()

        self._selection_shortcuts: list[QShortcut] = []
        for key, callback in (
            ("Ctrl+A", self._select_all_filtered),
            ("Delete", self._request_recycle),
        ):
            shortcut = QShortcut(QKeySequence(key), self.page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda action=callback, key=key: self._leave_focus_or(key, action))
            self._selection_shortcuts.append(shortcut)
        QApplication.instance().focusChanged.connect(self._selection_focus_changed)
        self._sync_selection_ui()
        QTimer.singleShot(0, self._responsive_selection)

    def _selection_focus_changed(self, _previous, current) -> None:
        """Release native Ctrl+A/Delete handling whenever text controls own focus."""
        editable = isinstance(current, (QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit))
        for shortcut in self._selection_shortcuts:
            shortcut.setEnabled(not editable)

    def _leave_focus_or(self, key: str, action) -> None:
        if key == "Escape" and self.page.property("imageFocus"):
            toggle_image_focus(self.page, False)
        else:
            self._selection_shortcut(action)

    def _selection_shortcut(self, callback) -> None:
        """Do not steal standard editing, pop-up or global generation shortcuts."""
        focus = QApplication.focusWidget()
        if focus is None or isinstance(
            focus, (QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit, QCheckBox)
        ):
            return
        # Shortcuts never operate from navigation/toolbar buttons or dialogs.
        if (
            isinstance(focus, QPushButton)
            and focus not in self._grid_buttons.values()
            and focus not in self._film_buttons
        ):
            return
        if self.page.findChild(QWidget, "galleryPromptSearch").hasFocus():
            return
        if focus is self.detail_check:
            return
        if isinstance(focus, QPushButton) and focus in (self.export_button, self.delete_button):
            return
        callback()

    def _responsive_selection(self) -> None:
        """Keep one toolbar row: secondary actions collapse into a Qt menu."""
        width = self.selection_bar.width()
        compact = width < 1190
        for button in (self.clear_selection_button, self.copy_paths_button):
            button.setVisible(not compact)
        self.select_filtered_button.setVisible(True)
        self.more_button.setVisible(compact)
        self._position_header_check()

    def _position_header_check(self) -> None:
        header = self.table.horizontalHeader()
        position = header.sectionViewportPosition(0)
        self._header_check.setVisible(position >= 0 and position < header.viewport().width())
        if position >= 0:
            self._header_check.setGeometry(position + 9, 5, 24, 24)

    def _selection_notice(self, text: str) -> None:
        """Surface bulk-action results without adding another toolbar row."""
        self.selection_count.setToolTip(text)
        self.selection_bar.setToolTip(text)
        window = self.page.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(text, 8000)

    def _sync_selection_ui(self) -> None:
        count = len(self.selection.checked)
        self.selection_count.setText(f"{count} selected")
        self.hidden_count_label.setText(f"{self._hidden_checks} hidden by filters")
        self.hidden_count_label.setVisible(self._hidden_checks > 0)
        label = (
            "Cancel selection"
            if self._selection_busy
            else ("Unselect all" if self._all_filtered_checked else "Select all")
        )
        self.select_filtered_button.setText(label)
        self.select_filtered_button.setAccessibleName(label)
        self.select_filtered_button.setToolTip(
            "Unselect every checked image, including hidden checks."
            if self._all_filtered_checked
            else "Select matching images across all pages, up to 10,000 checked in total."
        )
        self.select_filtered_button.setEnabled(not self._snapshot_at and (self.total > 0 or self._selection_busy))
        self.clear_selection_button.setEnabled(count > 0)
        self.copy_paths_button.setEnabled(count > 0 and not self._snapshot_at)
        self.export_button.setEnabled(self._export_callback is not None)
        callback = self._permanent_callback if self.removal_mode == "permanent" else self._recycle_callback
        self.delete_button.setEnabled(
            count > 0
            and not self._snapshot_at
            and not self._recycle_busy
            and self.removal_ready
            and callback is not None
        )
        self._widget("galleryRemovalMenuButton", QPushButton).setEnabled(self.removal_ready and not self._recycle_busy)
        self._sync_detail_checkbox()
        for row, item in enumerate(self.items):
            self._paint_checked_item(row, item.identity)
        self._sync_header_check()

    def _sync_detail_checkbox(self) -> None:
        if not hasattr(self, "detail_check"):
            return
        checked = self.selected_id in self.selection.checked if self.selected_id else False
        blocked = self.detail_check.blockSignals(True)
        self.detail_check.setChecked(checked)
        self.detail_check.setEnabled(self.selected_id is not None)
        self.detail_check.blockSignals(blocked)

    def _sync_header_check(self) -> None:
        if not hasattr(self, "_header_check"):
            return
        total = len(self.items)
        selected = sum(item.identity in self.selection.checked for item in self.items)
        state = (
            Qt.CheckState.Unchecked
            if selected == 0
            else Qt.CheckState.Checked
            if selected == total
            else Qt.CheckState.PartiallyChecked
        )
        blocked = self._header_check.blockSignals(True)
        self._header_check.setCheckState(state)
        self._header_check.setEnabled(total > 0)
        self._header_check.setToolTip(f"{selected} of {total} displayed images checked")
        self._header_check.blockSignals(blocked)

    def _paint_checked_item(self, row: int, identity: str) -> None:
        checked = identity in self.selection.checked
        check = self._check_boxes.get(identity)
        if check is not None:
            blocked = check.blockSignals(True)
            check.setChecked(checked)
            check.blockSignals(blocked)
        button = self._grid_buttons.get(identity)
        if button is not None and button.property("checkedSelection") != checked:
            button.setProperty("checkedSelection", checked)
            button.update()
        card = button.parentWidget() if button is not None else None
        if card is not None and card.property("checkedSelection") != checked:
            card.setProperty("checkedSelection", checked)
            card.style().unpolish(card)
            card.style().polish(card)
            card.update()
        cell = self.table.item(row, 0)
        if cell is not None:
            blocked = self.table.blockSignals(True)
            cell.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.table.blockSignals(blocked)
        if self.table.isVisible():
            first = self.table.visualRect(self.table.model().index(row, 0))
            last = self.table.visualRect(self.table.model().index(row, self.table.columnCount() - 1))
            self.table.viewport().update(first.united(last))

    def _after_selection_edit(self) -> None:
        self.recycle_scope_revision += 1
        if hasattr(self.page.window(), "_inline_feedback") and feedback(self.page)._armed:
            feedback(self.page).dismiss()
        if self._selection_busy:
            self._cancel_selection_scan()
        self._selection_generation += 1
        try:
            same_filter = self._hidden_query == self.export_query()
        except ValidationError:
            same_filter = False
        if same_filter:
            # Ordinary checkbox edits stay local: loaded rows match this filter,
            # and the last snapshot retains membership of off-page checked IDs.
            self._matching_checked.intersection_update(self.selection.checked)
            self._matching_checked.update(
                item.identity for item in self.items if item.identity in self.selection.checked
            )
            self._hidden_checks = len(self.selection.checked) - len(self._matching_checked)
            self._all_filtered_checked = self.total > 0 and len(self._matching_checked) == self.total
        else:
            self._hidden_query = None
            self._all_filtered_checked = False
        self._sync_selection_ui()
        if not same_filter:
            self._refresh_hidden_count()

    def _set_checked(self, identity: str, value: bool) -> None:
        if self._syncing_selection or (identity in self.selection.checked) == value:
            return
        if value and len(self.selection.checked) >= MAX_CHECKED_IMAGES:
            self._selection_notice("Selection limit reached: 10,000 images. Unselect some images first.")
            self._sync_selection_ui()
            return
        self.selection.toggle(identity)
        self._after_selection_edit()

    def _toggle_checked(self, identity: str) -> None:
        self._set_checked(identity, identity not in self.selection.checked)

    def _detail_check_toggled(self, value: bool) -> None:
        if self.selected_id:
            self._set_checked(self.selected_id, value)

    def _select_range(self, identity: str) -> None:
        ids = [item.identity for item in self.items]
        anchor = self.selection.anchor
        if anchor not in ids or identity not in ids:
            self._set_checked(identity, True)
            return
        a, b = ids.index(anchor), ids.index(identity)
        self.selection.add(ids[min(a, b) : max(a, b) + 1])
        self._after_selection_edit()

    def _table_item_changed(self, cell: QTableWidgetItem) -> None:
        if not self._syncing_selection and cell.column() == 0:
            identity = cell.data(Qt.ItemDataRole.UserRole)
            if identity:
                self._set_checked(str(identity), cell.checkState() == Qt.CheckState.Checked)

    def _table_clicked(self, cell: QTableWidgetItem) -> None:
        if cell.column() == 0:
            return
        identity = str(cell.data(Qt.ItemDataRole.UserRole))
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            self._select_range(identity)
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            self._toggle_checked(identity)
        else:
            self._open_from_list(identity)

    def _table_activated(self, cell: QTableWidgetItem) -> None:
        if cell.column() != 0:
            self._open_from_list(str(cell.data(Qt.ItemDataRole.UserRole)))

    def _toggle_visible_rows(self, *_args) -> None:
        ids = [item.identity for item in self.items]
        if ids and all(identity in self.selection.checked for identity in ids):
            self.selection.checked.difference_update(ids)
            self._after_selection_edit()
        else:
            self._select_visible_rows()

    def _select_visible_rows(self) -> None:
        wanted = [item.identity for item in self.items]
        added = self.selection.add(wanted)
        omitted = sum(identity not in self.selection.checked for identity in wanted)
        if omitted:
            self._selection_notice(f"{added} visible images added; {omitted} omitted at the 10,000-image limit.")
        self._after_selection_edit()

    def _unselect_all(self) -> None:
        if self.selection.checked:
            self.selection.clear()
            self._hidden_checks = 0
            self._after_selection_edit()

    def _begin_selection_scan(self, *, mode: str) -> None:
        if self._snapshot_at:
            return
        if self._selection_busy:
            return
        try:
            query = self.export_query()
        except ValidationError as exc:
            self._selection_notice(str(exc))
            return
        checked = self.selection.snapshot()
        self._selection_generation += 1
        generation = self._selection_generation
        self._selection_query = query
        self._selection_busy = True
        self.select_filtered_button.setText("Cancel selection")
        self.select_filtered_button.setEnabled(True)
        self._selection_notice("Resolving saved output identities… Click Cancel selection to retain previous checks.")
        strip = activity_strip(self.page)
        if strip is not None:
            strip.start("selection", "Gallery selection", self._cancel_selection_scan, stop_text="Cancel selection")
        self.runner.submit(
            asyncio.to_thread(
                gallery_storage.selection_candidates,
                self.state_dir,
                query,
                checked,
                add=mode == "add",
                maximum=MAX_CHECKED_IMAGES,
            ),
            lambda result, error: self._selection_scan_done(generation, mode, result, error),
        )

    def _cancel_selection_scan(self) -> None:
        self._selection_generation += 1
        self._selection_busy = False
        self._selection_query = None
        strip = activity_strip(self.page)
        if strip is not None:
            strip.finish("selection")
        self.select_filtered_button.setEnabled(True)
        self._sync_selection_ui()
        message = "Selection operation canceled; previous checks are retained."
        self._selection_notice(message)
        if strip is not None:
            strip.complete("selection", "Gallery selection stopped", message, "stopped")

    def _selection_scan_done(self, generation, mode, result, error) -> None:
        if generation != self._selection_generation:
            return
        self._selection_busy = False
        used_query = self._selection_query
        self._selection_query = None
        strip = activity_strip(self.page)
        if strip is not None:
            strip.finish("selection")
        self.select_filtered_button.setEnabled(True)
        self._sync_selection_ui()
        if error is not None:
            message = f"Selection query failed: {type(error).__name__}"
            self._selection_notice(message)
            if strip is not None:
                strip.complete("selection", "Gallery selection failed", message, "error")
            return
        additions, hidden, omitted, matching = result
        self._hidden_query = used_query
        self._matching_checked = set(matching) | set(additions)
        if mode == "add":
            added = self.selection.add(additions)
            self._hidden_checks = hidden
            self._selection_notice(
                f"{added} added; {omitted} matching images not added at the 10,000-image limit."
                if omitted
                else f"{added} images added to the checked selection."
            )
        elif mode == "clear-hidden":
            self.selection.checked.intersection_update(matching)
            if self.selection.anchor not in self.selection.checked:
                self.selection.anchor = None
            self._hidden_checks = 0
        self._after_selection_edit()
        if strip is not None:
            message = (
                f"{added} added; {omitted} omitted at the 10,000-image limit."
                if mode == "add"
                else "Hidden checks cleared."
            )
            strip.complete("selection", "Gallery selection updated", message, "partial" if omitted else "success")

    def _toggle_all_filtered(self) -> None:
        """Toggle the permanent toolbar action, without changing Ctrl+A semantics."""
        if self._selection_busy:
            self._cancel_selection_scan()
            return
        try:
            same_filter = self._hidden_query == self.export_query()
        except ValidationError:
            same_filter = False
        if self._all_filtered_checked and same_filter:
            self._unselect_all()
        else:
            self._select_all_filtered()

    def _select_all_filtered(self) -> None:
        self._begin_selection_scan(mode="add")

    def _unselect_hidden(self) -> None:
        # Re-resolve the current filter even if an earlier hidden-count query
        # has not completed. Never rely on a stale label to decide scope.
        if self.selection.checked:
            self._begin_selection_scan(mode="clear-hidden")

    def _refresh_hidden_count(self) -> None:
        if self._snapshot_at:
            self._sync_selection_ui()
            return
        if self._selection_busy:
            return
        checked = self.selection.snapshot()
        if not checked:
            self._hidden_checks = 0
            try:
                self._hidden_query = self.export_query()
            except ValidationError:
                self._hidden_query = None
            self._sync_selection_ui()
            return
        try:
            query = self.export_query()
        except ValidationError:
            return
        if query == self._hidden_query:
            return
        self._selection_generation += 1
        generation = self._selection_generation
        self.runner.submit(
            asyncio.to_thread(gallery_storage.selection_candidates, self.state_dir, query, checked),
            lambda result, error: self._hidden_count_done(generation, query, result, error),
        )

    def _hidden_count_done(self, generation, query, result, error) -> None:
        if generation != self._selection_generation:
            return
        if error is not None:
            self._selection_notice("Hidden checked-image count could not be refreshed.")
            return
        self._hidden_checks = result[1]
        self._matching_checked = set(result[3])
        self._all_filtered_checked = self.total > 0 and len(result[3]) == self.total
        self._hidden_query = query
        self._sync_selection_ui()

    def _copy_checked_paths(self) -> None:
        if self._snapshot_at:
            return
        ids = self.selection.snapshot()
        if not ids:
            return
        self.runner.submit(
            asyncio.to_thread(copy_output_paths, self.state_dir, ids),
            self._paths_resolved,
        )

    def _paths_resolved(self, result, error) -> None:
        if error is not None or result is None:
            self._selection_notice("Checked paths could not be resolved.")
            return
        paths, missing = result
        if paths:
            QApplication.clipboard().setText("\n".join(paths))
        self._selection_notice(f"Copied {len(paths)} paths; {missing} records unavailable.")

    def _open_checked_export(self) -> None:
        """Opening export settings is independent of the number of checked images."""
        if self._export_callback is not None:
            self._export_callback()

    def _request_recycle(self) -> None:
        """Only the confirmation controller can authorize a Recycle Bin operation."""
        self.video_player.release()
        if self._snapshot_at:
            return
        callback = self._permanent_callback if self.removal_mode == "permanent" else self._recycle_callback
        if self.selection.checked and self.removal_ready and not self._recycle_busy and callback is not None:
            try:
                query = self.export_query()
            except ValidationError as exc:
                self._selection_notice(str(exc))
                return
            callback(self.selection.snapshot(), self._hidden_checks, query)

    def set_recycle_busy(self, busy: bool) -> None:
        """Prevent overlapping deletion requests without blocking ordinary browsing."""
        self._recycle_busy = busy
        self._sync_selection_ui()

    def recycle_completed(self, recycled: tuple[str, ...]) -> None:
        """Clear settled removal identities; retain failed/uncertain checks."""
        if recycled:
            self.selection.checked.difference_update(recycled)
            if self.selection.anchor in recycled:
                self.selection.anchor = None
            self._hidden_query = None
            self._after_selection_edit()
        self.refresh(preserve=True, trigger="removal")

    def _restore_preferences(self) -> None:
        document = load_viewer(self.state_dir)
        self.slideshow.restore_preferences(document)
        self.video_player.autoplay = document.get("video_autoplay") is True
        query_doc = document.get("query", {})
        try:
            stored_query = GalleryQuery(**query_doc)
        except (TypeError, ValidationError):
            stored_query = GalleryQuery(limit=_read_limit())
        # A different -ml batch size must still start on a batch boundary.
        self.offset = stored_query.offset - stored_query.offset % PAGE_SIZE
        filters = document.get("filters", {}) if isinstance(document.get("filters"), dict) else {}
        self.prompt.setText(str(filters.get("prompt", stored_query.prompt)))
        self.model.setCurrentText(str(filters.get("model", stored_query.model or "All models")))
        self._set_lora_filter(str(filters.get("lora", stored_query.lora or "All LoRAs")))
        self.date_filter.setCurrentText(str(filters.get("date", "All time")))
        dimensions = filters.get("dimensions")
        if dimensions is None and stored_query.width and stored_query.height:
            dimensions = f"{stored_query.width} × {stored_query.height}"
        self.dimensions.setCurrentText(str(dimensions or "Any size"))
        self.ratio.setCurrentText(str(filters.get("ratio", stored_query.ratio or "Any ratio")))
        self.sort.setCurrentText(str(filters.get("sort", SORT_NAMES.get(stored_query.sort, "Newest first"))))
        self.mode = str(document.get("view", "Grid"))
        if self.mode not in self.modes:
            self.mode = "Grid"
        self._restore_thumbnail_percents(document)
        self.slider.setValue(self._thumbnail_percents[self.mode])
        selected = document.get("selected")
        self.selected_id = selected if isinstance(selected, str) and selected else None
        auto = bool(document.get("auto_refresh", True))
        self._apply_live(0 if not auto else 2 if bool(document.get("follow", True)) else 1)
        scroll = document.get("scroll")
        if isinstance(scroll, dict):
            for name in self._scroll:
                value = scroll.get(name)
                if type(value) is int and value >= 0:
                    self._scroll[name] = value

    def _restore_thumbnail_percents(self, document: dict) -> None:
        def valid(value) -> bool:
            return type(value) is int and MIN_THUMBNAIL_PERCENT <= value <= MAX_THUMBNAIL_PERCENT

        # Preferences written before per-view zoom hold one shared value; it seeds
        # all three views. PR #34 initially persisted the raw pixel size under
        # "density", which is converted to the percentage contract.
        shared = document.get("thumbnail_scale_percent")
        if not valid(shared):
            legacy_density = document.get("density")
            if type(legacy_density) is int and legacy_density > 0:
                migrated = round(legacy_density * 100 / STANDARD_THUMBNAIL_PX)
                shared = max(MIN_THUMBNAIL_PERCENT, min(MAX_THUMBNAIL_PERCENT, migrated))
            else:
                shared = None
        stored = document.get("thumbnail_scale_percents")
        stored = stored if isinstance(stored, dict) else {}
        for mode in self._thumbnail_percents:
            value = stored.get(mode)
            if valid(value):
                self._thumbnail_percents[mode] = value
            elif shared is not None:
                self._thumbnail_percents[mode] = shared

    def set_active(self, active: bool) -> None:
        first_activation = active and not self._active and not self.items
        returning = active and not self._active
        self._active = active
        if not active:
            self.video_player.release(show_play=True)
            self.slideshow.stop()
            self._cancel_open_output()
        self._sync_canvas_loading()
        self._sync_snapshot_warning()
        self._sync_auto_refresh()
        if first_activation:
            self.refresh(preserve=True, trigger="activation")
        elif active:
            self._queue_neighbor_prefetch()
            # Catch up immediately on images added while another tab was open; the cheap
            # metadata check decides whether a read is needed.
            self._auto_refresh_tick()
        if returning and self.mode == "Detail" and self.video_player.autoplay and not self.video_player.playing:
            self._play_video()
        if active and self._pending_page_jump is not None:
            self._pending_jump_timer.start()

    def notify_changed(self) -> None:
        """In-process hint that saved outputs changed; polling remains for other processes."""
        if self._active and self.auto_button.isChecked() and not self.runner.closed:
            self.push_timer.start()

    def _sync_auto_refresh(self) -> None:
        enabled = self.auto_button.isChecked()
        self._sync_live_text()
        if enabled and self._active:
            self.auto_timer.start()
        else:
            self.auto_timer.stop()
            if not enabled:
                self._auto_signature = None

    def _auto_refresh_tick(self) -> None:
        """Check for gallery changes without rebuilding unchanged pages on each poll."""
        if not self._active or not self.auto_button.isChecked() or self.runner.closed:
            return
        reasons = []
        if self._loading:
            reasons.append("query")
        if self._adjacent_loading:
            reasons.append("adjacent")
        if self.preparation.active:
            reasons.append("preparation")
        if self._grid_mount_keys is not None:
            reasons.append("mount")
        if self._pending_page_jump is not None:
            reasons.append("page_jump")
        if self._suppress_batch_scroll:
            reasons.append("anchor_restore")
        if self.filter_timer.isActive():
            reasons.append("filter_debounce")
        if self._auto_checking:
            reasons.append("signature_check")
        if reasons:
            now = monotonic()
            if perf_enabled() and (reasons != self._auto_skip_reasons or now - self._auto_skip_at >= 10):
                self._auto_skip_reasons, self._auto_skip_at = reasons, now
                measurement(
                    log,
                    "gallery.auto_refresh",
                    reasons=reasons,
                    mounted=len(self.items),
                    action="skipped",
                )
            return
        # A snapshot/failure episode needs every live retry and its existing
        # two-success recovery semantics; a file-stat hint cannot gate recovery.
        if self._snapshot_at or self._snapshot_warning_episode or self._refresh_failing:
            if monotonic() - self._auto_last_full < LIVE_RECOVERY_INTERVAL_S:
                return
            if perf_enabled():
                measurement(
                    log, "gallery.auto_refresh", action="started", reason="live_recovery", mounted=len(self.items)
                )
            self._auto_last_full = monotonic()
            self.refresh(preserve=True, trigger="live_recovery")
            return
        self._auto_checking = True
        self.runner.submit(gallery_file_signature(self.state_dir), self._auto_signature_done)

    def _auto_signature_done(self, signature, error) -> None:
        """Use inexpensive file metadata as a hint, with a bounded full-read fallback."""
        self._auto_checking = False
        if error is not None and not self.runner.closed and perf_enabled():
            measurement(log, "gallery.auto_refresh", action="signature_error", reason=type(error).__name__)
        if (
            self.runner.closed
            or not self._active
            or not self.auto_button.isChecked()
            or self._loading
            or self._adjacent_loading
            or self.preparation.active
            or self._grid_mount_keys is not None
            or self._pending_page_jump is not None
            or self.filter_timer.isActive()
        ):
            if not self.runner.closed and perf_enabled():
                reasons = [
                    name
                    for name, active in (
                        ("inactive", not self._active),
                        ("disabled", not self.auto_button.isChecked()),
                        ("query", self._loading),
                        ("adjacent", self._adjacent_loading),
                        ("preparation", self.preparation.active),
                        ("mount", self._grid_mount_keys is not None),
                        ("page_jump", self._pending_page_jump is not None),
                        ("filter_debounce", self.filter_timer.isActive()),
                    )
                    if active
                ]
                measurement(
                    log, "gallery.auto_refresh", action="late_skipped", reasons=reasons, mounted=len(self.items)
                )
            return
        now = monotonic()
        due = now - self._auto_last_full >= AUTO_REFRESH_FULL_INTERVAL_S
        changed = (
            error is not None
            or signature is None
            or signature != self._auto_signature
            or self._snapshot_at is not None
            or self._snapshot_warning_episode
            or self._refresh_failing
        )
        if not changed and not due:
            self._auto_unchanged_polls += 1
            if perf_enabled() and self._auto_unchanged_polls % 6 == 0:
                measurement(
                    log,
                    "gallery.auto_refresh",
                    checks=self._auto_unchanged_polls,
                    mounted=len(self.items),
                    action="unchanged",
                )
            return
        if perf_enabled():
            measurement(
                log,
                "gallery.auto_refresh",
                reason="changed" if changed else "periodic",
                skipped_checks=self._auto_unchanged_polls,
                mounted=len(self.items),
                action="started",
            )
        self._auto_unchanged_polls = 0
        self._auto_signature = signature if error is None else None
        self._auto_last_full = now
        self.refresh(preserve=True, trigger="auto")

    def _auto_refresh_changed(self, checked: bool) -> None:
        # Live toggled by the user (or setChecked from elsewhere): on is Live only, off also drops Follow.
        if not self._syncing_live:
            self._apply_live(1 if checked else 0)

    def _follow_clicked(self) -> None:
        state = self._live_state
        if state == "paused":
            self._jump_to_newest()  # resume; the ring plays when the state returns to "following"
        elif state == "following":
            self._apply_live(1)
        else:  # off, live, newest-only: Follow implies Live, one click from Off
            self._apply_live(2)

    def follow_newest(self) -> None:
        """Enable Live + Follow and return to the newest matching image in the current view."""
        if self.runner.closed:
            return
        self.slideshow.stop()
        with QSignalBlocker(self.sort):
            self.sort.setCurrentText("Newest first")
        already_following = self._live_mode == 2
        self._apply_live(2)
        if already_following:
            self._jump_to_newest()  # F5 resumes a paused Follow instead of toggling it off.
            self._schedule_save()  # sort may have changed even though the mode did not

    def _jump_to_newest(self) -> None:
        if self.mode == "Detail":
            self._pending_page_selection = "first"
        self._page_scroll_target = "start"
        self.refresh(reset=True, preserve=False, trigger="follow")

    def _apply_live(self, mode: int) -> None:
        """Set Live to 0 Off, 1 Auto or 2 Follow; the single place that changes the mode."""
        previous = self._live_mode
        self._live_mode = mode
        self._syncing_live = True
        try:
            self.auto_button.setChecked(mode >= 1)
        finally:
            self._syncing_live = False
        self._sync_auto_refresh()
        if mode == previous:
            return
        if mode == 0 and self._snapshot_live_since is not None:
            # A successful live read already recovered; stopping polling must
            # not leave its presentation waiting for a check that will not run.
            self._end_snapshot_warning_episode()
            self._sync_snapshot_warning()
        if mode == 2 and not self._restoring and SORT_LABELS.get(self.sort.currentText(), "newest") == "newest":
            # Turning Follow on goes straight to the newest image in every view.
            self._jump_to_newest()
        self._schedule_save()

    def _following_newest(self) -> bool:
        """True while the view sits on the newest image of a newest-first list (Follow would act)."""
        if self.offset != 0 or self.sort.currentText() not in ("Newest first", "") or not self.items:
            return False
        if self.mode == "Detail":
            return self.selected_id == self.items[0].identity
        if self._density_anchor is not None or self._suppress_batch_scroll:
            return False
        bar = self.grid_scroll.verticalScrollBar() if self.mode == "Grid" else self.table.verticalScrollBar()
        return bar.value() == 0

    def _live_visual_state(self) -> str:
        if self._live_mode == 0:
            return "off"
        if self._live_mode == 1:
            return "live"
        if self.sort.currentText() not in ("Newest first", ""):
            return "newest-only"
        if self.items and not self._following_newest():
            return "paused"
        return "following"

    def _sync_live_text(self) -> None:
        """Draw the Live control: liveState on both buttons, icons, tooltips and the motion effects."""
        state = self._live_visual_state()
        if state == self._live_state:
            return
        previous, self._live_state = self._live_state, state
        shown = "paused" if state == "newest-only" else state
        for button in (self.auto_button, self.follow_button):
            button.setProperty("liveState", shown)
            button.style().unpolish(button)
            button.style().polish(button)
        self.auto_button.setText("Live")
        self.auto_button.setIcon(
            live_dot_icon(
                self._live_mode >= 1, QColor(self._tokens["color.focus"]), QColor(self._tokens["color.secondary"])
            )
        )
        self.auto_button.setToolTip(
            "Live updates on. Click to turn off." if self._live_mode else "Live updates off. Click to turn on."
        )
        self.follow_button.setIcon(self._follow_icons[state == "following"])
        self.follow_button.setAccessibleName(
            "Follow newest image (paused)" if shown == "paused" else "Follow newest image"
        )
        self.follow_button.setToolTip(LIVE_FOLLOW_TIPS[state])
        self._live_fx.apply(previous, state)

    def _follow_applies(self, query: GalleryQuery, page: GalleryPage) -> bool:
        """Follow keeps the viewer on the newest image, but only when it is already there.

        Scrolling away from the top (Grid, List) or selecting an older image (Detail) pauses
        it implicitly (the button reads "Follow (paused)"), so new arrivals never pull the view away from
        an inspected image; it resumes when the view returns to the newest image.
        """
        if self._live_mode != 2 or query.sort != "newest" or query.offset != 0:
            return False
        if not page.items or (self.items and self.items[0].identity == page.items[0].identity):
            return False
        return self._following_newest()

    def _filter_edited(self, *_args) -> None:
        self._cancel_open_output()
        self.recycle_scope_revision += 1
        if hasattr(self.page.window(), "_inline_feedback") and feedback(self.page)._armed:
            feedback(self.page).dismiss()
        if self._restoring:
            return
        self.filter_timer.start()
        self._schedule_save()

    def _apply_filter_change(self) -> None:
        """A filter/sort change starts over: spinner, then the top of page 1."""
        self._clear_on_refresh = True
        self.refresh(reset=True, preserve=False, trigger="filter")

    def _clear_results_for_reload(self) -> None:
        # Detail keeps its viewer; the reset read reselects within page 1.
        if self.mode == "Detail":
            return
        self.items = []
        self._render_page()
        self._rendered_page = (0, ())
        self._page_scroll_target = "start"
        for bar in (self.grid_scroll.verticalScrollBar(), self.table.verticalScrollBar()):
            bar.setValue(0)

    def _date_query(self) -> tuple[str | None, str | None]:
        return _date_bounds(self.date_filter.currentText())

    def _query(self, *, offset: int | None = None) -> GalleryQuery:
        since, until = self._date_query()
        dimensions_text = self.dimensions.currentText().strip()
        width = height = None
        size_class = SIZE_CLASS_LABELS.get(dimensions_text, "")
        if dimensions_text and dimensions_text.casefold() != "any size" and not size_class:
            match = DIMENSIONS.match(dimensions_text)
            if not match:
                raise ValidationError("Dimensions must look like WIDTH × HEIGHT.")
            width, height = int(match.group(1)), int(match.group(2))

        ratio_text = self.ratio.currentText().strip()
        ratio = ""
        if ratio_text and ratio_text.casefold() != "any ratio":
            lowered = ratio_text.casefold()
            ratio = lowered if lowered in ("square", "portrait", "landscape") else ratio_text.replace("×", ":")
        model = self.model.currentText().strip()
        lora = self._lora_identifier()
        sort = SORT_LABELS.get(self.sort.currentText(), "newest")
        return GalleryQuery(
            prompt=self.prompt.text(),
            negative="",
            model="" if model.casefold() == "all models" else model,
            lora="" if lora.casefold() == "all loras" else lora,
            since=since,
            until=until,
            ratio=ratio,
            width=width,
            height=height,
            size_class=size_class,
            sort=sort,
            offset=self.offset if offset is None else offset,
            limit=_read_limit(),
        )

    def refresh(self, *, reset: bool = False, preserve: bool = True, trigger: str = "refresh") -> None:
        if self._open_request is not None and trigger in ("auto", "live_recovery"):
            return
        if self._loading or self._adjacent_loading:
            if self._pending_refresh is not None:
                reset = reset or self._pending_refresh[0]
                preserve = preserve and self._pending_refresh[1]
                if trigger == "auto":
                    # Polling never relabels an explicit deferred request.
                    trigger = self._pending_refresh[2]
            self._pending_refresh = (reset, preserve, trigger)
            return
        self._capture_scroll()
        self._prefetch_zone_hint = None
        if reset:
            self._pending_page_jump = None
            self._pending_jump_timer.stop()
            self.offset = 0
        try:
            query = self._query(offset=self.offset)
        except ValidationError as exc:
            self.refresh_button.setToolTip(str(exc))
            self._clear_on_refresh = False
            return
        if self._clear_on_refresh:
            # Busy reads deferred this through _pending_refresh; clear now.
            self._clear_on_refresh = False
            self._clear_results_for_reload()

        anchor = self._browsing_anchor() if preserve else None
        # Preserve the mounted window through independently bounded reads;
        # developer launch overrides may exceed the per-query cap.
        target_count = min(MAX_MOUNTED_IMAGES, len(self.items)) if preserve and not reset and self.items else PAGE_SIZE
        if target_count > query.limit:
            query = replace(query, limit=min(MAX_QUERY_ITEMS, target_count))
        self._submit_query(query, anchor=anchor, target_count=target_count, trigger=trigger)

    def _submit_query(
        self,
        query: GalleryQuery,
        *,
        anchor: str | None,
        target_count: int | None = None,
        trigger: str = "navigation",
    ) -> None:
        if self.runner.closed:
            self._cancel_open_output()
            return
        if trigger != "open-output":
            self._cancel_open_output()
        target_count = min(MAX_MOUNTED_IMAGES, target_count if target_count is not None else query.limit)
        self._query_generation += 1
        generation = self._query_generation
        if trigger == "open-output":
            self._open_generation = generation
        self._held_anchor = None
        # A new query supersedes any older adjacent anchor restoration.
        # Obsolete callbacks must not retain (or clear) its scroll guard.
        self._suppress_batch_scroll = False
        self._neighbor_prefetch_timer.stop()
        self._query_started = monotonic()
        if perf_enabled():
            measurement(
                log,
                "gallery.query_start",
                trigger=trigger,
                offset=query.offset,
                limit=query.limit,
                target_count=target_count,
                mounted=len(self.items),
            )
        self._loading = True
        self._adjacent_failed_offset = None
        if not self.items and self._rendered_page is None:
            # The initial Designer examples are not saved-output records.
            self._clear_grid()
        # Do not gray out the Refresh control on every five-second poll:
        # the previously loaded cards remain readable throughout the query.
        if not self.items:
            self.refresh_button.setEnabled(False)
        # Existing cards are still readable: no transient disabled chips/status
        # flash every five seconds during an ordinary background refresh.
        self._sync_pager(loading=not self.items)
        self._sync_canvas_loading()

        async def extend_page(current: GalleryQuery, page: GalleryPage, read) -> GalleryPage:
            return await _extend_page(current, page, read, target_count)

        async def work():
            current = query
            anchor_found = False
            if anchor:
                locate_started = monotonic()
                position = await locate_output(self.state_dir, replace(current, offset=0), anchor)
                if perf_enabled():
                    measurement(
                        log,
                        "gallery.query_locate",
                        elapsed_ms=(monotonic() - locate_started) * 1000,
                        found=position is not None,
                    )
                if position is not None:
                    anchor_found = True
                    # Keep the current contiguous window when the anchor is still
                    # inside it; otherwise jump directly to its aligned batch.
                    if not (current.offset <= position < current.offset + target_count):
                        current = replace(current, offset=(position // PAGE_SIZE) * PAGE_SIZE)
            page_started = monotonic()
            page = await query_outputs(self.state_dir, current)
            if perf_enabled():
                measurement(
                    log,
                    "gallery.query_read",
                    elapsed_ms=(monotonic() - page_started) * 1000,
                    offset=current.offset,
                    returned=len(page.items),
                )
            if page.total and not page.items and current.offset >= page.total:
                current = replace(current, offset=((page.total - 1) // PAGE_SIZE) * PAGE_SIZE)
                page = await query_outputs(self.state_dir, current)
            page = await extend_page(current, page, lambda q: query_outputs(self.state_dir, q))
            facets_started = monotonic()
            choices = await filter_choices(self.state_dir)
            if perf_enabled():
                measurement(
                    log,
                    "gallery.query_facets",
                    elapsed_ms=(monotonic() - facets_started) * 1000,
                )
            return current, page, choices, anchor_found

        async def with_fallback():
            try:
                return await work()
            except PersistenceError as live_error:
                if trigger == "open-output":
                    raise  # an explicit identity handoff needs fresh committed authority, not a browsing snapshot
                cause = live_error.__cause__ or live_error
                diagnostic_event(
                    log,
                    "gallery.live_query_unavailable",
                    level=logging.DEBUG,
                    data={
                        "reason": type(cause).__name__,
                        "sqlite_code": getattr(cause, "sqlite_errorname", "unavailable"),
                    },
                )
                try:
                    current, page, choices, anchor_found = await query_snapshot(self.state_dir, query, anchor)

                    async def snapshot_read(q: GalleryQuery) -> GalleryPage:
                        _query, found, _choices, _anchor_found = await query_snapshot(self.state_dir, q, None)
                        return found

                    page = await extend_page(current, page, snapshot_read)
                    return current, page, choices, anchor_found
                except (PersistenceError, OSError):
                    raise live_error from None

        worker_finished = [None]

        async def timed_query():
            try:
                return await with_fallback()
            finally:
                worker_finished[0] = monotonic()

        def delivered(result, error):
            if perf_enabled() and worker_finished[0] is not None:
                measurement(
                    log,
                    "gallery.query_delivery",
                    elapsed_ms=(monotonic() - worker_finished[0]) * 1000,
                    generation_current=generation == self._query_generation,
                )
            self._query_done(generation, anchor, result, error)

        self.runner.submit(timed_query(), delivered)

    def _query_done(self, generation: int, anchor: str | None, result, error) -> None:
        if generation != self._query_generation:
            return
        if self.slideshow.scrubbing:
            self.slideshow.deferred_query = (False, (generation, anchor, result, error))
            return
        if perf_enabled():
            elapsed_ms = (monotonic() - self._query_started) * 1000
            measurement(
                log,
                "gallery.query_done",
                elapsed_ms=elapsed_ms,
                returned=len(result[1].items) if result is not None and error is None else 0,
                source="snapshot" if result is not None and error is None and result[1].snapshot_at else "live",
                error=type(error).__name__ if error else None,
            )
        if error is None and result is not None and self._pending_refresh is None:
            self._prepare_page(
                result[1].items,
                generation,
                lambda: self._apply_query_done(generation, anchor, result, error),
            )
        else:
            self._apply_query_done(generation, anchor, result, error)

    def _apply_query_done(self, generation: int, anchor: str | None, result, error) -> None:
        if generation != self._query_generation:
            return
        if self.slideshow.scrubbing:
            self.slideshow.deferred_query = (True, (generation, anchor, result, error))
            return
        opening = anchor is not None and anchor == self._open_request and generation == self._open_generation
        self._loading = False
        self.refresh_button.setEnabled(True)
        self._sync_pager()
        if error is not None or (result is not None and result[1].snapshot_at):
            self._snapshot_live_since = None
        if opening:
            self._pending_refresh = None  # an automatic refresh must not supersede explicit identity navigation
        if self._pending_refresh is not None:
            pending = self._pending_refresh
            self._pending_refresh = None
            self.refresh(reset=pending[0], preserve=pending[1], trigger=pending[2])
            return
        if error is not None:
            if opening:
                self._open_output_failed("query_failed")
            # Auto-refresh retries every few seconds; report a failure streak once.
            detail = str(error) if isinstance(error, PersistenceError) else type(error).__name__
            (log.debug if self._refresh_failing else log.warning)("gallery refresh failed reason=%s", detail)
            self._show_refresh_failure(error, first=not self._refresh_failing)
            self._refresh_failing = True
            self._sync_canvas_loading()
            return
        recovered = self._refresh_failing
        if recovered:
            self._refresh_failing = False
            # Force a normal reconciliation, not first-render fixture teardown.
            diagnostic_event(
                log,
                "gallery.refresh_recovered",
                level=logging.INFO,
                data={},
            )

        query, page, choices, anchor_found = result
        mode_changed = self._snapshot_at != page.snapshot_at
        if mode_changed:
            self.recycle_scope_revision += 1
            inline = feedback(self.page)
            if inline._armed and inline._armed[0] is self.delete_button:
                inline.dismiss()
            self._presented_item = None
            diagnostic_event(
                log,
                "gallery.source_selected",
                level=logging.INFO,
                data={
                    "source": "snapshot" if page.snapshot_at else "live",
                },
            )
        if page.snapshot_at:
            self._snapshot_warning_episode = True
            self._snapshot_live_since = None
        elif self._snapshot_warning_episode:
            now = monotonic()
            if not self.auto_button.isChecked() or (
                self._snapshot_live_since is not None and now - self._snapshot_live_since >= SNAPSHOT_RECOVERY_GRACE_S
            ):
                # Manual refresh needs no second check when polling is off.
                # With polling on, keep the presentation-only stability grace.
                self._end_snapshot_warning_episode()
            elif self._snapshot_live_since is None:
                self._snapshot_live_since = now
        self._snapshot_at = page.snapshot_at
        self._sync_snapshot_warning()
        status = self._widget("gallerySnapshotStatus", QLabel)
        status.setVisible(bool(self._snapshot_at))
        if self._snapshot_at:
            status.setText(
                f"Read-only snapshot · {self._snapshot_at} · Live updates unavailable; retrying automatically."
            )
        following = not opening and not page.snapshot_at and self._follow_applies(query, page)
        self.offset = query.offset
        self.items = list(page.items)
        self.total = page.total
        self.slideshow.refreshed()
        if following:
            self._page_scroll_target = "start"
            if self.mode == "Detail":
                self.selected_id = page.items[0].identity
        self._sync_pager()
        self._sync_canvas_loading()
        # Refresh may add/remove outputs even when filters and visible cards match.
        self._hidden_query = None
        self._all_filtered_checked = False
        if self.mode == "Detail" and anchor == self.selected_id and anchor and not anchor_found:
            self.selected_id = None
        self._populate_facets(*choices)
        visible_page = (query.offset, page.items)
        if (
            visible_page == self._rendered_page
            and not recovered
            and not mode_changed
            and self._pending_page_selection is None
            and self._page_scroll_target is None
            and self._open_request is None
        ):
            # Polling identical results must not destroy cards/cells, discard
            # in-flight decodes or reset Detail zoom/pan and browsing position.
            # The total can change outside this page without changing its pixels.
            self._update_navigation()
            QTimer.singleShot(0, self._sync_pager)
            if self.mode == "Detail" and self._selected() is not None:
                position = (
                    self.offset + next(i for i, item in enumerate(self.items) if item.identity == self.selected_id) + 1
                )
                self._widget("galleryPositionOverlay", QLabel).setText(f"{position} / {self.total}")
            self.refresh_button.setToolTip(self._page_summary())
            self._refresh_hidden_count()
            self._schedule_save()
            return
        self._render_page()
        self._rendered_page = visible_page
        self._refresh_hidden_count()

        if opening:
            target = next((item for item in self.items if item.identity == anchor), None)
            if target is not None and not target.issue and not page.snapshot_at:
                self._cancel_open_output("opened")
                self.selected_id = anchor
                self.show_view("Detail")
            else:
                self._open_output_failed()

        pending = self._pending_page_selection
        self._pending_page_selection = None
        if pending and self.items:
            self.select(self.items[0 if pending == "first" else -1].identity)
        elif self.mode == "Detail":
            if self.selected_id and any(item.identity == self.selected_id for item in self.items):
                self.select(self.selected_id, open_detail=False)
            elif self.items:
                self.select(self.items[0].identity, open_detail=False)
            else:
                self._clear_detail()
        elif not self.items:
            self._clear_detail()

        self.refresh_button.setToolTip(self._page_summary())
        self._restore_scroll_later(anchor if anchor_found else None)
        QTimer.singleShot(0, self._sync_pager)
        self._schedule_save()

    def _end_snapshot_warning_episode(self):
        self._snapshot_warning_episode = False
        self._snapshot_warning_dismissed = False
        self._snapshot_live_since = None
        diagnostic_event(
            log,
            "gallery.live_access_recovered",
            level=logging.INFO,
            data={
                "auto_refresh": self._live_mode >= 1,
            },
        )

    def _dismiss_snapshot_warning(self):
        self._snapshot_warning_dismissed = True

    def _sync_snapshot_warning(self):
        strip = activity_strip(self.page)
        if strip is None:
            return
        key = "gallery-snapshot"
        if not self._snapshot_warning_episode or not self._active or self._snapshot_warning_dismissed:
            if key in strip.rows:
                strip.finish(key)
            return
        text = (
            f"Live gallery unavailable · Read-only snapshot {self._snapshot_at} · Retrying automatically"
            if self._snapshot_at
            else "Live gallery access recovering · Checking stability"
        )
        strip.status(key, text, self._dismiss_snapshot_warning)

    def _lora_labels(self, identifiers: tuple[str, ...]) -> tuple[str, ...]:
        """Shown names for the LoRA filter: ShortName[Tag], with the filename added where two would read the same."""
        labels = [display_label(identifier) for identifier in identifiers]
        return tuple(
            label if labels.count(label) == 1 else f"{label} ({identifier})"
            for label, identifier in zip(labels, identifiers, strict=True)
        )

    def _lora_identifier(self) -> str:
        """The recorded identifier the filter means: the picked item's data, else a unique shown name, else the text."""
        combo = self.lora
        text = combo.currentText().strip()
        index = combo.currentIndex()
        if index >= 0 and combo.itemText(index) == combo.currentText():
            data = combo.itemData(index)
            return str(data) if data else text
        matches = [
            combo.itemData(i)
            for i in range(1, combo.count())
            if combo.itemText(i).casefold() == text.casefold() and combo.itemData(i)
        ]
        return str(matches[0]) if len(matches) == 1 else text

    def _set_lora_filter(self, identifier: str) -> None:
        index = self.lora.findData(identifier) if identifier else -1
        if index >= 0:
            self.lora.setCurrentIndex(index)
        else:
            self.lora.setCurrentText(identifier)

    def refresh_lora_labels(self) -> None:
        """Re-show the LoRA filter names after the saved short names arrive, keeping the chosen identifier."""
        identifiers = tuple(str(self.lora.itemData(i)) for i in range(1, self.lora.count()) if self.lora.itemData(i))
        if identifiers:
            self._populate_facets(self._facet_models, identifiers)

    def _populate_facets(self, models: tuple[str, ...], loras: tuple[str, ...]) -> None:
        self._facet_models = models
        labels = self._lora_labels(loras)
        for combo, placeholder, values in (
            (self.model, "All models", models),
            (self.lora, "All LoRAs", labels),
            (self.dimensions, "Any size", tuple(SIZE_CLASS_LABELS)),
        ):
            if combo is self.lora:
                current = self._lora_identifier()
                if tuple(combo.itemText(i) for i in range(combo.count())) == (placeholder, *values):
                    continue
                combo.blockSignals(True)
                combo.clear()
                combo.addItem(placeholder)
                for label, identifier in zip(values, loras, strict=True):
                    combo.addItem(label, identifier)
                self._set_lora_filter(current if current and current != placeholder else placeholder)
                combo.blockSignals(False)
                continue
            current = combo.currentText()
            if tuple(combo.itemText(i) for i in range(combo.count())) == (placeholder, *values):
                continue
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder)
            combo.addItems(values)
            combo.setCurrentText(current or placeholder)
            combo.blockSignals(False)

    def _set_grid_message(self, text: str) -> None:
        """Show one wrapped status line in place of Grid cards."""
        if self._grid_empty is None:
            self._grid_empty = QLabel(self.grid_cards)
            self._grid_empty.setProperty("role", "secondary")
            self._grid_empty.setWordWrap(True)
            self.cards_layout.addWidget(self._grid_empty, 0, 0)
        self._grid_empty.setText(text)

    def _show_refresh_failure(self, error: BaseException, *, first: bool) -> None:
        """Make a failed refresh visible; cards already shown stay as they were."""
        message = (
            str(error) if isinstance(error, PersistenceError) else "Saved outputs could not be refreshed."
        ) + " Retrying automatically."
        self.refresh_button.setToolTip(f"Saved outputs could not be refreshed: {error}")
        if not self.items:
            # Nothing loaded yet: an empty Grid would otherwise look like an empty library.
            self._set_grid_message(message)
        if first:
            feedback(self.page).notice("Saved outputs unavailable", message, "warning")

    def _sync_canvas_loading(self) -> None:
        """Indicate pending reads only when no committed cards are displayed."""
        if not self._active or self.items:
            self.canvas_loading.clear()
        elif self._loading:
            # The same query may return either live SQLite rows or snapshot fallback.
            self.canvas_loading.loading()
        elif self._refresh_failing:
            self.canvas_loading.failed()
        else:
            self.canvas_loading.clear()

    def _visible_range(self) -> tuple[int, int]:
        """Resolve visible saved-output positions from mounted Qt widgets."""
        if not self.items:
            return 0, 0
        if self.mode == "Detail":
            try:
                position = next(i for i, item in enumerate(self.items) if item.identity == self.selected_id)
            except StopIteration:
                position = 0
            result = self.offset + position + 1
            return result, result
        if self.mode == "List":
            start = self.table.rowAt(0)
            if start < 0:
                start = 0
            last = self.table.rowAt(max(0, self.table_viewport.height() - 1))
            if last < 0:
                last = len(self.items) - 1
            return self.offset + start + 1, self.offset + min(last + 1, len(self.items))
        scroll_top = self.grid_scroll.verticalScrollBar().value()
        scroll_bottom = scroll_top + self.grid_viewport.height()
        visible = [
            self.offset + index + 1
            for index, item in enumerate(self.items)
            if (button := self._grid_buttons.get(item.identity)) is not None
            and button.parentWidget().geometry().bottom() > scroll_top
            and button.parentWidget().geometry().top() < scroll_bottom
        ]
        if not visible:
            # A queued grid reflow can briefly leave cards at stale positions.
            first = self.offset + 1
            return first, min(self.total, first + len(self.items) - 1)
        return visible[0], visible[-1]

    def _resume_pending_page_jump(self) -> None:
        """Honor the latest page-chip input once a background mount is stable."""
        if self.runner.closed:
            self._pending_page_jump = None
            return
        if self._pending_page_jump is None or not self._active:
            return
        if self.slideshow.scrubbing:
            return  # the final interaction target resumes the coalesced jump on release
        if (
            self._loading
            or self._adjacent_loading
            or self._suppress_batch_scroll
            or self._pending_refresh is not None
            or self.reflow_timer.isActive()
            or self._grid_mount_keys is not None
        ):
            self._pending_jump_timer.start()
            return
        target, self._pending_page_jump = self._pending_page_jump, None
        self._jump_to_page(target)

    def _jump_to_page(self, page_number: int) -> None:
        """Navigate immediately when ready, or queue the latest busy-time jump."""
        if not (1 <= page_number <= math.ceil(self.total / PAGE_SIZE)):
            return
        if (
            self.slideshow.scrubbing
            or self._loading
            or self._adjacent_loading
            or self._suppress_batch_scroll
            or self.reflow_timer.isActive()
            or self._grid_mount_keys is not None
        ):
            self._pending_page_jump = page_number
            self._pending_jump_timer.start()
            return
        target = (page_number - 1) * PAGE_SIZE
        self._prefetch_zone_hint = target
        if perf_enabled():
            measurement(
                log,
                "gallery.page_jump",
                page=page_number,
                target_offset=target,
                mounted_start=self.offset,
                mounted=len(self.items),
                resident=self.offset <= target < self.offset + len(self.items),
            )
        if self.items and self.offset <= target < self.offset + len(self.items):
            index = target - self.offset
            if self.mode == "Grid":
                button = self._grid_buttons.get(self.items[index].identity)
                if button is not None:
                    self.reflow_timer.stop()
                    self._layout_cards()
                    self.cards_layout.activate()
                    self.grid_scroll.verticalScrollBar().setValue(button.parentWidget().y())
            elif self.mode == "List":
                cell = self.table.item(index, 0)
                if cell is not None:
                    self.table.scrollToItem(cell, QAbstractItemView.ScrollHint.PositionAtTop)
            else:
                self.select(self.items[index].identity, open_detail=False)
            QTimer.singleShot(0, self._sync_pager)
            self._prioritize_thumbnails()
            self._queue_neighbor_prefetch()
            return
        try:
            query = self._query(offset=target)
        except ValidationError as exc:
            self.refresh_button.setToolTip(str(exc))
            return
        self._capture_scroll()
        self._page_scroll_target = "start"
        self._submit_query(query, anchor=None)

    def _sync_pager(self, *, loading: bool = False) -> None:
        """Use the viewport's first visible image, not the fetched window offset."""
        if self._slideshow_position is not None and self._slideshow_position >= self.total:
            self._slideshow_position = None
            if self.total:
                self.slideshow_seek(self.total)
        if self._slideshow_position is not None and not self._loading and self._pending_page_jump is None:
            target = self._slideshow_position
            if self.offset <= target < self.offset + len(self.items):
                self._slideshow_position = None
                self.select(self.items[target - self.offset].identity, open_detail=False)
        self.slideshow.sync_position()
        self._sync_live_text()
        first, last = self._visible_range()
        self.grid_position_changed.emit(first if self.mode == "Grid" else 0, self.total)
        self.pager.present(
            total=self.total,
            visible_start=first,
            visible_end=last,
            loaded=len(self.items),
            active_page=((first - 1) // PAGE_SIZE + 1) if first else 0,
            batch_size=PAGE_SIZE,
            # Eager adjacent preparation is background work: keep jump chips
            # available instead of disabling navigation for each preload.
            loading=(loading or self._loading) and not self.items,
        )

    def _page_summary(self) -> str:
        if not self.total:
            return "No committed saved outputs match these filters."
        first, last = self._visible_range()
        return (
            f"Showing {first}–{last} of {self.total}; {len(self.items)} buffered. "
            "Images above and below load automatically as you scroll; use the numbered chips to jump."
        )

    def _queue_neighbor_prefetch(self) -> None:
        """Coalesce viewport changes into one nearest-neighbor check."""
        if not self.runner.closed and not self._neighbor_prefetch_timer.isActive():
            self._neighbor_prefetch_timer.start(0)

    def _prefetch_neighbors(self) -> None:
        """Keep LOOKAHEAD_SCREENS of images loaded beyond the viewport.

        Only one read/preparation runs at once; each settled load re-checks,
        so batches chain until the lookahead is met or -mc stops eviction.
        """
        if (
            self.runner.closed
            or not self._active
            or self.mode not in ("Grid", "List")
            or not self.items
            or self._loading
            or self._adjacent_loading
            or self._pending_refresh is not None
            or self.filter_timer.isActive()
        ):
            return
        if (
            self._suppress_batch_scroll
            or self._density_anchor is not None
            or self.reflow_timer.isActive()
            or self._grid_mount_keys is not None
        ):
            # The mount/anchor may settle after an earlier queued check.
            # Retry later rather than silently losing a neighbor preload.
            self._neighbor_prefetch_timer.start(40)
            return
        first, last = self._visible_range()
        if first < 1:
            return
        # Infinite-scroll lookahead: keep several screens loaded beyond the
        # viewport in both directions, measured in images, not batch zones.
        self._prefetch_zone_hint = None
        resident_end = self.offset + len(self.items)
        need = max(PAGE_SIZE, LOOKAHEAD_SCREENS * (last - first + 1))
        room = {1: resident_end - last, -1: (first - 1) - self.offset}
        preferred = self._scroll_direction
        for direction in (preferred, -preferred):
            if room[direction] >= need:
                continue
            if (direction > 0 and resident_end >= self.total) or (direction < 0 and self.offset <= 0):
                continue
            if len(self.items) + PAGE_SIZE > MAX_MOUNTED_IMAGES:
                # Only the direction of travel may evict, and only a batch the
                # viewport has fully left; the opposite side never evicts, so
                # the two directions cannot ping-pong.
                if direction != preferred or room[-direction] < PAGE_SIZE:
                    continue
            if perf_enabled():
                measurement(
                    log,
                    "gallery.neighbor_prefetch",
                    direction="forward" if direction > 0 else "backward",
                    ahead=room[1],
                    behind=room[-1],
                    need=need,
                    resident=len(self.items),
                )
            self._load_adjacent(direction)
            return

    def _maybe_load_boundary(self, direction: int) -> None:
        """User scrolls accelerate the same bidirectional neighbor buffer."""
        if direction in (-1, 1):
            self._queue_neighbor_prefetch()

    def _load_adjacent(self, direction: int) -> None:
        """Read one contiguous batch from the same browsing authority as the window."""
        offset = self.offset + len(self.items) if direction > 0 else self.offset - PAGE_SIZE
        if offset < 0 or offset >= self.total or offset == self._adjacent_failed_offset:
            return
        try:
            query = self._query(offset=offset)
        except ValidationError:
            return
        generation = self._query_generation
        source = self._snapshot_at
        started = monotonic()
        if perf_enabled():
            measurement(
                log,
                "gallery.adjacent_start",
                direction="forward" if direction > 0 else "backward",
                offset=offset,
                mounted=len(self.items),
                source="snapshot" if source else "live",
            )
        self._adjacent_loading = True
        self._sync_pager()

        async def read_one(current: GalleryQuery) -> GalleryPage:
            if source:
                _query, page, _choices, _found = await query_snapshot(self.state_dir, current, None)
                return page
            return await query_outputs(self.state_dir, current)

        async def read():
            return await _extend_page(query, await read_one(query), read_one, PAGE_SIZE)

        self.runner.submit(
            read(),
            lambda page, error: self._adjacent_done(generation, direction, query, source, page, error, started),
        )

    def _adjacent_done(
        self, generation: int, direction: int, query: GalleryQuery, source, page, error, started: float | None = None
    ) -> None:
        if generation != self._query_generation:
            return
        if perf_enabled() and started is not None:
            measurement(
                log,
                "gallery.adjacent_read",
                elapsed_ms=(monotonic() - started) * 1000,
                returned=len(page.items) if page is not None else 0,
                error=type(error).__name__ if error else None,
            )
        if error is None and page is not None and self._pending_refresh is None and self.mode != "Detail":
            self._prepare_page(
                page.items,
                generation,
                lambda: self._apply_adjacent_done(generation, direction, query, source, page, error, started),
            )
        else:
            self._apply_adjacent_done(generation, direction, query, source, page, error, started)

    def _apply_adjacent_done(
        self, generation: int, direction: int, query: GalleryQuery, source, page, error, started: float | None = None
    ) -> None:
        if generation != self._query_generation:
            return
        self._adjacent_loading = False
        if self._pending_refresh is not None:
            pending = self._pending_refresh
            self._pending_refresh = None
            self.refresh(reset=pending[0], preserve=pending[1], trigger=pending[2])
            return
        try:
            current_query = self._query(offset=query.offset)
        except ValidationError:
            self._sync_pager()
            return
        if query != current_query:
            # A debounced filter/sort edit invalidates this request.
            self._sync_pager()
            return
        if self.mode == "Detail":
            # A Grid/List prefetch must not evict the selected Detail image after
            # the user switches views while that worker is still returning.
            self._sync_pager()
            return
        if error is not None:
            self._adjacent_failed_offset = query.offset
            diagnostic_event(
                log,
                "gallery.adjacent_batch_read_failed",
                level=logging.WARNING,
                data={
                    "reason": type(error).__name__,
                },
            )
            self.refresh_button.setToolTip("The next image batch could not be loaded. Refresh to retry.")
            self._sync_pager()
            return
        if (
            page is None
            or page.total != self.total
            or page.snapshot_at != source
            or not page.items
            or (direction > 0 and query.offset != self.offset + len(self.items))
            or (direction < 0 and query.offset + len(page.items) != self.offset)
        ):
            # Never splice records from different snapshots or shifted sorts.
            self.refresh(preserve=True, trigger="snapshot_mismatch")
            return
        if {item.identity for item in page.items} & {item.identity for item in self.items}:
            self.refresh(preserve=True, trigger="snapshot_mismatch")
            return
        anchor = self._browsing_anchor()
        mode = self.mode
        # Only expose the preceding row automatically after the user has
        # actually reached the top of the mounted window. An early prefetch
        # must preserve the current viewport without pulling it backwards.
        reveal_previous = direction < 0 and (
            (mode == "Grid" and self.grid_scroll.verticalScrollBar().value() == 0)
            or (mode == "List" and self.table.verticalScrollBar().value() == 0)
        )
        pixel_offset = 0
        if mode == "Grid" and anchor in self._grid_buttons:
            pixel_offset = self._grid_buttons[anchor].parentWidget().y() - self.grid_scroll.verticalScrollBar().value()
        elif mode == "List":
            row = self.table.rowAt(0)
            pixel_offset = self.table.rowViewportPosition(row) if row >= 0 else 0
        # Keep the viewed card still while cards are evicted or inserted, and
        # keep any wheel/drag the user makes until the mount settles.
        self._held_scroll_delta = 0
        self._held_anchor = (anchor, pixel_offset) if mode == "Grid" and anchor in self._grid_buttons else None

        # Reflow and table reconciliation can emit scrollbar events. Do not
        # launch another adjacent query before the anchor has been restored.
        self._suppress_batch_scroll = True
        render_started = monotonic()
        if direction > 0:
            combined = [*self.items, *page.items]
            if len(combined) > MAX_MOUNTED_IMAGES:
                combined = combined[PAGE_SIZE:]
                self.offset += PAGE_SIZE
        else:
            combined = [*page.items, *self.items]
            self.offset = query.offset
            if len(combined) > MAX_MOUNTED_IMAGES:
                combined = combined[:MAX_MOUNTED_IMAGES]
        self.items = combined
        self._render_page()
        self._rendered_page = (self.offset, tuple(self.items))
        self._hold_anchor()
        self._adjacent_failed_offset = None
        if perf_enabled():
            measurement(
                log,
                "gallery.adjacent_reconcile",
                elapsed_ms=(monotonic() - render_started) * 1000,
                mounted=len(self.items),
            )

        def prepare():
            if self.runner.closed or generation != self._query_generation:
                return
            # Adding rows to QGridLayout posts geometry updates; setting the
            # scrollbar before those updates apply can silently clamp to zero.
            # Finalize the layout in this turn, then restore in the next one.
            if anchor and self.mode == mode and mode == "Grid":
                self.reflow_timer.stop()
                self._start_grid_mount()
                if self._grid_mount_keys is not None:
                    self._grid_mount_callbacks.append(prepare)
                    return
                self.cards_layout.activate()
                self.grid_cards.adjustSize()
            # Moving 300 cards can queue a second Qt geometry pass after
            # activate(). Restoring immediately reads stale Y coordinates.
            QTimer.singleShot(20, self, settle)

        def settle():
            try:
                if self.runner.closed or generation != self._query_generation:
                    return
                if anchor and self.mode == mode and anchor in {item.identity for item in self.items}:
                    if mode == "Grid":
                        button = self._grid_buttons.get(anchor)
                        bar = self.grid_scroll.verticalScrollBar()
                        if button is not None:
                            bar.setValue(button.parentWidget().y() - pixel_offset + self._held_scroll_delta)
                        if reveal_previous:
                            # The last card of the preceding batch is directly
                            # above the old first row (or alongside it if the
                            # column count crosses the 100-card boundary).
                            preceding = self._grid_buttons.get(page.items[-1].identity)
                            if preceding is not None:
                                preceding_card = preceding.parentWidget()
                                if preceding_card.y() < bar.value():
                                    bar.setValue(preceding_card.y())
                    elif mode == "List":
                        row = next(i for i, item in enumerate(self.items) if item.identity == anchor)
                        cell = self.table.item(row, 0)
                        if cell is not None:
                            self.table.scrollToItem(cell, QAbstractItemView.ScrollHint.PositionAtTop)
                        if reveal_previous:
                            # One row above, not the first item of the earlier
                            # 100-record page. Keep the previous anchor mounted.
                            preceding_row = next(
                                (i for i, item in enumerate(self.items) if item.identity == page.items[-1].identity),
                                None,
                            )
                            if preceding_row is not None:
                                preceding_cell = self.table.item(preceding_row, 0)
                                if preceding_cell is not None:
                                    self.table.scrollToItem(preceding_cell, QAbstractItemView.ScrollHint.PositionAtTop)
                self._capture_scroll()
                self._sync_pager()
                if perf_enabled():
                    first, last = self._visible_range()
                    measurement(
                        log,
                        "gallery.adjacent_scroll",
                        direction="forward" if direction > 0 else "backward",
                        reveal_previous=reveal_previous,
                        first=first,
                        last=last,
                        mounted=len(self.items),
                    )
            finally:
                if not self.runner.closed and generation == self._query_generation:
                    self._held_anchor = None
                    self._suppress_batch_scroll = False
                    self._queue_neighbor_prefetch()

        QTimer.singleShot(0, self, prepare)
        self._schedule_save()

    def _dispose_prepared_entry(self, entry):
        self.retirement.retire(entry["button"].parentWidget())
        self.retirement.retire(entry["film"][0])

    @Slot()
    def _cancel_preparation(self):
        self.preparation.cancel()
        if self.runner.closed:
            self._grid_mount_timer.stop()
            self._grid_mount_keys = None
            self._grid_mount_callbacks.clear()
            self._suppress_batch_scroll = False
        if self.runner.closed:
            for timer in self.findChildren(QTimer):
                timer.stop()
        self._preparation_icons = None
        for entry in self._prepared_entries.values():
            self._dispose_prepared_entry(entry)
        self._prepared_entries.clear()

    def _observe_preparation(self, count, elapsed_ms):
        if perf_enabled():
            self._perf_stats.observe("prepare_slices", elapsed_ms, count=count)

    def _prepare_page(self, records, generation, done):
        self._cancel_preparation()
        missing = tuple(item for item in records if item.identity not in self._grid_buttons)
        prepare_started = monotonic()
        if not missing:
            if perf_enabled():
                measurement(
                    log,
                    "gallery.prepare_complete",
                    reused=len(records),
                    elapsed_ms=0.0,
                    new=0,
                )
            done()
            return

        def ready(entries):
            if perf_enabled():
                self._perf_stats.flush("prepare_slices")
                measurement(
                    log,
                    "gallery.prepare_complete",
                    elapsed_ms=(monotonic() - prepare_started) * 1000,
                    new=len(missing),
                    reused=len(records) - len(missing),
                    success=entries is not None,
                )
            if entries is None:
                self._loading = self._adjacent_loading = False
                self.refresh_button.setEnabled(True)
                self._show_refresh_failure(RuntimeError("Gallery preparation failed"), first=True)
                self._sync_canvas_loading()
                self._sync_pager()
                return
            self._prepared_entries = entries
            try:
                done()
            finally:
                # A pending refresh, changed source/filter or Detail switch can
                # reject the result without mounting the staged entries.
                for entry in self._prepared_entries.values():
                    self._dispose_prepared_entry(entry)
                self._prepared_entries.clear()

        self.preparation.start(
            missing,
            factory=self._build_prepared_entry,
            done=ready,
            valid=lambda: generation == self._query_generation and not self.runner.closed,
        )

    def _build_prepared_entry(self, item):
        button, painter, labels, check = self._make_card(item)
        film = self._make_film_entry()
        film[0].setProperty("identity", item.identity)
        dimensions, size = self._dimensions(item), _human_size(item.file_size)
        filename = _filename(item.path)
        tooltip = item.issue or item.path
        button.setAccessibleName(f"Open {filename} in Detail")
        button.setToolTip(tooltip)
        painter.badges = (dimensions, size)
        labels[0].setText(f"{filename}  ·  {size}")
        labels[0].setToolTip(item.path)
        labels[1].setText(item.model or "Model not recorded")
        film[0].setAccessibleName(f"Open {filename}")
        film[0].setToolTip(tooltip)
        clip = clip_summary(self._request(item))
        film[1].badges = (f"▶ {clip[0]}", size) if clip else (dimensions, size)
        checked = item.identity in self.selection.checked
        button.setProperty("checkedSelection", checked)
        button.parentWidget().setProperty("checkedSelection", checked)
        cells = []
        for column, value in enumerate(self._entry_fields(item)):
            cell = QTableWidgetItem(value)
            cell.setData(Qt.ItemDataRole.UserRole, item.identity)
            cell.setToolTip(tooltip if column == 1 else value)
            if column == PROMPT_COLUMN:
                self._tag_prompt_cell(cell, item)
            if column == 0:
                cell.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
                )
                cell.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            cells.append(cell)
        # Polish off-layout widgets now, inside the timed slice, rather than
        # making the eventual page switch polish 100 new cards synchronously.
        for widget in (button.parentWidget(), button, check, *labels, film[0]):
            widget.ensurePolished()
        percent = self.slider.value()
        if self._preparation_icons is None or self._preparation_icons[0] != percent:
            density = self._thumbnail_pixels(percent)
            placeholder = QPixmap(density, density)
            placeholder.fill(Qt.GlobalColor.transparent)
            film_width, film_height = density + 8, round((density + 8) * 0.63)
            film_placeholder = QPixmap(max(1, film_width - 8), max(1, film_height - 8))
            film_placeholder.fill(Qt.GlobalColor.transparent)
            self._preparation_icons = (
                percent,
                density,
                QIcon(placeholder),
                placeholder.size(),
                film_width,
                film_height,
                QIcon(film_placeholder),
                film_placeholder.size(),
            )
        _percent, density, grid_icon, grid_size, film_width, film_height, film_icon, film_size = self._preparation_icons
        button.setIcon(grid_icon)
        button.setIconSize(grid_size)
        self._size_grid_card(button, density)
        film[0].setFixedSize(film_width, film_height)
        film[0].setIcon(film_icon)
        film[0].setIconSize(film_size)
        return dict(
            item=item,
            button=button,
            painter=painter,
            labels=labels,
            check=check,
            film=film,
            cells=cells,
            percent=percent,
        )

    def _render_page(self) -> None:
        started = monotonic()
        previous = {item.identity: item for item in self._rendered_page[1]} if self._rendered_page else {}
        identities = {item.identity for item in self.items}
        image_changes = [
            item
            for item in self.items
            if item.identity not in previous or self._image_key(previous[item.identity]) != self._image_key(item)
        ]
        containers = (self.grid_cards, self.table, self._widget("galleryFilmstripScrollContents"))
        updates = [widget.updatesEnabled() for widget in containers]
        for widget in containers:
            widget.setUpdatesEnabled(False)
        self._syncing_selection = True
        try:
            if self._rendered_page is None:
                self._clear_grid()  # Remove Designer fixture cards only on the first render.
                self.table.setRowCount(0)
            for identity in set(self._grid_buttons) - identities:
                card = self._grid_buttons.pop(identity).parentWidget()
                self.cards_layout.removeWidget(card)
                self._grid_slots.pop(identity, None)
                self.retirement.retire(card)
                del self._grid_painters[identity]
                del self._grid_labels[identity]
                self._check_boxes.pop(identity, None)
                self._thumb_versions.pop(identity, None)

            list_started = monotonic()
            self._reconcile_rows()
            if perf_enabled():
                measurement(
                    log,
                    "gallery.list_reconcile",
                    wall_ms=(monotonic() - list_started) * 1000,
                    rows=self.table.rowCount(),
                    mode=self.mode,
                )
            film_started = monotonic()
            self._reconcile_filmstrip()
            if perf_enabled():
                measurement(
                    log,
                    "gallery.filmstrip_reconcile",
                    wall_ms=(monotonic() - film_started) * 1000,
                    count=len(self._film_entries),
                    mode=self.mode,
                )
            for row, item in enumerate(self.items):
                if item.identity not in self._grid_buttons:
                    self._create_card(item)
                if previous.get(item.identity) != item:
                    self._update_entry(row, item)
            for item in image_changes:
                for painter in (self._grid_painters[item.identity], self._film_entries[item.identity][1]):
                    painter.pixmap, painter.placeholder = QPixmap(), ""
                row = next(i for i, record in enumerate(self.items) if record.identity == item.identity)
                self.table.item(row, 1).setIcon(QIcon())

            if self.items:
                if self._grid_empty is not None:
                    self.cards_layout.removeWidget(self._grid_empty)
                    self._grid_empty.hide()
                    self._grid_empty.deleteLater()
                    self._grid_empty = None
                # Dict order is the query order consumed by the shared grid reflow.
                self._grid_buttons = {item.identity: self._grid_buttons[item.identity] for item in self.items}
            else:
                # Clear cached positions at the empty commit itself, not only
                # when the reflow timer eventually gets its event-loop turn.
                self._reset_empty_grid_mount()
                self._set_grid_message("No saved outputs match these filters.")
            # New cards always need sizing, including the initial read: the
            # slider may already have been applied before any cards existed.
            # Retained cards must not be resized again during reconciliation.
            added = [item.identity for item in self.items if item.identity not in previous]
            self._size_new_entries(added)
            if added or len(previous) != len(self.items):
                self.reflow_timer.start(0)
        finally:
            self._syncing_selection = False
            for widget, enabled in zip(containers, updates, strict=True):
                widget.setUpdatesEnabled(enabled)
        self._sync_selection_ui()
        self._queue_thumbnails(image_changes)
        if perf_enabled():
            measurement(
                log,
                "gallery.render_page",
                elapsed_ms=(monotonic() - started) * 1000,
                mounted=len(self.items),
                added=sum((item.identity not in previous for item in self.items)),
                removed=len(set(previous) - identities),
                decoded_enqueued=len(image_changes),
            )

    @staticmethod
    def _image_key(item: GalleryItem) -> tuple[str, int | None, str]:
        return item.read_path, item.file_size, item.issue

    def _create_card(self, item: GalleryItem) -> None:
        prepared = self._prepared_entries.get(item.identity)
        if prepared is None:
            button, painter, labels, check = self._make_card(item)
        else:
            button, painter, labels, check = (prepared[key] for key in ("button", "painter", "labels", "check"))
            self._prepared_sizes[item.identity] = prepared["percent"]
        self._grid_buttons[item.identity] = button
        self._grid_painters[item.identity] = painter
        self._grid_labels[item.identity] = labels
        self._check_boxes[item.identity] = check

    def _make_card(self, item: GalleryItem):
        card = QFrame(self.grid_cards)
        card.setProperty("role", "galleryCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(5)
        button = QPushButton(card)
        button.setProperty("role", "galleryCardImage")
        button.clicked.connect(lambda checked=False, identity=item.identity: self._open_from_grid(identity))
        check = GallerySelectionCheck(button)
        check.setToolTip("Check image for bulk actions")
        check.setAccessibleName(f"Check {_filename(item.path)}")
        check.setChecked(item.identity in self.selection.checked)
        check.toggled.connect(lambda value, identity=item.identity: self._set_checked(identity, value))
        check.raise_()
        painter = CoverPainter(button, "", ("", ""))
        painter.mode = "Fit"
        clip = clip_summary(self._request(item))
        if clip is not None:
            over = QVBoxLayout(button)
            over.setContentsMargins(8, 8, 8, 8)
            badge = QLabel(f"▶ Video · {clip[0]}", button)
            badge.setObjectName("galleryVideoBadge")
            badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            over.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
            over.addStretch()
        layout.addWidget(button)
        caption = QLabel(f"{_filename(item.path)}  ·  {_human_size(item.file_size)}", card)
        caption.setProperty("role", "galleryCardCaption")
        caption.setToolTip(item.path)
        layout.addWidget(caption)
        model = QLabel(item.model or "Model not recorded", card)
        model.setProperty("role", "secondary")
        model.setProperty("textRole", "supporting")
        layout.addWidget(model)
        # Preparation measures this layout before the card/children are shown.
        # Hidden items must contribute their final image and text-row sizes.
        for widget in (button, caption, model):
            policy = widget.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(policy)
        return button, painter, (caption, model), check

    @staticmethod
    def _size_grid_card(button: QPushButton, density: int) -> None:
        """Reserve the complete polished card before its first visible layout."""
        card = button.parentWidget()
        card.ensurePolished()
        for index in range(card.layout().count()):
            card.layout().itemAt(index).widget().ensurePolished()
        card.setFixedWidth(density + 16)
        button.setFixedSize(density + 16, density + 16)
        card.layout().invalidate()
        card.setFixedHeight(card.sizeHint().height())
        card.layout().activate()

    def _reconcile_rows(self) -> None:
        """Keep the actual table items (and icons) when inserting or moving rows."""
        desired = [item.identity for item in self.items]
        current = [str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in range(self.table.rowCount())]
        selected = self.table.currentItem()
        selected_identity = selected.data(Qt.ItemDataRole.UserRole) if selected is not None else None
        selected_column = selected.column() if selected is not None else 0
        # Ordinary page jumps replace all rows; a single resize avoids 100–200
        # separate removals/insertions and their repeated model notifications.
        if not set(current).intersection(desired):
            self.table.setRowCount(0)
            self.table.setRowCount(len(desired))
            self.table.setProperty("hoverRow", -1)
            return
        if desired[: len(current)] == current:
            self.table.setRowCount(len(desired))
            self.table.setProperty("hoverRow", -1)
            return
        for row in reversed(range(len(current))):
            if current[row] not in desired:
                self.table.removeRow(row)
                current.pop(row)
        for row, identity in enumerate(desired):
            if row < len(current) and current[row] == identity:
                continue
            cells = None
            if identity in current:
                source = current.index(identity)
                cells = [self.table.takeItem(source, column) for column in range(self.table.columnCount())]
                self.table.removeRow(source)
                current.pop(source)
            self.table.insertRow(row)
            current.insert(row, identity)
            if cells is not None:
                for column, cell in enumerate(cells):
                    self.table.setItem(row, column, cell)
        if selected_identity in desired:
            cell = self.table.item(desired.index(selected_identity), selected_column)
            if self.table.currentItem() is not cell:
                self.table.setCurrentItem(cell)
        self.table.setProperty("hoverRow", -1)

    def _update_entry(self, row: int, item: GalleryItem) -> None:
        """Update only changed metadata, without replacing retained image widgets/cells."""
        prepared = self._prepared_entries.pop(item.identity, None)
        if prepared is not None and prepared["item"] == item:
            for column, cell in enumerate(prepared["cells"]):
                self.table.setItem(row, column, cell)
            return
        dimensions, size = self._dimensions(item), _human_size(item.file_size)
        filename = _filename(item.path)
        tooltip = item.issue or item.path
        button = self._grid_buttons[item.identity]
        button.setAccessibleName(f"Open {filename} in Detail")
        button.setToolTip(tooltip)
        painter = self._grid_painters[item.identity]
        painter.badges = (dimensions, size)
        caption, model = self._grid_labels[item.identity]
        caption.setText(f"{filename}  ·  {size}")
        caption.setToolTip(item.path)
        model.setText(item.model or "Model not recorded")
        fields = self._entry_fields(item)
        for column, value in enumerate(fields):
            cell = self.table.item(row, column)
            if cell is None:
                cell = QTableWidgetItem()
                cell.setData(Qt.ItemDataRole.UserRole, item.identity)
                self.table.setItem(row, column, cell)
            cell.setText(value)
            if column == 0:
                cell.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
                )
                cell.setCheckState(
                    Qt.CheckState.Checked if item.identity in self.selection.checked else Qt.CheckState.Unchecked
                )
            cell.setToolTip(tooltip if column == 1 else value)
            if column == PROMPT_COLUMN:
                self._tag_prompt_cell(cell, item)
        film_button, film_painter = self._film_entries[item.identity]
        film_button.setAccessibleName(f"Open {filename}")
        film_button.setToolTip(tooltip)
        clip = clip_summary(self._request(item))
        film_painter.badges = (f"▶ {clip[0]}", size) if clip else (dimensions, size)
        button.update()
        film_button.update()

    @staticmethod
    def _tag_prompt_cell(cell: QTableWidgetItem, item: GalleryItem) -> None:
        """Hand the list view's prompt cell the parts its prompt variation produced; its delegate paints them."""
        runs = stored_runs(item.request_json) if item.prompt else ()
        cell.setData(PROMPT_RUNS_ROLE, runs or None)
        if runs:
            cell.setToolTip(f"<p style='white-space:pre-wrap'>{tagged_html(item.prompt, runs)}</p>")

    def _entry_fields(self, item):
        loras = (
            "; ".join(f"{display_label(identifier)} · {weight:g} · Unknown" for identifier, weight in item.loras)
            or "None"
        )
        clip = clip_summary(self._request(item))
        filename = _filename(item.path)
        if clip:
            filename = f"▶ {clip[0]} · {filename}"
        return (
            "",
            "",
            filename,
            item.model or "Not recorded",
            loras,
            self._dimensions(item),
            _human_size(item.file_size),
            _local_timestamp(item.saved_at_utc).replace("\n", " "),
            item.prompt or "Not recorded",
        )

    def _clear_grid(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        # A first query can fail before the Designer fixtures are removed.
        # Its message is owned by this same layout and was just disposed too.
        self._grid_empty = None
        self._grid_layout_keys = ()
        self._grid_layout_columns = 0
        self._grid_slots.clear()

    def _reconcile_filmstrip(self) -> None:
        layout = self._widget("galleryFilmstripScrollContents").layout()
        identities = {item.identity for item in self.items}
        for identity in set(self._film_entries) - identities:
            button, painter = self._film_entries.pop(identity)
            layout.removeWidget(button)
            button.hide()
            button.setAutoExclusive(False)
            button.setChecked(False)
            button.setAutoExclusive(True)
            if button in self._designer_film_buttons:
                button.setProperty("identity", None)
                painter.pixmap = QPixmap()
                self._film_spares.append((button, painter))
            else:
                self.retirement.retire(button)
        for row, item in enumerate(self.items):
            if item.identity not in self._film_entries:
                prepared = self._prepared_entries.get(item.identity)
                if prepared is not None:
                    button, painter = prepared["film"]
                elif self._film_spares:
                    button, painter = self._film_spares.pop(0)
                else:
                    button, painter = self._make_film_entry()
                button.setProperty("identity", item.identity)
                self._film_entries[item.identity] = button, painter
                clip = clip_summary(self._request(item))
                painter.badges = (
                    (f"▶ {clip[0]}", _human_size(item.file_size))
                    if clip
                    else (self._dimensions(item), _human_size(item.file_size))
                )
            button, _painter = self._film_entries[item.identity]
            if layout.indexOf(button) != row:
                layout.insertWidget(row, button)
            button.setChecked(item.identity == self.selected_id)
            button.show()
        self._film_buttons = [self._film_entries[item.identity][0] for item in self.items]
        self._film_painters = [self._film_entries[item.identity][1] for item in self.items]

    def _make_film_entry(self):
        button = QPushButton(self._widget("galleryFilmstripScrollContents"))
        button.setProperty("role", "galleryThumbnail")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.clicked.connect(lambda checked=False, source=button: self.select(str(source.property("identity"))))
        return button, CoverPainter(button, "", ("", ""))

    def _prioritize_thumbnails(self) -> None:
        """Decode visible saved images before speculative neighboring thumbnails."""
        if len(self._thumb_waiting) < 2 or not self.items:
            return
        positions = {item.identity: index for index, item in enumerate(self.items)}
        if self._prefetch_zone_hint is not None and self.offset <= self._prefetch_zone_hint < self.offset + len(
            self.items
        ):
            first = self._prefetch_zone_hint - self.offset
            last = min(len(self.items) - 1, first + 19)
        else:
            visible_first, visible_last = self._visible_range()
            first = max(0, min(len(self.items) - 1, visible_first - self.offset - 1))
            last = max(first, min(len(self.items) - 1, visible_last - self.offset - 1))
            if last - first >= PAGE_SIZE:
                last = min(len(self.items) - 1, first + 19)

        def priority(entry):
            position = positions.get(entry[1], len(self.items))
            return (0 if first <= position <= last else 1, abs(position - first))

        self._thumb_waiting = deque(sorted(self._thumb_waiting, key=priority))

    def _queue_thumbnails(self, changed: list[GalleryItem]) -> None:
        for item in changed:
            self._thumb_generation += 1
            generation = self._thumb_generation
            self._thumb_versions[item.identity] = generation
            if not item.issue:
                self._thumb_waiting.append((generation, item.identity, item.path))
            else:
                self._show_unavailable(item.identity, item.issue)
        self._thumb_waiting = deque(
            entry for entry in self._thumb_waiting if self._thumb_versions.get(entry[1]) == entry[0]
        )
        self._prioritize_thumbnails()
        self._pump_thumbnails()

    def _pump_thumbnails(self) -> None:
        if self.slideshow.scrubbing:
            return
        while self._thumb_active < THUMBNAIL_CONCURRENCY and self._thumb_waiting:
            generation, identity, path = self._thumb_waiting.popleft()
            if self._thumb_versions.get(identity) != generation:
                continue
            self._thumb_active += 1
            started = monotonic()
            selected = next(x for x in self.items if x.identity == identity)

            async def work(filename=path, item=selected):
                return await asyncio.to_thread(
                    _timed_thumbnail_image, filename, THUMBNAIL_SIDE, self.state_dir, item=item, stats=self._perf_stats
                )

            self.runner.submit(
                work(),
                lambda result, error, gen=generation, ident=identity, stamp=started: self._thumbnail_done(
                    gen, ident, result, error, stamp
                ),
            )

    def _thumbnail_done(self, generation: int, identity: str, result, error, started: float | None = None) -> None:
        if perf_enabled() and started is not None:
            self._perf_stats.observe(
                "thumbnail_end_to_end",
                (monotonic() - started) * 1000,
                success=bool(error is None and result and result[0] is not None),
            )
        self._thumb_active = max(0, self._thumb_active - 1)
        if generation == self._thumb_versions.get(identity) and error is None and result:
            image, issue = result
            if image is not None:
                pixmap = QPixmap.fromImage(image)
                painter = self._grid_painters.get(identity)
                if painter is not None:
                    painter.placeholder = ""
                    painter.pixmap = pixmap
                    painter.widget.update()
                try:
                    row = next(i for i, item in enumerate(self.items) if item.identity == identity)
                except StopIteration:
                    row = -1
                if row >= 0:
                    table_item = self.table.item(row, 1)
                    if table_item is not None:
                        table_item.setIcon(QIcon(pixmap))
                    if row < len(self._film_painters):
                        self._film_painters[row].placeholder = ""
                        self._film_painters[row].pixmap = pixmap
                        self._film_buttons[row].update()
            elif issue:
                if issue == "Original temporarily busy":
                    QTimer.singleShot(2000, lambda: self._retry_thumbnail(generation, identity))
                else:
                    self._show_unavailable(identity, issue)
        self._pump_thumbnails()
        if perf_enabled() and self._thumb_active == 0 and not self._thumb_waiting:
            self._perf_stats.flush("thumbnail_workers")
            self._perf_stats.flush("thumbnail_end_to_end")

    def _show_unavailable(self, identity: str, issue: str) -> None:
        """Paint a confirmed-missing pictogram or the generic unavailable text in Grid, List and filmstrip.

        A short placeholder fits a small card; the full safe reason stays on hover.
        """
        missing = issue == MISSING_ISSUE
        short, detail = ("File missing", issue) if missing else ("Image unavailable", f"Image unavailable: {issue}")
        icon = _missing_icon() if missing else QIcon()
        button = self._grid_buttons.get(identity)
        if button is not None:
            button.setToolTip(detail)
        painter = self._grid_painters.get(identity)
        if painter is not None:
            painter.pixmap = QPixmap()
            painter.placeholder, painter.placeholder_icon = short, icon
            painter.widget.update()
        row = next((i for i, item in enumerate(self.items) if item.identity == identity), -1)
        if row < 0:
            return
        table_item = self.table.item(row, 1)
        if table_item is not None:
            table_item.setToolTip(detail)
            table_item.setIcon(icon)
        if row < len(self._film_painters):
            film_painter = self._film_painters[row]
            film_painter.pixmap = QPixmap()
            film_painter.placeholder, film_painter.placeholder_icon = short, icon
            self._film_buttons[row].setToolTip(detail)
            self._film_buttons[row].update()

    def _retry_thumbnail(self, generation: int, identity: str) -> None:
        """Retry only the same visible identity after a transient original-file lease."""
        if self._thumb_versions.get(identity) != generation:
            return
        item = next((entry for entry in self.items if entry.identity == identity), None)
        if item is not None:
            self._queue_thumbnails([item])

    def _open_from_grid(self, identity: str) -> None:
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            self._select_range(identity)
            return
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self._toggle_checked(identity)
            return
        self._origin_identity = identity
        self.select(identity)

    def _open_from_list(self, identity: str) -> None:
        self._origin_identity = None
        self.select(identity)

    def select(self, identity: str, *, open_detail: bool = True) -> None:
        if (
            identity != self.selected_id
            and hasattr(self.page.window(), "_inline_feedback")
            and feedback(self.page)._armed
        ):
            feedback(self.page).dismiss()
        item = next((record for record in self.items if record.identity == identity), None)
        if item is None:
            return
        self.selected_id = identity
        self._sync_detail_checkbox()
        if open_detail:
            self.show_view("Detail")
        for index, record in enumerate(self.items):
            if index < len(self._film_buttons):
                self._film_buttons[index].setChecked(record.identity == identity)
        self._present_item(item)
        self._update_navigation()
        self._sync_pager()
        self._schedule_save()

    def _update_navigation(self) -> None:
        if not self.items or not self.selected_id:
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        try:
            index = next(i for i, item in enumerate(self.items) if item.identity == self.selected_id)
        except StopIteration:
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        self.previous_button.setEnabled(index > 0 or self.offset > 0)
        self.next_button.setEnabled(index + 1 < len(self.items) or self.offset + len(self.items) < self.total)

    def _show_positive_prompt(self, text: str, runs) -> None:
        """The saved prompt with the parts its prompt variation produced styled as tags."""
        edit = self._widget("galleryPositivePrompt")
        tags = getattr(self, "_prompt_tags", None)
        if tags is None:
            tags = self._prompt_tags = PromptTagHighlighter(edit, scan=False)
        edit.setPlainText(text)
        tags.set_runs(runs)

    _USE_TIP = "Populate Generate from this recorded request without submitting."
    _REGENERATE_TIP = "Create a fresh generation attempt from these recorded settings after confirmation."

    def _present_item(self, item: GalleryItem) -> None:
        if self.slideshow.scrubbing:
            return  # retain the displayed photo while the gesture previews only an ordinal
        position = self.offset + self.items.index(item) + 1
        if self.slideshow.active and not self.slideshow.wants_position(position):
            return  # a queued seek does not decode intermediate page selections
        self._widget("galleryPositionOverlay", QLabel).setText(f"{position} / {self.total}")
        previous = self._presented_item
        selected_video_entry = previous is None or previous.identity != item.identity
        if previous == item:
            return
        image_changed = (
            previous is None or previous.identity != item.identity or self._image_key(previous) != self._image_key(item)
        )
        self._presented_item = item
        request = self._request(item)
        if image_changed:
            self.video_player.show_for_video(False)  # A poster alone does not establish that its clip is available.
        clip = clip_summary(request)
        if clip:
            self.video_player.allow_keyboard_start()
            self.surface_painter.mode = "Fit"
        self._widget("galleryDimensionsOverlay", QLabel).setText(
            self._dimensions(item) + (f" · Video {clip[0]}" if clip else "")
        )
        self._widget("galleryModelValue", QLabel).setText(item.model or "Not recorded")
        video_facts = self._widget("galleryModelIdentifier", QLabel)
        video_facts.setVisible(clip is not None)
        if clip:
            video_facts.setText("Video · reading saved media details…")
        self._present_loras(item)
        if not self.slideshow.active:
            self.hover_card.set_item(item)
        self._widget("galleryStepsValue", QLabel).setText(self._value(request.get("steps")))
        self._widget("galleryGuidanceValue", QLabel).setText(self._value(request.get("guidance")))
        self._widget("gallerySamplerValue", QLabel).setText(self._value(request.get("sampler_id")))
        self._widget("galleryDateValue", QLabel).setText(_local_timestamp(item.saved_at_utc))
        self._show_positive_prompt(item.prompt or "Not recorded", request_runs(request) if item.prompt else ())
        self._widget("galleryAdditionalNegativePromptValue", QLabel).setText(item.negative or "Not recorded")
        self._widget("galleryAdditionalSeedValue", QLabel).setText(self._value(request.get("seed")))
        operation = request.get("operation")
        self._widget("galleryAdditionalOperationValue", QLabel).setText(
            (
                ("Image to video" if request.get("start_image") else "Text to video")
                if clip
                else {"txt2img": "Text to image", "img2img": "Image to image", "inpaint": "Inpaint"}.get(
                    operation, self._value(operation)
                )
            )
        )
        self._widget("galleryAdditionalStrengthValue", QLabel).setText(self._value(request.get("strength")))
        filename = _filename(item.path)
        self._widget("galleryAdditionalFilenameValue", QLabel).setText(filename)
        self._widget("galleryAdditionalProviderValue", QLabel).setText(item.backend_id or "Not recorded")
        self._widget("galleryAdditionalFileSizeLabel", QLabel).setText("Video file size" if clip else "File size")
        self._widget("galleryAdditionalFileSizeValue", QLabel).setText(
            "Not recorded" if clip else _human_size(item.file_size)
        )
        self._widget("galleryAdditionalDurationLabel", QLabel).setText(
            "Playback duration" if clip else "Provider duration"
        )
        self._widget("galleryAdditionalDurationValue", QLabel).setText("Not recorded")
        self._widget("galleryAdditionalPathValue", QLabel).setText(_display_output_path(item.path))
        edited = request.get("operation", "txt2img") != "txt2img"
        foreign = foreign_windows_path(item.path)
        # Use settings and Regenerate need only the recorded request, never the original file, so where it was
        # saved does not matter; Regenerate keeps its confirmation and dispatch-time readiness checks.
        recorded = bool(request.get("model_id"))
        reusable = recorded and not edited and not self._snapshot_at and clip is None
        self.use_settings_button.setEnabled(reusable and self._reuse_callback is not None)
        posed = request.get("pose_control") is not None
        self.regenerate_button.setEnabled(reusable and not posed and self._regenerate_callback is not None)
        for button, tip in (
            (self.use_settings_button, self._USE_TIP),
            (self.regenerate_button, self._REGENERATE_TIP),
        ):
            button.setToolTip(
                "Not available for video yet."
                if clip
                else "Not available for edited images: the source and mask are not restored here. Use Edit image instead."
                if edited
                else "Regenerate is not available for a pose image. Use settings restores the pose into Controlnet mode."
                if posed and button is self.regenerate_button
                else tip
                if recorded
                else "Not available: this output has no recorded model."
            )
        self.edit_button.setEnabled(
            not self._snapshot_at
            and not item.issue
            and bool(item.path)
            and self._edit_callback is not None
            and clip is None
        )
        self.edit_button.setToolTip(
            "The editor will verify this Windows-recorded image under the shared original-file lock before "
            "loading it. Missing or unsafe sources are refused."
            if foreign
            else self._edit_tip
        )
        if self._snapshot_at:
            for button in (self.edit_button, self.use_settings_button, self.regenerate_button):
                button.setToolTip("Browsing a snapshot; wait for live state before using this action.")
        self.copy_button.setEnabled(bool(item.path))
        self.copy_button.setText("Copy video path" if clip else "Copy path")
        self.external_button.setText("Open video in player" if clip else "Open in external viewer")
        available = not item.issue
        self.folder_button.setEnabled(available)
        if image_changed:
            self.external_button.setEnabled(available)
            self.surface.setToolTip(item.issue or "")
            if not self.slideshow.active:
                self.surface_painter.finish_transition()
                self.surface_painter.pixmap = QPixmap()
                self.surface_painter.zoom = 1.0
                self.surface_painter.offset = [0.0, 0.0]
            missing = item.issue == MISSING_ISSUE
            self.surface_painter.placeholder = "File missing" if missing else "Image unavailable" if item.issue else ""
            self.surface_painter.placeholder_icon = _missing_icon() if missing else QIcon()
            self.surface.update()
            self._image_generation += 1
            image_generation = self._image_generation
            self._image_pending = True

            async def read_image():
                return await asyncio.to_thread(_image, item.path, 0, self.state_dir, item=item)

            self.runner.submit(
                read_image(),
                lambda result, error: self._image_done(image_generation, item.identity, result, error),
            )
        self._detail_generation += 1
        detail_generation = self._detail_generation
        self.runner.submit(
            asyncio.to_thread(load_detail, item, state=self.state_dir)
            if self._snapshot_at
            else inspect_output(self.state_dir, item.identity),
            lambda result, error: self._detail_done(detail_generation, item.identity, result, error),
        )
        if clip is not None and not item.issue:
            self.external_button.setEnabled(False)
            self.copy_button.setEnabled(False)
            autoplay = self.video_player.autoplay and self._active and self.mode == "Detail" and selected_video_entry
            self.runner.submit(
                asyncio.to_thread(self._video_companion_path, item),
                lambda result, error: self._video_companion_done(
                    detail_generation, item.identity, result, error, autoplay=autoplay
                ),
            )

    def _video_companion_path(self, item: GalleryItem) -> str:
        """Resolve the poster's companion on a worker, never using its path as mutation authority."""
        with original_images_lock(self.state_dir, timeout=12):
            poster = resolve_item_read(item, self.state_dir)[0]
            clip = video_path_for(poster)
            if not S_ISREG(clip.stat().st_mode):
                raise OSError("The video companion is not a regular file.")
            return str(clip)

    def _video_companion_done(self, generation: int, identity: str, path, error, *, autoplay: bool = False) -> None:
        if self.runner.closed or generation != self._detail_generation or identity != self.selected_id:
            return
        available = error is None and bool(path)
        self.external_button.setEnabled(available)
        self.copy_button.setEnabled(available)
        if not available:
            self.video_player.show_for_video(False)
            message = (
                "The video file is missing; only its poster remains."
                if isinstance(error, FileNotFoundError)
                else "The video file is unavailable; only its poster can be shown."
            )
            self.surface.setToolTip(message)
            label = self._widget("galleryDimensionsOverlay", QLabel)
            label.setText(f"{label.text()} · Video unavailable (poster only)")
            if self._active:
                feedback(self.page).notice("Video unavailable", message, "warning")
        elif not self.video_player.playing:
            if (
                autoplay
                and self._active
                and self.mode == "Detail"
                and self.video_player.autoplay
                and not self._recycle_busy
            ):
                self._start_video(path)
            else:
                self.video_player.show_for_video(True)

    def _image_done(self, generation: int, identity: str, result, error) -> None:
        if generation != self._image_generation or identity != self.selected_id:
            return
        if self.slideshow.scrubbing:
            self.slideshow.deferred_image = (generation, identity, result, error)
            return
        self._image_pending = False
        if self.slideshow.active:
            index = next((i for i, item in enumerate(self.items) if item.identity == identity), -1)
            if not self.slideshow.wants_position(self.offset + index + 1):
                return  # discard an old in-flight display result while waiting for the final seek target
        self.slideshow.image_ready()
        if error is not None or not result or result[0] is None:
            if error is not None:
                diagnostic_event(
                    log,
                    "gallery.detail_image_read_failed",
                    level=logging.WARNING,
                    data={
                        "reason": type(error).__name__,
                    },
                )
            issue = "decode failed" if error is not None or not result else result[1] or "decode failed"
            self.surface_painter.finish_transition()
            self.surface_painter.pixmap = QPixmap()
            if self.slideshow.active:
                self.hover_card.set_item(self._selected())
            if issue == "Original temporarily busy":
                QTimer.singleShot(2000, lambda: self._retry_detail_image(generation, identity))
                return
            if not self.surface_painter.placeholder:
                self.surface_painter.placeholder = "Image unavailable"
            self.surface.setToolTip(f"Image is corrupt or unreadable: {issue}")
            self.external_button.setEnabled(False)
            self.surface.update()
            return
        fade_ms, fade_fps = self.slideshow.transition_timing() if self.slideshow.active else (0, 60)
        self.surface_painter.set_pixmap(QPixmap.fromImage(result[0]), fade_ms=fade_ms, fade_fps=fade_fps)
        if self.slideshow.active:
            self.hover_card.set_item(self._selected())
        self.surface_painter.placeholder = ""
        self.surface.update()

    def _retry_detail_image(self, generation: int, identity: str) -> None:
        if self.runner.closed:
            return
        if self.slideshow.scrubbing:
            self.slideshow.deferred_retry = (generation, identity)
            return
        if generation != self._image_generation or identity != self.selected_id:
            return
        item = self._selected()
        if item is not None:

            async def read_again():
                return await asyncio.to_thread(_image, item.path, 0, self.state_dir, item=item)

            self.runner.submit(
                read_again(),
                lambda result, error: self._image_done(generation, identity, result, error),
            )

    def _detail_done(self, generation: int, identity: str, detail, error) -> None:
        if generation != self._detail_generation or identity != self.selected_id:
            return
        raw = self._widget("galleryRawMetadata")
        if error is not None or detail is None:
            raw.setPlainText(json.dumps({"identity": identity, "metadata_status": "unavailable"}, indent=2))
            if self._selected() is not None and clip_summary(self._request(self._selected())):
                self._widget("galleryModelIdentifier", QLabel).setText("Video · saved media details unavailable")
            return
        raw.setPlainText(detail.metadata_json)
        try:
            metadata = json.loads(detail.metadata_json)
        except (TypeError, ValueError):
            if self._selected() is not None and clip_summary(self._request(self._selected())):
                self._widget("galleryModelIdentifier", QLabel).setText("Video · saved media details unavailable")
            return
        clip = clip_summary(self._request(self._selected())) if self._selected() is not None else None
        if clip:
            video = metadata.get("video")
            if not isinstance(video, dict):
                self._widget("galleryModelIdentifier", QLabel).setText("Video · saved media details unavailable")
                return
            facts = ["Video"]
            seconds = video.get("duration_seconds")
            if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0:
                value = duration_text(seconds)
                facts.append(value)
                self._widget("galleryAdditionalDurationValue", QLabel).setText(value)
            width, height = video.get("width_px"), video.get("height_px")
            if type(width) is int and type(height) is int and width > 0 and height > 0:
                facts.append(f"{width} × {height}")
            fps = video.get("fps")
            if type(fps) in (int, float) and math.isfinite(fps) and fps > 0:
                facts.append(f"{fps:g} fps")
            if type(video.get("has_audio")) is bool:
                facts.append("Audio" if video["has_audio"] else "No audio")
            size = video.get("bytes")
            if type(size) is int and size >= 0:
                readable = _human_size(size)
                facts.append(readable)
                self._widget("galleryAdditionalFileSizeValue", QLabel).setText(readable)
            self._widget("galleryModelIdentifier", QLabel).setText(" · ".join(facts))
            return
        duration = self._recorded_duration(metadata)
        if duration is not None:
            self._widget("galleryAdditionalDurationValue", QLabel).setText(duration)

    @staticmethod
    def _recorded_duration(metadata: dict) -> str | None:
        timing = metadata.get("timing")
        if not isinstance(timing, dict):
            return None
        value = timing.get("backend_elapsed_seconds")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return None
        return f"{value:.2f} s"

    def _present_loras(self, item: GalleryItem) -> None:
        chips = [self._widget(f"galleryLoRAChip{i}") for i in (1, 2)]
        for chip in chips:
            chip.hide()
        if not item.loras:
            self._widget("galleryLoRALabel", QLabel).setText("LoRAs · none")
            return
        self._widget("galleryLoRALabel", QLabel).setText("LoRAs")
        full = "\n".join(
            f"{display_label(identifier)} · {weight:g}"
            + (f"  ({identifier})" if display_label(identifier) != identifier else "")
            for identifier, weight in item.loras
        )
        first = item.loras[0]
        chips[0].show()
        self._widget("galleryLoRAName1", QLabel).setText(display_label(first[0]))
        self._widget("galleryLoRAWeight1", QLabel).setText(f"{first[1]:g}")
        self._widget("galleryLoRAStatus1", QLabel).setText("Unknown")
        self._widget("galleryLoRAStatus1", QLabel).setToolTip(
            "No persisted LoRA application/compatibility evidence is available in the saved request."
        )
        if len(item.loras) > 1:
            chips[1].show()
            rest = "; ".join(f"{display_label(identifier)} · {weight:g}" for identifier, weight in item.loras[1:])
            self._widget("galleryLoRAName2", QLabel).setText(rest)
            self._widget("galleryLoRAName2", QLabel).setToolTip(full)
            self._widget("galleryLoRAWeight2", QLabel).setText("")
            self._widget("galleryLoRAStatus2", QLabel).setText("Unknown")
            self._widget("galleryLoRAStatus2", QLabel).setToolTip(
                "No persisted LoRA application/compatibility evidence is available in the saved request."
            )

    @staticmethod
    def _request(item: GalleryItem) -> dict:
        try:
            value = json.loads(item.request_json)
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _value(value) -> str:
        return "Not recorded" if value is None or value == "" else str(value)

    @staticmethod
    def _dimensions(item: GalleryItem) -> str:
        return f"{item.width} × {item.height}" if item.width and item.height else "Unknown"

    def _use_settings(self) -> None:
        if self._snapshot_at:
            return
        item = self._selected()
        if item is not None and self._reuse_callback is not None:
            self._reuse_callback(self._request(item), item.identity)

    def _edit_image(self) -> None:
        if self._snapshot_at:
            return
        item = self._selected()
        if item is not None and not item.issue and self._edit_callback is not None:
            if foreign_windows_path(item.path):
                self._edit_callback(GalleryEditSource(self.state_dir, item))
            elif item.read_path != item.path:
                self._read_action("edit")
            else:
                self._edit_callback(item.path)

    def _regenerate(self) -> None:
        if self._snapshot_at:
            return
        item = self._selected()
        if item is not None and self._regenerate_callback is not None:
            self._regenerate_callback(self._request(item), item.identity)

    def step(self, delta: int) -> None:
        if self.slideshow.active:
            self.slideshow.navigate(delta)
            return
        self._step_detail(delta)

    def slideshow_step(self, delta: int) -> None:
        """Loop through the filtered result using the ordinary bounded paging path."""
        if not self.items:
            return
        index = next((i for i, item in enumerate(self.items) if item.identity == self.selected_id), 0)
        if delta > 0 and index == len(self.items) - 1 and self.offset + len(self.items) >= self.total:
            if self.offset:
                self._pending_page_selection = "first"
                self._jump_to_page(1)
            else:
                self.select(self.items[0].identity, open_detail=False)
        elif delta < 0 and index == 0 and self.offset == 0:
            if len(self.items) < self.total:
                self._pending_page_selection = "last"
                self._jump_to_page(math.ceil(self.total / PAGE_SIZE))
            else:
                self.select(self.items[-1].identity, open_detail=False)
        else:
            self._step_detail(delta)

    def slideshow_seek(self, number: int) -> None:
        """Seek a filtered ordinal through the existing latest-busy-jump/bounded-query path."""
        if not self.slideshow.active or self.runner.closed or not self.total:
            return
        target = min(max(1, number), self.total) - 1
        if self._slideshow_position == target:
            return
        self._slideshow_position = target
        if not self._loading and not self._adjacent_loading and self.offset <= target < self.offset + len(self.items):
            self._slideshow_position = None
            identity = self.items[target - self.offset].identity
            if identity != self.selected_id or self._presented_item != self.items[target - self.offset]:
                self.select(identity, open_detail=False)
        else:
            self._jump_to_page(target // PAGE_SIZE + 1)

    def _step_detail(self, delta: int) -> None:
        if not self.items:
            return
        try:
            index = next(i for i, item in enumerate(self.items) if item.identity == self.selected_id)
        except StopIteration:
            index = 0
        target = index + delta
        if 0 <= target < len(self.items):
            self.select(self.items[target].identity, open_detail=False)
            return
        if delta > 0 and self.offset + len(self.items) < self.total:
            self._pending_page_selection = "first"
            self._jump_to_page((self.offset + len(self.items)) // PAGE_SIZE + 1)
        elif delta < 0 and self.offset > 0:
            self._pending_page_selection = "last"
            self._jump_to_page((self.offset - 1) // PAGE_SIZE + 1)

    def _change_page(self, delta: int) -> bool:
        if not self.total:
            return False
        first, _last = self._visible_range()
        current = self._pending_page_jump or ((first - 1) // PAGE_SIZE + 1 if first else self.offset // PAGE_SIZE + 1)
        target = current + delta
        if target < 1 or target > math.ceil(self.total / PAGE_SIZE):
            return False
        self._jump_to_page(target)
        return True

    def open_output(self, identity: str, *, attempts: int = 20) -> None:
        """Open a live identity, clearing incompatible browse filters rather than silently showing another image."""
        self._cancel_open_output()
        if self.runner.closed or not self._active:
            return
        self.filter_timer.stop()
        self._pending_refresh = None
        self._pending_jump_timer.stop()
        self._pending_page_jump = None
        self._pending_page_selection = None
        defaults = (
            (self.prompt, ""),
            (self.model, "All models"),
            (self.lora, "All LoRAs"),
            (self.date_filter, "All time"),
            (self.dimensions, "Any size"),
            (self.ratio, "Any ratio"),
        )
        for widget, value in defaults:
            blocked = widget.blockSignals(True)
            try:
                widget.setText(value) if widget is self.prompt else widget.setCurrentText(value)
            finally:
                widget.blockSignals(blocked)
        self._open_request, self._open_attempts = identity, attempts
        self._open_started = monotonic() if perf_enabled() else None
        self._open_waits = 0
        self._continue_open_output()

    def _cancel_open_output(self, outcome: str = "cancelled") -> None:
        if self._open_request is not None and self._open_started is not None and perf_enabled():
            measurement(
                log,
                "gallery.open_output",
                outcome="shutdown" if self.runner.closed else "navigation" if not self._active else outcome,
                elapsed_ms=(monotonic() - self._open_started) * 1000,
                busy_waits=self._open_waits,
            )
        self._open_timer.stop()
        self._open_request = self._open_generation = None
        self._open_started = None

    def _open_output_failed(self, reason: str = "unavailable") -> None:
        self._cancel_open_output(reason)
        if self._active and not self.runner.closed:
            feedback(self.page).notice(
                "Open in Images", "The selected saved image is no longer available from live history.", "warning"
            )

    def _continue_open_output(self) -> None:
        if self._open_request is None or self.runner.closed or not self._active:
            self._cancel_open_output()
            return
        if self._loading or self._adjacent_loading:
            if self._open_attempts > 0:
                self._open_attempts -= 1
                self._open_waits += 1
                self._open_timer.start()
            else:
                self._open_output_failed("busy_exhausted")
            return
        try:
            query = self._query(offset=0)
        except ValidationError:
            self._open_output_failed("query_invalid")
            return
        self._submit_query(query, anchor=self._open_request, target_count=PAGE_SIZE, trigger="open-output")

    def show_view(self, mode: str, *, persist: bool = True) -> None:
        entering_detail = mode == "Detail" and self.mode != "Detail"
        if mode != "Detail" and self.slideshow.active:
            self.slideshow.stop()
        if mode != "Detail" and self.video_player.playing:
            self.video_player.release(show_play=True)
        if mode not in self.modes:
            return
        if mode != self.mode:
            self._prefetch_zone_hint = None
        self.mode = mode
        self.slider.setValue(self._thumbnail_percents[mode])
        self.stack.setCurrentWidget(self.modes[mode])
        self._widget(f"gallery{mode}Button", QPushButton).setChecked(True)
        if mode == "Detail":
            selected = next((item for item in self.items if item.identity == self.selected_id), None)
            if selected is None and self.items:
                selected = self.items[0]
            if selected is not None:
                already_presented = self._presented_item == selected
                self.select(selected.identity, open_detail=False)
                if (
                    entering_detail
                    and already_presented
                    and self._active
                    and self.video_player.autoplay
                    and not self.video_player.playing
                ):
                    self._play_video()
            else:
                self._clear_detail()
        self._widget("galleryThumbnailSizeLabel", QLabel).setText("Row density" if mode == "List" else "Thumbnail size")
        self.detail_check.setVisible(mode == "Detail")
        self._responsive_selection()
        self._density_changed()
        if mode == "Grid" and self._origin_identity:
            button = self._grid_buttons.get(self._origin_identity)
            if button is not None:
                QTimer.singleShot(0, lambda: self._focus_grid_origin(button))
        self.canvas_loading.ensure_front()
        QTimer.singleShot(0, self._sync_pager)
        self._queue_neighbor_prefetch()
        if persist:
            self._schedule_save()

    def _focus_grid_origin(self, button: QPushButton) -> None:
        if self.runner.closed:
            return
        if self._grid_mount_keys is not None:
            self._grid_mount_callbacks.append(lambda: self._focus_grid_origin(button))
            return
        button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grid_scroll.ensureWidgetVisible(button)

    @staticmethod
    def _thumbnail_pixels(scale_percent: int) -> int:
        """Convert the user-facing percentage to the internal logical-pixel thumbnail size."""

        return max(1, round(STANDARD_THUMBNAIL_PX * scale_percent / 100))

    def _density_changed(self, *_args) -> None:
        scale_percent = self.slider.value()
        self._thumbnail_percents[self.mode] = scale_percent
        self._widget("galleryThumbnailSizeValue", QLabel).setText(f"{scale_percent}%")
        if scale_percent == self._applied_thumbnail_percent:
            # View switches do not imply a size change or a second full repaint.
            return
        # Capture one stable identity before the first geometry mutation. A
        # scrollbar pixel value points at a different image after row/column
        # counts change; new slider events reuse this anchor until settled.
        if self._density_anchor is None and self.items and self._rendered_page is not None:
            if self.mode == "Grid":
                identity = self._browsing_anchor()
                if identity in self._grid_buttons:
                    card = self._grid_buttons[identity].parentWidget()
                    self._density_anchor = ("Grid", identity, card.y() - self.grid_scroll.verticalScrollBar().value())
            elif self.mode == "List":
                row = self.table.rowAt(0)
                if 0 <= row < len(self.items):
                    self._density_anchor = ("List", self.items[row].identity, self.table.rowViewportPosition(row))
        resize_started = monotonic()
        self._applied_thumbnail_percent = scale_percent
        density = self._thumbnail_pixels(scale_percent)

        # CoverPainter owns the visible pixels. One shared transparent icon
        # preserves QPushButton's content size hint across asynchronous relayouts
        # without allocating one full-size placeholder pixmap per Grid card.
        grid_placeholder = QPixmap(density, density)
        grid_placeholder.fill(Qt.GlobalColor.transparent)
        grid_icon = QIcon(grid_placeholder)
        for identity, button in self._grid_buttons.items():
            button.setIcon(grid_icon)
            button.setIconSize(grid_placeholder.size())
            self._size_grid_card(button, density)
            painter = self._grid_painters.get(identity)
            if painter is not None:
                painter.widget.update()

        row_height = max(36, round(density * 0.35))
        thumbnail_width = max(48, round(row_height * 4 / 3))
        thumbnail_size = QSize(thumbnail_width, max(24, row_height - 10))
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        if self.table.iconSize() != thumbnail_size:
            # The Image column must grow alongside the thumbnail, not clip it.
            # Leave an individually resized column alone until zoom changes.
            self.table.setIconSize(thumbnail_size)
            self.table.setColumnWidth(1, thumbnail_width + 16)
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, row_height)

        self._widget("galleryThumbnailSizeValue", QLabel).setText(f"{scale_percent}%")

        self.film_scroll.setFixedWidth(density + 20)
        film_width, film_height = density + 8, round((density + 8) * 0.63)
        film_placeholder = QPixmap(max(1, film_width - 8), max(1, film_height - 8))
        film_placeholder.fill(Qt.GlobalColor.transparent)
        film_icon = QIcon(film_placeholder)
        for button in self._film_buttons:
            button.setFixedSize(film_width, film_height)
            button.setIcon(film_icon)
            button.setIconSize(film_placeholder.size())

        if perf_enabled():
            measurement(
                log,
                "gallery.zoom_reflow",
                wall_ms=(monotonic() - resize_started) * 1000,
                mode=self.mode,
                mounted=len(self.items),
                scale=scale_percent,
            )
        self.reflow_timer.start(0)
        if self._density_anchor is not None:
            self.density_anchor_timer.start(0)
        self._schedule_save()

    def _restore_density_anchor(self) -> None:
        """Keep the same saved-output identity visible across thumbnail zoom."""
        anchor = self._density_anchor
        self._density_anchor = None
        if anchor is None:
            return
        mode, identity, within_viewport = anchor
        if mode != self.mode:
            self._sync_pager()
            return
        self._suppress_batch_scroll = True
        try:
            if mode == "Grid":
                button = self._grid_buttons.get(identity)
                if button is not None:
                    self.reflow_timer.stop()
                    self._layout_cards()
                    self.cards_layout.activate()
                    self.grid_cards.adjustSize()
                    # Retain the first visible card and its partial-row offset.
                    bar = self.grid_scroll.verticalScrollBar()
                    bar.setValue(button.parentWidget().y() - within_viewport)
            elif mode == "List":
                row = next((i for i, item in enumerate(self.items) if item.identity == identity), -1)
                if row >= 0:
                    cell = self.table.item(row, 0)
                    if cell is not None:
                        self.table.scrollToItem(cell, QAbstractItemView.ScrollHint.PositionAtTop)
                        if self.table.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel:
                            bar = self.table.verticalScrollBar()
                            bar.setValue(bar.value() + self.table.rowViewportPosition(row) - within_viewport)
            self._capture_scroll()
            self._sync_pager()
            if perf_enabled():
                first, last = self._visible_range()
                measurement(
                    log,
                    "gallery.thumbnail_resize",
                    mode=mode,
                    scale=self.slider.value(),
                    first=first,
                    last=last,
                    mounted=len(self.items),
                )
        finally:
            self._suppress_batch_scroll = False

    def _size_new_entries(self, identities: list[str]) -> None:
        """Size newly mounted cards without repainting unchanged previews."""
        percent = self.slider.value()
        identities = [identity for identity in identities if self._prepared_sizes.pop(identity, None) != percent]
        if not identities:
            return
        density = self._thumbnail_pixels(self.slider.value())
        placeholder = QPixmap(density, density)
        placeholder.fill(Qt.GlobalColor.transparent)
        grid_icon = QIcon(placeholder)
        film_width, film_height = density + 8, round((density + 8) * 0.63)
        film_placeholder = QPixmap(max(1, film_width - 8), max(1, film_height - 8))
        film_placeholder.fill(Qt.GlobalColor.transparent)
        film_icon = QIcon(film_placeholder)
        for identity in identities:
            button = self._grid_buttons[identity]
            button.setIcon(grid_icon)
            button.setIconSize(placeholder.size())
            self._size_grid_card(button, density)
            film, _painter = self._film_entries[identity]
            film.setFixedSize(film_width, film_height)
            film.setIcon(film_icon)
            film.setIconSize(film_placeholder.size())

    def _finish_grid_mount_callbacks(self) -> None:
        """Release deferred anchor/scroll operations after mount or emptying."""
        callbacks, self._grid_mount_callbacks = self._grid_mount_callbacks, []
        for callback in callbacks:
            QTimer.singleShot(0, self, callback)

    def _reset_empty_grid_mount(self) -> None:
        """An empty result invalidates cell positions even if keys return later."""
        self._grid_mount_timer.stop()
        self._grid_mount_keys = None
        self._grid_layout_keys = ()
        self._grid_layout_columns = 0
        self._finish_grid_mount_callbacks()

    @Slot()
    def _start_grid_mount(self) -> None:
        """Mount a committed Grid page in short GUI-thread turns."""
        if self.runner.closed:
            return
        if not self._grid_buttons:
            self._reset_empty_grid_mount()
            return
        width = self._thumbnail_pixels(self.slider.value()) + 16
        columns = max(1, (self.grid_viewport.width() + 10) // (width + 10))
        keys = tuple(self._grid_buttons)
        if self._grid_mount_keys is None and keys == self._grid_layout_keys and columns == self._grid_layout_columns:
            return
        if self._grid_mount_keys == keys and self._grid_mount_columns == columns:
            return
        self._grid_mount_timer.stop()
        self._grid_mount_keys = keys
        self._grid_mount_columns = columns
        self._grid_mount_retained = (
            len(self._grid_layout_keys)
            if columns == self._grid_layout_columns and keys[: len(self._grid_layout_keys)] == self._grid_layout_keys
            else 0
        )
        self._grid_mount_cursor = self._grid_mount_retained
        self._grid_mount_added = 0
        self._grid_mount_moved = 0
        self._grid_mount_work_ms = 0.0
        self._grid_mount_max_slice_ms = 0.0
        self._grid_mount_slices = 0
        self._grid_mount_started = monotonic()
        self._grid_mount_timer.start()

    @Slot()
    def _mount_grid_slice(self) -> None:
        """Yield between Qt layout insertions; preserve synchronous fixture fallback."""
        keys = self._grid_mount_keys
        if keys is None or self.runner.closed:
            return
        if keys != tuple(self._grid_buttons):
            self._start_grid_mount()
            return
        width = self._thumbnail_pixels(self.slider.value()) + 16
        columns = max(1, (self.grid_viewport.width() + 10) // (width + 10))
        if columns != self._grid_mount_columns:
            self._start_grid_mount()
            return
        started = monotonic()
        while self._grid_mount_cursor < len(keys):
            index = self._grid_mount_cursor
            key = keys[index]
            # Absolute rows: evicting or appending a batch never moves the
            # retained cards. Empty QGridLayout rows take no height.
            slot = divmod(self.offset + index, columns)
            if self._grid_slots.get(key) != slot:
                card = self._grid_buttons[key].parentWidget()
                if self.cards_layout.indexOf(card) >= 0:
                    self.cards_layout.removeWidget(card)
                    self._grid_mount_moved += 1
                else:
                    self._grid_mount_added += 1
                self.cards_layout.addWidget(card, *slot, Qt.AlignmentFlag.AlignTop)
                self._grid_slots[key] = slot
                card.show()
            self._grid_mount_cursor += 1
            if (monotonic() - started) * 1000 >= self._grid_mount_budget_ms:
                break
        self._hold_anchor()  # its layout pass is GUI work of this slice, so it is timed with it
        slice_ms = (monotonic() - started) * 1000
        self._grid_mount_work_ms += slice_ms
        self._grid_mount_max_slice_ms = max(self._grid_mount_max_slice_ms, slice_ms)
        self._grid_mount_slices += 1
        if self._grid_mount_cursor < len(keys):
            self._grid_mount_timer.start()
            return
        self._grid_layout_keys = keys
        self._grid_layout_columns = columns
        self._grid_mount_keys = None
        if perf_enabled():
            measurement(
                log,
                "gallery.grid_mount_complete",
                elapsed_ms=(monotonic() - self._grid_mount_started) * 1000,
                work_ms=self._grid_mount_work_ms,
                max_slice_ms=self._grid_mount_max_slice_ms,
                slices=self._grid_mount_slices,
                mounted=len(keys),
                columns=columns,
                retained=self._grid_mount_retained,
                added=self._grid_mount_added,
                moved=self._grid_mount_moved,
            )
        self._finish_grid_mount_callbacks()

    def _hold_anchor(self) -> None:
        """Finalize visible row geometry, then pin an adjacent-load anchor if held."""
        if self.mode != "Grid":
            return
        # Initial loading has no held anchor, but its newly shown rows still
        # need final positions before this slice returns to the paint loop.
        # Otherwise later rows can paint against the previous layout geometry.
        self.cards_layout.activate()
        self.grid_cards.adjustSize()
        held = self._held_anchor
        if held is None:
            return
        button = self._grid_buttons.get(held[0])
        bar = self.grid_scroll.verticalScrollBar()
        if button is None or bar.isSliderDown():
            return  # a scrollbar drag owns the position; rewriting it would jump the handle
        bar.setValue(button.parentWidget().y() - held[1] + self._held_scroll_delta)

    @Slot()
    def _layout_cards(self) -> None:
        if self.runner.closed:
            return
        if not self._grid_buttons:
            self._reset_empty_grid_mount()
            return
        self._grid_mount_timer.stop()
        self._grid_mount_keys = None
        started = monotonic()
        width = self._thumbnail_pixels(self.slider.value()) + 16
        columns = max(1, (self.grid_viewport.width() + 10) // (width + 10))
        keys = tuple(self._grid_buttons)
        if keys == self._grid_layout_keys and columns == self._grid_layout_columns:
            self._finish_grid_mount_callbacks()
            return
        # A pure append at the same column count cannot move existing cells.
        # Skip them altogether: QLayout.indexOf() may scan layout items, so
        # checking 100-200 unchanged cards introduces an avoidable GUI pause.
        retained_prefix = (
            len(self._grid_layout_keys)
            if columns == self._grid_layout_columns and keys[: len(self._grid_layout_keys)] == self._grid_layout_keys
            else 0
        )
        moved = 0
        added = 0
        for index in range(retained_prefix, len(keys)):
            key = keys[index]
            slot = divmod(self.offset + index, columns)
            if self._grid_slots.get(key) == slot:
                continue
            card = self._grid_buttons[key].parentWidget()
            if self.cards_layout.indexOf(card) >= 0:
                self.cards_layout.removeWidget(card)
                moved += 1
            else:
                added += 1
            self.cards_layout.addWidget(card, *slot, Qt.AlignmentFlag.AlignTop)
            self._grid_slots[key] = slot
            # Include prepared cards in geometry before restoring the anchor.
            card.show()
        self._grid_layout_keys = keys
        self._grid_layout_columns = columns
        self._finish_grid_mount_callbacks()
        if perf_enabled():
            measurement(
                log,
                "gallery.grid_layout",
                elapsed_ms=(monotonic() - started) * 1000,
                mounted=len(keys),
                columns=columns,
                retained=retained_prefix,
                added=added,
                moved=moved,
            )

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if watched is self.table_viewport:
            if kind == QEvent.Type.MouseMove:
                row = self.table.indexAt(event.position().toPoint()).row()
                if row != self.table.property("hoverRow"):
                    self.table.setProperty("hoverRow", row)
                    self.table_viewport.update()
            elif kind == QEvent.Type.Leave:
                self.table.setProperty("hoverRow", -1)
                self.table_viewport.update()
        if watched is self.page and kind == QEvent.Type.Resize:
            QTimer.singleShot(0, self._responsive_selection)
        if watched is self.grid_viewport and kind == QEvent.Type.Resize:
            if perf_enabled():
                measurement(
                    log,
                    "gallery.grid_resize",
                    mounted=len(self.items),
                    width=self.grid_viewport.width(),
                )
            self.reflow_timer.start(0)
        if kind == QEvent.Type.Wheel and watched in (self.grid_viewport, self.table_viewport):
            vertical_delta = event.angleDelta().y() or event.pixelDelta().y()
            if vertical_delta:
                self._request_boundary_prefetch(1 if vertical_delta < 0 else -1, "wheel")
        return False

    def _request_boundary_prefetch(self, direction: int, source: str) -> None:
        if direction not in (-1, 1):
            return
        self._prefetch_zone_hint = None
        self._scroll_direction = direction
        if perf_enabled():
            measurement(
                log,
                "gallery.scroll_intent",
                direction="forward" if direction > 0 else "backward",
                source=source,
                offset=self.offset,
                mounted=len(self.items),
            )
        QTimer.singleShot(0, lambda requested=direction: self._maybe_load_boundary(requested))

    def _scrollbar_action(self, mode: str, action: int) -> None:
        if mode != self.mode.casefold():
            return
        if self._held_anchor is not None and mode == "grid":
            # Qt has moved sliderPosition but not yet value: keep this input.
            bar = self.grid_scroll.verticalScrollBar()
            self._held_scroll_delta += bar.sliderPosition() - bar.value()
        # actionTriggered(int) emits a plain integer under Qt; compare the
        # explicit enum values rather than relying on Enum/int equality.
        action_value = action.value if isinstance(action, QAbstractSlider.SliderAction) else action
        # SliderMove is handled by sliderMoved, which yields drag direction.
        if action_value in (
            QAbstractSlider.SliderAction.SliderSingleStepAdd.value,
            QAbstractSlider.SliderAction.SliderPageStepAdd.value,
            QAbstractSlider.SliderAction.SliderToMaximum.value,
        ):
            self._request_boundary_prefetch(1, "scrollbar")
        elif action_value in (
            QAbstractSlider.SliderAction.SliderSingleStepSub.value,
            QAbstractSlider.SliderAction.SliderPageStepSub.value,
            QAbstractSlider.SliderAction.SliderToMinimum.value,
        ):
            self._request_boundary_prefetch(-1, "scrollbar")

    def _scrollbar_drag(self, mode: str, position: int) -> None:
        if mode != self.mode.casefold():
            return
        bar = self.grid_scroll.verticalScrollBar() if mode == "grid" else self.table.verticalScrollBar()
        previous = self._drag_scroll_positions.get(mode, bar.value())
        self._drag_scroll_positions[mode] = position
        if position != previous:
            self._request_boundary_prefetch(1 if position > previous else -1, "drag")

    def _scroll_changed(self, name: str, value: int) -> None:
        self._scroll[name] = max(0, value)
        if (
            name in ("grid", "list")
            and name == self.mode.casefold()
            and self._density_anchor is None
            and not self._suppress_batch_scroll
        ):
            # Intermediate Qt geometry changes must not briefly select a
            # different page while the viewport anchor is being restored.
            self._sync_pager()
            self._prioritize_thumbnails()
            self._queue_neighbor_prefetch()
        self._schedule_save()

    def _capture_scroll(self) -> None:
        self._scroll["grid"] = self.grid_scroll.verticalScrollBar().value()
        self._scroll["list"] = self.table.verticalScrollBar().value()
        self._scroll["filmstrip"] = self.film_scroll.verticalScrollBar().value()

    def _browsing_anchor(self) -> str | None:
        if not self.items:
            return None
        if self.mode == "Detail":
            return self.selected_id
        if self.mode == "List":
            row = self.table.rowAt(0)
            if row < 0:
                row = 0
            item = self.table.item(row, 0)
            return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None
        position = self.grid_scroll.verticalScrollBar().value()
        for item in self.items:
            button = self._grid_buttons.get(item.identity)
            if button is not None and button.parentWidget().geometry().bottom() >= position:
                return item.identity
        return self.items[-1].identity

    def _restore_scroll_later(self, anchor: str | None = None) -> None:
        target = self._page_scroll_target
        self._page_scroll_target = None
        generation = self._query_generation

        def restore():
            if self.runner.closed or generation != self._query_generation:
                return
            if self.reflow_timer.isActive():
                self.reflow_timer.stop()
                self._start_grid_mount()
            if self._grid_mount_keys is not None:
                self._grid_mount_callbacks.append(restore)
                return
            for name, bar in (
                ("grid", self.grid_scroll.verticalScrollBar()),
                ("list", self.table.verticalScrollBar()),
                ("filmstrip", self.film_scroll.verticalScrollBar()),
            ):
                if target == "start":
                    bar.setValue(0)
                elif target == "end":
                    bar.setValue(bar.maximum())
                else:
                    bar.setValue(min(self._scroll[name], bar.maximum()))

            if not anchor or target is not None:
                self._queue_neighbor_prefetch()
                return
            if self.mode == "Grid":
                button = self._grid_buttons.get(anchor)
                if button is not None:
                    self.grid_scroll.verticalScrollBar().setValue(button.parentWidget().y())
            elif self.mode == "List":
                try:
                    row = next(i for i, item in enumerate(self.items) if item.identity == anchor)
                except StopIteration:
                    return
                cell = self.table.item(row, 0)
                if cell is not None:
                    self.table.scrollToItem(cell, QAbstractItemView.ScrollHint.PositionAtTop)
            elif self.mode == "Detail":
                try:
                    row = next(i for i, item in enumerate(self.items) if item.identity == anchor)
                except StopIteration:
                    return
                if row < len(self._film_buttons):
                    self.film_scroll.ensureWidgetVisible(self._film_buttons[row])
            self._queue_neighbor_prefetch()

        QTimer.singleShot(0, self, restore)

    def _read_action(self, action: str) -> None:
        if self._snapshot_at and action == "edit":
            return
        """Revalidate source provenance under a lease without blocking the Qt thread."""
        item = self._selected()
        if item is None:
            return

        video = action in ("copy", "external") and clip_summary(self._request(item)) is not None

        def read():
            if video:
                return self._video_companion_path(item)
            with original_images_lock(self.state_dir, timeout=12):
                path = resolve_item_read(item, self.state_dir)[0]
            return path

        self.runner.submit(
            asyncio.to_thread(read),
            lambda result, error: self._read_action_done(action, item.identity, result, error),
        )

    def _read_action_done(self, action: str, identity: str, result, error) -> None:
        if self._snapshot_at and action == "edit":
            return
        if self.selected_id != identity:
            return
        if error is not None or not result:
            if isinstance(error, OriginalImagesBusyError):
                reason = "another operation is using the original images; try again shortly."
            elif isinstance(error, (OSError, OriginalImagesUnavailableError)):
                reason = str(error)
            else:
                if error is not None:
                    diagnostic_event(
                        log,
                        "gallery.read_action_failed",
                        level=logging.WARNING,
                        data={
                            "action": action,
                            "reason": type(error).__name__,
                        },
                    )
                reason = "the image could not be read."
            self._selection_notice(f"Image action unavailable: {reason}")
            return
        if action == "copy":
            QApplication.clipboard().setText(result)
        elif action == "folder":
            open_local_path(
                str(Path(result).parent),
                runner=self.runner,
                directory=True,
                on_error=lambda reason: self._selection_notice(f"Could not open the image folder: {reason}"),
            )
        elif action == "external":
            open_local_path(
                result,
                runner=self.runner,
                directory=False,
                on_error=lambda reason: self._selection_notice(
                    f"Could not open the image in an external viewer: {reason}"
                ),
            )
        elif action == "edit" and self._edit_callback is not None:
            self._edit_callback(result)

    def _video_playback_changed(self, active: bool) -> None:
        """Keep existing image overlays from covering the video's bottom controls."""
        for name in ("galleryPositionOverlay", "galleryDimensionsOverlay", "galleryImageToolbar"):
            self._widget(name).setVisible(not active)

    def _play_video(self) -> None:
        """Resolve the MP4 on a worker, then play it in Images without blocking Qt."""
        item = self._selected()
        if item is None or clip_summary(self._request(item)) is None:
            return
        generation = self._detail_generation

        def done(result, error) -> None:
            if (
                self.selected_id != item.identity
                or generation != self._detail_generation
                or self.runner.closed
                or not self._active
                or self.mode != "Detail"
                or self._recycle_busy
            ):
                return
            if error is not None or not result:
                self._selection_notice(f"Video unavailable: {error or 'it could not be read.'}")
                return
            if not self.video_player.playing:
                self._start_video(result)

        self.runner.submit(asyncio.to_thread(self._video_companion_path, item), done)

    def _start_video(self, path: str) -> None:
        """Open an already-resolved MP4 without repeating the companion check."""
        try:
            self.video_player.start(path)
        except (OSError, RuntimeError, ImportError) as exc:
            self.video_player.release(show_play=True)
            self._selection_notice(f"Inline video unavailable: {exc}. Use Open video in player.")

    def _copy_path(self) -> None:
        self._read_action("copy")

    def _open_folder(self) -> None:
        self._read_action("folder")

    def _open_external(self) -> None:
        self._read_action("external")

    def export_query(self) -> GalleryQuery:
        """Capture current filters without changing selection, paging or viewer state."""
        return replace(self._query(offset=0), limit=200)

    def export_identity(self) -> str | None:
        """Expose only the committed identity of the current single selection."""
        if self._snapshot_at:
            raise ValidationError("Snapshot browsing cannot authorize export; wait for live state access.")
        item = self._selected()
        return item.identity if item is not None else None

    def export_checked(self) -> tuple[str, ...]:
        """Freeze explicitly checked image identities for the shared exporter."""
        if self._snapshot_at:
            raise ValidationError("Snapshot browsing cannot authorize export; wait for live state access.")
        return self.selection.snapshot()

    def _selected(self) -> GalleryItem | None:
        return next((item for item in self.items if item.identity == self.selected_id), None)

    def _clear_detail(self) -> None:
        self.video_player.show_for_video(False)
        self.surface_painter.finish_transition()
        self._image_pending = False
        self._presented_item = None
        self.hover_card.set_item(None)
        self._image_generation += 1
        self._detail_generation += 1
        self.surface_painter.pixmap = QPixmap()
        self.surface_painter.placeholder = ""
        self.surface.update()
        self._widget("galleryPositionOverlay", QLabel).setText("0 / 0")
        self._widget("galleryDimensionsOverlay", QLabel).setText("—")
        self._widget("galleryModelValue", QLabel).setText("No saved output selected")
        self._widget("galleryModelIdentifier", QLabel).hide()
        self._widget("galleryAdditionalFileSizeLabel", QLabel).setText("File size")
        self._widget("galleryAdditionalDurationLabel", QLabel).setText("Provider duration")
        self._widget("galleryLoRALabel", QLabel).setText("LoRAs")
        self._widget("galleryLoRAChip1").hide()
        self._widget("galleryLoRAChip2").hide()
        for name in ("galleryStepsValue", "galleryGuidanceValue", "gallerySamplerValue", "galleryDateValue"):
            self._widget(name, QLabel).setText("—")
        self._show_positive_prompt("No saved output selected.", ())
        for name in (
            "galleryAdditionalNegativePromptValue",
            "galleryAdditionalSeedValue",
            "galleryAdditionalOperationValue",
            "galleryAdditionalStrengthValue",
            "galleryAdditionalFilenameValue",
            "galleryAdditionalProviderValue",
            "galleryAdditionalFileSizeValue",
            "galleryAdditionalDurationValue",
            "galleryAdditionalPathValue",
        ):
            self._widget(name, QLabel).setText("—")
        self._widget("galleryRawMetadata").setPlainText("{}")
        self.use_settings_button.setEnabled(False)
        self.regenerate_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self.external_button.setEnabled(False)
        self._update_navigation()

    def _schedule_save(self) -> None:
        if self._restoring or self.runner.closed:
            return
        self._save_dirty = True
        self.save_timer.start()

    def _state_document(self) -> dict:
        try:
            query = self._query(offset=self.offset)
        except ValidationError:
            query = GalleryQuery(offset=self.offset, limit=_read_limit())
        return {
            "query": asdict(query),
            "filters": {
                "prompt": self.prompt.text(),
                "model": self.model.currentText(),
                "lora": self._lora_identifier(),
                "date": self.date_filter.currentText(),
                "dimensions": self.dimensions.currentText(),
                "ratio": self.ratio.currentText(),
                "sort": self.sort.currentText(),
            },
            "view": self.mode,
            "thumbnail_scale_percents": dict(self._thumbnail_percents),
            "slideshow": self.slideshow.preferences(),
            "video_autoplay": self.video_player.autoplay,
            "selected": self.selected_id,
            "auto_refresh": self._live_mode >= 1,
            "follow": self._live_mode == 2,
            "scroll": dict(self._scroll),
        }

    @Slot()
    def _flush_save(self) -> None:
        if self.runner.closed or not self._save_dirty or self._save_running:
            return
        document = self._state_document()
        self._save_dirty = False
        self._save_running = True

        async def write():
            await asyncio.to_thread(save_viewer, self.state_dir, document)

        try:
            self._save_future = self.runner.submit(write(), self._save_done)
        except RuntimeError:
            # The owner may shut down the shared runner directly (for example
            # during Qt teardown). A stale debounce must never raise into the
            # Qt event loop after that lifecycle boundary.
            self._save_running = False
            self._save_future = None
            self._save_dirty = True
            diagnostic_event(
                log,
                "gallery.preferences_save_skipped",
                level=logging.INFO,
                data={},
            )

    def save_now(self) -> None:
        """Persist the latest viewer state before the shared async runner is shut down."""

        if perf_enabled():
            self._perf_stats.flush(force=True)
        self.save_timer.stop()
        future = self._save_future
        if future is not None and not future.done():
            try:
                future.result(timeout=5)
            except Exception as exc:
                diagnostic_event(
                    log,
                    "gallery.preferences_save_failed",
                    level=logging.WARNING,
                    data={
                        "reason": type(exc).__name__,
                    },
                )
                return
        try:
            save_viewer(self.state_dir, self._state_document())
            self._save_dirty = False
        except Exception as exc:
            diagnostic_event(
                log,
                "gallery.preferences_save_failed",
                level=logging.WARNING,
                data={
                    "reason": type(exc).__name__,
                },
            )

    def _save_done(self, _result, error) -> None:
        self._save_running = False
        self._save_future = None
        if error is not None:
            diagnostic_event(
                log,
                "gallery.preferences_save_failed",
                level=logging.WARNING,
                data={
                    "reason": type(error).__name__,
                },
            )
        if self._save_dirty:
            self.save_timer.start(0)

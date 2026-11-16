"""Bounded, asynchronous thumbnails and the hover preview for the queue items table.

Decoding reuses the Images gallery's `_image` at the gallery's own thumbnail size: the shared original-images lease,
a scaled decode (never the full original) and the same background runner. When the row has a gallery identity
("attempt:index"), the gallery record is looked up on the worker thread and passed along, so `_image` reads and
writes the gallery's on-disk thumbnail cache: a thumbnail made here also serves Images, and the reverse. One decode
per output feeds both the 128 px square tile (cropped) and, when requested, the hover preview (the 600 px image as is). Results are delivered on the
GUI thread and kept in small LRU stores.
"""

import asyncio
import logging
from collections import OrderedDict, deque
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel, QTableView

from dtflow.diagnostics import measurement
from dtflow.logging_setup import perf_enabled
from dtflow.persistence import gallery as gallery_storage
from dtflow.services.gallery import item_from_record
from dtflow.ui.gallery_page import THUMBNAIL_SIDE, _image

ROW_SIDE = 128  # the 64 px tile at 2x, centre-cropped to a square
PREVIEW_SIDE = THUMBNAIL_SIDE  # the hover preview is the gallery's 600 px thumbnail, shown as is (aspect-fit)
MAX_ROW_PIXMAPS = 300
MAX_PREVIEW_PIXMAPS = 12
CONCURRENCY = 3  # lighter than the gallery's 6: this table shares the window with a running queue
PREVIEW_DELAY_MS = 350
PREVIEW_COLUMN = 0
PREVIEW_BOX = 440  # longest side of the hover preview in logical px (the 600 px source covers it up to 1.35x DPI)
REVALIDATE_MS = 3000  # selected-item action availability stays short-lived
DISPLAY_REVALIDATE_MS = 60_000  # other preview pixels can be reused between ordinary queue polls
SUMMARY_SECONDS = 30
log = logging.getLogger(__name__)


def _decode(path: str, identity: str | None, state: Path):
    """Decode at the gallery's thumbnail size; with a committed identity the gallery's disk cache is used."""
    if not identity:
        return None, "No committed saved image"
    record = gallery_storage.get_record(state, identity)
    if record is None or record["path"] != path:
        return None, "Saved image is no longer available"
    item = item_from_record(record, state=state)
    return _image(path, THUMBNAIL_SIDE, state, item=item, cache=True)


class QueueThumbnails(QObject):
    """LRU pixmaps per size. `pixmap()` never blocks: it returns None and schedules one decode per output."""

    changed = Signal(str)  # a pixmap for this path arrived

    def __init__(self, runner, state: Path, parent=None):
        super().__init__(parent)
        self._runner, self._state = runner, Path(state)
        self._view = parent if isinstance(parent, QTableView) else None
        self._stores = {ROW_SIDE: (OrderedDict(), MAX_ROW_PIXMAPS), PREVIEW_SIDE: (OrderedDict(), MAX_PREVIEW_PIXMAPS)}
        self._waiting: deque = deque()
        self._queued: set[tuple[str, str | None]] = set()
        self._failed: set[tuple[str, str | None]] = set()
        self._wanted: dict[tuple[str, str | None], set[int]] = {}
        self._checked: dict[tuple[str, str | None], float] = {}
        self._active = 0
        self._epoch = 0
        self._closed = False
        self._diagnostic_counts: dict[str, int] = {}
        self._diagnostic_total_ms = self._diagnostic_max_ms = 0.0
        self._diagnostic_since = monotonic()
        self._expiry = QTimer(self)
        self._expiry.setInterval(REVALIDATE_MS)
        self._expiry.timeout.connect(self._expire)
        runner.shutdown_started.connect(self.shutdown)
        if parent is not None:
            parent.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Hide:
            self._expiry.stop()
            self.clear()
        elif event.type() == QEvent.Type.Show and not self._closed:
            self._expiry.start()
        return False

    def shutdown(self) -> None:
        self._closed = True
        self._expiry.stop()
        self.clear()

    def _expire(self) -> None:
        now = monotonic()
        if now - self._diagnostic_since >= SUMMARY_SECONDS:
            self._flush_diagnostics()
        visible = None
        selected = None
        if self._view is not None:
            if not self._view.isVisible():
                return
            model = self._view.model()
            index = self._view.currentIndex()
            row = model.row_at(index.row()) if index.isValid() else None
            if row is not None and row.outputs:
                selected = (row.outputs[0], row.gallery_identity)
            first = self._view.rowAt(0)
            if first >= 0:
                last = self._view.rowAt(max(0, self._view.viewport().height() - 1))
                if last < first:
                    last = model.rowCount() - 1
                visible = {
                    (item.outputs[0], item.gallery_identity)
                    for index in range(first, last + 1)
                    if (item := model.row_at(index)) is not None and item.outputs
                }
                if selected is not None:
                    visible.add(selected)
            else:
                visible = {selected} if selected is not None else set()
        for key, checked in tuple(self._checked.items()):
            if key in self._queued or (visible is not None and key not in visible):
                continue
            # The selected Open in Images action validates through the gallery on click;
            # successful preview pixels need no three-second availability repaint.
            threshold = REVALIDATE_MS if key in self._failed else DISPLAY_REVALIDATE_MS
            if now - checked >= threshold / 1000:
                if key in self._failed:
                    self._forget(key)  # let failed visible previews retry without resetting successful pixels
                self.changed.emit(key[0])

    def _forget(self, key) -> None:
        for store, _ in self._stores.values():
            store.pop(key, None)
        self._checked.pop(key, None)
        self._failed.discard(key)

    def _request(self, key, side: int) -> None:
        self._wanted.setdefault(key, set()).add(side)
        if key not in self._queued:
            self._queued.add(key)
            self._waiting.append((self._epoch, key))
            self._pump()

    def pixmap(self, path: str, side: int = ROW_SIDE, identity: str | None = None) -> QPixmap | None:
        if self._closed or self._runner.closed or not identity:
            return None
        key = (path, identity)
        age_ms = (monotonic() - self._checked.get(key, monotonic())) * 1000
        stale = age_ms >= DISPLAY_REVALIDATE_MS
        if key in self._failed and age_ms >= REVALIDATE_MS:
            self._forget(key)
        store = self._stores[side][0]
        found = store.get(key)
        if found is not None:
            store.move_to_end(key)
        if (found is None or stale) and key not in self._failed:
            self._request(key, side)
        return found

    def is_failed(self, path: str, side: int = ROW_SIDE, identity: str | None = None) -> bool:
        return not identity or (path, identity) in self._failed

    def available(self, path: str, identity: str | None) -> bool:
        """Keep selected-item availability fresh without decoding every displayed tile on each poll."""
        pixmap = self.pixmap(path, identity=identity)
        key = (path, identity)
        fresh = monotonic() - self._checked.get(key, float("-inf")) < REVALIDATE_MS / 1000
        if (
            pixmap is not None
            and not fresh
            and key not in self._queued
            and key not in self._failed
            and not self._closed
            and not self._runner.closed
        ):
            self._request(key, ROW_SIDE)
        return pixmap is not None and fresh and key not in self._queued

    def clear(self) -> None:
        """Forget everything; results still in flight are ignored when they arrive."""
        self._flush_diagnostics()
        self._epoch += 1
        for store, _ in self._stores.values():
            store.clear()
        self._waiting.clear()
        self._queued.clear()
        self._failed.clear()
        self._wanted.clear()
        self._checked.clear()
        # In-flight jobs still occupy slots until their callbacks arrive; never oversubscribe after clearing.

    def _flush_diagnostics(self) -> None:
        if self._diagnostic_counts:
            measurement(
                log,
                "queue.thumbnail_summary",
                **self._diagnostic_counts,
                elapsed_total_ms=self._diagnostic_total_ms,
                elapsed_max_ms=self._diagnostic_max_ms,
                active=self._active,
                waiting=len(self._waiting),
            )
        self._diagnostic_counts.clear()
        self._diagnostic_total_ms = self._diagnostic_max_ms = 0.0
        self._diagnostic_since = monotonic()

    def _pump(self) -> None:
        if self._closed or self._runner.closed:
            return
        while self._active < CONCURRENCY and self._waiting:
            epoch, key = self._waiting.popleft()
            if epoch != self._epoch:
                continue
            self._active += 1
            path, identity = key

            async def work(filename=path, ident=identity):
                return await asyncio.to_thread(_decode, filename, ident, self._state)

            started = monotonic() if perf_enabled() else None
            self._runner.submit(
                work(), lambda result, error, e=epoch, k=key, s=started: self._done(e, k, result, error, s)
            )

    @staticmethod
    def _square(image, side: int) -> QPixmap:
        """The tile: a centred square crop of the 600 px thumbnail, scaled to the tile's pixel size."""
        edge = min(image.width(), image.height())
        square = image.copy((image.width() - edge) // 2, (image.height() - edge) // 2, edge, edge)
        return QPixmap.fromImage(
            square.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    def _done(self, epoch: int, key, result, error, started: float | None = None) -> None:
        self._active = max(0, self._active - 1)
        if started is not None and perf_enabled():
            image, issue = result if error is None and result else (None, "")
            outcome = (
                "obsolete"
                if epoch != self._epoch or self._closed
                else "worker_failed"
                if error is not None
                else "available"
                if image is not None
                else "unavailable"
                if issue in {"No committed saved image", "Saved image is no longer available"}
                else "busy"
                if issue == "Original temporarily busy"
                else "read_decode_failed"
            )
            self._diagnostic_counts[outcome] = self._diagnostic_counts.get(outcome, 0) + 1
            elapsed = (monotonic() - started) * 1000
            self._diagnostic_total_ms += elapsed
            self._diagnostic_max_ms = max(self._diagnostic_max_ms, elapsed)
            if self._closed or not self._expiry.isActive():
                self._flush_diagnostics()
        if epoch == self._epoch and not self._closed:
            self._queued.discard(key)
            image, _issue = result if error is None and result else (None, "unreadable")
            self._checked[key] = monotonic()
            if image is not None:
                wanted = self._wanted.pop(key, {ROW_SIDE})
                wanted.update(side for side, (store, _) in self._stores.items() if key in store)
                for side in wanted:
                    store, limit = self._stores[side]
                    store[key] = QPixmap.fromImage(image) if side == PREVIEW_SIDE else self._square(image, side)
                    while len(store) > limit:
                        evicted, _ = store.popitem(last=False)
                        if not any(evicted in other for other, _ in self._stores.values()):
                            self._checked.pop(evicted, None)
            else:
                self._forget(key)  # failed live revalidation invalidates every retained size
                self._checked[key] = monotonic()
                self._wanted.pop(key, None)
                self._failed.add(key)  # includes temporary lease refusal; the owned expiry timer permits retry
            self.changed.emit(key[0])
        self._pump()


class HoverPreview(QObject):
    """A read-only preview (up to 440 px) beside the cursor over a Preview tile.

    A frameless, mouse-transparent tool window: it can never intercept a row click or selection. It shows the
    gallery's 600 px thumbnail (the same disk-cached file Images uses), never the original, and waits briefly so a passing pointer does not trigger decodes.
    """

    def __init__(self, view: QTableView, thumbnails: QueueThumbnails):
        super().__init__(view)
        self.view, self.thumbnails = view, thumbnails
        self._row: int | None = None
        self._path = ""
        self._box = PREVIEW_BOX
        self._identity: str | None = None
        self._anchor = QPoint()
        self.label = QLabel(view, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.label.setObjectName("queueThumbPreview")
        self.label.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setFixedSize(QSize(232, 232))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._timeout)
        view.setMouseTracking(True)
        view.viewport().installEventFilter(self)
        thumbnails.changed.connect(self._arrived)
        thumbnails._runner.shutdown_started.connect(self.hide)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseMove:
            index = self.view.indexAt(event.position().toPoint())
            row = index.row() if index.isValid() and index.column() == PREVIEW_COLUMN else None
            if row != self._row:
                self.hide()
                self._row = row
                if row is not None:
                    self._anchor = event.globalPosition().toPoint()
                    self._timer.start()
        elif kind in (QEvent.Type.Leave, QEvent.Type.Wheel, QEvent.Type.Hide):
            self.hide()
            self._row = None
        return False

    def hide(self) -> None:
        self._timer.stop()
        self.label.hide()

    def _timeout(self) -> None:
        if self._row is not None:
            self.show_row(self._row, self._anchor)

    def show_row(self, row: int, global_pos: QPoint) -> None:
        item = self.view.model().row_at(row)
        if item is None or item.is_script_parent or not item.outputs:
            return  # parent scripts are summaries, not an extra image attempt
        self._path = item.outputs[0]
        self._identity = item.gallery_identity
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self._box = max(160, min(PREVIEW_BOX, area.height() - 48, area.width() - 48))  # never taller than the screen
        self._fill()
        x = max(area.left(), min(global_pos.x() + 16, area.right() - self.label.width()))
        y = max(area.top(), min(global_pos.y() + 16, area.bottom() - self.label.height()))
        self.label.move(x, y)
        self.label.show()

    def _fill(self) -> None:
        pixmap = self.thumbnails.pixmap(self._path, PREVIEW_SIDE, self._identity)
        if pixmap is None:
            self.label.setPixmap(QPixmap())
            self.label.setFixedSize(QSize(232, 232))
            self.label.setText(
                "Loading\u2026"
                if not self.thumbnails.is_failed(self._path, PREVIEW_SIDE, self._identity)
                else "No preview"
            )
            return
        ratio = self.label.devicePixelRatioF()
        edge = int(self._box * ratio)
        scaled = pixmap.scaled(
            edge, edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        scaled.setDevicePixelRatio(ratio)
        self.label.setPixmap(scaled)
        # Hug the picture (frame is 1 px border + 5 px padding on each side) instead of leaving empty bars.
        self.label.setFixedSize(QSize(round(scaled.width() / ratio) + 12, round(scaled.height() / ratio) + 12))

    def _arrived(self, path: str) -> None:
        if self.label.isVisible() and path == self._path:
            self._fill()

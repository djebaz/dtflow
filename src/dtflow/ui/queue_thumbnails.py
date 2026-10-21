"""Bounded, asynchronous thumbnails and the hover preview for the queue items table.

Decoding reuses the Images gallery's `_image`: the shared original-images lease, a scaled decode (never the full
original) and the same background runner. Results are delivered on the GUI thread and kept in small LRU stores.
Passing a verified GalleryItem to `_image` would add the on-disk thumbnail cache; that needs the queue-output to
gallery-identity mapping from the plan's integration audit, so it is deliberately not guessed here.
"""

import asyncio
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel, QTableView

from dtflow.ui.gallery_page import _image

ROW_SIDE = 128  # the 64 px tile at 2x
PREVIEW_SIDE = 440  # the 220 px hover preview at 2x
MAX_ROW_PIXMAPS = 300
MAX_PREVIEW_PIXMAPS = 12
CONCURRENCY = 3  # lighter than the gallery's 6: this table shares the window with a running queue
PREVIEW_DELAY_MS = 350
PREVIEW_COLUMN = 0
BUSY = "Original temporarily busy"


class QueueThumbnails(QObject):
    """LRU pixmaps keyed by (path, side). `pixmap()` never blocks: it returns None and schedules a decode."""

    changed = Signal(str)  # a pixmap for this path arrived

    def __init__(self, runner, state: Path, parent=None):
        super().__init__(parent)
        self._runner, self._state = runner, Path(state)
        self._stores = {ROW_SIDE: (OrderedDict(), MAX_ROW_PIXMAPS), PREVIEW_SIDE: (OrderedDict(), MAX_PREVIEW_PIXMAPS)}
        self._waiting: deque = deque()
        self._queued: set = set()
        self._failed: set = set()
        self._active = 0
        self._epoch = 0

    def pixmap(self, path: str, side: int = ROW_SIDE) -> QPixmap | None:
        store = self._stores[side][0]
        found = store.get(path)
        if found is not None:
            store.move_to_end(path)
            return found
        key = (path, side)
        if key not in self._queued and key not in self._failed:
            self._queued.add(key)
            self._waiting.append((self._epoch, key))
            self._pump()
        return None

    def is_failed(self, path: str, side: int = ROW_SIDE) -> bool:
        return (path, side) in self._failed

    def clear(self) -> None:
        """Forget everything; results still in flight are ignored when they arrive."""
        self._epoch += 1
        for store, _ in self._stores.values():
            store.clear()
        self._waiting.clear()
        self._queued.clear()
        self._failed.clear()

    def _pump(self) -> None:
        while self._active < CONCURRENCY and self._waiting:
            epoch, key = self._waiting.popleft()
            if epoch != self._epoch:
                continue
            self._active += 1
            path, side = key

            async def work(filename=path, size=side):
                return await asyncio.to_thread(_image, filename, size, self._state)

            self._runner.submit(work(), lambda result, error, e=epoch, k=key: self._done(e, k, result, error))

    def _done(self, epoch: int, key: tuple, result, error) -> None:
        self._active = max(0, self._active - 1)
        if epoch == self._epoch:
            self._queued.discard(key)
            path, side = key
            image, issue = result if error is None and result else (None, "unreadable")
            if image is not None:
                store, limit = self._stores[side]
                store[path] = QPixmap.fromImage(image)
                while len(store) > limit:
                    store.popitem(last=False)
                self.changed.emit(path)
            elif issue == BUSY:
                QTimer.singleShot(2000, lambda: self.pixmap(path, side) if epoch == self._epoch else None)
            else:
                self._failed.add(key)
        self._pump()


class HoverPreview(QObject):
    """A read-only 220 px preview beside the cursor over a Preview tile.

    A frameless, mouse-transparent tool window: it can never intercept a row click or selection. It shows the
    bounded 440 px decode, not the original, and waits briefly so a passing pointer does not trigger decodes.
    """

    def __init__(self, view: QTableView, thumbnails: QueueThumbnails):
        super().__init__(view)
        self.view, self.thumbnails = view, thumbnails
        self._row: int | None = None
        self._path = ""
        self._anchor = QPoint()
        self.label = QLabel(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
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
        if item is None or not item.outputs:
            return  # a missing result gets no preview, never a fabricated one
        self._path = item.outputs[0]
        self._fill()
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(global_pos.x() + 16, area.right() - self.label.width()))
        y = max(area.top(), min(global_pos.y() + 16, area.bottom() - self.label.height()))
        self.label.move(x, y)
        self.label.show()

    def _fill(self) -> None:
        pixmap = self.thumbnails.pixmap(self._path, PREVIEW_SIDE)
        if pixmap is None:
            self.label.setPixmap(QPixmap())
            self.label.setText("Loading\u2026" if not self.thumbnails.is_failed(self._path, PREVIEW_SIDE) else "No preview")
            return
        ratio = self.label.devicePixelRatioF()
        scaled = pixmap.scaled(
            int(220 * ratio), int(220 * ratio), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        scaled.setDevicePixelRatio(ratio)
        self.label.setPixmap(scaled)

    def _arrived(self, path: str) -> None:
        if self.label.isVisible() and path == self._path:
            self._fill()

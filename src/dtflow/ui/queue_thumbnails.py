"""Bounded, asynchronous thumbnails and the hover preview for the queue items table.

Decoding reuses the Images gallery's `_image` at the gallery's own thumbnail size: the shared original-images lease,
a scaled decode (never the full original) and the same background runner. When the row has a gallery identity
("attempt:index"), the gallery record is looked up on the worker thread and passed along, so `_image` reads and
writes the gallery's on-disk thumbnail cache: a thumbnail made here also serves Images, and the reverse. One decode
per output feeds both the 128 px square tile (cropped) and, when requested, the hover preview (the 600 px image as is). Results are delivered on the
GUI thread and kept in small LRU stores.
"""

import asyncio
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel, QTableView

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
BUSY = "Original temporarily busy"


def _decode(path: str, identity: str | None, state: Path):
    """Decode at the gallery's thumbnail size; with a committed identity the gallery's disk cache is used."""
    item = None
    if identity:
        try:
            record = gallery_storage.get_record(state, identity)
            item = item_from_record(record, state=state) if record else None
        except Exception:  # noqa: BLE001 - any lookup failure only means no disk cache: fall back to a plain decode
            item = None
    return _image(path, THUMBNAIL_SIDE, state, item=item, cache=item is not None)


class QueueThumbnails(QObject):
    """LRU pixmaps per size. `pixmap()` never blocks: it returns None and schedules one decode per output."""

    changed = Signal(str)  # a pixmap for this path arrived

    def __init__(self, runner, state: Path, parent=None):
        super().__init__(parent)
        self._runner, self._state = runner, Path(state)
        self._stores = {ROW_SIDE: (OrderedDict(), MAX_ROW_PIXMAPS), PREVIEW_SIDE: (OrderedDict(), MAX_PREVIEW_PIXMAPS)}
        self._waiting: deque = deque()
        self._queued: set[str] = set()
        self._failed: set[str] = set()
        self._wanted: dict[str, set[int]] = {}
        self._identity: dict[str, str | None] = {}
        self._active = 0
        self._epoch = 0

    def pixmap(self, path: str, side: int = ROW_SIDE, identity: str | None = None) -> QPixmap | None:
        store = self._stores[side][0]
        found = store.get(path)
        if found is not None:
            store.move_to_end(path)
            return found
        if path not in self._failed:
            self._wanted.setdefault(path, set()).add(side)
            self._identity[path] = identity
            if path not in self._queued:
                self._queued.add(path)
                self._waiting.append((self._epoch, path))
                self._pump()
        return None

    def is_failed(self, path: str, side: int = ROW_SIDE) -> bool:
        return path in self._failed

    def clear(self) -> None:
        """Forget everything; results still in flight are ignored when they arrive."""
        self._epoch += 1
        for store, _ in self._stores.values():
            store.clear()
        self._waiting.clear()
        self._queued.clear()
        self._failed.clear()
        self._wanted.clear()
        self._identity.clear()

    def _pump(self) -> None:
        while self._active < CONCURRENCY and self._waiting:
            epoch, path = self._waiting.popleft()
            if epoch != self._epoch:
                continue
            self._active += 1
            identity = self._identity.get(path)

            async def work(filename=path, ident=identity):
                return await asyncio.to_thread(_decode, filename, ident, self._state)

            self._runner.submit(work(), lambda result, error, e=epoch, p=path: self._done(e, p, result, error))

    @staticmethod
    def _square(image, side: int) -> QPixmap:
        """The tile: a centred square crop of the 600 px thumbnail, scaled to the tile's pixel size."""
        edge = min(image.width(), image.height())
        square = image.copy((image.width() - edge) // 2, (image.height() - edge) // 2, edge, edge)
        return QPixmap.fromImage(square.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _done(self, epoch: int, path: str, result, error) -> None:
        self._active = max(0, self._active - 1)
        if epoch == self._epoch:
            self._queued.discard(path)
            image, issue = result if error is None and result else (None, "unreadable")
            if image is not None:
                for side in self._wanted.pop(path, {ROW_SIDE}):
                    store, limit = self._stores[side]
                    store[path] = QPixmap.fromImage(image) if side == PREVIEW_SIDE else self._square(image, side)
                    while len(store) > limit:
                        store.popitem(last=False)
                self.changed.emit(path)
            elif issue == BUSY:
                QTimer.singleShot(2000, lambda: self.pixmap(path, ROW_SIDE, self._identity.get(path)) if epoch == self._epoch else None)
            else:
                self._wanted.pop(path, None)
                self._failed.add(path)
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
            self.label.setText("Loading\u2026" if not self.thumbnails.is_failed(self._path, PREVIEW_SIDE) else "No preview")
            return
        ratio = self.label.devicePixelRatioF()
        edge = int(self._box * ratio)
        scaled = pixmap.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.label.setPixmap(scaled)
        # Hug the picture (frame is 1 px border + 5 px padding on each side) instead of leaving empty bars.
        self.label.setFixedSize(QSize(round(scaled.width() / ratio) + 12, round(scaled.height() / ratio) + 12))

    def _arrived(self, path: str) -> None:
        if self.label.isVisible() and path == self._path:
            self._fill()

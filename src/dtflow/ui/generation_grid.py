"""The Generate output grid for a multi-result run (#560): one square tile per queued output item.

A tile is a temporary projection of a queue item, never a store: its status and saved image or video poster come from
the durable queue view the Queues page already reads, and its provisional preview from the live backend event. The
grid paints only the tiles in view (no widget per item), asks the backend for a provisional preview only while a tile
is in view (``PreviewGate``), keeps at most one decoded preview per visible running tile, and decodes saved images at
the Images gallery's thumbnail size through the shared, bounded ``QueueThumbnails`` cache.
"""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QLabel, QSizePolicy

from dtflow.core.preview_gate import PreviewGate
from dtflow.ui.theme import load_tokens
from dtflow.ui.queue_thumbnails import PREVIEW_BOX, PREVIEW_DELAY_MS, PREVIEW_SIDE, QueueThumbnails

GAP = 8
MIN_TILE = 190  # logical px: columns follow the width, 3 at the usual panel width, 4 when wide, down to 1
MAX_COLUMNS = 4
MARGIN_ROWS = 1  # a small justified margin of rows around the viewport
GRID_PIXMAPS = 24  # decoded 600 px thumbnails kept for tiles in view (about 35 MB at most)
LIVE_STATES = ("pending", "running")
DONE_STATES = ("succeeded",)
STATE_TEXT = {
    "pending": "Waiting",
    "running": "Running",
    "failed": "Failed",
    "save_failed": "Not saved",
    "cancelled": "Stopped",
    "unknown": "Outcome unknown",
}


@dataclass(frozen=True, slots=True)
class GridItem:
    """One queue item as the grid shows it."""

    item_id: str
    request_id: str | None
    state: str
    outputs: tuple[str, ...] = ()
    identity: str | None = None  # the Images gallery identity of its saved image or video poster
    stage: str = ""  # the live stage of a running item
    video: bool = False

    @property
    def saved(self) -> bool:
        return self.state in DONE_STATES and bool(self.outputs) and bool(self.identity)


def columns_for(width: int) -> int:
    """Columns from the available width, not fixed breakpoints: tiles stay between MIN_TILE and about 1.3 x that."""
    return max(1, min(MAX_COLUMNS, (width + GAP) // (MIN_TILE + GAP)))


class GenerationGrid(QAbstractScrollArea):
    openRequested = Signal(str)  # a saved tile was clicked: the Images identity to open in Detail
    shown = Signal()  # the grid became visible again: the owner re-reads the durable state

    def __init__(self, runner, state: Path, parent=None):
        super().__init__(parent)
        self.setObjectName("generationGrid")
        self.setAccessibleName("Generated items")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(240)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setMouseTracking(True)
        colors = load_tokens()  # the design system's colours: the grid paints itself, so it reads the tokens
        self._colors = {
            name: QColor(colors[f"color.{name}"])
            for name in ("canvas", "itemSurface", "sectionBorder", "secondary", "text")
        }
        self.gate = PreviewGate()
        self.thumbnails = QueueThumbnails(runner, state, self, preview_cap=GRID_PIXMAPS)
        self.thumbnails.changed.connect(self._thumbnail_arrived)
        self._items: list[GridItem] = []
        self._live: dict[str, QPixmap] = {}  # item id -> its latest provisional preview, visible running tiles only
        self._visible: tuple[int, int] = (0, -1)
        self._hover: int | None = None
        self._popup_for: int | None = None
        self._press: int | None = None
        self._anchor = QPoint()
        self._box = PREVIEW_BOX
        self.queue_id: str | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._show_popup)
        self._popup = QLabel(self, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self._popup.setObjectName("queueThumbPreview")
        self._popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._popup.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._popup.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.verticalScrollBar().valueChanged.connect(self._moved)
        runner.shutdown_started.connect(self._popup.hide)
        self.hide()

    # -- data ------------------------------------------------------------------------------------------------------
    def begin(self, queue_id: str, items: list[GridItem]) -> None:
        """A new Generate batch replaces whatever the grid showed."""
        self.queue_id = queue_id
        self._items, self._live = [], {}
        self.verticalScrollBar().setValue(0)
        self.apply(items)

    def end(self) -> None:
        """Nothing is shown any more: no previews are wanted and nothing is held."""
        self.queue_id = None
        self._items, self._live = [], {}
        self._popup.hide()
        self.gate.set_wanted(())
        self.viewport().update()

    def apply(self, items: list[GridItem]) -> None:
        """The durable view of the batch: statuses and saved outputs, in queue order."""
        self._items = items
        self._layout()
        self.viewport().update()

    def preview(self, item_id: str, encoded: bytes) -> None:
        """A provider preview for a running item: kept only for a tile in view, replacing the earlier one."""
        index = next((i for i, item in enumerate(self._items) if item.item_id == item_id), None)
        if index is None or not self._in_view(index) or self._items[index].state != "running":
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(encoded):
            self._live[item_id] = pixmap
            self.viewport().update(self._rect(index))

    # -- geometry --------------------------------------------------------------------------------------------------
    def _side(self) -> tuple[int, int]:
        width = self.viewport().width()
        columns = columns_for(width)
        return columns, max(1, (width - GAP * (columns - 1)) // columns)

    def _rect(self, index: int) -> QRect:
        columns, side = self._side()
        row, column = divmod(index, columns)
        return QRect(
            column * (side + GAP),
            row * (side + GAP) - self.verticalScrollBar().value(),
            side,
            side,
        )

    def _layout(self) -> None:
        columns, side = self._side()
        rows = -(-len(self._items) // columns)
        total = rows * (side + GAP) - GAP if rows else 0
        bar = self.verticalScrollBar()
        bar.setSingleStep(max(1, (side + GAP) // 2))
        bar.setPageStep(self.viewport().height())
        bar.setRange(0, max(0, total - self.viewport().height()))
        self._sync_visible()

    def _in_view(self, index: int) -> bool:
        return self._visible[0] <= index <= self._visible[1]

    def _sync_visible(self) -> None:
        """Which tiles are in view (plus a margin row), and so which running ones may show a provisional preview."""
        if not self.isVisible() or not self._items:
            self._visible = (0, -1)
        else:
            columns, side = self._side()
            top = self.verticalScrollBar().value()
            first = max(0, top // (side + GAP) - MARGIN_ROWS)
            last = (top + self.viewport().height()) // (side + GAP) + MARGIN_ROWS
            self._visible = (first * columns, min(len(self._items) - 1, (last + 1) * columns - 1))
        wanted = self._items[self._visible[0] : self._visible[1] + 1]
        self.gate.set_wanted(item.request_id for item in wanted if item.state in LIVE_STATES and item.request_id)
        self.thumbnails.keep_only({(item.outputs[0], item.identity) for item in wanted if item.saved})
        running = {item.item_id for item in wanted if item.state == "running"}
        self._live = {key: pm for key, pm in self._live.items() if key in running}

    def _moved(self, *_) -> None:
        self._popup.hide()
        self._hover = None
        self._sync_visible()
        self.viewport().update()

    # -- painting --------------------------------------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(event.rect(), self._colors["canvas"])
        frame, base, text = self._colors["sectionBorder"], self._colors["itemSurface"], self._colors["secondary"]
        for index in range(self._visible[0], self._visible[1] + 1):
            rect = self._rect(index)
            if not rect.intersects(event.rect()):
                continue
            item = self._items[index]
            painter.fillRect(rect, base)
            pixmap = self._picture(item)
            painter.setPen(QPen(frame))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            if pixmap is None:
                painter.setPen(text)
                painter.drawText(
                    rect.adjusted(6, 6, -6, -6),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    self._label(item),
                )
                continue
            fitted = QSize(pixmap.size() / pixmap.devicePixelRatio()).scaled(
                rect.size(), Qt.AspectRatioMode.KeepAspectRatio
            )
            target = QRectF(0, 0, fitted.width(), fitted.height())
            target.moveCenter(QRectF(rect).center())
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            band = QRect(rect.left(), rect.bottom() - 19, rect.width(), 20)
            painter.fillRect(band, QColor(0, 0, 0, 150))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                band.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, "Preview" if not item.saved else "Saved"
            )

    def _picture(self, item: GridItem) -> QPixmap | None:
        if item.saved:
            return self.thumbnails.pixmap(item.outputs[0], PREVIEW_SIDE, item.identity)
        return self._live.get(item.item_id) if item.state == "running" else None

    @staticmethod
    def _label(item: GridItem) -> str:
        if item.state == "succeeded":
            return "Saved" if item.saved else "No saved image verified"
        if item.state == "running" and item.stage:
            return f"{item.stage}"
        return STATE_TEXT.get(item.state, item.state.capitalize())

    def _thumbnail_arrived(self, path: str) -> None:
        if self.isVisible():
            self.viewport().update()
            if self._popup.isVisible():
                self._fill_popup()

    # -- events ----------------------------------------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._layout()
        self.shown.emit()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._popup.hide()
        self._visible = (0, -1)
        self.gate.set_wanted(())  # hidden: no preview is decoded for this grid
        self._live = {}

    def _index_at(self, point: QPoint) -> int | None:
        columns, side = self._side()
        top = point.y() + self.verticalScrollBar().value()
        row, column = top // (side + GAP), point.x() // (side + GAP)
        if column >= columns or top % (side + GAP) >= side or point.x() % (side + GAP) >= side:
            return None
        index = row * columns + column
        return index if 0 <= index < len(self._items) else None

    def mouseMoveEvent(self, event) -> None:
        index = self._index_at(event.position().toPoint())
        if index != self._hover:
            self._popup.hide()
            self._timer.stop()
            self._hover = index
            if index is not None and (self._items[index].saved or self._items[index].item_id in self._live):
                self._anchor = event.globalPosition().toPoint()
                self._timer.start()
        clickable = index is not None and self._items[index].saved
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event) -> None:
        self._popup.hide()
        self._timer.stop()
        self._hover = None

    def mousePressEvent(self, event) -> None:
        self._press = (
            self._index_at(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else None
        )

    def mouseReleaseEvent(self, event) -> None:
        index = self._index_at(event.position().toPoint())
        if index is not None and index == self._press and self._items[index].saved:
            self._popup.hide()
            self.openRequested.emit(self._items[index].identity)
        self._press = None

    # -- hover -----------------------------------------------------------------------------------------------------
    def _show_popup(self) -> None:
        if self._hover is None:
            return
        self._popup_for = self._hover
        screen = QGuiApplication.screenAt(self._anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self._box = max(160, min(PREVIEW_BOX, area.height() - 48, area.width() - 48))
        self._fill_popup()
        x = max(area.left(), min(self._anchor.x() + 16, area.right() - self._popup.width()))
        y = max(area.top(), min(self._anchor.y() + 16, area.bottom() - self._popup.height()))
        self._popup.move(x, y)
        self._popup.show()

    def _fill_popup(self) -> None:
        index = self._popup_for
        if index is None or index >= len(self._items):
            return
        pixmap = self._picture(self._items[index])
        if pixmap is None:
            self._popup.setPixmap(QPixmap())
            self._popup.setFixedSize(QSize(232, 232))
            self._popup.setText("Loading…")
            return
        ratio = self._popup.devicePixelRatioF()
        edge = int(self._box * ratio)
        scaled = pixmap.scaled(
            edge, edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        scaled.setDevicePixelRatio(ratio)
        self._popup.setPixmap(scaled)
        self._popup.setFixedSize(QSize(round(scaled.width() / ratio) + 12, round(scaled.height() / ratio) + 12))

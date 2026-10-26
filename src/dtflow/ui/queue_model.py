"""Table model over queue item rows: stable order, in-place updates, never re-sorted."""

from html import escape
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from dtflow.ui.prompt_tags import tagged_html
from dtflow.ui.queue_presenter import LORA_GAUGE_MIN_SCALE, ItemRow, display_name, gauge_scale
from dtflow.ui.theme import load_tokens

# Preview, Prompt, Model, LoRAs, Parameters, Status and a duration column whose header is a painted stopwatch icon.
COLUMNS = ("Preview", "Prompt", "Model", "LoRAs", "Parameters", "Status", "")
DURATION_HEADER = "Provider duration"
# Shared state tokens; "secondary" keeps the default text color. Unknown is neutral gray, never tinted.
_TOKENS = load_tokens()
LEVEL_COLORS = {
    "ok": _TOKENS["color.success"],
    "warning": _TOKENS["color.warning"],
    "error": _TOKENS["color.error"],
    "unknown": _TOKENS["color.unknown"],
}
ITEM_ID_ROLE = Qt.ItemDataRole.UserRole
_STOPWATCH: list[QIcon] = []


def stopwatch_icon() -> QIcon:
    """A painted stopwatch (no font or asset dependence); the glyph U+23F1 is missing from many UI fonts."""
    if not _STOPWATCH:
        pixmap = QPixmap(32, 32)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(_TOKENS["color.secondary"]), 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(QRectF(2.5, 4.5, 11, 11))
        painter.drawLine(QPointF(6.5, 1.5), QPointF(9.5, 1.5))
        painter.drawLine(QPointF(8, 1.5), QPointF(8, 4.5))
        painter.drawLine(QPointF(8, 10), QPointF(8, 7))
        painter.drawLine(QPointF(8, 10), QPointF(10.2, 8.4))
        painter.end()
        _STOPWATCH.append(QIcon(pixmap))
    return _STOPWATCH[0]


ROW_ROLE = Qt.ItemDataRole.UserRole + 1  # the ItemRow, for the painted cells
STATUS_COLUMN = 5
DURATION_COLUMN = 6


def _tooltip(row: ItemRow, column: int) -> str:
    """Read-only hover text; never an action and never a Copy affordance."""
    if column == 0:
        # The enlarged preview is a separate bounded overlay (queue_thumbnails.HoverPreview); a rich-text <img>
        # tooltip would decode the full original on the GUI thread.
        return "" if row.outputs else "No saved image"
    if column == 1:
        positive = tagged_html(row.prompt, row.prompt_runs) or "—"
        negative = escape(row.negative_prompt) or "—"
        return f"<p style='white-space:pre-wrap'><b>Positive</b><br>{positive}</p><p><b>Negative</b><br>{negative}</p>"
    if column == 2:
        return row.model
    if column == 3:
        if not row.loras:
            return ""
        lines = "".join(
            f"<tr><td>{escape(display_name(name))}</td><td align='right'>&nbsp;{'' if weight is None else f'{weight:.2f}'}</td></tr>"
            for name, weight in row.loras
        )
        return f"<table>{lines}</table>"
    if column == 4:
        return row.sampler
    if column == STATUS_COLUMN:
        return row.item_id
    return DURATION_HEADER + " (observed)"


class QueueItemModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: tuple[ItemRow, ...] = ()
        self.thumbnails = None  # QueueThumbnails, set by the page; the painted cells ask it for pixmaps
        self.gauge_scale = LORA_GAUGE_MIN_SCALE  # shared full-bar LoRA weight for the painted gauges

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        if section == DURATION_COLUMN:
            if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
                return DURATION_HEADER
        return None

    @staticmethod
    def _cell(row: ItemRow, column: int) -> str:
        # Preview, LoRAs and Parameters are painted from ROW_ROLE; their text is only the accessible fallback.
        return (
            "",
            row.prompt,
            row.model,
            ", ".join(name for name, _ in row.loras),
            f"{row.steps_text}, {row.guidance_text}, {row.sampler_text}",
            row.state_text,
            row.duration_text,
        )[column]

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._cell(row, index.column())
        if role == ITEM_ID_ROLE:
            return row.item_id
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == STATUS_COLUMN and row.level in LEVEL_COLORS:
            return QColor(LEVEL_COLORS[row.level])
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == DURATION_COLUMN:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return _tooltip(row, index.column())
        return None

    def row_at(self, index: int) -> ItemRow | None:
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def row_of(self, item_id: str) -> int:
        return next((index for index, row in enumerate(self._rows) if row.item_id == item_id), -1)

    def apply(self, rows: tuple[ItemRow, ...]) -> None:
        """Update in place when the item set is unchanged (the normal case), otherwise reset; rows never move."""
        self.gauge_scale = gauge_scale(rows)
        if tuple(row.item_id for row in rows) != tuple(row.item_id for row in self._rows):
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
            return
        old, self._rows = self._rows, rows
        for index, (before, after) in enumerate(zip(old, rows, strict=True)):
            if before != after:
                self.dataChanged.emit(self.index(index, 0), self.index(index, len(COLUMNS) - 1))

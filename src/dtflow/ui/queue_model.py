"""Table model over queue item rows: stable order, in-place updates, never re-sorted."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from dtflow.ui.queue_presenter import ItemRow
from dtflow.ui.theme import load_tokens

COLUMNS = ("#", "Item", "Seed", "State", "Attempt", "Outputs", "Edit")
# Shared state tokens; "secondary" keeps the default text color. Unknown is neutral gray, never tinted.
_TOKENS = load_tokens()
LEVEL_COLORS = {
    "ok": _TOKENS["color.success"],
    "warning": _TOKENS["color.warning"],
    "error": _TOKENS["color.error"],
    "unknown": _TOKENS["color.unknown"],
}
ITEM_ID_ROLE = Qt.ItemDataRole.UserRole
NUMERIC_COLUMNS = (0, 2)  # "#" and "Seed"


class QueueItemModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: tuple[ItemRow, ...] = ()

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    @staticmethod
    def _cell(row: ItemRow, column: int) -> str:
        return (
            str(row.position),
            row.item_id[:8],
            str(row.seed),
            row.state_text,
            (row.attempt_id or "—")[:8],
            f"{len(row.outputs)} file(s)" if row.outputs else "—",
            row.edit_text,
        )[column]

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._cell(row, index.column())
        if role == ITEM_ID_ROLE:
            return row.item_id
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 3 and row.level in LEVEL_COLORS:
            return QColor(LEVEL_COLORS[row.level])
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in NUMERIC_COLUMNS:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.item_id
        return None

    def row_at(self, index: int) -> ItemRow | None:
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def row_of(self, item_id: str) -> int:
        return next((index for index, row in enumerate(self._rows) if row.item_id == item_id), -1)

    def apply(self, rows: tuple[ItemRow, ...]) -> None:
        """Update in place when the item set is unchanged (the normal case), otherwise reset; rows never move."""
        if tuple(row.item_id for row in rows) != tuple(row.item_id for row in self._rows):
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
            return
        old, self._rows = self._rows, rows
        for index, (before, after) in enumerate(zip(old, rows, strict=True)):
            if before != after:
                self.dataChanged.emit(self.index(index, 0), self.index(index, len(COLUMNS) - 1))

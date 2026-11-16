"""One row of the selected-LoRA list: the name (elided) and a weight spin box that edits that weight in place (#487)."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QWidget

from dtflow.ui.search_select import LORA_WEIGHT_MAX, LORA_WEIGHT_MIN, LORA_WEIGHT_STEP

ROW_HEIGHT = 22  # px: the list's height is a whole number of these rows (10 rows, twice the former ~5 lines of text)
VISIBLE_ROWS = 10
SPIN_WIDTH = 74


class _ElidedLabel(QLabel):
    """Shows as much of its text as fits, with the middle replaced by an ellipsis; clicks fall through to the row."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._full = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMinimumWidth(0)

    def setFullText(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(self.width(), 1)))


class LoraWeightRow(QWidget):
    """``activated`` fires when the row is clicked or its spin box takes focus; ``weight_changed`` on a user edit."""

    activated = Signal()
    weight_changed = Signal(float)

    def __init__(self, name: str, weight: float, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 2, 0)
        layout.setSpacing(6)
        self.label = _ElidedLabel(self)
        self.spin = QDoubleSpinBox(self)
        self.spin.setObjectName("loraRowWeight")
        self.spin.setDecimals(2)
        self.spin.setSingleStep(LORA_WEIGHT_STEP)  # one wheel notch on a focused spin box is exactly one step
        self.spin.setFixedSize(SPIN_WIDTH, ROW_HEIGHT - 2)
        self.spin.setKeyboardTracking(False)  # typed digits apply on Enter or focus loss, never half-typed
        self.spin.setAccessibleName(f"{name} weight")
        self.set_weight(weight)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.spin)
        self.spin.installEventFilter(self)
        self.spin.lineEdit().installEventFilter(self)
        self.spin.valueChanged.connect(self.weight_changed.emit)

    def set_weight(self, weight: float) -> None:
        """Show ``weight`` without announcing a user edit; a loaded weight outside 0.0–3.0 widens the range to hold it."""
        self.spin.blockSignals(True)
        self.spin.setRange(min(LORA_WEIGHT_MIN, weight), max(LORA_WEIGHT_MAX, weight))
        self.spin.setValue(weight)
        self.spin.blockSignals(False)

    def mousePressEvent(self, event) -> None:
        self.activated.emit()
        super().mousePressEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            self.activated.emit()
        return False

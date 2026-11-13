"""Read-only view of what a safetensors file declares about this LoRA's training (#414).

A label/value grid on the card background (selectable labels, nothing that looks editable and no inner scroll area)
plus the top tags as one wrapped line pair. Up to three label/value pairs per row when the view is wide, one when narrow. The file's own title, author, license, description and base-model lines are deliberately not shown (they are
often inherited from the base model). No information is hover-only and the view never asks its parent for width."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from dtflow.services.lora_convert.source_metadata import DetailModel

_LABEL_WIDTH = 100
_GAP = 10


def _label(text: str, *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    if muted:
        label.setProperty("role", "secondary")
    return label


class SourceDetailsView(QWidget):
    """Grid of declared facts and tags for one model; call :meth:`set_model` to change it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sourceDetailsView")
        self._model = DetailModel()
        self._columns = 0
        self._values: list[QLabel] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        self._note = _label("", muted=True)
        self._grid_host = QWidget(self)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_GAP)
        self._grid.setVerticalSpacing(3)
        self._tags = _label("")
        for widget in (self._note, self._grid_host, self._tags):
            outer.addWidget(widget)

    def set_model(self, model: DetailModel) -> None:
        self._model = model
        self._note.setText(model.note)
        self._note.setVisible(bool(model.note))
        self._tags.setText(
            "Top training tags (not necessarily trigger words): " + " · ".join(model.tags) if model.tags else ""
        )
        self._tags.setVisible(bool(model.tags))
        self._tags.setMaximumHeight(2 * self._tags.fontMetrics().lineSpacing() + 2)
        self._rebuild(force=True)

    def _rebuild(self, *, force: bool = False) -> None:
        columns = 3 if self.width() >= 1000 else 2 if self.width() >= 640 else 1  # compact: few rows
        if columns != self._columns or force:
            self._columns = columns
            while self._grid.count():
                gone = self._grid.takeAt(0).widget()
                if gone is not None:
                    gone.hide()  # deleteLater alone leaves it painted until the event loop runs
                    gone.setParent(None)
                    gone.deleteLater()
            self._values = []
            for index, (name, value) in enumerate(self._model.facts):
                row, column = divmod(index, columns)
                caption = _label(name, muted=True)
                caption.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)  # Ignored collapses it
                caption.setFixedWidth(_LABEL_WIDTH)
                shown = _label(value)
                self._grid.addWidget(caption, row, column * 2, Qt.AlignmentFlag.AlignTop)
                self._grid.addWidget(shown, row, column * 2 + 1, Qt.AlignmentFlag.AlignTop)
                self._grid.setColumnStretch(column * 2 + 1, 1)
                self._values.append(shown)
            self._grid_host.setVisible(bool(self._model.facts))
        self._pin()

    def _pin(self) -> None:
        """Give each wrapped value exactly the height its text needs (a wrapped label guesses from a default width)."""
        columns = max(1, self._columns)
        width = max(60, (self.width() - _GAP * (columns - 1)) // columns - _LABEL_WIDTH - _GAP)
        for value in self._values:
            metrics = value.fontMetrics()
            needed = metrics.boundingRect(0, 0, width, 10000, int(Qt.TextFlag.TextWordWrap), value.text()).height()
            value.setFixedHeight(max(metrics.lineSpacing(), needed))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._rebuild()

    def minimumSizeHint(self) -> QSize:  # never force the parent wider
        return QSize(0, super().minimumSizeHint().height())

"""Read-only view of what a safetensors file declares about this LoRA's training (#414).

A label/value grid on the card background (selectable labels, nothing that looks editable and no inner scroll area)
plus the top tags as one wrapped line pair. Up to three label/value pairs per row when the view is wide, one when narrow. The file's own title, author, license, description and base-model lines are deliberately not shown (they are
often inherited from the base model). No information is hover-only and the view never asks its parent for width."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from dtflow.services.lora_convert.source_metadata import DetailModel

_LABEL_WIDTH = 100
_GAP = 10


def _label(text: str, *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)  # file content is untrusted: never rich text, never image loading
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
        self._rebuild(force=True)

    def _rebuild(self, *, force: bool = False) -> None:
        columns = 3 if self.width() >= 1000 else 2 if self.width() >= 440 else 1  # compact: few rows
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


class ElidedLabel(QObject):
    """Keeps a one-line QLabel's text cut to its width with an ellipsis (the full text is always in the popup)."""

    def __init__(self, label: QLabel) -> None:
        super().__init__(label)
        self._label, self._text = label, ""
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.installEventFilter(self)

    def set_text(self, text: str) -> None:
        self._text = text
        self._apply()

    def _apply(self) -> None:
        width = max(0, self._label.width())
        self._label.setText(self._label.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, width))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self._label and event.type() == QEvent.Type.Resize:
            self._apply()
        return False


class SourceDetailsPopup(QFrame):
    """Floating panel above its anchor (outside every layout): closes on a click outside or Escape, scrolls only
    past its height cap, and never changes the window or the rows list underneath."""

    MAX_HEIGHT = 260

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("sourceDetailsPopup")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.view = SourceDetailsView()
        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self.view)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self._scroll)

    def show_above(self, anchor: QWidget, model: DetailModel, *, width: int = 560) -> None:
        window = anchor.window()
        self._width = max(260, min(width, window.width() - 24))
        self._anchor = anchor
        self.view.set_model(model)
        self._fit()
        self.show()
        QTimer.singleShot(0, self._fit)  # fonts and styles are final once shown: re-measure, never cut the content

    def _fit(self) -> None:
        """Grow to the content up to the cap (scrolling only past it) and sit just above the anchor."""
        anchor = self._anchor
        inner = self._width - 16 - 4
        self.view.setFixedWidth(inner)
        self.view._rebuild(force=True)
        layout = self.view.layout()
        layout.activate()
        needed = max(layout.totalHeightForWidth(inner), layout.sizeHint().height())
        height = min(self.MAX_HEIGHT, needed + 4) + 12
        self.setFixedSize(self._width, height)
        window = anchor.window()
        top_left = anchor.mapToGlobal(anchor.rect().topLeft())
        right = anchor.mapToGlobal(anchor.rect().topRight())
        x = max(window.mapToGlobal(window.rect().topLeft()).x() + 8, right.x() - self._width)
        self.move(x, top_left.y() - height - 4)

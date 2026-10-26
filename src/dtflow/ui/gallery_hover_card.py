"""Compact prompt and LoRA overlay shown while hovering the Images Detail image.

Presentation only: it renders the already loaded GalleryItem and never reads metadata,
touches the backend or changes zoom, pan or navigation.
"""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from dtflow.core.gallery import GalleryItem

HOVER_DELAY_MS = 400
LEAVE_GRACE_MS = 150
EDGE_MARGIN = 12
MAX_WIDTH = 440
MAX_PROMPT_HEIGHT = 150
LORA_UNKNOWN_TIP = "No persisted LoRA application/compatibility evidence is available in the saved request."


class GalleryHoverCard(QFrame):
    """Child of the Detail image surface; swallows pointer input so the image is not panned or zoomed."""

    def __init__(self, surface: QWidget):
        super().__init__(surface)
        self.surface = surface
        self.setObjectName("galleryHoverCard")
        self.setProperty("role", "galleryHoverCard")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.prompt_label = QLabel("Positive prompt", self)
        self.prompt_label.setProperty("role", "secondary")
        self.prompt = QPlainTextEdit(self)
        self.prompt.setObjectName("galleryHoverPrompt")
        self.prompt.setReadOnly(True)
        self.prompt.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.prompt.setFrameShape(QFrame.Shape.NoFrame)
        self.prompt.setMaximumHeight(MAX_PROMPT_HEIGHT)
        self.lora_label = QLabel("LoRAs", self)
        self.lora_label.setProperty("role", "secondary")
        self.lora_rows = QVBoxLayout()
        self.lora_rows.setSpacing(4)
        layout.addWidget(self.prompt_label)
        layout.addWidget(self.prompt)
        layout.addWidget(self.lora_label)
        layout.addLayout(self.lora_rows)

        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.setInterval(HOVER_DELAY_MS)
        self.show_timer.timeout.connect(self._show_if_hovered)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(LEAVE_GRACE_MS)
        self.hide_timer.timeout.connect(self._hide_if_left)
        self._item: GalleryItem | None = None
        self._watcher = _SurfaceWatcher(self)
        surface.installEventFilter(self._watcher)

    def set_item(self, item: GalleryItem | None) -> None:
        """Replace the content for a new selection; always dismisses the card."""
        self.dismiss()
        self._item = item
        if item is None:
            return
        self.prompt.setPlainText(item.prompt or "Not recorded")
        while self.lora_rows.count():
            row = self.lora_rows.takeAt(0).widget()
            if row is not None:
                row.deleteLater()
        self.lora_label.setText("LoRAs" if item.loras else "LoRAs · none")
        for identifier, weight in item.loras:
            self.lora_rows.addWidget(self._chip(identifier, weight))

    def _chip(self, identifier: str, weight: float) -> QFrame:
        chip = QFrame(self)
        chip.setProperty("role", "loraChip")
        row = QHBoxLayout(chip)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)
        name = QLabel(identifier, chip)
        name.setProperty("textRole", "loraName")
        name.setToolTip(identifier)
        value = QLabel(f"{weight:g}", chip)
        value.setProperty("textRole", "identifier")
        status = QLabel("Unknown", chip)
        status.setProperty("role", "unknown")
        status.setProperty("statusBadge", "true")
        status.setToolTip(LORA_UNKNOWN_TIP)
        row.addWidget(name, 1)
        row.addWidget(value)
        row.addWidget(status)
        return chip

    def start_hover(self) -> None:
        self.hide_timer.stop()
        if not self.isVisible() and self._item is not None:
            self.show_timer.start()

    def restart_hover(self) -> None:
        """Pointer moved over the image: keep waiting while the card is not yet shown."""
        if not self.isVisible() and self._item is not None:
            self.show_timer.start()

    def begin_leave(self) -> None:
        self.show_timer.stop()
        if self.isVisible():
            self.hide_timer.start()

    def dismiss(self) -> None:
        self.show_timer.stop()
        self.hide_timer.stop()
        self.hide()

    def reposition(self) -> None:
        bounds = self.surface.rect().adjusted(EDGE_MARGIN, EDGE_MARGIN, -EDGE_MARGIN, -EDGE_MARGIN)
        if bounds.width() <= 0 or bounds.height() <= 0:
            return
        self.setFixedWidth(max(1, min(MAX_WIDTH, bounds.width())))
        self.adjustSize()
        height = min(self.height(), bounds.height())
        self.setFixedHeight(height)
        self.move(bounds.left(), bounds.bottom() - height + 1)

    def _pointer_over(self, widget: QWidget) -> bool:
        return widget.rect().contains(widget.mapFromGlobal(QCursor.pos()))

    def _show_if_hovered(self) -> None:
        if self._item is None or not self.surface.isVisible() or not self._pointer_over(self.surface):
            return
        self.reposition()
        self.show()
        self.raise_()

    def _hide_if_left(self) -> None:
        if not self._pointer_over(self) and not self._pointer_over(self.surface):
            self.dismiss()

    def enterEvent(self, event):
        self.hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hide_timer.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()  # never reaches the surface's zoom handler


class _SurfaceWatcher(QObject):
    def __init__(self, card: GalleryHoverCard):
        super().__init__(card)
        self.card = card

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.Enter:
            self.card.start_hover()
        elif kind == QEvent.Type.MouseMove:
            if event.buttons() == Qt.MouseButton.NoButton:
                self.card.restart_hover()
            else:
                self.card.dismiss()
        elif kind in (QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            self.card.dismiss()
        elif kind == QEvent.Type.Leave:
            self.card.begin_leave()
        elif kind == QEvent.Type.Hide:
            self.card.dismiss()
        elif kind == QEvent.Type.Resize and self.card.isVisible():
            self.card.reposition()
        return False

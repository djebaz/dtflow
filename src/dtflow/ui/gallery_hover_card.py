"""Compact prompt and LoRA overlay shown while hovering the info button on the Images Detail image.

Presentation only: it renders the already loaded GalleryItem and never reads metadata,
touches the backend or changes zoom, pan or navigation.
"""

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QCursor, QIcon, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dtflow.core.gallery import GalleryItem
from dtflow.ui.gallery_detail_preview import ASSETS
from dtflow.ui.prompt_tags import PromptTagHighlighter, stored_runs

HOVER_DELAY_MS = 200
BUTTON_SIDE = 30
LEAVE_GRACE_MS = 150
EDGE_MARGIN = 12
MAX_WIDTH = 440
QWIDGETSIZE_MAX = 16777215
PROMPT_POINT_SIZE = 10.5
PROMPT_MIN_POINT_SIZE = 8.0
TOOLBAR_GAP = 8
LORA_UNKNOWN_TIP = "No persisted LoRA application/compatibility evidence is available in the saved request."


class _ElidedLabel(QLabel):
    """Label that elides its text to the available width and keeps the full text as tooltip."""

    def __init__(self, text: str, parent: QWidget):
        super().__init__(parent)
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._elide()

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width())))


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
        self.prompt = QTextEdit(self)
        self.prompt.setObjectName("galleryHoverPrompt")
        self.prompt.setReadOnly(True)
        self.prompt.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.prompt.setFrameShape(QFrame.Shape.NoFrame)
        # The card is sized to the whole prompt, so the box never scrolls or clips.
        self._tags = PromptTagHighlighter(self.prompt, scan=False)
        self.prompt.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.prompt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.prompt.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.lora_label = QLabel("LoRAs", self)
        self.lora_label.setProperty("role", "secondary")
        self.lora_rows = QVBoxLayout()
        self.lora_rows.setSpacing(4)
        layout.addWidget(self.prompt_label)
        layout.addWidget(self.prompt)
        layout.addWidget(self.lora_label)
        layout.addLayout(self.lora_rows)

        self.button = QPushButton(surface)
        self.button.setObjectName("galleryInfoButton")
        self.button.setProperty("role", "galleryInfo")
        self.button.setIcon(QIcon(str(ASSETS / "info.svg")))
        self.button.setIconSize(QSize(18, 18))
        self.button.setFixedSize(BUTTON_SIDE, BUTTON_SIDE)
        self.button.setAccessibleName("Image details")
        self.button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keyboard users keep the Detail panels
        self.button.setMouseTracking(True)
        self.button.hide()

        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.setInterval(HOVER_DELAY_MS)
        self.show_timer.timeout.connect(self._show_if_hovered)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(LEAVE_GRACE_MS)
        self.hide_timer.timeout.connect(self._hide_if_left)
        self._item: GalleryItem | None = None
        self._dirty = False
        self._watcher = _SurfaceWatcher(self)
        surface.installEventFilter(self._watcher)
        self.button.installEventFilter(self._watcher)

    def set_item(self, item: GalleryItem | None) -> None:
        """Track the selection. The card closes only when what it shows would change."""
        current = self._item
        if (
            item is not None
            and current is not None
            and (item.identity, item.prompt, item.loras, item.request_json)
            == (current.identity, current.prompt, current.loras, current.request_json)
        ):
            self._item = item
            return
        self.dismiss()
        self._item = item
        self._dirty = item is not None
        self.button.setVisible(item is not None)
        self._place_button()

    def _populate(self) -> None:
        item = self._item
        self._dirty = False
        self.prompt.setPlainText(item.prompt or "Not recorded")
        self._tags.set_runs(stored_runs(item.request_json) if item.prompt else ())
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
        name = _ElidedLabel(identifier, chip)
        name.setProperty("textRole", "loraName")
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

    def _place_button(self) -> None:
        self.button.move(self.surface.width() - EDGE_MARGIN - BUTTON_SIDE, EDGE_MARGIN)
        self.button.raise_()

    def start_hover(self) -> None:
        self.hide_timer.stop()
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

    def _bottom_limit(self) -> int:
        """Lowest y the card may reach: above the next button and image toolbar that sit over the surface."""
        bottom = self.surface.rect().bottom() - EDGE_MARGIN
        for name in ("galleryNextButton", "galleryImageToolbar"):
            widget = self.surface.window().findChild(QWidget, name)
            if widget is not None and widget.isVisible():
                top = self.surface.mapFromGlobal(widget.mapToGlobal(widget.rect().topLeft())).y()
                if top <= bottom:
                    bottom = top - TOOLBAR_GAP
        return bottom

    def reposition(self) -> None:
        """Anchor under the info button at the image's top right; the prompt grows to show everything."""
        self._place_button()
        left, top = EDGE_MARGIN, EDGE_MARGIN + BUTTON_SIDE + TOOLBAR_GAP
        right = self.surface.rect().right() - EDGE_MARGIN
        bottom = self._bottom_limit()
        if right <= left or bottom <= top:
            return
        width = max(1, min(MAX_WIDTH, right - left + 1))
        # Release the previous fixed size so content from a different image is measured fresh.
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        self.prompt.setMaximumHeight(QWIDGETSIZE_MAX)
        self.setFixedWidth(width)
        margins = self.layout().contentsMargins()
        document = self.prompt.document()
        # Wrap at the viewport width the box will really have (no scrollbar).
        text_width = max(1, width - margins.left() - margins.right() - 2 * self.prompt.frameWidth())
        minimum = self.fontMetrics().height() + 8
        self.prompt.setFixedHeight(minimum)
        self.layout().activate()
        chrome = self.layout().sizeHint().height() - minimum
        room = max(minimum, bottom - top + 1 - chrome)
        wanted = minimum
        # Match the Detail panel prompt (14 px ~ 10.5 pt). Never scroll: only if the whole prompt does not
        # fit, step the type down, but not below a readable size.
        size = PROMPT_POINT_SIZE
        while True:
            self.prompt.setStyleSheet(f"font-size: {size:g}pt;")
            self.prompt.ensurePolished()
            document.setDefaultFont(self.prompt.font())
            document.setTextWidth(text_width)
            wanted = int(document.size().height() + 2 * document.documentMargin()) + 4
            if wanted <= room or size <= PROMPT_MIN_POINT_SIZE:
                break
            size -= 0.5
        self.prompt.setFixedHeight(max(minimum, wanted))
        self.layout().activate()
        height = self.layout().sizeHint().height()
        self.setFixedHeight(height)
        self.move(right - width + 1, top)

    def _is_over(self, widget: QWidget) -> bool:
        """True when the topmost widget under the pointer is the widget or one of its descendants."""
        under = QApplication.widgetAt(QCursor.pos())
        return under is not None and (under is widget or widget.isAncestorOf(under))

    def _show_if_hovered(self) -> None:
        if self._item is None or not self.surface.isVisible() or not self._is_over(self.button):
            return
        if self._dirty:
            self._populate()
        self.reposition()
        self.show()
        self.raise_()

    def _hide_if_left(self) -> None:
        if not self._is_over(self) and not self._is_over(self.button):
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
        if watched is self.card.button:
            if kind == QEvent.Type.Enter:
                self.card.start_hover()
            elif kind == QEvent.Type.Leave:
                self.card.begin_leave()
            return False
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.Hide):
            self.card.dismiss()
        elif kind == QEvent.Type.Resize:
            self.card.reposition() if self.card.isVisible() else self.card._place_button()
        return False

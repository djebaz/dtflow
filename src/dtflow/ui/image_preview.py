"""Backend-free, aspect-preserving image viewport with local inspection controls."""

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
)

from dtflow.ui.brand import brand_mark
from dtflow.ui.theme import load_tokens


WHEEL_STEP = 1.1  # per 120-unit wheel notch; Images → Detail uses the same step


class ImagePreview(QAbstractScrollArea):
    """Fit preserves the entire image; pixel zoom adds scrollbars, never resizes the saved artifact."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(160, 180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Image preview. Mouse wheel zooms; drag to pan.")
        self.viewport().setMouseTracking(True)
        self._pixmap = QPixmap()
        self._temporary_crop: tuple[QPixmap, tuple[int, int]] | None = None
        self._scale = 1.0
        self._fit = True
        self._drag = None
        self._text = "Your next image starts here\nSet a prompt and press Generate."
        self._brand_mark = brand_mark()
        self._expanded = False
        self._restore = None
        tokens = load_tokens()
        self._canvas_color = QColor(tokens["color.canvas"])
        self._secondary_color = QColor(tokens["color.secondary"])
        self._top = QFrame(self.viewport())
        self._top.setObjectName("previewToolbar")
        row = QHBoxLayout(self._top)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self.fitButton = self._button(row, "Fit", self.fit, "Fit the whole image (F)")
        self.actualButton = self._button(row, "100%", lambda: self.zoom(1.0), "Actual size")
        self.minusButton = self._button(row, "−", lambda: self.zoom(self._scale / 1.25), "Zoom out (−)")
        self.zoomSlider = QSlider(Qt.Orientation.Horizontal, self._top)
        self.zoomSlider.setRange(5, 400)
        self.zoomSlider.setFixedWidth(70)
        self.zoomSlider.setAccessibleName("Preview zoom percentage")
        self.zoomSlider.valueChanged.connect(lambda value: self.zoom(value / 100))
        row.addWidget(self.zoomSlider)
        self.plusButton = self._button(row, "+", lambda: self.zoom(self._scale * 1.25), "Zoom in (+)")
        self._expand = QPushButton("Expand", self.viewport())
        self._expand.setObjectName("previewExpandButton")
        self._expand.setToolTip("Expand the canvas; Escape restores the workspace.")
        self._expand.clicked.connect(self.toggle_expanded)
        self._badge = QLabel(self.viewport())
        self._badge.setObjectName("previewDesignation")
        self._files = QFrame(self.viewport())
        self._files.setObjectName("previewFileToolbar")
        self._file_row = QGridLayout(self._files)
        self._file_buttons = []
        self._file_layout = None
        self._file_row.setContentsMargins(4, 4, 4, 4)
        self._file_row.setSpacing(4)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(2500)
        self._idle.timeout.connect(self._hide_idle_controls)
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self._sync()

    @staticmethod
    def _button(row, text, callback, tip=""):
        button = QPushButton(text)
        button.setToolTip(tip)
        button.setMinimumWidth(28)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def add_file_button(self, button):
        self._file_buttons.append(button)
        button.show()
        self._position_controls()

    def enable_inspection(self):
        self._expand.setEnabled(True)

    def setText(self, text):  # Designer/controller presentation seam
        self._text = text
        self.viewport().update()

    def pixmap(self):
        return self._pixmap

    def clear(self):
        self._pixmap = QPixmap()
        self.setProperty("designation", "")
        self._fit = True
        self._sync()

    def setPixmap(self, pixmap):
        self._pixmap = pixmap
        self.fit()

    def show_temporary_crop(self, pixmap: QPixmap, output_size: tuple[int, int]) -> None:
        """Temporarily show the start image and its centered output crop without replacing the saved result."""
        if pixmap.isNull() or min(output_size) <= 0:
            return
        self._temporary_crop = (pixmap, output_size)
        self._sync()

    def clear_temporary_crop(self) -> None:
        """Restore the current result, including one that arrived while the pointer was over the source."""
        if self._temporary_crop is not None:
            self._temporary_crop = None
            self._sync()

    def set_designation(self, designation, *, fixture=False):
        self.setProperty("designation", designation)
        kind = "Fixture · no inference" if fixture else designation.title()
        self._badge.setText(f"{self._pixmap.width()} × {self._pixmap.height()}  ·  {kind}")
        self._position_controls()

    def fit(self):
        self._fit = True
        self._sync()

    def zoom(self, scale, anchor=None):
        if self._pixmap.isNull():
            return
        anchor = anchor or self.viewport().rect().center()
        old = self.image_rect()
        x = (anchor.x() - old.x()) / self._factor()
        y = (anchor.y() - old.y()) / self._factor()
        self._fit = False
        self._scale = max(0.05, min(4.0, scale))
        self._sync()
        new = self.image_rect()
        horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
        horizontal.setValue(round(x * self._factor() + new.x() + horizontal.value() - anchor.x()))
        vertical.setValue(round(y * self._factor() + new.y() + vertical.value() - anchor.y()))

    def _factor(self):
        """Logical pixels per image pixel: the displayed scale is in device pixels, so 100% is true size."""
        return self._scale / self.devicePixelRatioF()

    def image_rect(self):
        width = self._pixmap.width() * self._factor()
        height = self._pixmap.height() * self._factor()
        return QRectF(
            max(0, (self.viewport().width() - width) / 2) - self.horizontalScrollBar().value(),
            max(0, (self.viewport().height() - height) / 2) - self.verticalScrollBar().value(),
            width,
            height,
        )

    def _sync(self):
        available = not self._pixmap.isNull()
        if self._fit and available:
            # Clear old overflow before measuring the full fit area.
            self.horizontalScrollBar().setRange(0, 0)
            self.verticalScrollBar().setRange(0, 0)
            ratio = self.devicePixelRatioF()
            self._scale = min(
                max(1, self.viewport().width() - 24) * ratio / self._pixmap.width(),
                max(1, self.viewport().height() - 24) * ratio / self._pixmap.height(),
            )
        for bar, extent, visible in (
            (self.horizontalScrollBar(), self._pixmap.width() * self._factor(), self.viewport().width()),
            (self.verticalScrollBar(), self._pixmap.height() * self._factor(), self.viewport().height()),
        ):
            bar.setPageStep(visible)
            bar.setRange(0, 0 if self._fit else max(0, round(extent - visible)))
        self.zoomSlider.blockSignals(True)
        self.zoomSlider.setValue(round(self._scale * 100))
        self.zoomSlider.blockSignals(False)
        self.zoomSlider.setToolTip(f"{round(self._scale * 100)}%")
        for control in (self.fitButton, self.actualButton, self.minusButton, self.plusButton, self.zoomSlider):
            control.setEnabled(available)
        self._badge.setVisible(available and self._temporary_crop is None)
        self._show_controls()
        self._position_controls()
        self.viewport().update()

    def _position_controls(self):
        if not hasattr(self, "_files"):
            return
        self._top.adjustSize()
        self._files.adjustSize()
        self._badge.adjustSize()
        self._expand.adjustSize()
        width, height = self.viewport().width(), self.viewport().height()
        self.zoomSlider.setVisible(width >= 460)
        columns = 4 if width >= 500 else 2
        if (len(self._file_buttons), columns) != self._file_layout:
            self._file_layout = (len(self._file_buttons), columns)
            for index, button in enumerate(self._file_buttons):
                self._file_row.addWidget(button, index // columns, index % columns)
        self._top.adjustSize()
        self._files.adjustSize()
        self._top.move(10, 10)
        # On narrow canvases Expand gets its own row, rather than overlapping zoom.
        expand_y = 10 if width > self._top.width() + self._expand.width() + 30 else self._top.height() + 16
        self._expand.move(max(10, width - self._expand.width() - 10), expand_y)
        self._files.move(max(10, width - self._files.width() - 10), max(10, height - self._files.height() - 10))
        badge_y = height - self._badge.height() - self._files.height() - 18
        self._badge.move(12, max(10, badge_y))

    def _show_controls(self):
        if self._temporary_crop is not None:
            self._top.hide()
            self._files.hide()
            self._expand.hide()
            return
        self._top.show()
        self._files.show()
        self._expand.show()
        self._idle.start()

    def _hide_idle_controls(self):
        from PySide6.QtWidgets import QApplication

        focused = QApplication.focusWidget()
        if self.underMouse() or (focused is not None and (focused is self or self.isAncestorOf(focused))):
            self._idle.start()
            return
        self._top.hide()
        self._files.hide()
        self._expand.hide()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), self._canvas_color)
        if self._temporary_crop is not None:
            self._paint_temporary_crop(painter)
            return
        if self._pixmap.isNull():
            if not self._brand_mark.isNull():
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(
                    QRectF((self.viewport().width() - 56) / 2, self.viewport().height() / 2 - 110, 56, 56),
                    self._brand_mark,
                    QRectF(self._brand_mark.rect()),
                )
            painter.setPen(self._secondary_color)
            painter.drawText(
                QRectF(0, self.viewport().height() / 2 - 40, self.viewport().width(), 80),
                Qt.AlignmentFlag.AlignCenter,
                self._text,
            )
        else:
            # Smooth when shrinking; at 100% and above pixels stay crisp so detail can be inspected.
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._scale < 1.0)
            painter.drawPixmap(self.image_rect(), self._pixmap, QRectF(self._pixmap.rect()))

    def _paint_temporary_crop(self, painter: QPainter) -> None:
        """The shaded areas are exactly the centered cover-fit cut used for the submitted start frame."""
        pixmap, (target_w, target_h) = self._temporary_crop
        source_w, source_h = pixmap.width(), pixmap.height()
        area = self.viewport().rect()
        factor = min(max(1, area.width() - 24) / source_w, max(1, area.height() - 24) / source_h)
        shown = QRectF(
            (area.width() - source_w * factor) / 2,
            (area.height() - source_h * factor) / 2,
            source_w * factor,
            source_h * factor,
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(shown, pixmap, QRectF(pixmap.rect()))
        # PIL ImageOps.fit uses centering=(0.5, 0.5). No separate crop controls or saved offsets.
        if source_w * target_h > source_h * target_w:
            kept_width = source_h * target_w / target_h
            kept = QRectF(
                shown.x() + (source_w - kept_width) * factor / 2,
                shown.y(),
                kept_width * factor,
                shown.height(),
            )
        else:
            kept_height = source_w * target_h / target_w
            kept = QRectF(
                shown.x(),
                shown.y() + (source_h - kept_height) * factor / 2,
                shown.width(),
                kept_height * factor,
            )
        shade = QColor(0, 0, 0, 145)
        painter.fillRect(QRectF(shown.left(), shown.top(), shown.width(), kept.top() - shown.top()), shade)
        painter.fillRect(QRectF(shown.left(), kept.bottom(), shown.width(), shown.bottom() - kept.bottom()), shade)
        painter.fillRect(QRectF(shown.left(), kept.top(), kept.left() - shown.left(), kept.height()), shade)
        painter.fillRect(QRectF(kept.right(), kept.top(), shown.right() - kept.right(), kept.height()), shade)
        painter.setPen(QColor(255, 255, 255))
        painter.drawRect(kept)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_pixmap"):
            self._sync()

    def wheelEvent(self, event):
        # Same policy as Images → Detail: the wheel over the canvas zooms (Ctrl optional), anchored at the pointer,
        # and never scrolls a surrounding panel. Panning stays on drag, scrollbars and keys.
        delta = event.angleDelta().y()
        if delta and not self._pixmap.isNull():
            self.zoom(self._scale * WHEEL_STEP ** (delta / 120), event.position().toPoint())
        event.accept()
        self._show_controls()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._fit:
            self._drag = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self._show_controls()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            point = event.position().toPoint()
            delta = point - self._drag
            self._drag = point
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
        self._show_controls()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.viewport().unsetCursor()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._show_controls()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._expanded:
            self.toggle_expanded()
        elif event.key() == Qt.Key.Key_F:
            self.fit()
        elif event.key() == Qt.Key.Key_Plus:
            self.zoom(self._scale * 1.25)
        elif event.key() == Qt.Key.Key_Minus:
            self.zoom(self._scale / 1.25)
        else:
            super().keyPressEvent(event)

    def toggle_expanded(self):
        if not self._expanded:
            parent = self.parentWidget()
            owner = self.window()
            owner.installEventFilter(self)
            self._restore = (parent, parent.layout(), parent.layout().indexOf(self), owner)
            parent.layout().removeWidget(self)
            self.setParent(None)
            self.setWindowTitle("DTFlow — Image inspection")
            self.showMaximized()
        else:
            parent, layout, index, owner = self._restore
            owner.removeEventFilter(self)
            self.setParent(parent)
            layout.insertWidget(index, self)
            self.show()
        self._expanded = not self._expanded
        self._expand.setText("Restore" if self._expanded else "Expand")
        self.setFocus()

    def eventFilter(self, watched, event):
        if self._expanded and event.type() == QEvent.Type.Close:
            self.toggle_expanded()
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if self._expanded:
            event.ignore()
            self.toggle_expanded()
        else:
            super().closeEvent(event)

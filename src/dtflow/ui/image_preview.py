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

from dtflow.ui.brand import APP_ICON, ILLUSTRATIVE_SAMPLE
from dtflow.ui.theme import load_tokens


class ImagePreview(QAbstractScrollArea):
    """Fit preserves the entire image; pixel zoom adds scrollbars, never resizes the saved artifact."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(160, 180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Image preview. Control-wheel zooms; drag to pan.")
        self.viewport().setMouseTracking(True)
        self._pixmap = QPixmap()
        self._scale = 1.0
        self._fit = True
        self._drag = None
        self._text = "Your next image starts here\nSet a prompt or view a sample."
        self._brand_mark = QPixmap(str(APP_ICON))
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
        self.fitButton = self._button(row, "Fit", self.fit)
        self.actualButton = self._button(row, "100%", lambda: self.zoom(1.0))
        self.minusButton = self._button(row, "−", lambda: self.zoom(self._scale / 1.25))
        self.zoomSlider = QSlider(Qt.Orientation.Horizontal, self._top)
        self.zoomSlider.setRange(5, 400)
        self.zoomSlider.setFixedWidth(70)
        self.zoomSlider.setAccessibleName("Preview zoom percentage")
        self.zoomSlider.valueChanged.connect(lambda value: self.zoom(value / 100))
        row.addWidget(self.zoomSlider)
        self.plusButton = self._button(row, "+", lambda: self.zoom(self._scale * 1.25))
        self._expand = QPushButton("Expand", self.viewport())
        self._expand.setObjectName("previewExpandButton")
        self._expand.setToolTip("Expand the canvas; Escape restores the workspace.")
        self._expand.clicked.connect(self.toggle_expanded)
        self._badge = QLabel(self.viewport())
        self._badge.setObjectName("previewDesignation")
        self.sampleButton = QPushButton("View sample", self.viewport())
        self.sampleButton.setObjectName("previewSampleButton")
        self.sampleButton.setToolTip("Illustrative artwork only. No model inference or saved file.")
        self.sampleButton.clicked.connect(self.show_sample)
        self._files = QFrame(self.viewport())
        self._files.setObjectName("previewFileToolbar")
        self._file_row = QGridLayout(self._files)
        self._file_buttons = []
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
    def _button(row, text, callback):
        button = QPushButton(text)
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

    def show_sample(self):
        sample = QPixmap(str(ILLUSTRATIVE_SAMPLE))
        if sample.isNull():
            return
        self.setPixmap(sample)
        self.setProperty("designation", "illustrative_sample")
        self._badge.setText(f"{sample.width()} × {sample.height()}  ·  Illustrative sample · no inference")
        self._position_controls()

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
        x = (anchor.x() - old.x()) / self._scale
        y = (anchor.y() - old.y()) / self._scale
        self._fit = False
        self._scale = max(0.05, min(4.0, scale))
        self._sync()
        new = self.image_rect()
        horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
        horizontal.setValue(round(x * self._scale + new.x() + horizontal.value() - anchor.x()))
        vertical.setValue(round(y * self._scale + new.y() + vertical.value() - anchor.y()))

    def image_rect(self):
        width = self._pixmap.width() * self._scale
        height = self._pixmap.height() * self._scale
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
            self._scale = min(
                max(1, self.viewport().width() - 24) / self._pixmap.width(),
                max(1, self.viewport().height() - 24) / self._pixmap.height(),
            )
        for bar, extent, visible in (
            (self.horizontalScrollBar(), self._pixmap.width() * self._scale, self.viewport().width()),
            (self.verticalScrollBar(), self._pixmap.height() * self._scale, self.viewport().height()),
        ):
            bar.setPageStep(visible)
            bar.setRange(0, 0 if self._fit else max(0, round(extent - visible)))
        self.zoomSlider.blockSignals(True)
        self.zoomSlider.setValue(round(self._scale * 100))
        self.zoomSlider.blockSignals(False)
        self.zoomSlider.setToolTip(f"{round(self._scale * 100)}%")
        for control in (self.fitButton, self.actualButton, self.minusButton, self.plusButton, self.zoomSlider):
            control.setEnabled(available)
        self._badge.setVisible(available)
        self.sampleButton.setVisible(not available)
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
        self.sampleButton.adjustSize()
        width, height = self.viewport().width(), self.viewport().height()
        self.sampleButton.move(max(0, (width - self.sampleButton.width()) // 2), height // 2 + 55)
        self.zoomSlider.setVisible(width >= 460)
        columns = 4 if width >= 500 else 2
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
        if self._pixmap.isNull():
            if not self._brand_mark.isNull():
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(QRectF((self.viewport().width() - 56) / 2, self.viewport().height() / 2 - 110, 56, 56), self._brand_mark, QRectF(self._brand_mark.rect()))
            painter.setPen(self._secondary_color)
            painter.drawText(QRectF(0, self.viewport().height() / 2 - 40, self.viewport().width(), 80), Qt.AlignmentFlag.AlignCenter, self._text)
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.image_rect(), self._pixmap, QRectF(self._pixmap.rect()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_pixmap"):
            self._sync()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(self._scale * (1.25 if event.angleDelta().y() > 0 else 0.8), event.position().toPoint())
            event.accept()
        else:
            super().wheelEvent(event)
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

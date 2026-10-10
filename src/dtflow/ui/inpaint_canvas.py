"""Manual source-grid painting only; no Pillow, backend or generation ownership."""

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

from dtflow.ui.image_preview import ImagePreview
from dtflow.ui.theme import load_tokens


def _arrow_icon(path: str, color: str) -> QIcon:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


UNDO_PATH = '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>'
REDO_PATH = '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>'


class InpaintCanvas(ImagePreview):
    maskEdited = Signal()
    viewChanged = Signal(str)
    sourceDropped = Signal(str)
    HISTORY_BYTES = 64 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mask = QImage()
        self._overlay = QImage()
        self._result = QPixmap()
        self._undo = []
        self._redo = []
        self._stroke = None
        self._space = False
        self._view = "Source"
        self.tool = "Brush"
        self.brush_size = 24
        self._accent = QColor(load_tokens()["color.accent"])
        self._expand.hide()  # The editor never detaches into a popup window.
        self._expand.setEnabled(False)
        text = self._secondary_color.name()
        self._history_bar = QFrame(self.viewport())
        self._history_bar.setObjectName("previewToolbar")
        row = QHBoxLayout(self._history_bar)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self.undoButton = self._icon_button(row, UNDO_PATH, text, "Undo (Ctrl+Z)", "Undo mask edit", self.undo)
        self.redoButton = self._icon_button(row, REDO_PATH, text, "Redo (Ctrl+Shift+Z)", "Redo mask edit", self.redo)
        self._view_bar = QFrame(self.viewport())
        self._view_bar.setObjectName("previewToolbar")
        row = QHBoxLayout(self._view_bar)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self._view_buttons = {}
        for name in ("Source", "Mask", "Result"):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(lambda _=False, name=name: self.set_view(name))
            row.addWidget(button)
            self._view_buttons[name] = button
        self._view_buttons["Source"].setChecked(True)
        self._position_controls()
        self.setAcceptDrops(True)
        self._refresh = QTimer(self)  # strokes repaint the tint at most ~30 times a second
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(33)
        self._refresh.timeout.connect(self._rebuild_overlay)
        self.setText("Choose a source image, then paint the area to change.")
        self.setAccessibleName("Inpaint canvas. Space-drag pans; Control-wheel zooms. Alt reverses painting.")

    def _icon_button(self, row, path, color, tip, name, slot):
        button = QPushButton()
        button.setIcon(_arrow_icon(path, color))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tip)
        button.setAccessibleName(name)
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    def _position_controls(self):
        super()._position_controls()
        if not hasattr(self, "_view_bar"):
            return
        width, height = self.viewport().width(), self.viewport().height()
        self._history_bar.adjustSize()
        self._view_bar.adjustSize()
        self._history_bar.move(10, 10)
        self._view_bar.move(max(10, width - self._view_bar.width() - 10), 10)
        # Zoom controls sit bottom-left, as in the mockup; the size badge rises above them.
        self._top.move(10, max(10, height - self._top.height() - 10))
        self._badge.hide()  # no designation badge on the editor canvas
        self._history_bar.show()
        self._view_bar.show()

    def set_source(self, pixels: bytes, width: int, height: int):
        image = QImage(pixels, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        self.setPixmap(QPixmap.fromImage(image))
        self._mask = QImage(width, height, QImage.Format.Format_Grayscale8)
        self._mask.fill(0)
        self._result = QPixmap()
        self._undo.clear()
        self._redo.clear()
        self._stroke = None
        self._changed()

    def remove_source(self):
        self.cancel_stroke()
        self.clear()
        self._result = QPixmap()
        self._mask = QImage()
        self._undo.clear()
        self._redo.clear()
        self._changed()

    def mask_pixels(self) -> bytes:
        image = self._mask
        if image.isNull():
            return b""
        raw = bytes(image.constBits())
        stride, width = image.bytesPerLine(), image.width()
        if stride == width:
            return raw[: width * image.height()]
        return b"".join(raw[row * stride : row * stride + width] for row in range(image.height()))

    def set_result(self, pixmap):
        """Display-only result; the source and mask stay untouched and editable."""
        self._result = pixmap
        self.viewport().update()

    def has_result(self):
        return not self._result.isNull()

    def replace_mask(self, pixels: bytes, label="Load mask"):
        width, height = self._mask.width(), self._mask.height()
        if len(pixels) != width * height or pixels.count(b"\x00") + pixels.count(b"\xff") != len(pixels):
            raise ValueError("Mask must be binary and match the source grid.")
        before = self._mask.copy()
        self._mask = QImage(pixels, width, height, width, QImage.Format.Format_Grayscale8).copy()
        self._commit(before, label)

    def clear_mask(self):
        if self._mask.isNull():
            return
        before = self._mask.copy()
        self._mask.fill(0)
        self._commit(before, "Clear mask")

    def edit_labels(self):
        return [label for _, label in self._undo]

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def _trim(self, history):
        limit = max(1, min(32, self.HISTORY_BYTES // max(1, self._mask.sizeInBytes())))
        del history[:-limit]

    def _commit(self, before, label):
        if before != self._mask:
            self._undo.append((before, label))
            self._trim(self._undo)
            self._redo.clear()
        self._changed()

    def undo(self):
        self.cancel_stroke()
        if self._undo:
            before, label = self._undo.pop()
            self._redo.append((self._mask, label))
            self._mask = before
            self._trim(self._redo)
            self._changed()

    def redo(self):
        self.cancel_stroke()
        if self._redo:
            after, label = self._redo.pop()
            self._undo.append((self._mask, label))
            self._mask = after
            self._trim(self._undo)
            self._changed()

    def _rebuild_overlay(self):
        self._overlay = QImage()
        if not self._mask.isNull():
            alpha = self._mask.scaled(
                768, 768, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
            )
            self._overlay = QImage(alpha.size(), QImage.Format.Format_ARGB32)
            self._overlay.fill(self._accent)
            self._overlay.setAlphaChannel(alpha)
        self.viewport().update()

    def _changed(self):
        """A committed mask change: rebuild the tint now and tell the editor (coverage, history)."""
        self._refresh.stop()
        self._rebuild_overlay()
        self.maskEdited.emit()

    def set_view(self, view):
        self.cancel_stroke()
        self._view = view
        for name, button in self._view_buttons.items():
            button.setChecked(name == view)
        self.viewport().update()
        self.viewChanged.emit(view)

    def _point(self, position):
        rect = self.image_rect()
        return QPointF((position.x() - rect.x()) / self._factor(), (position.y() - rect.y()) / self._factor())

    def _paint_line(self, start, end, erase):
        painter = QPainter(self._mask)
        painter.setPen(
            QPen(
                Qt.GlobalColor.black if erase else Qt.GlobalColor.white,
                self.brush_size,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(start, end)
        painter.end()
        if not self._refresh.isActive():
            self._refresh.start()

    def mousePressEvent(self, event):
        if self._space or event.button() == Qt.MouseButton.MiddleButton:
            self._drag = event.position().toPoint()
            return
        if event.button() != Qt.MouseButton.LeftButton or self._mask.isNull() or self._view == "Result":
            return
        if not self.image_rect().contains(event.position()):
            return
        self.setFocus()
        point = self._point(event.position())
        erase = (self.tool == "Erase") != bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        self._stroke = (self._mask.copy(), point, point, erase, event.position())
        self._stroke_size = self.brush_size
        if self.tool in ("Brush", "Erase"):
            self._paint_line(point, point, erase)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            super().mouseMoveEvent(event)
        elif self._stroke:
            before, start, last, erase, screen_start = self._stroke
            point = self._point(event.position())
            if self.tool in ("Brush", "Erase"):
                self._paint_line(last, point, erase)
            self._stroke = (before, start, point, erase, screen_start)
            self.viewport().update()

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            super().mouseReleaseEvent(event)
            return
        if not self._stroke:
            return
        before, start, _, erase, screen_start = self._stroke
        end = self._point(event.position())
        self._stroke = None
        shape = self.tool in ("Rectangle", "Circle")
        label = (
            f"{'Remove' if erase else 'Add'} {self.tool.lower()}"
            if shape
            else (f"{'Erase' if erase else 'Brush'} · {self._stroke_size} px")
        )
        if shape and (event.position() - screen_start).manhattanLength() > 3:
            painter = QPainter(self._mask)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.GlobalColor.black if erase else Qt.GlobalColor.white)
            box = QRectF(start, end).normalized()
            if self.tool == "Circle":
                painter.drawEllipse(box)
            else:
                painter.drawRect(box)
            painter.end()
        self._commit(before, label)

    def cancel_stroke(self):
        if self._stroke:
            self._mask = self._stroke[0]
            self._stroke = None
            self._changed()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._mask.isNull():
            return
        painter = QPainter(self.viewport())
        if self._view == "Result" and not self._result.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            rect = self.image_rect()
            scale = min(rect.width() / self._result.width(), rect.height() / self._result.height())
            width, height = self._result.width() * scale, self._result.height() * scale
            painter.drawPixmap(
                QRectF(rect.x() + (rect.width() - width) / 2, rect.y() + (rect.height() - height) / 2, width, height),
                self._result,
                QRectF(self._result.rect()),
            )
        elif self._view == "Mask":
            painter.drawImage(self.image_rect(), self._mask)
        elif not self._overlay.isNull():
            painter.setOpacity(0.45)
            painter.drawImage(self.image_rect(), self._overlay)
            painter.setOpacity(1)
        if self._stroke and self.tool in ("Rectangle", "Circle"):
            _, start, end, _, _ = self._stroke
            rect = self.image_rect()
            box = QRectF(
                QPointF(rect.x(), rect.y()) + start * self._factor(), QPointF(rect.x(), rect.y()) + end * self._factor()
            ).normalized()
            painter.setPen(QPen(self._accent, 1, Qt.PenStyle.DashLine))
            if self.tool == "Circle":
                painter.drawEllipse(box)
            else:
                painter.drawRect(box)
        painter.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._space = True
        elif event.key() == Qt.Key.Key_Escape:
            self.cancel_stroke()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Z:
            self.redo() if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else self.undo()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._space = False
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._space = False
        self.cancel_stroke()
        super().focusOutEvent(event)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.sourceDropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()

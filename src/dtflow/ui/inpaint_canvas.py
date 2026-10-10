"""Manual source-grid painting only; no Pillow, backend or generation ownership."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap

from dtflow.ui.image_preview import ImagePreview
from dtflow.ui.theme import load_tokens


class InpaintCanvas(ImagePreview):
    maskEdited = Signal()
    sourceDropped = Signal(str)
    HISTORY_BYTES = 64 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mask = QImage()
        self._overlay = QImage()
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
        self.setAcceptDrops(True)
        self.setText("Choose a source image, then paint the area to change.")
        self.setAccessibleName("Inpaint canvas. Space-drag pans; Control-wheel zooms. Alt reverses painting.")

    def set_source(self, pixels: bytes, width: int, height: int):
        image = QImage(pixels, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        self.setPixmap(QPixmap.fromImage(image))
        self._mask = QImage(width, height, QImage.Format.Format_RGB32)
        self._mask.fill(Qt.GlobalColor.black)
        self._undo.clear()
        self._redo.clear()
        self._stroke = None
        self._changed()

    def remove_source(self):
        self.cancel_stroke()
        self.clear()
        self._mask = QImage()
        self._undo.clear()
        self._redo.clear()
        self._changed()

    def mask_pixels(self) -> bytes:
        image = self._mask.convertToFormat(QImage.Format.Format_Grayscale8)
        if image.isNull():
            return b""
        raw = bytes(image.constBits())
        stride, width = image.bytesPerLine(), image.width()
        return b"".join(raw[row * stride : row * stride + width] for row in range(image.height()))

    def replace_mask(self, pixels: bytes):
        width, height = self._mask.width(), self._mask.height()
        if len(pixels) != width * height or pixels.count(b"\x00") + pixels.count(b"\xff") != len(pixels):
            raise ValueError("Mask must be binary and match the source grid.")
        before = self._mask.copy()
        self._mask = QImage(pixels, width, height, width, QImage.Format.Format_Grayscale8).convertToFormat(
            QImage.Format.Format_RGB32
        )
        self._commit(before)

    def clear_mask(self):
        if self._mask.isNull():
            return
        before = self._mask.copy()
        self._mask.fill(Qt.GlobalColor.black)
        self._commit(before)

    def _trim(self, history):
        limit = max(1, min(32, self.HISTORY_BYTES // max(1, self._mask.sizeInBytes())))
        del history[:-limit]

    def _commit(self, before):
        if before != self._mask:
            self._undo.append(before)
            self._trim(self._undo)
            self._redo.clear()
        self._changed()

    def undo(self):
        self.cancel_stroke()
        if self._undo:
            self._redo.append(self._mask)
            self._mask = self._undo.pop()
            self._trim(self._redo)
            self._changed()

    def redo(self):
        self.cancel_stroke()
        if self._redo:
            self._undo.append(self._mask)
            self._mask = self._redo.pop()
            self._trim(self._undo)
            self._changed()

    def _changed(self):
        self._overlay = QImage()
        if not self._mask.isNull():
            alpha = self._mask.scaled(
                512, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
            ).convertToFormat(QImage.Format.Format_Grayscale8)
            self._overlay = QImage(alpha.size(), QImage.Format.Format_ARGB32)
            self._overlay.fill(self._accent)
            self._overlay.setAlphaChannel(alpha)
        self.viewport().update()
        self.maskEdited.emit()

    def set_view(self, view):
        self.cancel_stroke()
        self._view = view
        self.viewport().update()

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
        self._changed()

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
        if self.tool in ("Rectangle", "Circle") and (event.position() - screen_start).manhattanLength() > 3:
            painter = QPainter(self._mask)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.GlobalColor.black if erase else Qt.GlobalColor.white)
            box = QRectF(start, end).normalized()
            if self.tool == "Circle":
                painter.drawEllipse(box)
            else:
                painter.drawRect(box)
            painter.end()
        self._commit(before)

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
        if self._view == "Mask":
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

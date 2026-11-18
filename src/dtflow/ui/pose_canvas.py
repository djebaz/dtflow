"""Read-only pose preview: the chosen source, the extracted body map or the latest result, with the people found
drawn on the source."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

from dtflow.ui.image_preview import ImagePreview
from dtflow.ui.theme import load_tokens

VIEWS = ("Source", "Pose", "Result")


class PoseCanvas(ImagePreview):
    """No brush, mask or history. Source and Pose are two pictures of the same request; people are only drawn on the
    source, in source pixels, and clicking one selects it."""

    viewChanged = Signal(str)
    personPicked = Signal(int)  # index into the people given to set_people
    sourceDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = QPixmap()
        self._pose = QPixmap()
        self._result = QPixmap()
        self._people = ()  # (label, (x0, y0, x1, y1)) in source pixels
        self._selected = None
        self._view = "Source"
        self._accent = QColor(load_tokens()["color.accent"])
        self._expand.hide()  # like the Inpaint editor: the canvas never detaches into a popup window
        self._expand.setEnabled(False)
        self._view_bar = QFrame(self.viewport())
        self._view_bar.setObjectName("previewToolbar")
        row = QHBoxLayout(self._view_bar)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self._view_buttons = {}
        for name in VIEWS:
            button = QPushButton(name)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(lambda _=False, name=name: self.set_view(name))
            row.addWidget(button)
            self._view_buttons[name] = button
        self._view_buttons["Source"].setChecked(True)
        self._position_controls()
        self.setAcceptDrops(True)
        self.setText("Choose a photo or a body map to see the pose here.")
        self.setAccessibleName("Pose preview. Source or extracted pose; mouse wheel zooms.")

    def _position_controls(self):
        super()._position_controls()
        if not hasattr(self, "_view_bar"):
            return
        width, height = self.viewport().width(), self.viewport().height()
        self._view_bar.adjustSize()
        self._view_bar.move(max(10, width - self._view_bar.width() - 10), 10)
        self._top.move(10, max(10, height - self._top.height() - 10))  # zoom bottom-left, as in Inpaint
        self._badge.hide()
        self._view_bar.show()

    def set_source(self, pixmap: QPixmap):
        self._source = pixmap
        self._show()

    def set_pose(self, pixmap: QPixmap):
        self._pose = pixmap
        self._show()

    def set_result(self, pixmap: QPixmap):
        """Display-only: the latest generated image; the source and the map stay as they are."""
        self._result = pixmap
        self._show()

    def has_result(self):
        return not self._result.isNull()

    def clear_all(self):
        self._source, self._pose, self._result = QPixmap(), QPixmap(), QPixmap()
        self._people, self._selected = (), None
        self._view = "Source"
        self._view_buttons["Source"].setChecked(True)
        self._show()

    def has_pose(self):
        return not self._pose.isNull()

    def set_people(self, people, selected=None):
        self._people, self._selected = tuple(people), selected
        self.viewport().update()

    def select(self, index):
        self._selected = index
        self.viewport().update()

    def set_view(self, view):
        self._view = view
        for name, button in self._view_buttons.items():
            button.setChecked(name == view)
        self._show()
        self.viewChanged.emit(view)

    def _show(self):
        pixmap = self._source
        if self._view == "Pose" and not self._pose.isNull():
            pixmap = self._pose
        elif self._view == "Result" and not self._result.isNull():
            pixmap = self._result
        self._pixmap = pixmap
        self._fit = True
        self._sync()

    def _point(self, position):
        rect = self.image_rect()
        return QPointF((position.x() - rect.x()) / self._factor(), (position.y() - rect.y()) / self._factor())

    def _person_at(self, position):
        point = self._point(position)
        hits = [
            (index, (box[2] - box[0]) * (box[3] - box[1]))
            for index, (_label, box) in enumerate(self._people)
            if box[0] <= point.x() <= box[2] and box[1] <= point.y() <= box[3]
        ]
        return min(hits, key=lambda hit: hit[1])[0] if hits else None  # the smallest box: the one in front

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._fit and self._people and self._shows_people():
            index = self._person_at(event.position())
            if index is not None:
                self.setFocus()
                self.personPicked.emit(index)
                event.accept()
                return
        super().mousePressEvent(event)

    def _shows_people(self):
        return self._pixmap is self._source or self._pixmap.cacheKey() == self._source.cacheKey()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap.isNull() or not self._people or not self._shows_people():
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect, factor = self.image_rect(), self._factor()
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF()))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        for index, (label, (x0, y0, x1, y1)) in enumerate(self._people):
            box = QRectF(rect.x() + x0 * factor, rect.y() + y0 * factor, (x1 - x0) * factor, (y1 - y0) * factor)
            chosen = index == self._selected
            colour = self._accent if chosen else QColor(255, 255, 255, 150)
            painter.setPen(
                QPen(colour, 3 if chosen else 1.5, Qt.PenStyle.SolidLine if chosen else Qt.PenStyle.DashLine)
            )
            painter.setBrush(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 40 if chosen else 0))
            painter.drawRect(box)
            chip = QRectF(
                box.x(), box.y() - metrics.height() - 6, metrics.horizontalAdvance(label) + 12, metrics.height() + 4
            )
            if chip.y() < rect.y():
                chip.moveTop(box.y())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._accent if chosen else QColor(0, 0, 0, 160))
            painter.drawRoundedRect(chip, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)
        painter.end()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.sourceDropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()

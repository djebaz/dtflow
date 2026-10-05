"""Bundled DTFlow identity and icons."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from dtflow.ui.theme import load_tokens

IMAGES = Path(__file__).parents[1] / "resources" / "images"
APP_ICON = IMAGES / "dtflow-app.png"


def app_icon() -> QIcon:
    return QIcon(str(APP_ICON))


def panel_icon() -> QIcon:
    """Palette-backed outline of a workspace with a right-hand details panel."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(load_tokens()["color.secondary"]), 2))
    painter.drawRoundedRect(5, 6, 22, 20, 4, 4)
    painter.drawLine(20, 6, 20, 26)
    painter.end()
    return QIcon(pixmap)

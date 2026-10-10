"""Bundled DTFlow identity and icons."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from dtflow.ui.theme import load_tokens

IMAGES = Path(__file__).parents[1] / "resources" / "images"
APP_ICON = IMAGES / "dtflow-app.png"


def app_icon() -> QIcon:
    return QIcon(str(APP_ICON))


def connection_icon(level: str = "unknown") -> QIcon:
    """Power glyph with last-check dot, painted from the shared palette."""
    tokens = load_tokens()
    state_color = {"ok": "color.connectionAccent", "warning": "color.warning", "error": "color.error"}.get(
        level, "color.secondary"
    )
    pixmap = QPixmap(80, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tokens["color.connectionGlyph"]), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawArc(12, 6, 12, 12, 135 * 16, 270 * 16)
    painter.drawLine(18, 3, 18, 11)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(tokens[state_color]))
    painter.drawEllipse(34, 1, 6, 6)
    painter.end()
    return QIcon(pixmap)


def splash_pixmap() -> QPixmap:
    """Small startup card painted from the shared palette, shown while the window is built."""
    tokens = load_tokens()
    pixmap = QPixmap(360, 220)
    pixmap.fill(QColor(tokens["color.background"]))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    logo = QPixmap(str(APP_ICON))
    if not logo.isNull():
        logo = logo.scaled(84, 84, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((pixmap.width() - logo.width()) // 2, 38, logo)
    painter.setPen(QColor(tokens["color.text"]))
    font = painter.font()
    font.setPointSize(18)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(
        pixmap.rect().adjusted(0, 0, 0, -50), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, "DTFlow"
    )
    painter.setPen(QColor(tokens["color.secondary"]))
    font.setPointSize(9)
    font.setBold(False)
    painter.setFont(font)
    painter.drawText(
        pixmap.rect().adjusted(0, 0, 0, -22), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, "Starting…"
    )
    painter.end()
    return pixmap

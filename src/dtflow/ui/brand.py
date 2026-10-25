"""Bundled DTFlow identity and icons."""

from functools import cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap

from dtflow.ui.theme import load_tokens

IMAGES = Path(__file__).parents[1] / "resources" / "images"
APP_ICON = IMAGES / "dtflow-app.png"


BRAND_IMAGE_PX = 256  # largest size any window icon or brand mark is drawn at


@cache
def _brand_image() -> QImage:
    """Decode the large artwork once: window icons and brand marks only need a small copy."""
    image = QImage(str(APP_ICON))
    if image.isNull():
        return image
    return image.scaled(
        BRAND_IMAGE_PX,
        BRAND_IMAGE_PX,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def brand_mark() -> QPixmap:
    return QPixmap.fromImage(_brand_image())


def app_icon() -> QIcon:
    image = _brand_image()
    return QIcon(str(APP_ICON)) if image.isNull() else QIcon(QPixmap.fromImage(image))


def tinted_icon(path: Path, token: str, size: int) -> QIcon:
    """Single-colour SVG artwork recoloured to a palette token (Qt derives the disabled mode)."""
    ratio = 2
    pixmap = QIcon(str(path)).pixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(load_tokens()[token]))
    painter.end()
    return QIcon(pixmap)


def status_dot(level: str = "unknown", diameter: int = 14) -> QPixmap:
    """Painted connection-status dot; a font glyph would pay a Windows font fallback at first show."""
    color = {"ok": "color.success", "warning": "color.warning", "error": "color.error"}.get(level, "color.glyphNeutral")
    ratio = 2
    pixmap = QPixmap(diameter * ratio, diameter * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(load_tokens()[color]))
    painter.drawEllipse(0, 0, diameter, diameter)
    painter.end()
    return pixmap


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

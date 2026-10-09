"""Shared danger-red Trash glyph based on the canonical Images Delete icon."""

from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from dtflow.ui.theme import load_tokens

DELETE_ICON = Path(__file__).parents[1] / "resources" / "images" / "gallery" / "delete.svg"


def trash_icon_pixmap(size: int = 28) -> QPixmap:
    """Render the existing Delete SVG in the danger color for inline feedback."""
    pixmap = QIcon(str(DELETE_ICON)).pixmap(size, size)
    if pixmap.isNull():
        raise OSError("The Images Delete icon could not be loaded.")
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(load_tokens()["color.dangerText"]))
    painter.end()
    return pixmap

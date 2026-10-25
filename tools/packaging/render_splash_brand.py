"""Pre-render the splash pictogram and DTFlow wordmark without bundling font files.

Run on Windows to capture the installed Segoe UI Semibold face. Outputs are
generated package data consumed by the splash at runtime.
"""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "src" / "dtflow" / "resources" / "images"
ICON_SIZE = 96
GAP = 22
TITLE_PIXELS = 38
TEXT = "DTFlow"
TEXT_COLOR = "#e6e6e6"


def render(scale: int) -> Path:
    """Rasterize the existing icon and right-hand wordmark at one DPI scale."""
    icon = QImage(str(IMAGES / f"dtflow-splash{'@2x' if scale == 2 else ''}.png"))
    if icon.isNull():
        raise FileNotFoundError("Existing DTFlow splash pictogram is missing")
    font = QFont("Segoe UI")
    font.setStyleName("Semibold")
    font.setWeight(QFont.Weight.DemiBold)
    font.setPixelSize(TITLE_PIXELS * scale)
    metrics = QFontMetrics(font)
    width = ICON_SIZE * scale + GAP * scale + metrics.horizontalAdvance(TEXT) + 4 * scale
    height = ICON_SIZE * scale
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.drawImage(0, 0, icon)
    painter.setFont(font)
    painter.setPen(QColor(TEXT_COLOR))
    painter.drawText(
        ICON_SIZE * scale + GAP * scale,
        0,
        width - (ICON_SIZE + GAP) * scale,
        height,
        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        TEXT,
    )
    painter.end()
    dest = IMAGES / f"dtflow-splash-brand{'@2x' if scale == 2 else ''}.png"
    if not image.save(str(dest), "PNG"):
        raise OSError(f"Could not write {dest}")
    return dest


def main() -> int:
    """Generate the 1x and 2x branding assets."""
    _application = QApplication.instance() or QApplication(sys.argv[:1])
    for scale in (1, 2):
        print(f"Rendered {render(scale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

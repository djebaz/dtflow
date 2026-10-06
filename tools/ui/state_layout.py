"""Shared, non-stretching DPR-1 state-sheet layout for both capture engines."""

FRAMES = {
    "30-buttons": (320, 100),
    "31-connection-button": (100, 70),
    "32-gallery-buttons": (260, 140),
    "33-text-inputs": (260, 120),
    "34-combo-spin": (240, 100),
    "35-checkbox-slider-progress": (320, 100),
    "36-lists-tables": (400, 220),
    "37-tabs-scrollbar": (320, 180),
    "38-labels-status": (440, 200),
    "39-popups": (500, 300),
}


def compose(path, definition, rows):
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    width, height = definition["frame"]
    columns = max(len(row["states"]) for row in definition["rows"])
    cw, rh = width * 2 + 12, height * 2 + 42
    image = QImage(162 + columns * cw, 52 + len(rows) * rh, QImage.Format.Format_ARGB32)
    image.fill(QColor("#1f1f1f"))
    painter = QPainter(image)
    painter.setFont(QFont("Segoe UI", 9))
    painter.setPen(QColor("#e6e6e6"))
    painter.drawText(12, 26, path.stem + " — DPR 1, cells 2x")
    try:
        for r, (spec, cells) in enumerate(zip(definition["rows"], rows, strict=True)):
            y = 40 + r * rh
            painter.drawText(QRect(12, y, 126, rh), Qt.TextFlag.TextWordWrap, spec["label"])
            for c, state in enumerate(spec["states"]):
                x = 150 + c * cw
                painter.drawText(x, y + 12, state)
                frame = QImage(width, height, QImage.Format.Format_ARGB32)
                frame.fill(QColor("#252525"))
                cell = cells.get(state)
                fp = QPainter(frame)
                if isinstance(cell, QImage):
                    if cell.width() > width or cell.height() > height or cell.devicePixelRatio() != 1:
                        fp.end()
                        raise ValueError(f"{path.stem}/{spec['label']}/{state}: capture exceeds frame or DPR != 1")
                    fp.drawImage(0, 0, cell)
                else:
                    fp.setFont(QFont("Segoe UI", 9))
                    fp.setPen(QColor("#b2b2b2"))
                    fp.drawText(QRect(8, 8, width - 16, height - 16), Qt.TextFlag.TextWordWrap, cell or "Unavailable")
                fp.end()
                painter.drawImage(x, y + 18, frame.scaled(width * 2, height * 2))
    finally:
        painter.end()
    if not image.save(str(path)):
        raise ValueError(f"Cannot save {path}")
    return image.size().toTuple()

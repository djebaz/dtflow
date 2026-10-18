"""Actual thumbnail paints keep metadata badges inside the image."""

import pytest
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QPushButton

from dtflow.ui import gallery_detail_preview as preview


@pytest.mark.parametrize("width", [336, 96])
def test_thumbnail_badges_fit_without_overlap(qtbot, monkeypatch, width):
    painted = []

    class RecordingPainter(QPainter):
        def drawText(self, rect, *args):
            painted.append(rect)
            return super().drawText(rect, *args)

    monkeypatch.setattr(preview, "QPainter", RecordingPainter)
    button = QPushButton()
    qtbot.addWidget(button)
    button.setFixedSize(width, 160)
    cover = preview.CoverPainter(button, "", ("1024 × 768", "117.7 MB"))
    assert not button.grab().isNull()
    assert painted
    assert all(rect.left() >= 6 and rect.right() <= width - 6 for rect in painted)
    assert all(not left.intersects(right) for i, left in enumerate(painted) for right in painted[i + 1 :])
    assert cover.badges == ("1024 × 768", "117.7 MB")  # full metadata is retained

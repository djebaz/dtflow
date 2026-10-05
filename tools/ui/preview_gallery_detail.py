"""Offline Designer/QSS preview. No gallery services, credentials or provider imports.

Run: uv run python tools/ui/preview_gallery_detail.py
Capture: uv run python tools/ui/preview_gallery_detail.py --capture tmp/detail.png --state details
"""

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QFontDatabase, QIcon, QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton, QWidget

from dtflow.ui.theme import apply_native_dark_titlebar, apply_theme

ROOT = Path(__file__).resolve().parents[2]
FORM = ROOT / "src/dtflow/ui/forms/gallery_detail_page.ui"
ARTWORK = ROOT / "src/dtflow/resources/images/illustrative-lighthouse.png"


class DetailFixture(QObject):
    """Presentation-only fixture controller; never reads saved state or real metadata."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.source = QPixmap(str(ARTWORK))
        self.image = page.findChild(QLabel, "galleryImageSurface")
        self.image.installEventFilter(self)
        self.mode = page.findChild(QWidget, "galleryViewMode")
        self.mode.currentTextChanged.connect(self.update_image)
        self.additional = page.findChild(QWidget, "galleryAdditionalScroll")
        self.raw = page.findChild(QPlainTextEdit, "galleryRawMetadata")
        self.additional.hide()
        self.raw.hide()
        self.additional_button = page.findChild(QPushButton, "galleryAdditionalButton")
        self.raw_button = page.findChild(QPushButton, "galleryRawButton")
        self.additional_button.toggled.connect(lambda checked: self.show_panel("details", checked))
        self.raw_button.toggled.connect(lambda checked: self.show_panel("raw", checked))
        self.raw.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.raw.setPlainText(
            json.dumps(
                {
                    "designation": "illustrative_fixture",
                    "saved_output": False,
                    "provider": None,
                    "attempt_id": None,
                    "request_id": None,
                    "generation_date": None,
                    "model": "fixture-model.ckpt",
                    "loras": [{"identifier": "fixture-portrait", "weight": 0.8, "compatibility": "unknown"}],
                    "note": "Presentation fixture only. Not a native preset or real generation record.",
                },
                indent=2,
            )
        )
        page.findChild(QLabel, "galleryDimensionsOverlay").setText(
            f"{self.source.width()} × {self.source.height()} (fixture)"
        )
        self.thumbnails = [page.findChild(QPushButton, f"galleryThumbnail{i}") for i in range(1, 7)]
        for i, button in enumerate(self.thumbnails, 1):
            button.setText("")
            button.setToolTip("Repeated illustrative artwork; not six saved outputs.")
            button.clicked.connect(lambda _, index=i: self.select(index))
        self.slider = page.findChild(QWidget, "galleryThumbnailSizeSlider")
        self.slider.valueChanged.connect(self.resize_thumbnails)
        self.resize_thumbnails(self.slider.value())
        QTimer.singleShot(0, self.update_image)

    def show_panel(self, name, checked):
        panel = self.additional if name == "details" else self.raw
        other_button = self.raw_button if name == "details" else self.additional_button
        if checked:
            other_button.setChecked(False)
        panel.setVisible(checked)

    def select(self, index):
        self.page.findChild(QLabel, "galleryPositionOverlay").setText(f"{index} / 6")

    def resize_thumbnails(self, width):
        for button in self.thumbnails:
            icon_size = QSize(width - 16, width * 3 // 4 - 16)
            icon = self.source.scaled(
                icon_size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            icon = icon.copy(
                (icon.width() - icon_size.width()) // 2,
                (icon.height() - icon_size.height()) // 2,
                icon_size.width(),
                icon_size.height(),
            )
            button.setIcon(QIcon(icon))
            button.setIconSize(icon_size)
            button.setMinimumSize(width, width * 3 // 4)
        self.page.findChild(QLabel, "galleryThumbnailSizeValue").setText(f"{width} px")
        self.page.findChild(QWidget, "galleryFilmstripScroll").setFixedWidth(width + 24)

    def update_image(self, *_):
        if self.source.isNull():
            return
        target = self.image.size()
        cover = self.mode.currentText() == "Cover"
        aspect = Qt.AspectRatioMode.KeepAspectRatioByExpanding if cover else Qt.AspectRatioMode.KeepAspectRatio
        result = self.source.scaled(target, aspect, Qt.TransformationMode.SmoothTransformation)
        if cover:
            result = result.copy(
                (result.width() - target.width()) // 2,
                (result.height() - target.height()) // 2,
                target.width(),
                target.height(),
            )
        self.image.setPixmap(result)

    def eventFilter(self, watched, event):
        if watched is self.image and event.type() == QEvent.Type.Resize:
            self.update_image()
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, help="Write one offline layout capture, then exit")
    parser.add_argument("--state", choices=("closed", "details", "raw"), default="closed")
    parser.add_argument("--size", default="1280x1000", help="Preview size, e.g. 800x680")
    args = parser.parse_args()
    try:
        width, height = (int(part) for part in args.size.split("x"))
        if width < 400 or height < 400:
            raise ValueError
    except ValueError:
        parser.error("--size needs WIDTHxHEIGHT, both at least 400")
    app = QApplication(sys.argv[:1])
    apply_theme(app)
    ui_file = QFile(str(FORM))
    if not ui_file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(ui_file.errorString())
    loader = QUiLoader()
    page = loader.load(ui_file)
    ui_file.close()
    if page is None:
        raise RuntimeError(loader.errorString())
    controller = DetailFixture(page)
    if args.state == "details":
        controller.additional_button.setChecked(True)
    elif args.state == "raw":
        controller.raw_button.setChecked(True)
    page.resize(width, height)
    page.show()
    apply_native_dark_titlebar(page)
    if args.capture:
        args.capture.parent.mkdir(parents=True, exist_ok=True)

        def capture():
            if not page.grab().save(str(args.capture)):
                raise OSError("Could not save the layout capture")
            app.quit()

        QTimer.singleShot(350, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline Designer/QSS preview. No gallery services, credentials or provider imports.

Run: uv run python tools/ui/preview_gallery_detail.py
Capture: uv run python tools/ui/preview_gallery_detail.py --capture tmp/detail.png --state details
"""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QTimer
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication

from dtflow.ui.gallery_detail_preview import DetailFixture
from dtflow.ui.theme import apply_native_dark_titlebar, apply_theme

ROOT = Path(__file__).resolve().parents[2]
FORM = ROOT / "src/dtflow/ui/forms/gallery_detail_page.ui"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, help="Write one offline layout capture, then exit")
    parser.add_argument("--state", choices=("closed", "details", "raw"), default="closed")
    parser.add_argument("--view", choices=("grid", "list", "detail"), default="detail")
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
    controller.show_view(args.view.title() if args.state == "closed" else "Detail")
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

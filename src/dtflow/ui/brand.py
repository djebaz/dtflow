"""Bundled DTFlow identity and illustrative artwork."""

from pathlib import Path

from PySide6.QtGui import QIcon

IMAGES = Path(__file__).parents[1] / "resources" / "images"
APP_ICON = IMAGES / "dtflow-app.png"
ILLUSTRATIVE_SAMPLE = IMAGES / "illustrative-lighthouse.png"


def app_icon() -> QIcon:
    return QIcon(str(APP_ICON))

"""Shared application theme loading for the Qt presentation layer."""

from pathlib import Path

from PySide6.QtWidgets import QApplication

THEME_FILE = Path(__file__).parents[1] / "resources" / "styles" / "dtflow_dark.qss"


def load_theme() -> str:
    """Read the tracked QSS source used by source and packaged Qt execution."""
    try:
        return THEME_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError("The DTFlow Qt stylesheet could not be loaded.") from exc


def apply_theme(application: QApplication) -> None:
    """Apply the shared stylesheet once at application startup."""
    application.setStyleSheet(load_theme())

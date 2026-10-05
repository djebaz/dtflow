"""Qt application entry used by ``dtflow gui``."""

import sys

from PySide6.QtWidgets import QApplication

from dtflow.ui.main_window import MainWindow
from dtflow.ui.theme import apply_theme


def run_gui(argv: list[str] | None = None, *, no_cache: bool = False) -> int:
    application = QApplication.instance() or QApplication(argv if argv is not None else sys.argv[:1])
    apply_theme(application)
    window = MainWindow(no_cache=no_cache)
    # The session is remembered before the runner stops, so the last form values survive a normal quit.
    application.aboutToQuit.connect(lambda: window.remember(background=False))
    application.aboutToQuit.connect(window.runner.shutdown)
    window.show()
    return application.exec()

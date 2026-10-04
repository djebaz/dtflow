"""Qt application entry used by ``dtflow gui``."""

import sys

from PySide6.QtWidgets import QApplication

from dtflow.ui.main_window import MainWindow


def run_gui(argv: list[str] | None = None) -> int:
    application = QApplication.instance() or QApplication(argv if argv is not None else sys.argv[:1])
    window = MainWindow()
    application.aboutToQuit.connect(window.runner.shutdown)
    window.show()
    return application.exec()

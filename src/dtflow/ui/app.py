"""Qt application entry used by ``dtflow gui``."""

import logging
import sys
import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from dtflow import startup
from dtflow.persistence.gallery_snapshot import configure, finish_at_close
from dtflow.ui.brand import app_icon
from dtflow.ui.main_window import MainWindow
from dtflow.ui.theme import apply_theme


def run_gui(argv: list[str] | None = None, *, no_cache: bool = False) -> int:
    configure(no_cache=no_cache)
    application = QApplication.instance() or QApplication(argv if argv is not None else sys.argv[:1])
    apply_theme(application)
    application.setWindowIcon(app_icon())
    startup.mark("application and theme")
    window = MainWindow(no_cache=no_cache)
    startup.mark("main window built")
    # The session is remembered before the runner stops, so the last form values survive a normal quit.
    application.aboutToQuit.connect(lambda: window.remember(background=False))
    application.aboutToQuit.connect(window.gallery.save_now)
    application.aboutToQuit.connect(window.image_export.cancel)
    application.aboutToQuit.connect(window.runner.shutdown)
    window.show()
    startup.mark("window shown")
    # The first event-loop turn follows the first paint; then the heavy gRPC library loads without delaying display.
    QTimer.singleShot(0, lambda: _after_first_paint(window))
    result = application.exec()
    # The event loop has exited cleanly; never perform this disk work in a Qt slot.
    window.runner.shutdown(timeout=30)
    finish_at_close(window.state_dir)
    return result


def _after_first_paint(window) -> None:
    startup.mark("first paint")
    startup.report("startup phases")
    threading.Thread(target=_preload_drawthings, name="dtflow-preload", daemon=True).start()


def _preload_drawthings() -> None:
    """Import the Draw Things provider library in the background so the first connection check does not pay for it."""
    started = time.perf_counter()
    try:
        import dtflow.backends.drawthings.backend  # noqa: F401
    except Exception:  # pragma: no cover - the real use reports the failure with context
        logging.getLogger("dtflow.startup").debug("Draw Things library preload failed", exc_info=True)
        return
    logging.getLogger("dtflow.startup").debug(
        "draw things library loaded in background after window display: %.0fms", (time.perf_counter() - started) * 1000
    )

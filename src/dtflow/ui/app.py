"""Qt application entry used by ``dtflow gui``."""

import logging
import sys
import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from dtflow import startup
from dtflow.diagnostics import event as diagnostic_event
from dtflow.diagnostics import measurement
from dtflow.persistence import thumbnail_cache
from dtflow.persistence.gallery_snapshot import configure, finish_at_close
from dtflow.ui.brand import app_icon
from dtflow.ui.diagnostic_notice import DiagnosticNoticeReceiver
from dtflow.ui.main_window import MainWindow
from dtflow.ui.theme import apply_theme


def open_window(
    application: QApplication,
    *,
    no_cache: bool = False,
    gallery_limits: tuple[int, int] | None = None,
    thumbnail_format: str = "jpg",
    diagnostic_notices=None,
    before_show=None,
) -> MainWindow:
    """Configure and show the main window; the caller runs the event loop and then calls ``close_window``.

    ``before_show`` receives the window just before ``show()`` so a caller can watch its first paint without a race.
    """
    configure(no_cache=no_cache)
    thumbnail_cache.configure(no_cache=no_cache, thumbnail_format=thumbnail_format)
    if gallery_limits is not None:
        from dtflow.ui.gallery_page import configure_limits

        configure_limits(batch=gallery_limits[0], cards=gallery_limits[1])
    apply_theme(application)
    application.setWindowIcon(app_icon())
    startup.mark("application and theme")
    window = MainWindow(no_cache=no_cache)
    if diagnostic_notices is not None:
        window._diagnostic_notice = DiagnosticNoticeReceiver(window, diagnostic_notices)
    startup.mark("main window built")
    # The session is remembered before the runner stops, so the last form values survive a normal quit.
    application.aboutToQuit.connect(lambda: window.remember(background=False))
    application.aboutToQuit.connect(window.gallery.save_now)
    application.aboutToQuit.connect(window.image_export.cancel)
    application.aboutToQuit.connect(window.runner.shutdown)
    if before_show is not None:
        before_show(window)
    window.show()
    startup.mark("window shown")
    # The first event-loop turn follows the first paint; then the heavy gRPC library loads without delaying display.
    startup_timer = QTimer(window.form)
    startup_timer.setSingleShot(True)
    startup_timer.timeout.connect(lambda: _after_first_paint(window))
    startup_timer.start(0)
    return window


def close_window(window: MainWindow, result: int) -> int:
    # The event loop has exited cleanly; never perform this disk work in a Qt slot.
    window.runner.shutdown(timeout=30)
    finish_at_close(window.state_dir)
    return result


def run_gui(
    argv: list[str] | None = None,
    *,
    no_cache: bool = False,
    gallery_limits: tuple[int, int] | None = None,
    thumbnail_format: str = "jpg",
    diagnostic_notices=None,
) -> int:
    application = QApplication.instance() or QApplication(argv if argv is not None else sys.argv[:1])
    window = open_window(
        application,
        no_cache=no_cache,
        gallery_limits=gallery_limits,
        thumbnail_format=thumbnail_format,
        diagnostic_notices=diagnostic_notices,
    )
    return close_window(window, application.exec())


# Importing the Draw Things library parses a YAML schema in pure Python and holds the GIL, which froze the
# freshly shown window for 0.7-2.7 s on Windows. Starting it a moment after first paint lets the first
# interactions land before that work does; an early manual Check still supersedes the queued startup check.
STARTUP_WORK_DELAY_MS = 1000


def _after_first_paint(window) -> None:
    if window.runner.closed:
        return
    startup.mark("first paint")
    startup.report("startup phases")
    QTimer.singleShot(STARTUP_WORK_DELAY_MS, window.form, lambda: _start_background_work(window))


def _start_background_work(window) -> None:
    if window.runner.closed:
        return
    window.start_startup_check()
    threading.Thread(target=_preload_drawthings, name="dtflow-preload", daemon=True).start()


def _preload_drawthings() -> None:
    """Import the Draw Things provider library in the background so the first connection check does not pay for it."""
    started = time.perf_counter()
    try:
        import dtflow.backends.drawthings.backend  # noqa: F401
    except Exception:  # pragma: no cover - the real use reports the failure with context
        diagnostic_event(
            logging.getLogger("dtflow.startup"), "app.provider_preload_failed", level=logging.DEBUG, exc_info=True
        )
        return
    measurement(
        logging.getLogger("dtflow.startup"), "app.provider_preloaded", elapsed_ms=(time.perf_counter() - started) * 1000
    )

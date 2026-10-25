"""Minimal startup splash: the DTFlow logo and a thin busy bar, shown before the main UI is imported.

Only the smallest Qt modules and one bundled image are touched here. The theme, Designer forms, main window and
services load after the splash has painted, in a queued continuation on the GUI thread. No fixed delays are used; the
splash closes at the main window's first paint or when startup fails.
"""

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from dtflow import startup

RESOURCES = Path(__file__).parents[1] / "resources"
LOGO = RESOURCES / "images" / "dtflow-splash.png"  # pre-scaled copies of the app artwork: no decode of the large PNG
LOGO_2X = LOGO.with_name("dtflow-splash@2x.png")
TOKENS = RESOURCES / "styles" / "tokens.json"

WIDTH = 360
HEIGHT = 280
LOGO_SIZE = 168
BAR_HEIGHT = 4
BAR_SEGMENT = 0.28  # fraction of the bar width covered by the moving segment
BAR_PERIOD_S = 1.4
FRAME_MS = 16
LOGO_2X_RATIO = 2.0
# Fill reached when each real startup stage completes. These are stage milestones, not time estimates: the bar
# moves as an indeterminate segment until the first stage finishes, then fills in steps as work really completes.
STAGE_FILL = {"imports": 0.2, "theme": 0.35, "built": 0.9, "shown": 0.95}
# The first-paint signal is the only trigger for loading the main UI; this guards against a platform that never paints.
FIRST_PAINT_FALLBACK_MS = 500
# Bounded fallback if the shown window never reports a paint event.
WINDOW_PAINT_FALLBACK_MS = 250


class GuiUnavailable(ImportError):
    """The Qt shell modules could not be imported."""


class _FirstPaint(QObject):
    """Calls back once with ``"paint"`` on the first paint event of the watched window, or ``"fallback"`` if none comes.

    Install it before the window is shown, then ``arm()`` the bounded fallback after ``show()``.
    """

    def __init__(self, form: QWidget, callback) -> None:
        super().__init__(form)
        self._callback = callback
        self._done = False
        form.installEventFilter(self)

    def arm(self) -> None:
        QTimer.singleShot(WINDOW_PAINT_FALLBACK_MS, lambda: self._fire("fallback"))

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Paint:
            self._fire("paint")
        return False

    def _fire(self, source: str) -> None:
        if self._done:
            return
        self._done = True
        self.parent().removeEventFilter(self)
        self._callback(source)


class StartupSplash(QWidget):
    """Opaque, non-stay-on-top splash with an indeterminate bar that only moves while the event loop runs."""

    painted = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(WIDTH, HEIGHT)
        try:
            tokens = json.loads(TOKENS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tokens = {}
        self._background = QColor(tokens.get("color.background", "#1f1f1f"))
        self._border = QColor(tokens.get("color.border", "#3a3a3a"))
        self._accent = QColor(tokens.get("color.accent", "#0068b8"))
        self._track = QColor(tokens.get("color.surface", "#252525"))
        self._logo = self._load_logo()
        self._announced = False
        self._finishing = False
        self._fill: float | None = None
        self._clock = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._advance)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())

    def _load_logo(self) -> QImage:
        ratio = self.devicePixelRatioF()
        image = QImage(str(LOGO_2X if ratio > 1.25 else LOGO))
        if not image.isNull():
            image.setDevicePixelRatio(LOGO_2X_RATIO if ratio > 1.25 else 1.0)
        return image

    def _bar_rect(self) -> QRectF:
        return QRectF(1, HEIGHT - 1 - BAR_HEIGHT, WIDTH - 2, BAR_HEIGHT)

    def set_stage(self, stage: str) -> None:
        """Record a completed startup stage and paint it at once; the GUI thread may be blocked right after."""
        fill = STAGE_FILL.get(stage, 1.0)
        if self._fill is not None and fill <= self._fill:
            return
        self._fill = fill
        if self.isVisible():
            self.repaint()

    def _advance(self) -> None:
        self.update(self._bar_rect().toAlignedRect())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._background)
        if not self._logo.isNull():
            logical = self._logo.deviceIndependentSize()
            painter.drawImage(
                round((WIDTH - logical.width()) / 2),
                round((HEIGHT - BAR_HEIGHT - logical.height()) / 2),
                self._logo,
            )
        bar = self._bar_rect()
        painter.fillRect(bar, self._track)
        if self._fill is not None:
            painter.fillRect(QRectF(bar.left(), bar.top(), bar.width() * self._fill, bar.height()), self._accent)
        else:
            phase = ((time.perf_counter() - self._clock) / BAR_PERIOD_S) % 1.0
            segment = bar.width() * BAR_SEGMENT
            start = bar.left() - segment + phase * (bar.width() + segment)
            painter.setClipRect(bar)
            painter.fillRect(QRectF(start, bar.top(), segment, bar.height()), self._accent)
            painter.setClipping(False)
        painter.setPen(QPen(self._border, 1))
        painter.drawRect(0, 0, WIDTH - 1, HEIGHT - 1)
        painter.end()
        if not self._announced:
            self._announced = True
            QTimer.singleShot(0, self.painted.emit)

    def closeEvent(self, event) -> None:
        # The splash is not user-dismissible: closing it early would leave a running app without a window.
        if self._finishing:
            super().closeEvent(event)
        else:
            event.ignore()

    def dismiss(self) -> None:
        self._finishing = True
        self.close()

    def finish(self, form: QWidget) -> None:
        """Close once the main window is up; the window form, not the splash, keeps the focus."""
        self.dismiss()
        form.raise_()
        form.activateWindow()


def run_gui_with_splash(**options) -> int:
    """Show the splash, then import and build the main UI inside the event loop and run it."""
    application = QApplication.instance() or QApplication(sys.argv[:1])
    startup.mark("application ready")
    splash = StartupSplash()
    outcome: dict = {}
    state = {"started": False}

    def proceed() -> None:
        if state["started"]:
            return
        state["started"] = True
        startup.mark("splash painted")
        try:
            try:
                from dtflow.ui import app as gui
            except ImportError as exc:
                raise GuiUnavailable(str(exc)) from exc
            startup.mark("ui imports")
            splash.set_stage("imports")
            outcome["gui"] = gui
            outcome["window"] = gui.open_window(
                application, before_show=watch_window, progress=splash.set_stage, **options
            )
            outcome["paint_watch"].arm()
        except BaseException as exc:
            outcome["error"] = exc
            splash.dismiss()
            application.exit(1)
            return

    def watch_window(window) -> None:
        def window_painted(source: str) -> None:
            startup.mark("window painted" if source == "paint" else "window paint fallback")
            splash.set_stage("painted")
            splash.finish(window.form)

        # Watch before show(), so a synchronous first paint cannot be missed; report timings only once it is marked.
        startup.expect("window painted", "window paint fallback")
        outcome["paint_watch"] = _FirstPaint(window.form, window_painted)

    splash.painted.connect(proceed)
    splash.show()
    startup.mark("splash shown")
    QTimer.singleShot(FIRST_PAINT_FALLBACK_MS, proceed)
    result = application.exec()
    splash.dismiss()
    if "error" in outcome:
        raise outcome["error"]
    if "window" not in outcome:
        return result
    return outcome["gui"].close_window(outcome["window"], result)

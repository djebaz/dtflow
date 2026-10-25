"""Minimal startup splash: the DTFlow logo and a thin busy bar, shown before the main UI is imported.

Only the smallest Qt modules and one bundled image are touched here. The theme, Designer forms, main window and
services load after the splash has painted, in a queued continuation on the GUI thread. No fixed delays are used; the
splash closes on the event-loop turn after the main window is shown (so it has painted) or when startup fails.
"""

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from dtflow import startup

RESOURCES = Path(__file__).parents[1] / "resources"
LOGO = RESOURCES / "images" / "dtflow-app.png"
TOKENS = RESOURCES / "styles" / "tokens.json"

WIDTH = 360
HEIGHT = 280
LOGO_SIZE = 168
BAR_HEIGHT = 4
BAR_SEGMENT = 0.28  # fraction of the bar width covered by the moving segment
BAR_PERIOD_S = 1.4
FRAME_MS = 16
# The first-paint signal is the only trigger for loading the main UI; this guards against a platform that never paints.
FIRST_PAINT_FALLBACK_MS = 500


class GuiUnavailable(ImportError):
    """The Qt shell modules could not be imported."""


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
        self._clock = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._advance)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())

    def _load_logo(self) -> QImage:
        ratio = max(1.0, self.devicePixelRatioF())
        image = QImage(str(LOGO))
        if image.isNull():
            return image
        side = round(LOGO_SIZE * ratio)
        scaled = image.scaled(
            side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        scaled.setDevicePixelRatio(ratio)
        return scaled

    def _bar_rect(self) -> QRectF:
        return QRectF(1, HEIGHT - 1 - BAR_HEIGHT, WIDTH - 2, BAR_HEIGHT)

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

    def finish(self, form: QWidget) -> None:
        """Close once the main window is up; the window form, not the splash, keeps the focus."""
        self.close()
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
            outcome["gui"] = gui
            outcome["window"] = gui.open_window(application, **options)
        except BaseException as exc:
            outcome["error"] = exc
            splash.close()
            application.exit(1)
            return
        # The window is already shown; the splash stays through the next event-loop turn, which paints it.
        QTimer.singleShot(0, lambda: splash.finish(outcome["window"].form))

    splash.painted.connect(proceed)
    splash.show()
    startup.mark("splash shown")
    QTimer.singleShot(FIRST_PAINT_FALLBACK_MS, proceed)
    result = application.exec()
    splash.close()
    if "error" in outcome:
        raise outcome["error"]
    if "window" not in outcome:
        return result
    return outcome["gui"].close_window(outcome["window"], result)

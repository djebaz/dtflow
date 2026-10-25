"""Measure one hidden QLineEdit in a fresh process, without launching DTFlow.

Run native and dtflow styles in separate processes, alternating their order.
This diagnostic neither opens application state nor resolves credentials/providers.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from time import perf_counter

from PySide6.QtCore import qVersion
from PySide6.QtGui import QFontMetrics
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget
from shiboken6 import delete

from dtflow.ui.theme import apply_theme, load_theme, load_tokens


def measure(style: str) -> dict:
    """Record prelude costs too, so moved font/style work is not mistaken for a saving."""
    timings = {}
    started = previous = perf_counter()

    def mark(name):
        nonlocal previous
        now = perf_counter()
        timings[name] = (now - previous) * 1000
        previous = now

    app = QApplication([])
    mark("application_ms")
    if style == "dtflow":
        apply_theme(app)
    elif style in ("dtflow-exact-font", "dtflow-generic-fallback"):
        # Retain an explicit old-policy baseline after production adopts the exact family.
        family = load_tokens()["type.family"]
        source = load_theme()
        declaration = f'font-family: "{family}", sans-serif;'
        exact = f'font-family: "{family}";'
        if source.count(declaration) + source.count(exact) != 1:
            raise ValueError("Expected exactly one global font-family declaration")
        source = (
            source.replace(declaration, exact, 1)
            if style == "dtflow-exact-font"
            else source.replace(exact, declaration, 1)
        )
        app.setStyleSheet(source)
    mark("theme_ms")
    loader = QUiLoader()
    mark("loader_ms")
    basic = QWidget()
    basic.ensurePolished()
    delete(basic)
    del basic
    mark("first_widget_ms")
    QFontMetrics(QApplication.font()).horizontalAdvance("Ag")
    mark("font_metrics_ms")
    probe = QLineEdit()
    mark("construction_ms")
    probe.ensurePolished()
    mark("polishing_ms")
    delete(probe)
    del probe
    mark("destruction_ms")
    elapsed_ms = (previous - started) * 1000
    screen = app.primaryScreen()
    # Metadata is deliberately collected after the measured font/style operations.
    result = {
        "style": style,
        "qt": qVersion(),
        "python": platform.python_version(),
        "os": sys.platform,
        "qpa": app.platformName(),
        "qt_style": app.style().objectName(),
        "font": app.font().family(),
        "dpi": screen.logicalDotsPerInch() if screen is not None else None,
        "dpr": screen.devicePixelRatio() if screen is not None else None,
        "timings_ms": timings,
        "elapsed_ms": elapsed_ms,
        "measurement_boundary": "QApplication construction through hidden probe disposal; excludes Python/Qt imports and full desktop construction",
    }
    del loader
    app.quit()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--style", required=True, choices=("native", "dtflow", "dtflow-exact-font", "dtflow-generic-fallback")
    )
    args = parser.parse_args()
    print(json.dumps(measure(args.style)))


if __name__ == "__main__":
    main()

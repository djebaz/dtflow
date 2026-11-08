"""Application-wide Python event filters and the one place that can pause them (#308).

An application-level Python filter makes PySide build a wrapper for every object that receives any event, including a
graphics item that is half way through its destructor. A native Windows heap-corruption crash (0xC0000374) was seen in
exactly that spot while a workflow graphics proxy was torn down. The filters are still needed for every other widget, so
they register here and ``app_filters_suspended`` takes them off the application only while a graphics item is destroyed.
"""

from __future__ import annotations

import weakref
from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

_installed: weakref.WeakSet[QObject] = weakref.WeakSet()


def install_app_filter(filter_object: QObject) -> None:
    """Install ``filter_object`` on the application and remember it so teardown can pause it."""
    app = QApplication.instance()
    if app is None:
        return
    app.installEventFilter(filter_object)
    _installed.add(filter_object)


@contextmanager
def app_filters_suspended() -> Iterator[None]:
    """Take every registered application filter off while the body runs, then put back those still alive."""
    app = QApplication.instance()
    paused = [f for f in list(_installed) if isValid(f)] if app is not None else []
    for f in paused:
        app.removeEventFilter(f)
    try:
        yield
    finally:
        for f in paused:
            if isValid(f):
                app.installEventFilter(f)

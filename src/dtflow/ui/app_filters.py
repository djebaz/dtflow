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

_installed: list[weakref.ref] = []  # install order, oldest first; Qt consults the newest filter first
_suspend_depth = 0
_paused: list[weakref.ref] = []


def _live() -> list[QObject]:
    return [f for f in (ref() for ref in _installed) if f is not None and isValid(f)]


def install_app_filter(filter_object: QObject) -> None:
    """Install ``filter_object`` on the application and remember it so teardown can pause it."""
    app = QApplication.instance()
    if app is None:
        return
    app.installEventFilter(filter_object)
    if filter_object not in _live():
        _installed.append(weakref.ref(filter_object))


def remove_app_filter(filter_object: QObject) -> None:
    app = QApplication.instance()
    if app is not None:
        app.removeEventFilter(filter_object)
    _installed[:] = [ref for ref in _installed if ref() is not None and ref() is not filter_object]


@contextmanager
def app_filters_suspended() -> Iterator[None]:
    """Take every registered application filter off while the body runs, then put back those still alive.

    Re-entrant: only the outermost exit reinstalls, in the original install order so event precedence is unchanged.
    Do not call from inside one of the registered filters' own callbacks: Qt may be iterating the filter list.
    """
    global _suspend_depth, _paused
    app = QApplication.instance()
    _suspend_depth += 1
    try:
        if _suspend_depth == 1 and app is not None:
            _paused = _live()
            for f in _paused:
                app.removeEventFilter(f)
        yield
    finally:
        _suspend_depth -= 1
        if _suspend_depth == 0 and app is not None:
            paused, _paused = _paused, []
            for f in paused:  # oldest first, so the newest ends up consulted first again
                if isValid(f):
                    app.installEventFilter(f)

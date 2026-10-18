"""Time-bounded GUI-thread construction before an atomic gallery page commit."""

import logging
from collections import deque
from time import monotonic

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger(__name__)


class GalleryRetirement(QObject):
    """Release retired card subtrees over several event-loop turns."""

    def __init__(self, parent):
        super().__init__(parent)
        self.widgets = deque()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(1)
        self.timer.timeout.connect(self._step)

    def retire(self, widget):
        widget.hide()
        self.widgets.append(widget)
        if not self.timer.isActive():
            self.timer.start()

    def _step(self):
        # deleteLater is intentionally released in small groups: posting all
        # 100 card subtrees at once merely moves the stall into DeferredDelete.
        for _ in range(min(4, len(self.widgets))):
            self.widgets.popleft().deleteLater()
        if self.widgets:
            self.timer.start()


class GalleryPreparation(QObject):
    """Keep uncommitted widgets private and yield to input between short slices."""

    def __init__(self, parent, *, dispose, budget_ms=4.0, observe=None):
        super().__init__(parent)
        self.dispose = dispose
        self.budget_ms = budget_ms
        self.observe = observe
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(1)
        self.timer.timeout.connect(self._step)
        self._entries = {}
        self._records = ()
        self._index = 0
        self._factory = self._done = self._valid = None

    @property
    def active(self):
        return self._done is not None

    def start(self, records, *, factory, done, valid):
        self.cancel()
        self._records = tuple(records)
        self._factory, self._done, self._valid = factory, done, valid
        self.timer.start()

    def cancel(self):
        self.timer.stop()
        for entry in self._entries.values():
            self.dispose(entry)
        self._entries = {}
        self._records = ()
        self._index = 0
        self._factory = self._done = self._valid = None

    def _step(self):
        if self._valid is None or not self._valid():
            self.cancel()
            return
        started = monotonic()
        count = 0
        try:
            while self._index < len(self._records):
                record = self._records[self._index]
                self._entries[record.identity] = self._factory(record)
                self._index += 1
                count += 1
                if (monotonic() - started) * 1000 >= self.budget_ms:
                    break
        except Exception as exc:
            # A failed preparation must not retain a callback/result or orphan
            # staged widgets. Let the controller report the construction error.
            done = self._done
            self.cancel()
            log.warning("gallery preparation failed reason=%s", type(exc).__name__)
            done(None)
            return
        if self.observe is not None:
            self.observe(count, (monotonic() - started) * 1000)
        if self._index < len(self._records):
            self.timer.start()
            return
        entries, done = self._entries, self._done
        self._entries = {}
        self.cancel()
        done(entries)

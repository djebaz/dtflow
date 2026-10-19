"""One bounded, Qt-free diagnostic notice mailbox for desktop startup/runtime."""

import threading


class DiagnosticNotice:
    def __init__(self):
        self._lock = threading.Lock()
        self._message = None
        self._reported = False

    def post(self, message):
        with self._lock:
            if not self._reported:
                self._message = message
                self._reported = True

    def take(self):
        with self._lock:
            message, self._message = self._message, None
            return message

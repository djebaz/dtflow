"""Progress, cancel and log plumbing handed to the conversion engines.

Qt-free. Callbacks run on the converting thread. Progress is throttled to about ten calls per
second, but every stage change and every message is delivered.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from dtflow.services.lora_convert.errors import ConversionCancelled

_MIN_INTERVAL = 0.1


@dataclass(frozen=True, slots=True)
class ConversionProgress:
    stage: str  # "reading" | "converting" | "writing" | "finishing"
    done: int  # tensors (modules) processed
    total: int  # known after the key scan; 0 before
    message: str = ""  # human line for the log, the text the old ``log()`` printed


@dataclass(slots=True)
class ConversionContext:
    progress: Callable[[ConversionProgress], None] | None = None
    cancel: threading.Event | None = None
    warnings: list[str] = field(default_factory=list)
    _stage: str = "reading"
    _last: float = 0.0

    def check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise ConversionCancelled("The conversion was cancelled.")

    def stage(self, stage: str, done: int = 0, total: int = 0) -> None:
        self.check_cancel()
        self._stage = stage
        self._emit(ConversionProgress(stage, done, total))

    def step(self, done: int, total: int) -> None:
        """One unit of work finished (throttled); also the cooperative cancel point."""
        self.check_cancel()
        now = time.monotonic()
        if now - self._last >= _MIN_INTERVAL:
            self._emit(ConversionProgress(self._stage, done, total))

    def log(self, message: str) -> None:
        """A human line. ``warning: ...`` lines are also returned in the result's warnings."""
        if message.startswith("warning: "):
            self.warnings.append(message[len("warning: ") :])
        self._emit(ConversionProgress(self._stage, 0, 0, message))

    def _emit(self, event: ConversionProgress) -> None:
        self._last = time.monotonic()
        if self.progress is not None:
            self.progress(event)

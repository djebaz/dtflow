"""Read real Grid viewport positions for native Windows pywinauto benchmark runs.

The visible range is calculated by DTFlow's GalleryPager, so it represents
image positions rather than wheel messages, pixel distances or batch offsets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_VISIBLE_RANGE = re.compile(r"(?<!\d)([\d,]+)\s*[-–—]\s*([\d,]+)\s+of\s+([\d,]+)(?!\d)")


@dataclass(frozen=True)
class GridPosition:
    first: int
    last: int
    total: int
    source: str

    def valid(self) -> bool:
        return self.total > 0 and 1 <= self.first <= self.last <= self.total


def parse_visible_range(text: str, *, source: str = "uia") -> GridPosition | None:
    """Parse the actual pager text, e.g. `1–24 of 1,200`."""

    # Accept only the range label itself, never a concatenated UIA subtree
    # containing a matching total or a separate "200 buffered" status.
    match = _VISIBLE_RANGE.fullmatch(text.strip())
    if not match:
        return None
    numbers = [int(part.replace(",", "")) for part in match.groups()]
    position = GridPosition(*numbers, source)
    return position if position.valid() else None




class GridProgressProbe:
    """Read the actual N/total status-bar text through UI Automation.

    Qt QLabel objectName is not guaranteed to appear as an AutomationId on
    Windows. Resolve the uniquely formatted status label by its UIA Name,
    cache the control, and re-resolve it if the wrapper becomes stale.
    """

    def __init__(self, window, logs: Path):
        self.window = window
        del logs
        self._control = None
        self.last: GridPosition | None = None
        self.last_error: str | None = None

    @staticmethod
    def _parse(value: str) -> GridPosition | None:
        match = re.fullmatch(r"([0-9]+)/([0-9]+)", value.strip())
        if match is None:
            return None
        first, total = (int(part) for part in match.groups())
        if not 1 <= first <= total:
            return None
        return GridPosition(first, first, total, "grid-footer-uia")

    def read(self) -> GridPosition | None:
        try:
            # Name is dynamic, but the compact raw N/total format is unique to
            # the status widget and does not depend on Qt exposing objectName.
            if self._control is None:
                control = self.window.child_window(
                    title_re=r"^[0-9]+/[0-9]+$", control_type="Text"
                )
                if not control.exists(timeout=0):
                    self.last_error = "status_label_not_found"
                    return None
                self._control = control
            control = self._control
            value = control.element_info.name or ""
            position = self._parse(value)
            if position is None:
                # Some UIA wrappers expose QLabel text via window_text() even
                # when the cached accessible Name is delayed.
                position = self._parse(control.window_text())
            if position is None:
                self.last_error = "status_label_value_invalid"
                self._control = None
                return None
            self.last_error = None
            self.last = position
            return position
        except (AttributeError, LookupError, RuntimeError, OSError) as exc:
            self.last_error = "uia_read_" + type(exc).__name__
            self._control = None
            return None

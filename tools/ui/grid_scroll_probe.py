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
    """Read the dedicated read-only status input through Windows UI Automation.

    The Qt QLineEdit has the stable objectName dtflowStatusGridFirstVisibleIndex.
    Use only its Edit control and ValuePattern; no text-label or log fallback.
    """

    def __init__(self, window, logs: Path):
        self.window = window
        del logs
        self._control = None
        self.last: GridPosition | None = None
        self.last_error: str | None = None

    @staticmethod
    def _parse(value: str) -> GridPosition | None:
        match = re.fullmatch(r"([0-9]+)/([0-9]+)", value)
        if match is None:
            return None
        first, total = (int(part) for part in match.groups())
        if not 1 <= first <= total:
            return None
        return GridPosition(first, first, total, "grid-footer-uia")

    def read(self) -> GridPosition | None:
        try:
            if self._control is None:
                candidate = self.window.child_window(
                    auto_id="dtflowStatusGridFirstVisibleIndex",
                    control_type="Edit",
                )
                if not candidate.exists(timeout=0):
                    self.last_error = "status_input_not_found"
                    return None
                self._control = candidate
            value = self._control.iface_value.CurrentValue
            position = self._parse(str(value))
            if position is None:
                self.last_error = "status_input_value_invalid"
                return None
            self.last = position
            self.last_error = None
            return position
        except (AttributeError, LookupError, RuntimeError, OSError) as exc:
            self.last_error = "status_input_read_" + type(exc).__name__
            self._control = None
            return None

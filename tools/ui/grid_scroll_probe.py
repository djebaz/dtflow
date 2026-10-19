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
    """Read the dedicated bottom-bar index, with no alternate UIA selector.

    dtflowStatusGridFirstVisibleIndex is the canonical Qt objectName. We retain
    a single cached UIA wrapper to avoid enumerating the full window tree.
    """

    def __init__(self, window, logs: Path):
        self.window = window
        del logs
        self._control = None
        self.last: GridPosition | None = None

    def read(self) -> GridPosition | None:
        try:
            if self._control is None:
                control = self.window.child_window(
                    auto_id="dtflowStatusGridFirstVisibleIndex", control_type="Text"
                )
                if not control.exists(timeout=0):
                    return None
                self._control = control
            name = self._control.element_info.name or ""
            match = re.fullmatch(r"([0-9]+)/([0-9]+)", name)
            if match is None:
                return None
            first = int(match.group(1))
            total = int(match.group(2))
            if first < 1 or total < first:
                return None
            position = GridPosition(first, first, total, "grid-footer-uia")
            self.last = position
            return position
        except (AttributeError, LookupError, RuntimeError, OSError):
            self._control = None
            return None

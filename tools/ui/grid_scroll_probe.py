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
    """Read the canonical Qt pager range using a targeted, cached UIA selector.

    Qt objectName 'galleryPagerRange' is the preferred AutomationId. Some
    Windows Qt builds expose only the dynamic accessible name, so a unique
    title regex is the fallback. Never walk the full window tree per sample.
    """

    def __init__(self, window, logs: Path):
        self.window = window
        del logs  # Kept for benchmark API compatibility.
        self._control = None
        self.last: GridPosition | None = None

    @staticmethod
    def _position(control) -> GridPosition | None:
        name = control.element_info.name or ""
        if name.startswith("Visible image range:"):
            value = name.split(":", 1)[1].strip()
            parsed = parse_visible_range(value)
            if parsed is not None:
                return parsed
        return parse_visible_range(control.window_text())

    def read(self) -> GridPosition | None:
        if self._control is not None:
            try:
                position = self._position(self._control)
                if position is not None:
                    self.last = position
                    return position
            except (AttributeError, LookupError, RuntimeError, OSError):
                self._control = None

        for selector in (
            {"auto_id": "galleryPagerRange", "control_type": "Text"},
            {"title_re": r"^Visible image range: ", "control_type": "Text"},
        ):
            try:
                candidate = self.window.child_window(**selector)
                if not candidate.exists(timeout=0):
                    continue
                position = self._position(candidate)
                if position is None:
                    continue
                self._control = candidate
                self.last = position
                return position
            except (AttributeError, LookupError, RuntimeError, OSError):
                continue
        return None

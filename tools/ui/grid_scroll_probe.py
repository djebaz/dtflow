"""Read real Grid viewport positions for native Windows pywinauto benchmark runs.

The visible range is calculated by DTFlow's GalleryPager, so it represents
image positions rather than wheel messages, pixel distances or batch offsets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_VISIBLE_RANGE = re.compile(r"(?<!\d)([\d,]+)\s*[-–—]\s*([\d,]+)\s+of\s+([\d,]+)(?!\d)")
_LOG_VIEW = re.compile(
    r"gallery_perf phase=benchmark_view\b[^\n]*\bfirst=(\d+)\b[^\n]*"
    r"\blast=(\d+)\b[^\n]*\btotal=(\d+)\b"
)


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


def parse_benchmark_view(text: str) -> GridPosition | None:
    """Read the most recent position published by the benchmark-only pager."""

    matches = list(_LOG_VIEW.finditer(text))
    if not matches:
        return None
    latest = matches[-1]
    position = GridPosition(*(int(value) for value in latest.groups()), "pager-log")
    return position if position.valid() else None


class GridProgressProbe:
    """Read the gallery's actual visible range via its live UIA Text element.

    Qt may expose accessibleName instead of QLabel text. The pager publishes
    its changing range in accessibleName, so reacquire the live Text control
    rather than caching an outdated element or parsing obsolete text logs.
    """

    def __init__(self, window, logs: Path):
        self.window = window
        self.logs = logs  # signature retained for the benchmark callers
        self.last: GridPosition | None = None

    def read(self) -> GridPosition | None:
        matches = []
        try:
            for control in self.window.descendants():
                info = control.element_info
                if info.control_type != "Text":
                    continue
                name = info.name or ""
                if not name.startswith("Visible image range"):
                    continue
                # The dynamically published accessible name is authoritative.
                value = name.removeprefix("Visible image range").lstrip(": ").strip()
                position = parse_visible_range(value)
                if position is None:
                    try:
                        position = parse_visible_range(control.window_text())
                    except (AttributeError, RuntimeError, ValueError):
                        pass
                if position is not None:
                    matches.append(position)
        except (LookupError, RuntimeError, OSError):
            return None
        if len(matches) != 1:
            return None  # never guess among duplicated/stale controls
        self.last = matches[0]
        return self.last

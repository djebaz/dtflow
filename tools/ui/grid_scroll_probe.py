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

    match = _VISIBLE_RANGE.search(text)
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
    """Prefer the recorded UIA Text control; fall back to benchmark pager logs.

    Windows UIA sometimes exposes the QLabel's accessibleName (the static
    'Visible image range') instead of its changing displayed text. Fallback
    reads the same pager values emitted in a benchmark child, not estimated
    wheel distances. No click or mutation of the control is required.
    """

    def __init__(self, window, logs: Path):
        self.logs = logs
        self._label = None
        try:
            self._label = window.child_window(title="Visible image range", control_type="Text")
        except (AttributeError, LookupError, RuntimeError):
            pass
        self.last: GridPosition | None = None

    def _uia(self) -> GridPosition | None:
        if self._label is None:
            return None
        label = self._label
        values = []
        try:
            values.append(label.window_text())
        except Exception:
            pass
        try:
            values.append(label.iface_text.DocumentRange.GetText(-1))
        except Exception:
            pass
        try:
            values.append(label.legacy_properties().get("Value", ""))
        except Exception:
            pass
        for text in values:
            match = parse_visible_range(str(text))
            if match:
                return match
        return None

    def _log(self) -> GridPosition | None:
        paths = []
        try:
            for path in self.logs.glob("dtflow-*.log*"):
                if "-gallery" not in path.name and path.is_file():
                    paths.append((path.stat().st_mtime_ns, path))
        except OSError:
            return None
        for _timestamp, path in sorted(paths, reverse=True):
            try:
                with path.open("rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 131_072))
                    content = stream.read().decode("utf-8", "replace")
            except OSError:
                continue
            position = parse_benchmark_view(content)
            if position:
                return position
        return None

    def read(self) -> GridPosition | None:
        position = self._uia() or self._log()
        if position is not None:
            self.last = position
        return position

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

    The observed Windows UIA AutomationId includes the complete Qt parent path.
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
                    auto_id="QApplication.MainWindow.statusbar.dtflowGridPositionGroup.dtflowStatusGridFirstVisibleIndex",
                    control_type="Edit",
                )
                if not candidate.exists(timeout=0):
                    self.last_error = "status_input_not_found"
                    return None
                # Resolve the WindowSpecification once. Keeping the spec would
                # repeat the potentially slow UIA search on every read.
                self._control = candidate.wrapper_object()
            value = self._control.get_value()
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

    def diagnose(self) -> dict:
        """Inspect only relevant UIA properties after a failed seek, not per pulse.

        Do not include arbitrary application input values in benchmark logs.
        """
        result = {"last_error": self.last_error, "matching_controls": []}
        try:
            for widget in self.window.descendants():
                info = widget.element_info
                ident = info.automation_id or ""
                kind = info.control_type or ""
                name = info.name or ""
                if ident != "dtflowStatusGridFirstVisibleIndex" and not (
                    kind == "Edit" and re.fullmatch(r"[0-9]+/[0-9]+", name)
                ):
                    continue
                result["matching_controls"].append(
                    {
                        "automation_id": ident,
                        "control_type": kind,
                        "name": name if re.fullmatch(r"[0-9]+/[0-9]+", name) else "<other>",
                        "value_pattern": self._value_pattern_status(widget),
                    }
                )
                if len(result["matching_controls"]) == 8:
                    break
        except Exception as exc:
            result["diagnostic_error"] = type(exc).__name__
        return result

    @staticmethod
    def _value_pattern_status(widget) -> str:
        try:
            # No untrusted text is logged; only pattern availability.
            return "available" if widget.iface_value is not None else "missing"
        except Exception as exc:
            return type(exc).__name__

"""Drive the actual DTFlow Images UI on a native Windows desktop and compare diagnostics.

Requires explicit acknowledgement of the existing saved-output catalogue and a
pinned, optional pywinauto overlay. No generation, export or deletion is driven.
Run --help on any platform; real automation is Windows-only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from tools.ui.gallery_perf_launch import validate_profile

# Each trial differs from production defaults in just one family.
DEFAULT_PROFILES = {
    "baseline": {},
    "workers-3": {"thumbnail_workers": 3},
    "lookahead-1": {"lookahead_screens": 1},
    "preparation-2ms": {"preparation_budget_ms": 2},
    "mount-2ms": {"mount_budget_ms": 2},
    "thumbnails-480px": {"thumbnail_side_px": 480},
    "pending-writes-64": {"pending_cache_writes": 64},
    "cache-prune-earlier": {"cache_entries": 10000, "cache_prune_to": 8000, "cache_prune_every": 250},
    "refresh-10s": {"refresh_ms": 10000, "full_refresh_s": 60},
    "debounce-faster": {"filter_debounce_ms": 150, "save_debounce_ms": 125, "page_jump_debounce_ms": 20},
}
DEFAULT_GRID_MATRIX = ((20, 50), (20, 100), (50, 150), (100, 300), (200, 600), (300, 1000))
# Keep the product's actual batch-rounding semantics for each case.
GRID_MAX_LOAD = 300
GRID_MAX_CARDS = 1000

SCROLL_INTENT = re.compile(r"gallery_perf phase=scroll_intent\b[^\n]*\bdirection=(forward|backward)\b[^\n]*\bsource=wheel\b")
QUERY_DONE = re.compile(r"gallery_perf phase=query_done\b[^\n]*\breturned=(\d+)\b[^\n]*\berror=none\b")


def load_profiles(path: Path | None, names: list[str] | None) -> dict[str, dict]:
    """Load one-factor experiments or explicit benchmark profiles."""

    raw = DEFAULT_PROFILES if path is None else json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Profiles must be a non-empty mapping of names to overrides.")
    selected = list(raw) if names is None else names
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("Select at least one distinct profile.")
    profiles = {}
    for name in selected:
        if name not in raw:
            raise ValueError(f"Unknown profile: {name}")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,48}", name):
            raise ValueError(f"Unsafe profile name: {name}")
        profiles[name] = validate_profile(raw[name])
    return profiles


def parse_grid_matrix(specs: list[str]) -> list[tuple[int, int, int]]:
    """Resolve requested `-ml:-mc` cases without changing DTFlow defaults.

    The effective card cap rounds down to full batches. Reject empty,
    duplicate or excessive matrices before opening any desktop windows.
    """

    from dtflow.gallery_limits import resolve as resolve_gallery_limits

    raw = list(specs) if specs else [f"{ml}:{mc}" for ml, mc in DEFAULT_GRID_MATRIX]
    if len(raw) > 16:
        raise ValueError("Grid limit matrix may contain at most 16 cases.")
    combinations = []
    seen = set()
    for spec in raw:
        parts = spec.split(":")
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            raise ValueError(f"Invalid Grid limit pair {spec!r}; expected ML:MC.")
        ml, mc = (int(part) for part in parts)
        if not 1 <= ml <= GRID_MAX_LOAD or not 1 <= mc <= GRID_MAX_CARDS:
            raise ValueError(
                f"Grid limit pair must have -ml 1..{GRID_MAX_LOAD} and -mc 1..{GRID_MAX_CARDS}."
            )
        if ml > mc:
            raise ValueError("Grid -mc must be at least -ml for a meaningful cap comparison.")
        if (ml, mc) in seen:
            raise ValueError(f"Duplicate Grid limit pair: {spec}.")
        seen.add((ml, mc))
        _batch, effective_mc = resolve_gallery_limits(ml, mc)
        combinations.append((ml, mc, effective_mc))
    return combinations


def _grid_sweep_steps(ml: int, effective_mc: int) -> int:
    """Bounded sweep depth scales with the number of resident batches."""

    return min(12, max(4, (effective_mc + ml - 1) // ml + 2))


def _require_complete_first_batch(returned: int, ml: int) -> None:
    """Prevent incomplete catalogue coverage from being ranked as clean."""

    if returned < ml:
        raise ValueError(
            f"Only {returned} first-query images for -ml {ml}; "
            "this case cannot exercise a complete initial batch."
        )


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def summarize_csv(path: Path) -> dict:
    """Read the application's existing sanitized listener-side gallery CSV.

    These are aggregated diagnostic samples. Their P95 cannot be reconstructed
    as a global percentile, so only directly observed maxima and medians of
    recorded per-event means are reported.
    """

    metrics: dict[tuple[str, str], list[float]] = {}
    events: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"phase", "metric", "samples", "mean", "max"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("DTFlow gallery CSV schema is missing required fields.")
        for row in reader:
            phase = row["phase"]
            metric = row["metric"]
            count = int(row["samples"] or 1)
            events[phase] = events.get(phase, 0) + count
            number = _number(row["max"])
            if number is not None and metric:
                metrics.setdefault((phase, metric), []).append(number)

    def maximum(*keys: tuple[str, str]) -> float | None:
        found = [number for key in keys for number in metrics.get(key, [])]
        return max(found) if found else None

    def median(phase: str, metric: str) -> float | None:
        values = metrics.get((phase, metric), [])
        return round(statistics.median(values), 2) if values else None

    if not any(name in events for name in ("query_done", "adjacent_read")):
        raise ValueError("No successful gallery query diagnostics were recorded.")
    return {
        "query_events": events.get("query_done", 0),
        "adjacent_events": events.get("adjacent_read", 0),
        "gui_gap_over_50_max_ms": maximum(("gui_gaps", "wall_max_ms"), ("gui_gap", "elapsed_ms")),
        "query_done_median_recorded_ms": median("query_done", "elapsed_ms"),
        "prepare_median_recorded_ms": median("prepare_complete", "elapsed_ms"),
        "grid_mount_median_recorded_ms": median("grid_mount_complete", "elapsed_ms"),
        "thumbnail_median_recorded_ms": median("thumbnail_end_to_end_summary", "elapsed_ms"),
        "phase_counts": events,
    }


def _completed_queries(directory: Path) -> dict[tuple, int]:
    """Read successful completion records with identities stable across rotation."""

    records = {}
    for path in directory.glob("dtflow-*.log*"):
        if "-gallery" in path.name:
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                identity = os.fstat(stream.fileno())
                data = stream.read()
        except OSError:
            continue
        for match in QUERY_DONE.finditer(data):
            key = (identity.st_dev, identity.st_ino, match.start(), match.group(0))
            records[key] = int(match.group(1))
    return records


def _read_query_count(directory: Path) -> int:
    """Read live query completion records from the rotating text log."""

    return max(_completed_queries(directory).values(), default=0)


def _await_query(directory: Path, proc: subprocess.Popen, seconds: float, *, after: set | None = None) -> int:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError("DTFlow exited before the Images query completed.")
        returned = _read_query_count(directory) if after is None else max(
            (count for key, count in _completed_queries(directory).items() if after is None or key not in after),
            default=0,
        )
        if returned:
            return returned
        time.sleep(0.2)
    raise TimeoutError("No successful Images query appeared in the DTFlow development log.")


def _button(window, name: str, *, alternatives: tuple[str, ...] = (),
            automation_id: str | None = None, timeout: float = 12):
    """Resolve a unique visible Qt button by UIA name or automation ID.

    Qt may expose the QPushButton visual label instead of accessibleName.
    Checkable QPushButtons expose CheckBox, or RadioButton when auto-exclusive,
    rather than Button. Inspect all three roles in the same child window.
    Never click a guessed position when the UIA element cannot be resolved.
    """

    names = (name, *alternatives)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = []
        for button in window.descendants():
            info = button.element_info
            if info.control_type not in {"Button", "CheckBox", "RadioButton"}:
                continue
            if info.name in names or (automation_id is not None and info.automation_id == automation_id):
                if button.is_visible() and button.is_enabled():
                    matches.append(button)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"Ambiguous Qt UIA button: {name}")
        time.sleep(0.25)
    raise LookupError(
        f"Qt UIA button {name!r} missing, disabled or hidden after {timeout:g}s "
        "(searched Button/CheckBox/RadioButton roles)"
    )


def _click(window, name: str, actions: list[dict], *,
           alternatives: tuple[str, ...] = (), automation_id: str | None = None) -> None:
    started = time.perf_counter()
    _button(window, name, alternatives=alternatives, automation_id=automation_id).click_input()
    actions.append({"action": name, "dispatch_ms": round((time.perf_counter() - started) * 1000, 2)})


def _gallery_mode_button(window, mode: str, *, timeout: float = 6):
    """Resolve the actual Grid/List/Detail CheckBox inside the gallery header.

    The Windows recorder locates these controls under the unique
    HeaderScrollContents group. A bare `Detail` search across the entire
    window also matches unrelated UIA elements, so it must not be used.
    """

    if mode not in {"Grid", "List", "Detail"}:
        raise ValueError(f"Unknown gallery mode: {mode}")
    automation_id = f"gallery{mode}Button"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        all_controls = window.descendants()
        headers = [
            item for item in all_controls
            if item.element_info.name == "HeaderScrollContents"
            and item.element_info.control_type == "Group"
            and item.is_visible()
        ]
        matching = [
            control
            for header in headers
            for control in header.descendants()
            if control.element_info.name == mode
            and control.element_info.control_type == "CheckBox"
            and control.is_visible()
            and control.is_enabled()
        ]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            raise RuntimeError(f"Ambiguous {mode} CheckBox inside the gallery header.")

        # Some Qt/PySide6 builds omit the accessible parent group. In that
        # case use only the unique Qt object ID, never the ambiguous name.
        by_id = [
            item for item in all_controls
            if item.element_info.automation_id == automation_id
            and item.element_info.control_type in {"CheckBox", "Button", "RadioButton"}
            and item.is_visible() and item.is_enabled()
        ]
        if len(by_id) == 1:
            return by_id[0]
        if len(by_id) > 1:
            raise RuntimeError(f"Ambiguous gallery UIA automation ID: {automation_id}")
        time.sleep(0.2)
    raise LookupError(f"Cannot locate gallery {mode} CheckBox in HeaderScrollContents.")


def _click_gallery_mode(window, mode: str, actions: list[dict]) -> None:
    """Switch one gallery view through its own header control."""

    control = _gallery_mode_button(window, mode)
    started = time.perf_counter()
    control.click_input()
    actions.append({
        "action": mode,
        "dispatch_ms": round((time.perf_counter() - started) * 1000, 2),
        "uia_target": "HeaderScrollContents/" + mode + "/CheckBox",
    })


def _attempt(record: dict, step: str, action) -> bool:
    """Keep running after a missed UI click, wheel or drag; retain evidence."""

    record["step"] = step
    try:
        action()
        return True
    except Exception as exc:
        from dtflow.logging_setup import sanitize

        failure = {
            "step": step,
            "error_type": type(exc).__name__,
            "message": sanitize(str(exc))[:240],
        }
        record.setdefault("warnings", []).append(failure)
        print(
            f"WARNING {step}: {failure['error_type']}: {failure['message']}",
            file=sys.stderr, flush=True,
        )
        return False


def _click_page(window, number: int, actions: list[dict]) -> None:
    """Click a numbered page and verify Qt's checked-state transition."""

    name = f"Jump to image batch {number}"
    button = _button(window, name, automation_id=f"galleryPagerPage{number}", timeout=5)
    started = time.perf_counter()
    if button.get_toggle_state() != 1:
        button.click_input()
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        # Pager refresh may replace the original Qt chip after a page jump.
        # Re-discover it instead of trusting a stale UIA wrapper.
        try:
            active = _button(window, name, automation_id=f"galleryPagerPage{number}", timeout=1)
            selected = active.get_toggle_state() == 1
        except (LookupError, RuntimeError):
            selected = False
        if selected:
            actions.append({
                "action": name, "verified": True,
                "dispatch_ms": round((time.perf_counter() - started) * 1000, 2),
            })
            return
        time.sleep(0.1)
    raise TimeoutError(f"Page {number} did not become selected after the click.")


def _click_grid_page_one(window, actions: list[dict], *, at: str) -> None:
    """Physically click page 1 even when it is already selected."""

    name = "Jump to image batch 1"
    button = _button(window, name, automation_id="galleryPagerPage1", timeout=5)
    started = time.perf_counter()
    button.click_input()
    # The pager may recreate chips on a page jump; find a fresh wrapper.
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        try:
            selected = _button(window, name, automation_id="galleryPagerPage1", timeout=1)
            if selected.get_toggle_state() == 1:
                actions.append({
                    "action": "click-page-one-" + at,
                    "verified": True,
                    "dispatch_ms": round((time.perf_counter() - started) * 1000, 2),
                })
                return
        except (LookupError, RuntimeError):
            pass
        time.sleep(0.15)
    raise TimeoutError(f"Page 1 was clicked at {at} but did not become selected.")


def _wheel_intents(directory: Path) -> set[tuple]:
    """Use existing -x events to verify that the gallery received a wheel."""

    found = set()
    for path in directory.glob("dtflow-*.log*"):
        if "-gallery" in path.name:
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                identity = os.fstat(stream.fileno())
                data = stream.read()
        except OSError:
            continue
        for match in SCROLL_INTENT.finditer(data):
            found.add((identity.st_dev, identity.st_ino, match.start(), match.group(1)))
    return found


def _gallery_wheel_point(window, mode: str) -> tuple[tuple[int, int], str]:
    """Prefer the active gallery's UIA rectangle over the entire window center.

    Qt can expose QWidget objectName through UIA AutomationId, or only the
    accessibleName. Fall back to the window center only when no unique, visible
    scroll surface can be identified. Never move the pointer to a guessed
    element by name alone.
    """

    if mode not in {"grid", "list"}:
        raise ValueError(f"Unsupported scroll view: {mode}")
    object_name = "galleryGridScroll" if mode == "grid" else "galleryListTable"
    accessible_name = "" if mode == "grid" else "Image list; activate a row to open Detail"
    candidates = []
    for control in window.descendants():
        info = control.element_info
        if info.automation_id != object_name and (not accessible_name or info.name != accessible_name):
            continue
        if not control.is_visible() or not control.is_enabled():
            continue
        rectangle = control.rectangle()
        if rectangle.width() >= 80 and rectangle.height() >= 80:
            candidates.append(rectangle)
    if len(candidates) == 1:
        rect = candidates[0]
        source = object_name
        x_factor, y_factor = 0.5, 0.5
    else:
        # The gallery's QScrollArea sometimes lacks a UIA AutomationId.
        # Use the visible GalleryDetailPage group instead of the complete
        # DTFlow window; its lower center is within the browsing surface.
        panels = [
            item.rectangle() for item in window.descendants()
            if item.element_info.name == "GalleryDetailPage"
            and item.element_info.control_type in {"Group", "Pane"}
            and item.is_visible()
            and item.rectangle().width() >= 120
            and item.rectangle().height() >= 160
        ]
        if len(panels) == 1:
            rect, source = panels[0], "GalleryDetailPage-fallback"
            x_factor, y_factor = 0.52, 0.72
        else:
            rect, source = window.rectangle(), "window-fallback"
            x_factor, y_factor = 0.56, 0.72
    # Avoid the horizontal toolbar, bottom overlays and right scrollbar.
    coords = (
        int(rect.left + rect.width() * x_factor),
        int(rect.top + rect.height() * y_factor),
    )
    return coords, source


def _scroll(
    window, direction: int, actions: list[dict], directory: Path,
    *, mode: str, notches: int = 192,
) -> None:
    """Send enough real Windows wheel input to traverse multiple gallery rows.

    The application's scroll_intent event is produced only for wheel events
    delivered directly to a Qt viewport. Actual scrolling can also occur
    without that hook firing, so absence of this optional telemetry does NOT
    mean the user's wheel did not scroll the gallery.
    """

    from pywinauto import mouse

    if direction not in (-1, 1):
        raise ValueError("Scroll direction must be -1 or 1.")
    if not 12 <= notches <= 240:
        raise ValueError("Scroll notches must be in 12..240.")
    coords, source = _gallery_wheel_point(window, mode)
    before = _wheel_intents(directory)
    started = time.perf_counter()
    # Small, spaced bursts let Qt process prefetch and paint between wheel
    # messages. Default 192 notches traverses twice the previous sweep.
    remaining = notches
    bursts = 0
    while remaining > 0:
        count = min(12, remaining)
        mouse.scroll(coords=coords, wheel_dist=direction * count)
        remaining -= count
        bursts += 1
        if remaining:
            time.sleep(0.12)
    wanted = "backward" if direction > 0 else "forward"
    # Listener-side logging is asynchronous. This is evidence when available,
    # not an acceptance gate: Qt may scroll via child widgets / scrollbars.
    time.sleep(0.25)
    observed = any(item[3] == wanted for item in _wheel_intents(directory) - before)
    actions.append({
        "action": "scroll-up" if direction > 0 else "scroll-down",
        "mode": mode,
        "notches": notches,
        "bursts": bursts,
        "wheel_target": source,
        "wheel_intent_observed": observed,
        "scroll_distance_verified": False,
        "dispatch_ms": round((time.perf_counter() - started) * 1000, 2),
    })



def _toggle_fullscreen(window, actions: list[dict]) -> None:
    """Enter via the actual Qt checkbox; Escape restores the normal window."""

    button = _button(
        window, "Full screen image view",
        automation_id="galleryFocusButton", timeout=5,
    )
    started = time.perf_counter()
    button.click_input()
    actions.append({
        "action": "fullscreen-enter",
        "dispatch_ms": round((time.perf_counter() - started) * 1000, 2),
    })
    time.sleep(0.5)
    # The fullscreen layout hides the header containing its original toggle,
    # so do not try to click that now-hidden control a second time.
    started = time.perf_counter()
    window.type_keys("{ESC}")
    actions.append({
        "action": "fullscreen-exit-esc",
        "dispatch_ms": round((time.perf_counter() - started) * 1000, 2),
    })
    time.sleep(0.5)


def _move_thumbnail_slider(window, actions: list[dict]) -> None:
    """Sweep thumbnail density from small to large and restore the initial value.

    Two distinct targets deliberately exercise major Qt geometry changes;
    the original value is restored even if a drag fails halfway through.
    """

    from pywinauto import mouse

    candidates = [
        control for control in window.descendants()
        if control.element_info.control_type == "Slider"
        and (control.element_info.name == "ThumbnailSizeSlider"
             or control.element_info.automation_id == "galleryThumbnailSizeSlider")
        and control.is_visible() and control.is_enabled()
    ]
    if len(candidates) != 1:
        raise LookupError(f"Expected one visible ThumbnailSizeSlider, found {len(candidates)}.")
    slider = candidates[0]
    # The real QSlider uses 25..250 (%). Do not infer its value from pixels:
    # observe UIA RangeValue and try to return to the owner's starting value.
    interface = slider.iface_range_value
    original = float(interface.CurrentValue)
    if not 25 <= original <= 250:
        raise ValueError("Thumbnail slider returned a value outside its Qt 25..250 range.")
    rect = slider.rectangle()
    if rect.width() < 60 or rect.height() < 8:
        raise RuntimeError("Thumbnail slider rectangle is too small to drag safely.")

    def point(value: float) -> tuple[int, int]:
        # Allow for the handle at both ends of the Qt slider groove.
        x = rect.left + 12 + (rect.width() - 24) * (value - 25) / 225
        return int(round(x)), int((rect.top + rect.bottom) / 2)

    def drag(start: float, end: float) -> None:
        mouse.press(coords=point(start))
        try:
            mouse.move(coords=point(end))
            time.sleep(0.2)
        finally:
            mouse.release(coords=point(end))

    # Two substantially different sizes rather than the former one small
    # movement (e.g. 131 -> 80). Reflow settles between the two sweeps.
    targets = (210.0, 55.0) if abs(original - 55.0) < 10 else (55.0, 210.0)
    try:
        for target in targets:
            before = float(interface.CurrentValue)
            started = time.perf_counter()
            drag(before, target)
            after = float(interface.CurrentValue)
            if abs(after - before) < 10:
                raise RuntimeError(
                    f"Thumbnail slider failed to move meaningfully (started={before:g}, after={after:g})."
                )
            actions.append({
                "action": "thumbnail-slider-drag",
                "before": before,
                "target": target,
                "after": after,
                "dispatch_ms": round((time.perf_counter() - started) * 1000, 2),
                "verified": True,
            })
            time.sleep(0.8)
    finally:
        current = float(interface.CurrentValue)
        if abs(current - original) >= 1:
            drag(current, original)
        restored = float(interface.CurrentValue)
        if abs(restored - original) > 5:
            raise RuntimeError(
                f"Thumbnail slider could not restore its initial value (started={original:g}, restored={restored:g})."
            )



def _reset_gallery_sort(window, directory: Path, proc: subprocess.Popen, record: dict, *, timeout: float) -> None:
    """Exercise the real Sort dropdown to discard the persisted page/scroll position."""

    record["step"] = "find-sort-dropdown"
    combo = window.child_window(title="Sort saved outputs", control_type="ComboBox")
    combo.wait("exists visible enabled", timeout=timeout)
    setup = record.setdefault("setup_actions", [])
    # Always end on Newest first, with at least one actual selection change.
    # Selecting an already-current value alone would not reset the saved offset.
    for label in ("Oldest first", "Newest first"):
        if combo.selected_text() == label:
            continue
        record["step"] = f"setup-sort-{label}"
        previous = set(_completed_queries(directory))
        started = time.perf_counter()
        # Owner recording confirms ComboBox -> List -> ListItem after a real
        # click opens the Qt popup. Wait for that exact item inside this combo;
        # do not rely on UIA SetFocus, immediate select(), or desktop-wide search.
        combo.click_input()
        option = combo.child_window(title=label, control_type="ListItem")
        option.wait("exists visible enabled", timeout=timeout)
        option.click_input()
        deadline = time.monotonic() + timeout
        while combo.selected_text() != label:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Sort popup selection did not reach {label!r}.")
            time.sleep(0.1)
        setup.append({"action": "sort", "value": label,
                      "dispatch_ms": round((time.perf_counter() - started) * 1000, 2)})
        record["step"] = f"await-sort-query-{label}"
        _await_query(directory, proc, timeout, after=previous)
        if combo.selected_text() != label:
            raise RuntimeError(f"Sort dropdown did not retain {label!r}.")
    record["step"] = "verify-page-one-after-sort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        first = _button(window, "Jump to image batch 1", automation_id="galleryPagerPage1", timeout=remaining)
        if first.get_toggle_state() == 1:
            return
        time.sleep(0.1)
    raise TimeoutError("Sort change did not reset the gallery to page 1.")


def _await_app_pid(path: Path, proc: subprocess.Popen, seconds: float) -> int:
    """Read the child-published PID, never guess a window from another trial."""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"DTFlow launcher exited before PID readiness (exit={proc.returncode}).")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            time.sleep(0.1)
            continue
        pid = data.get("pid") if isinstance(data, dict) else None
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("Invalid DTFlow child PID report.")
        return pid
    raise TimeoutError("DTFlow interpreter did not publish its PID before the readiness timeout.")


def _stop_trial_process(proc: subprocess.Popen) -> None:
    """Stop this live launcher's tree, including Windows venv redirector children."""

    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, timeout=10, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            # A concurrent normal exit is harmless; never hide an actual failure
            # to stop the tree by killing only its redirector and claiming success.
            if proc.poll() is None:
                raise RuntimeError(f"Trial process-tree cleanup failed (exit={result.returncode}).")
        proc.wait(timeout=5)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _drive(
    proc: subprocess.Popen, directory: Path, record: dict, *,
    cycles: int, timeout: float, scroll_notches: int = 192,
    grid_limits: tuple[int, int] | None = None,
) -> int:
    """Attach only to the launched child and track the failing UI step."""

    from pywinauto import Desktop
    from pywinauto.timings import TimeoutError as UIATimeoutError

    actions = record["actions"]
    record["launcher_pid"] = proc.pid
    record["step"] = "await-child-pid"
    app_pid = _await_app_pid(directory.parent / "app-process.json", proc, timeout)
    record["app_pid"] = app_pid
    record["step"] = "find-dtflow-window"
    window = Desktop(backend="uia").window(title="DTFlow", process=app_pid)
    try:
        window.wait("exists visible enabled", timeout=timeout)
    except (TimeoutError, UIATimeoutError) as exc:
        raise TimeoutError(
            f"DTFlow UIA window not ready (launcher_pid={proc.pid}, app_pid={app_pid})."
        ) from exc
    record["step"] = "focus-dtflow-window"
    window.set_focus()
    record["step"] = "find-images-navigation"
    nav = _button(
        window, "Show saved Images", alternatives=("Images",),
        automation_id="imagesNavigationButton",
    )
    record["step"] = "click-images-navigation"
    started = time.perf_counter()
    nav.click_input()
    actions.append({"action": "Show saved Images", "dispatch_ms": round((time.perf_counter() - started) * 1000, 2)})
    record["step"] = "verify-images-workspace"
    _button(window, "Grid", automation_id="galleryGridButton", timeout=timeout)
    record["step"] = "await-first-images-query"
    initial_count = _await_query(directory, proc, timeout)
    record["initial_query_records"] = initial_count
    _attempt(
        record, "setup-grid",
        lambda: _click_gallery_mode(window, "Grid", record.setdefault("setup_actions", [])),
    )
    if grid_limits is not None:
        # The dedicated limit sweep never opens or modifies the Sort dropdown.
        # Page 1 is physically clicked both before and after scrolling.
        ml, effective_mc = grid_limits
        record["initial_batch_complete"] = initial_count >= ml
        if initial_count < ml:
            _attempt(
                record, "insufficient-images-for-full-batch",
                lambda: _require_complete_first_batch(initial_count, ml),
            )
        _attempt(record, "grid-page-one-start", lambda: _click_grid_page_one(
            window, actions, at="start",
        ))
        steps = _grid_sweep_steps(ml, effective_mc)
        # More mounted batches need more wheel steps to traverse the buffer.
        # Individual bursts remain short enough for Qt to run its event loop.
        per_step_notches = max(12, scroll_notches // 3)
        record["grid_sweep"] = {
            "steps_per_direction": steps,
            "notches_per_step": per_step_notches,
            "passes": cycles,
            "max_planned_notches_per_direction": steps * per_step_notches * cycles,
            "scroll_distance_verified": False,
        }
        for cycle in range(cycles):
            prefix = f"cycle-{cycle + 1}"
            for direction, label in ((-1, "down"), (1, "up")):
                for step in range(1, steps + 1):
                    if proc.poll() is not None:
                        raise RuntimeError("DTFlow exited during the Grid limit matrix sweep.")
                    _attempt(
                        record, f"{prefix}-grid-scroll-{label}-{step}",
                        lambda d=direction: _scroll(
                            window, d, actions, directory,
                            mode="grid", notches=per_step_notches,
                        ),
                    )
                    time.sleep(0.25)
        _attempt(record, "grid-page-one-end", lambda: _click_grid_page_one(
            window, actions, at="end",
        ))
        record["step"] = "close-dtflow-window"
        window.close()
        record["step"] = "wait-for-shutdown"
        return initial_count
    _attempt(
        record, "setup-sort",
        lambda: _reset_gallery_sort(window, directory, proc, record, timeout=timeout),
    )
    _attempt(
        record, "verify-page-two",
        lambda: _button(window, "Jump to image batch 2", automation_id="galleryPagerPage2", timeout=4),
    )
    for cycle in range(cycles):
        prefix = f"cycle-{cycle + 1}"
        _attempt(record, f"{prefix}-grid", lambda: _click_gallery_mode(window, "Grid", actions))
        _attempt(record, f"{prefix}-grid-scroll-down", lambda: _scroll(
            window, -1, actions, directory, mode="grid", notches=scroll_notches,
        ))
        time.sleep(0.8)
        _attempt(record, f"{prefix}-grid-scroll-up", lambda: _scroll(
            window, 1, actions, directory, mode="grid", notches=scroll_notches,
        ))
        time.sleep(0.8)
        _attempt(record, f"{prefix}-page-two", lambda: _click_page(window, 2, actions))
        time.sleep(0.8)
        _attempt(record, f"{prefix}-page-one", lambda: _click_page(window, 1, actions))
        time.sleep(0.8)
        _attempt(record, f"{prefix}-slider", lambda: _move_thumbnail_slider(window, actions))
        time.sleep(0.5)
        # Fullscreen must be entered from Grid, not after switching to Detail.
        _attempt(record, f"{prefix}-grid-fullscreen", lambda: _toggle_fullscreen(window, actions))
        time.sleep(0.5)
        _attempt(record, f"{prefix}-list", lambda: _click_gallery_mode(window, "List", actions))
        _attempt(record, f"{prefix}-list-scroll-down", lambda: _scroll(
            window, -1, actions, directory, mode="list", notches=scroll_notches,
        ))
        time.sleep(0.8)
        _attempt(record, f"{prefix}-list-scroll-up", lambda: _scroll(
            window, 1, actions, directory, mode="list", notches=scroll_notches,
        ))
        time.sleep(0.8)
        _attempt(record, f"{prefix}-detail", lambda: _click_gallery_mode(window, "Detail", actions))
        time.sleep(0.5)
        _attempt(record, f"{prefix}-return-grid", lambda: _click_gallery_mode(window, "Grid", actions))
        time.sleep(0.8)
    record["step"] = "close-dtflow-window"
    window.close()
    record["step"] = "wait-for-shutdown"
    return initial_count


def run_trial(name: str, profile: dict, *, trial: int, directory: Path, cycles: int,
              timeout: float, no_cache: bool, scroll_notches: int = 192,
              grid_limits: tuple[int, int, int] | None = None) -> dict:
    """Launch one source GUI process and retain evidence even if automation fails."""

    directory.mkdir(parents=True, exist_ok=False)
    logs = directory / "logs"
    logs.mkdir()
    profile_path = directory / "profile.json"
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    args = [
        sys.executable, "-m", "tools.ui.gallery_perf_launch",
        "--profile", str(profile_path), "--logs", str(logs),
        "--pid-file", str(directory / "app-process.json"),
    ]
    if grid_limits is not None:
        args.extend(["--ml", str(grid_limits[0]), "--mc", str(grid_limits[1])])
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    record: dict = {
        "profile_name": name, "profile": profile, "trial": trial, "no_cache": no_cache,
        "status": "failed", "actions": [], "warnings": [], "error_type": None, "error_message": None,
        "step": "not-started",
        "initial_query_records": None,
        "benchmark_mode": "grid-matrix" if grid_limits is not None else "profiles",
        "requested_ml": grid_limits[0] if grid_limits else 20,
        "requested_mc": grid_limits[1] if grid_limits else 100,
        "effective_mc": grid_limits[2] if grid_limits else 100,
    }
    process = None
    stderr = directory / "app-stderr.txt"
    with stderr.open("w", encoding="utf-8") as output:
        try:
            record["step"] = "launch-child-process"
            process = subprocess.Popen(args, cwd=Path(__file__).resolve().parents[2],
                                       env=env, stdout=subprocess.DEVNULL, stderr=output)
            record["initial_query_records"] = _drive(
                process, logs, record, cycles=cycles, timeout=timeout,
                scroll_notches=scroll_notches,
                grid_limits=(grid_limits[0], grid_limits[2]) if grid_limits else None,
            )
            process.wait(timeout=timeout)
            csvs = sorted(logs.glob("dtflow-*-gallery.csv"))
            if len(csvs) != 1:
                raise ValueError("Expected exactly one session gallery CSV.")
            record["metrics"] = summarize_csv(csvs[0])
            record["status"] = "warning" if record.get("warnings") else "ok"
        except (Exception, KeyboardInterrupt) as exc:
            from dtflow.logging_setup import sanitize

            record["error_type"] = type(exc).__name__
            record["error_message"] = sanitize(str(exc))[:240]
            print(
                f"{name} trial={trial} failed at {record['step']}: "
                f"{record['error_type']}: {record['error_message']}", file=sys.stderr, flush=True,
            )
        finally:
            if process is not None:
                try:
                    _stop_trial_process(process)
                except Exception as exc:
                    record["status"] = "failed"
                    record["cleanup_error_type"] = type(exc).__name__
                    print(f"{name} trial={trial} process cleanup failed: {type(exc).__name__}",
                          file=sys.stderr, flush=True)
            (directory / "trial.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def write_report(records: list[dict], out: Path) -> None:
    """Emit inspectable raw trials and a comparison that never ranks missing data."""

    columns = (
        "case", "trial", "status", "step", "warning_count", "benchmark_mode",
        "requested_ml", "requested_mc", "effective_mc",
        "initial_batch_complete", "initial_query_records", "gui_gap_over_50_max_ms",
        "query_done_median_recorded_ms", "prepare_median_recorded_ms",
        "grid_mount_median_recorded_ms", "adjacent_events", "error_type",
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            metrics = record.get("metrics", {})
            writer.writerow({
                "case": record["profile_name"],
                "trial": record["trial"],
                "status": record["status"],
                "step": record.get("step"),
                "warning_count": len(record.get("warnings", [])),
                "benchmark_mode": record.get("benchmark_mode", "profiles"),
                "requested_ml": record.get("requested_ml", 20),
                "requested_mc": record.get("requested_mc", 100),
                "effective_mc": record.get("effective_mc", 100),
                "initial_batch_complete": record.get("initial_batch_complete"),
                "initial_query_records": record.get("initial_query_records"),
                "gui_gap_over_50_max_ms": metrics.get("gui_gap_over_50_max_ms"),
                "query_done_median_recorded_ms": metrics.get("query_done_median_recorded_ms"),
                "prepare_median_recorded_ms": metrics.get("prepare_median_recorded_ms"),
                "grid_mount_median_recorded_ms": metrics.get("grid_mount_median_recorded_ms"),
                "adjacent_events": metrics.get("adjacent_events"),
                "error_type": record.get("error_type"),
            })


    # Cross-trial comparison: keep missing >50 ms heartbeat gaps as unknown
    # observations rather than inventing a zero-millisecond latency.
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(record["profile_name"], []).append(record)
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "profile", "trials", "successful", "observed_gap_max_ms",
            "median_recorded_query_ms", "notes",
        ))
        writer.writeheader()
        for name, trials in grouped.items():
            good = [trial for trial in trials if trial["status"] == "ok"]
            gaps = [
                trial["metrics"]["gui_gap_over_50_max_ms"]
                for trial in good
                if trial["metrics"].get("gui_gap_over_50_max_ms") is not None
            ]
            queries = [
                trial["metrics"]["query_done_median_recorded_ms"]
                for trial in good
                if trial["metrics"].get("query_done_median_recorded_ms") is not None
            ]
            writer.writerow({
                "profile": name,
                "trials": len(trials),
                "successful": len(good),
                "observed_gap_max_ms": max(gaps) if gaps else "",
                "median_recorded_query_ms": round(statistics.median(queries), 2) if queries else "",
                "notes": (
                    "incomplete trials" if len(good) != len(trials)
                    else "no >=50ms gap samples" if not gaps else "observed threshold-crossing gaps"
                ),
            })


def _create_run_directory(root: Path, *, now: datetime | None = None) -> Path:
    """Reserve a fresh local-time MMddHHmm run; never merge same-minute runs."""

    stamp = (now or datetime.now()).strftime("%m%d%H%M")
    directory = root.expanduser().resolve() / "gallery_bench_smoke" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def main(argv: list[str] | None = None) -> int:
    """Drive named performance profiles on an interactive Windows desktop."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("tmp"),
                        help="Output root (default: tmp); creates gallery_bench_smoke/MMddHHmm below it.")
    parser.add_argument("--profiles-file", type=Path,
                        help="JSON mapping profile names to overrides; otherwise use built-in one-factor profiles.")
    parser.add_argument("--profiles", nargs="+", metavar="NAME",
                        help="Only selected profiles from the file or built-in set (default: all).")
    parser.add_argument(
        "--grid-matrix", nargs="*", metavar="ML:MC",
        help="Grid-only scroll matrix. With no pairs uses 20:50 20:100 50:150 "
             "100:300 200:600 300:1000. Does not change Sort.",
    )
    parser.add_argument("--runs", type=int, default=2, help="Trials per profile (1–10).")
    parser.add_argument("--cycles", type=int, default=2, help="Repeated UI action sequences (1–10).")
    parser.add_argument("--scroll-notches", type=int, default=192,
                        help="Windows wheel notches per scroll direction, 12–240 (default: 192).")
    parser.add_argument("--timeout", type=float, default=30, help="Seconds for GUI/process readiness.")
    parser.add_argument("--no-cache", action="store_true", help="Bypass disposable thumbnail cache.")
    parser.add_argument("--ack-user-state", action="store_true",
                        help="Allow reading saved outputs and updating preferences/disposable cache.")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Native Windows is required; Linux/offscreen cannot establish UI acceptance.")
    if not args.ack_user_state:
        parser.error("--ack-user-state is required before accessing the saved-output catalogue.")
    if not 12 <= args.scroll_notches <= 240:
        parser.error("--scroll-notches must be between 12 and 240.")
    if not 1 <= args.runs <= 10 or not 1 <= args.cycles <= 10:
        parser.error("--runs and --cycles must be between 1 and 10.")
    if not 5 <= args.timeout <= 120:
        parser.error("--timeout must be between 5 and 120 seconds.")
    if args.grid_matrix is not None and (args.profiles is not None or args.profiles_file is not None):
        parser.error("--grid-matrix cannot be combined with tuning profiles.")
    try:
        if args.grid_matrix is not None:
            combinations = parse_grid_matrix(args.grid_matrix)
            profiles = {
                f"grid-ml{ml:03d}-mc{mc:04d}": (validate_profile({}), (ml, mc, effective))
                for ml, mc, effective in combinations
            }
        else:
            profiles = {
                name: (profile, None)
                for name, profile in load_profiles(args.profiles_file, args.profiles).items()
            }
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if len(profiles) * args.runs > 40:
        parser.error("Limit the matrix to 40 runs per invocation.")
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        parser.error("pywinauto missing; use uv run --frozen --with pywinauto==0.6.9 python -m ...")
    try:
        output = _create_run_directory(args.out)
    except FileExistsError:
        parser.error("Timestamped run directory already exists; wait until the next minute or choose another --out root.")
    except OSError as exc:
        parser.error(f"Could not create benchmark run directory ({type(exc).__name__}).")
    print(f"Benchmark output: {output}", flush=True)
    records = []
    try:
        for name, (profile, grid_limits) in profiles.items():
            for trial in range(1, args.runs + 1):
                directory = output / f"{name}-r{trial:02}"
                record = run_trial(name, profile, trial=trial, directory=directory,
                                   cycles=args.cycles, timeout=args.timeout, no_cache=args.no_cache,
                                   scroll_notches=args.scroll_notches, grid_limits=grid_limits)
                records.append(record)
                write_report(records, output)
                print(f"{name} trial={trial} status={record['status']}", flush=True)
                if record.get("cleanup_error_type"):
                    # Do not overlap trials when a prior GUI may still be alive.
                    return 1
    except KeyboardInterrupt:
        write_report(records, output)
        return 130
    return 0 if records and all(record["status"] == "ok" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())

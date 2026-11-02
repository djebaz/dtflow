"""Offline captures of the real Generate > Scripts Qt presentation.

Every capture uses MainWindow's shared shell, actual ScriptsPage widgets,
shared activity strip and preview-only state data. The presenter never
executes JavaScript, submits generation, contacts providers or changes real
durable script history. Reference parity needs owner visual verification.
"""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Callable

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication


STATES = (
    "scripts_empty",
    "scripts_imported_supported",
    "scripts_imported_unsupported",
    "scripts_inspect_partial",
    "scripts_form_default",
    "scripts_form_long_values",
    "scripts_queued",
    "scripts_cancelled_before_start",
    "scripts_running",
    "scripts_waiting_input",
    "scripts_stopped",
    "scripts_failed",
    "scripts_interrupted",
    "scripts_outcome_unknown",
    "scripts_form_after_unknown",
    "scripts_results",
    "scripts_from_inpaint",
    "scripts_editor_new",
    "scripts_editor_dirty",
    "scripts_editor_syntax_error",
    "scripts_editor_run_active",
    "scripts_editor_narrow",
    "scripts_narrow",
    "scripts_narrow_details",
)


def _shared_overlay(window, state: str) -> None:
    """Show real shared overlay widgets for the illustrative lifecycle states."""
    key = "scripts-fixture"
    window.progress_strip.finish(key)
    if state == "scripts_queued":
        window.progress_strip.start(key, "Script", lambda: None, stop_text="Cancel before start", action_text="Cancel")
        window.progress_strip.update(
            key,
            0,
            0,
            detail="Nothing submitted; waiting for admission.",
            elapsed="Waiting 0:42",
            stage="Queued behind 3 items",
        )
    elif state == "scripts_running":
        window.progress_strip.start(key, "Script running", lambda: None, stop_text="Stop waiting locally")
    elif state == "scripts_waiting_input":
        window.progress_strip.start(key, "Waiting for your input", lambda: None, stop_text="Stop waiting locally")
    elif state in ("scripts_stopped", "scripts_cancelled_before_start"):
        window.progress_strip.complete(
            key,
            "Stopped locally" if state == "scripts_stopped" else "Cancelled before start",
            "Offline render fixture",
            "stopped",
        )
    elif state == "scripts_failed":
        window.progress_strip.complete(key, "Script failed", "Inspect the durable failure details.", "error")
    elif state in ("scripts_interrupted", "scripts_outcome_unknown"):
        window.progress_strip.complete(
            key,
            "Script interrupted" if state == "scripts_interrupted" else "Outcome unknown",
            "An earlier attempt may still complete; do not replay unknown work.",
            "warning",
        )


def render_scripts_states(out: Path, *, window_factory: Callable, state_names=STATES) -> list[Path]:
    """Create independently isolated, screenshot-ready native Qt Scripts states."""
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("The canonical renderer must initialize QApplication and shared theme first.")
    out.mkdir(parents=True, exist_ok=True)
    results = []
    started = monotonic()
    print("[scripts] Building native MainWindow...", flush=True)
    window = window_factory()
    print(f"[scripts] MainWindow built: {monotonic() - started:.1f}s", flush=True)
    try:
        window.form.resize(1600, 1000)
        window.show()
        print(f"[scripts] Window shown: {monotonic() - started:.1f}s", flush=True)
        window._show_scripts()
        print(f"[scripts] Scripts mode mounted: {monotonic() - started:.1f}s", flush=True)
        for index, state in enumerate(state_names):
            if state not in STATES:
                raise ValueError(f"Unknown scripting render state: {state}")
            narrow = state in ("scripts_narrow", "scripts_narrow_details", "scripts_editor_narrow")
            window.form.resize(800, 680) if narrow else window.form.resize(1600, 1000)
            window.scripts._drawer_requested = state == "scripts_narrow_details"
            window.scripts.preview(state)
            window.scripts.sync_responsive()
            _shared_overlay(window, state)
            if index == 0:
                print(f"[scripts] First state prepared: {monotonic() - started:.1f}s", flush=True)
            # Wrapped rows post parent layout requests after their first resize.
            # Drain the bounded layout cascade before grabbing, not midway
            # through a height-for-width update. No timers, scripts or providers.
            for _ in range(5):
                app.processEvents()
            if index == 0:
                print(f"[scripts] First Qt event drain: {monotonic() - started:.1f}s", flush=True)
            if index == 0 or state == "scripts_narrow":
                # The left scroll disables horizontal scrolling; trace the
                # actual panel's right edge, not inner list/item padding.
                viewport = window.shell.leftScroll.viewport()
                stack = window.shell.leftStack
                panel = window.scripts.left
                right_edge = panel.mapTo(viewport, QPoint(panel.width(), 0)).x()
                print(
                    f"[scripts] {state} left viewport={viewport.width()} "
                    f"stack={stack.width()} panel={panel.width()} "
                    f"panel-right={right_edge} excess={max(0, right_edge - viewport.width())}",
                    flush=True,
                )
            if state in ("scripts_form_default", "scripts_narrow", "scripts_narrow_details"):
                toggle = window.scripts.details_button
                print(
                    f"[scripts] {state} inspector toggle: parent={toggle.parentWidget().objectName()} "
                    f"checked={toggle.isChecked()} icon-null={toggle.icon().isNull()}",
                    flush=True,
                )
            target = out / f"{state}.png"
            if not window.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save Scripts Qt screenshot {target}")
            results.append(target)
    finally:
        window.runner.shutdown()
        window.form.close()
        app.processEvents()
    return results

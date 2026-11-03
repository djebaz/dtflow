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
    "scripts_partial_ready",
    "scripts_live_inspect",
    "scripts_live_configure",
    "scripts_review_unknown",
    "scripts_editor_checked",
    "scripts_editor_saved",
    "scripts_inpaint_entry",
    "scripts_starting",
    "scripts_input_cancelled",
    "scripts_preview_completed",
    "scripts_editor_unverified",
    "scripts_preview_capped",
    "scripts_previous_run_inspection",
    "scripts_setup_ready",
    "scripts_setup_loading",
    "scripts_setup_followup",
    "scripts_setup_narrow",
    "scripts_inspect_setup_preloaded",
    "scripts_setup_reloaded",
)


def _shared_overlay(window, state: str) -> None:
    """Use the presenter's real offline interaction path, not a competing render-only model."""
    window.scripts._present_activity()


def _prepare_inpaint_entry(window):
    """A deterministic in-memory source, never a file/provider/model load."""
    import hashlib
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage, QPixmap
    from dtflow.core.image_inputs import ImageContent

    editor = window.generation.inpaint
    pixels = bytes((45, 100, 65)) * (128 * 128)
    image = QPixmap.fromImage(QImage(pixels, 128, 128, 128 * 3, QImage.Format.Format_RGB888).copy())
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    encoded = bytes(buffer.data())
    editor._source = ImageContent(encoded, hashlib.sha256(encoded).hexdigest(), 128, 128, 3)
    editor.canvas.set_source(pixels, 128, 128)
    editor.canvas.replace_mask((b"\xff" * 48 + b"\x00" * 80) * 128)
    editor.form.sourceFacts.setText("Offline source fixture · 128 × 128")
    editor.form.sourceThumbnail.setPixmap(image.scaledToHeight(80))
    editor._loading = False
    editor._controls()
    window._leave_scripts(inpaint=True)


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
            narrow = state in ("scripts_narrow", "scripts_narrow_details", "scripts_editor_narrow", "scripts_setup_narrow")
            window.form.resize(800, 680) if narrow else window.form.resize(1600, 1000)
            window._show_scripts()
            window.scripts._drawer_requested = state == "scripts_narrow_details"
            base = {
                "scripts_partial_ready": "scripts_inspect_partial",
                "scripts_live_inspect": "scripts_running",
                "scripts_live_configure": "scripts_running",
                "scripts_review_unknown": "scripts_outcome_unknown",
                "scripts_editor_checked": "scripts_editor_dirty",
                "scripts_editor_saved": "scripts_editor_dirty",
                "scripts_inpaint_entry": "scripts_form_default",
                "scripts_starting": "scripts_form_default",
                "scripts_input_cancelled": "scripts_waiting_input",
                "scripts_preview_completed": "scripts_waiting_input",
                "scripts_editor_unverified": "scripts_editor_dirty",
                "scripts_preview_capped": "scripts_form_default",
                "scripts_previous_run_inspection": "scripts_form_after_unknown",
                "scripts_setup_ready": "scripts_form_default",
                "scripts_setup_loading": "scripts_form_default",
                "scripts_setup_followup": "scripts_waiting_input",
                "scripts_setup_narrow": "scripts_form_long_values",
                "scripts_inspect_setup_preloaded": "scripts_form_default",
                "scripts_setup_reloaded": "scripts_form_default",
            }.get(state, state)
            if state in (
                "scripts_queued",
                "scripts_running",
                "scripts_waiting_input",
                "scripts_cancelled_before_start",
            ):
                base = "scripts_form_default"
            window.scripts.preview(base)
            if state == "scripts_setup_loading":
                window.scripts.show_view(1)
                window.scripts.form.hide()
                window.scripts.form_note.setText("Preloading Setup from Inspect. No queue item exists.")
                window.scripts.form_note.show()
                window.scripts.start.setText("Run Script")
                window.scripts.start.setEnabled(False)
            elif state in ("scripts_inspect_setup_preloaded", "scripts_setup_reloaded"):
                window.scripts.form_note.setText("Setup is ready. No job is queued.")
                window.scripts.form_note.show()
                window.scripts.show_view(0 if state == "scripts_inspect_setup_preloaded" else 1)
            elif state == "scripts_setup_followup":
                window.scripts.show_view(1)
                window.scripts.start.setText("Continue")
                window.scripts.start.setEnabled(True)
            elif state in (
                "scripts_queued",
                "scripts_running",
                "scripts_waiting_input",
                "scripts_cancelled_before_start",
            ):
                window.scripts.start.click()
                if state == "scripts_cancelled_before_start":
                    window.scripts._stop_preview()
                elif state != "scripts_queued":
                    window.scripts.designer.advancePreviewButton.click()
                    if state == "scripts_waiting_input":
                        window.scripts.designer.advancePreviewButton.click()
            elif state == "scripts_partial_ready":
                window.scripts.source.setCurrentIndex(3)
                window.scripts.acknowledge.setChecked(True)
                window.scripts.show_view(1)
            elif state in ("scripts_live_inspect", "scripts_live_configure"):
                window.scripts.show_view(0 if state == "scripts_live_inspect" else 1)
            elif state == "scripts_review_unknown":
                window.scripts.designer.reviewRunButton.click()
            elif state in ("scripts_editor_checked", "scripts_editor_saved"):
                window.scripts.editor.check_syntax.click()
                if state == "scripts_editor_saved":
                    window.scripts.editor.save_new.click()
            elif state == "scripts_editor_unverified":
                window.scripts.editor.editor.appendPlainText("// Arbitrary edited code: no fixture outcome")
                window.scripts.editor.check_syntax.click()
            elif state == "scripts_starting":
                window.scripts.start.setEnabled(False)
                window.scripts.start.setText("Starting…")
            elif state == "scripts_preview_capped":
                window.scripts.cap.setValue(1)
                window.scripts.start.click()
                window.scripts.designer.advancePreviewButton.click()
                window.scripts.designer.advancePreviewButton.click()
                window.scripts.designer.continueInputButton.click()
                window.scripts.designer.advancePreviewButton.click()
            elif state == "scripts_previous_run_inspection":
                window.scripts.designer.viewUnresolvedRun.click()
            elif state in ("scripts_input_cancelled", "scripts_preview_completed"):
                window.scripts.designer.cancelInputButton.click() if state == "scripts_input_cancelled" else window.scripts.designer.continueInputButton.click()
                if state == "scripts_preview_completed":
                    window.scripts.designer.advancePreviewButton.click()
            elif state in ("scripts_inpaint_entry", "scripts_from_inpaint"):
                _prepare_inpaint_entry(window)
                if state == "scripts_from_inpaint":
                    window.generation.inpaint.form.runScriptOnImageButton.click()
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

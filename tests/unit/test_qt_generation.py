"""Generate page wiring against the fake backend: responsiveness, previews, uncertainty, interruption, configuration.

Resolver and error-to-field rules are owned by the presenter tests; session lifetime by the session tests. These cover
only the Qt composition: what the page shows for controlled service events and outcomes.
"""

import asyncio
import json
import os

import pytest

pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.core.models import (  # noqa: E402
    BackendEvent,
    Designation,
    ImageArtifact,
    InventoryEntry,
    InventorySnapshot,
    LoRASelection,
)
from dtflow.core.png import fixture_png  # noqa: E402
from dtflow.logging_setup import configure_logging  # noqa: E402
from dtflow.persistence.inventory_cache import save_inventory  # noqa: E402
from dtflow.persistence.store import SQLiteAttempts  # noqa: E402
from dtflow.services import session  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.ui.main_window import MainWindow  # noqa: E402
from dtflow.ui.runner import EventRelay  # noqa: E402

ENDPOINT = "grpcs://127.0.0.1:7859"


@pytest.fixture
def window(qtbot, tmp_path):
    shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=tmp_path / "state")
    qtbot.addWidget(shell.form)
    shell.form.backendCombo.setCurrentText("fake")
    shell.generation_page.promptEdit.setPlainText("a cat")
    yield shell
    shell.runner.shutdown()


@pytest.fixture
def fake(monkeypatch):
    """Route session backend creation to a controlled fake; returns the gate that holds the generation open."""
    gate = asyncio.Event()

    def use(**options):
        monkeypatch.setattr(session, "create_backend", lambda selection, **_: FakeBackend(**options))
        return gate

    return use, gate


def settled(window):
    # A single image runs as a one-item queue (#558): settled also means its dispatch ended and released the state.
    return lambda: (
        window.generation_page.generateButton.isEnabled()
        and not window.activity.generating
        and not window.activity.dispatching
    )


def test_offline_inpaint_mode_and_mask_history_do_not_dispatch(window, qtbot, tmp_path):
    page, editor = window.generation_page, window.generation.inpaint
    assert page.textToImageModeButton.isChecked() and not page.inpaintModeButton.isChecked()
    assert page.promptHeader.indexOf(page.generationModeSwitch) > page.promptHeader.indexOf(page.promptLabel)
    qtbot.waitUntil(page.inpaintModeButton.isEnabled)
    assert editor.form.strengthSpinBox.value() == 0.7
    page.inpaintModeButton.click()
    assert page.inpaintModeButton.isChecked() and not page.textToImageModeButton.isChecked()
    assert page.generateButton.text() == "Edit image" and not page.generateButton.isEnabled()
    assert not page.loadConfigurationButton.isEnabled()
    with pytest.raises(Exception, match="Choose a source image"):
        window.generation.request_for_queue()
    canvas = editor.canvas
    canvas.set_source(bytes([127, 127, 127]) * 35, 5, 7)
    pixels = bytes([0, 255, 0, 0, 0]) * 7
    canvas.replace_mask(pixels)
    assert canvas.mask_pixels() == pixels  # strips Qt row alignment on an odd-width grid
    canvas.clear_mask()
    assert canvas.mask_pixels() == bytes(35)
    canvas.undo()
    assert canvas.mask_pixels() == pixels
    canvas.undo()
    assert canvas.mask_pixels() == bytes(35)
    canvas.redo()
    assert canvas.mask_pixels() == pixels
    canvas.remove_source()
    assert canvas.mask_pixels() == b"" and not canvas._undo and not canvas._redo
    assert not (tmp_path / "state" / "attempts.sqlite3").exists()
    page.textToImageModeButton.click()
    assert page.generateButton.text() == "Generate image" and page.generateButton.isEnabled()
    editor._debounce.start()
    window.runner.shutdown()
    assert not editor._debounce.isActive()


def test_generate_action_lives_in_compact_prompt_header(window):
    page = window.generation_page
    assert page.generateSplitLayout.indexOf(page.generateButton) == 0  # the split button: Generate image, then its ▾
    assert page.generateSplitLayout.indexOf(page.queueGenerateButton) == 1
    assert page.promptHeader.indexOf(page.generateSplitLayout) >= 0
    assert page.expandPromptButton.text() == ""
    assert page.expandPromptButton.width() == 38
    assert page.expandPromptButton.accessibleName() == "Expand prompt"
    assert not page.expandPromptButton.icon().isNull()
    assert not page.generateButton.icon().isNull()
    assert page.generateButton.text() == "Generate image"
    assert page.promptEdit.minimumHeight() == page.promptEdit.maximumHeight() == 52
    assert not hasattr(page, "generationProgressBar")
    assert not hasattr(page, "cancelButton")
    expand_icon = page.expandPromptButton.icon().pixmap(16, 16).toImage()
    page.expandPromptButton.click()
    assert page.promptEdit.minimumHeight() == page.promptEdit.maximumHeight() == 180
    assert page.expandPromptButton.accessibleName() == "Collapse prompt"
    assert page.expandPromptButton.isChecked()
    assert page.expandPromptButton.icon().pixmap(16, 16).toImage() != expand_icon
    page.expandPromptButton.click()
    assert page.promptEdit.minimumHeight() == page.promptEdit.maximumHeight() == 52
    assert page.expandPromptButton.accessibleName() == "Expand prompt"
    assert not page.expandPromptButton.isChecked()


def test_simulation_controls_exist_only_for_the_fake_provider(window):
    page = window.generation_page
    assert not page.simulationBox.isHidden()
    assert [page.simulationScenarioCombo.itemData(i) for i in range(page.simulationScenarioCombo.count())][
        0
    ] == "success"
    page.simulationScenarioCombo.setCurrentIndex(2)
    page.simulationDelaySpinBox.setValue(2.5)
    assert window.generation.simulation() == {"scenario": "lost", "delay": 2.5, "previews": False}
    window.form.backendCombo.setCurrentText("local")
    assert page.simulationBox.isHidden() and window.generation.simulation() == {}


def test_generate_keeps_the_event_loop_responsive_and_saves_the_final_image(window, qtbot, fake):
    use, gate = fake
    use(gate=gate)
    page = window.generation_page
    page.generateButton.click()
    assert page.generateButton.isEnabled()  # every click is its own queue (#558): another one may follow at once
    assert window.progress_strip.rows["run"].activityStopButton.isEnabled()
    qtbot.wait(50)  # the GUI thread keeps processing events while the generation is held open
    assert page.generateButton.isEnabled()
    window.runner._loop.call_soon_threadsafe(gate.set)
    qtbot.waitUntil(settled(window), timeout=5000)
    assert page.generationStatusLabel.text().startswith("Saved")
    assert page.resultList.count() == 2  # one output plus its metadata
    page.copyOutputPathButton.click()  # with nothing selected, the first output is copied
    assert QGuiApplication.clipboard().text() == page.resultList.item(0).text().removeprefix("Output: ")
    assert page.openOutputFolderButton.isEnabled()
    assert page.resultPreviewLabel.property("designation") == "final"
    assert "field.prompt: override" in page.effectiveRequestBrowser.toPlainText()
    # The one-item queue's dispatch ends right after the result; the lock is then free for another owner.
    qtbot.waitUntil(lambda: not window.activity.dispatching, timeout=3000)
    other, opened = SQLiteAttempts(window.state_dir), []
    window.runner.submit(other.open(), lambda result, failure: opened.append(failure))
    qtbot.waitUntil(lambda: bool(opened), timeout=3000)
    assert opened == [None]
    window.runner.submit(other.aclose(), lambda *_: None)


def test_run_ring_preserves_reported_sampler_step_across_stage_and_preview(window, qtbot, fake):
    """Stage-only backend events must not erase a truthful step count from the header ring."""
    use, gate = fake
    use(gate=gate)
    page = window.generation_page
    page.generateButton.click()
    qtbot.waitUntil(lambda: window.activity.run_state.detail.startswith("Sending request"), timeout=5000)
    ring = window.shell.runControl
    assert ring._phase == "running" and ring._completed is None

    window.generation._event(BackendEvent("r", 100, "progress", "t", stage="sampling", completed=3, total=9))
    assert ring._completed == 3 and ring._total == 9
    assert window.progress_strip.rows["run"].activityCount.text() == "3 / 9"

    window.generation._event(BackendEvent("r", 101, "progress", "t", stage="decoding"))
    assert ring._completed == 3 and ring._total == 9
    assert "last reported step 3/9" in ring.toolTip()

    preview = ImageArtifact("p", Designation.PREVIEW, "image/png", fixture_png(64, 64, 1), 64, 64)
    window.generation._event(BackendEvent("r", 102, "preview", "t", artifact=preview))
    assert ring._completed == 3 and ring._total == 9
    assert "Preview shown (not the final image)" in ring.toolTip()

    window.generation._event(BackendEvent("r", 103, "progress", "t", stage="sampling", completed=4, total=9))
    assert ring._completed == 4 and ring._total == 9
    assert window.progress_strip.rows["run"].activityCount.text() == "4 / 9"

    window.runner._loop.call_soon_threadsafe(gate.set)
    qtbot.waitUntil(settled(window), timeout=5000)
    page.generateButton.click()
    assert ring._phase == "running" and ring._completed is None and ring._total is None
    qtbot.waitUntil(settled(window), timeout=5000)


def test_preview_is_shown_but_never_becomes_a_result(window, qtbot, fake):
    use, gate = fake
    use(gate=gate)
    page = window.generation_page
    page.generateButton.click()
    qtbot.waitUntil(
        lambda: window.activity.run_state.detail.startswith("Sending request"), timeout=5000
    )  # held at gate
    png = fixture_png(64, 64, 1)
    preview = ImageArtifact("p", Designation.PREVIEW, "image/png", png, 64, 64)
    window.generation._relay(BackendEvent("r", 99, "preview", "t", artifact=preview))
    qtbot.waitUntil(lambda: page.resultPreviewLabel.property("designation") == "preview", timeout=3000)
    assert page.resultList.count() == 0
    qtbot.waitUntil(lambda: "Preview shown (not the final image)" in window.activity.run_state.detail, timeout=3000)
    window.runner._loop.call_soon_threadsafe(gate.set)
    qtbot.waitUntil(settled(window), timeout=5000)


def test_unknown_outcome_is_not_presented_as_a_plain_failure(window, qtbot, fake):
    use, _ = fake
    use(scenario="lost")
    page = window.generation_page
    page.generateButton.click()
    qtbot.waitUntil(settled(window), timeout=5000)
    text = page.generationStatusLabel.text()
    assert text.startswith("Outcome unknown") and "not replayed" in text
    assert "run" not in window.progress_strip.rows  # no stale progress display after failure
    assert page.resultList.count() == 0


def test_local_stop_reports_recorded_evidence_not_remote_cancellation(window, qtbot, fake):
    use, gate = fake
    use(gate=gate)
    page = window.generation_page
    page.generateButton.click()
    qtbot.waitUntil(lambda: window.activity.run_state.detail.startswith("Sending request"), timeout=5000)
    window.progress_strip.rows["run"].activityStopButton.click()
    qtbot.waitUntil(settled(window), timeout=5000)
    text = page.generationStatusLabel.text()
    assert text.startswith("Local stop requested") and "Outcome unknown" in text
    assert "run" not in window.progress_strip.rows  # stop removes the active progress row
    assert "cancelled on" not in text.lower() and "Remote cancellation is not confirmed" in text


def test_invalid_field_is_marked_and_nothing_is_submitted(window, qtbot):
    page = window.generation_page
    page.seedEdit.setText("not-a-seed")
    page.generateButton.click()
    assert page.generationStatusLabel.text().startswith("Invalid request")
    assert page.seedEdit.property("invalid") is True
    assert page.generateButton.isEnabled()
    assert not (window.state_dir / "attempts.sqlite3").exists()  # no attempt, no state opened


def test_state_busy_is_reported_without_dispatch(window, qtbot):
    owner = SQLiteAttempts(window.state_dir)
    window.runner.submit(owner.open(), lambda *_: None)
    qtbot.wait(200)
    page = window.generation_page
    page.generateButton.click()
    qtbot.waitUntil(settled(window), timeout=5000)
    assert page.generationStatusLabel.text().startswith("Dispatch is owned by another process")
    window.runner.submit(owner.aclose(), lambda *_: None)
    qtbot.wait(100)


def test_inventory_refresh_populates_models_with_observation_time(window, qtbot):
    page = window.generation_page
    page.refreshInventoryButton.click()
    qtbot.waitUntil(lambda: page.refreshInventoryButton.isEnabled(), timeout=5000)
    assert page.modelCombo.findText("fake-fixture") >= 0
    assert "observed" in page.inventoryEvidenceLabel.text()


def test_checkpoint_details_inspects_exact_file_without_dispatch(window, monkeypatch):
    from dtflow.services import checkpoint_metadata

    page = window.generation_page
    page.modelCombo.setCurrentText("fixture_q8p.ckpt")
    monkeypatch.setattr(
        checkpoint_metadata,
        "exact_model_spec",
        lambda identifier: (
            {"file": identifier, "version": "flux2", "modifier": "none"} if identifier == "fixture_q8p.ckpt" else None
        ),
    )
    assert page.checkpointDetailsButton.isEnabled()
    page.checkpointDetailsButton.click()
    assert window.inline.form.inlineTitleLabel.text() == "Checkpoint details"
    body = window.inline.form.inlineTextBrowser.toPlainText()
    assert "Technical / version: flux2" in body
    assert "Q8P" in body
    assert "Not observed" in body
    assert not (window.state_dir / "attempts.sqlite3").exists()
    window.inline.dismiss()
    page.modelCombo.setCurrentText("fixture_q6p.ckpt")
    page.checkpointDetailsButton.click()
    assert "Not available for this exact checkpoint" in window.inline.form.inlineTextBrowser.toPlainText()
    assert "flux2" not in window.inline.form.inlineTextBrowser.toPlainText()


def test_configuration_round_trip_unbound_loras_and_unsupported_documents(window, qtbot, tmp_path):
    page, controller = window.generation_page, window.generation
    target = tmp_path / "saved.json"
    controller._choose_save = lambda parent, directory: str(target)
    page.seedEdit.setText("42")
    page.saveConfigurationButton.click()
    qtbot.waitUntil(target.exists, timeout=3000)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["generation"]["seed"] == 42 and document["generation"]["prompt"] == "a cat"
    document["generation"]["loras"] = [{"identifier": "style", "weight": 0.8}]
    document["generation"]["seed"] = 9
    target.write_text(json.dumps(document), encoding="utf-8")
    page.seedEdit.setText("1")
    controller._choose_open = lambda parent, directory: str(target)
    page.loadConfigurationButton.click()
    qtbot.waitUntil(lambda: page.seedEdit.text() == "9", timeout=3000)
    assert page.generationStatusLabel.text().startswith("Configuration loaded")
    assert "no cloud receipt is bound" in page.generationStatusLabel.text()
    assert page.loraList.count() == 1
    assert "config_file: saved.json" in page.effectiveRequestBrowser.toPlainText()
    # An unsupported document is reported and leaves the form as it was.
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "dtflow.generation-config", "version": 99, "generation": {}}))
    controller._choose_open = lambda parent, directory: str(bad)
    page.seedEdit.setText("5")
    page.loadConfigurationButton.click()
    qtbot.waitUntil(lambda: page.generationStatusLabel.text().startswith("Configuration not loaded"))
    assert page.seedEdit.text() == "5"


def test_relay_coalesces_transient_events_and_keeps_critical_ones(qtbot):
    seen = []
    relay = EventRelay(seen.append)
    for sequence in range(1, 501):
        relay(BackendEvent("r", sequence, "progress", "t", completed=sequence, total=500))
    relay(BackendEvent("r", 501, "submission_possible", "t"))
    relay(BackendEvent("r", 502, "terminal", "t"))
    qtbot.waitUntil(lambda: len(seen) >= 3, timeout=3000)
    qtbot.wait(50)
    assert [e.kind for e in seen] == ["progress", "submission_possible", "terminal"]
    assert seen[0].completed == 500  # only the latest progress survives coalescing


def local_snapshot(metadata="absent"):
    entries = tuple(InventoryEntry(f"m{n}.ckpt", "model", f"M{n}", None, "listed", "echo.files") for n in range(2))
    return InventorySnapshot(
        "drawthings-local", "s", "2026-10-05T10:00:00+00:00", True, entries, (("model_metadata", metadata),), ENDPOINT
    )


def test_cache_prefills_the_model_list_and_a_bypass_ignores_it(window, qtbot, tmp_path):
    page = window.generation_page
    save_inventory(window.state_dir, "local", local_snapshot())
    window.form.backendCombo.setCurrentText("local")
    window.form.endpointEdit.setText("127.0.0.1:7859")
    window.generation.request_cached()
    qtbot.waitUntil(lambda: page.modelCombo.findText("m0.ckpt") >= 0, timeout=3000)
    assert "cached" in page.inventoryEvidenceLabel.text() and "2 model(s)" in page.inventoryEvidenceLabel.text()
    window.form.backendCombo.setCurrentText("fake")  # another provider never shows this provider's list
    assert page.modelCombo.findText("m0.ckpt") < 0
    bypass = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=window.state_dir, no_cache=True)
    qtbot.addWidget(bypass.form)
    bypass.form.backendCombo.setCurrentText("local")
    bypass.form.endpointEdit.setText("127.0.0.1:7859")
    bypass.generation.request_cached()
    qtbot.wait(200)
    assert bypass.generation_page.modelCombo.count() == 0
    bypass.runner.shutdown()


def test_last_session_and_per_provider_fields_are_restored_by_the_next_window(window, qtbot):
    page = window.generation_page
    page.stepsSpinBox.setValue(9)  # remembered for the fake provider while the session moves on to local
    window.form.backendCombo.setCurrentText("local")
    window.form.endpointEdit.setText("10.0.0.2:7859")
    page.promptEdit.setPlainText("a red fox")
    page.modelCombo.setCurrentText("m1.ckpt")
    page.seedEdit.setText("77")
    page.widthSpinBox.setValue(640)
    page.stepsSpinBox.setValue(30)
    window.generation.lora.set_loras((LoRASelection("style", 0.7),))
    window.remember(background=False)
    again = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=window.state_dir)
    qtbot.addWidget(again.form)
    try:
        assert again.form.backendCombo.currentText() == "local"
        assert again.form.endpointEdit.text() == "10.0.0.2:7859"
        restored = again.generation_page
        assert restored.promptEdit.toPlainText() == "a red fox" and restored.modelCombo.currentText() == "m1.ckpt"
        assert restored.seedEdit.text() == "77" and restored.widthSpinBox.value() == 640
        assert restored.stepsSpinBox.value() == 30
        assert again.generation.lora.loras == (LoRASelection("style", 0.7),)
        again.form.backendCombo.setCurrentText("fake")  # the other provider's fields survived the restart too
        assert restored.stepsSpinBox.value() == 9
    finally:
        again.runner.shutdown()


@pytest.mark.parametrize("dev", [False, True])
def test_gui_logs_actions_and_results_without_prompts_or_inventory(window, qtbot, tmp_path, dev):
    logs = configure_logging(directory=tmp_path / "logs", session="gui", dev=dev)
    page = window.generation_page
    page.promptEdit.setPlainText("PROMPT-SENTINEL")
    page.refreshInventoryButton.click()
    qtbot.waitUntil(lambda: page.refreshInventoryButton.isEnabled(), timeout=5000)
    page.generateButton.click()
    qtbot.waitUntil(settled(window), timeout=5000)
    logs.close()
    text = logs.path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    backends = {row["event"]: row.get("data", {}).get("backend") for row in rows}
    assert backends["inventory.refresh_ok"] == backends["generation.submitted"] == "fake"
    assert backends["generation.outputs_saved"] == "fake"
    assert "PROMPT-SENTINEL" not in text and "Offline fixture swatch" not in text
    assert ("generation.request_validated" in backends) is dev
    # Bounded semantic fields, never an inventory or request dump.
    assert max(len(line) for line in text.splitlines()) < 2000


def test_a_failed_generation_logs_the_backend_code_not_just_the_exception_type(window, qtbot, tmp_path, fake):
    use, _ = fake
    use(scenario="reject")
    logs = configure_logging(directory=tmp_path / "logs", session="fail")
    page = window.generation_page
    page.generateButton.click()
    qtbot.waitUntil(settled(window), timeout=5000)
    logs.close()
    rows = [json.loads(line) for line in logs.path.read_text(encoding="utf-8").splitlines()]
    failed = next(row["data"] for row in rows if row["event"] == "generation.failed")
    assert failed["reason"] == "BackendError" and failed["code"] and failed["category"]


def test_a_size_that_is_not_a_multiple_of_64_is_adjusted_and_shown(window, qtbot, fake):
    use, _ = fake
    use()
    page = window.generation_page
    page.widthSpinBox.setValue(700)
    page.heightSpinBox.setValue(500)
    page.generateButton.click()
    assert (page.widthSpinBox.value(), page.heightSpinBox.value()) == (704, 512)
    assert "Size adjusted from 700x500 to 704x512" in page.generationStatusLabel.text()
    qtbot.waitUntil(settled(window), timeout=5000)
    assert "size: 704x512" in page.effectiveRequestBrowser.toPlainText()


@pytest.mark.parametrize("size", [(512, 1024), (1024, 576)])
def test_preview_fits_entire_original_image_and_zoom_adds_overflow(window, qtbot, size):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(*size, QImage.Format.Format_RGB32)
    image.fill(QColor("steelblue"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    page = window.generation_page
    window.generation._show_image(bytes(data), "final")
    window.form.generationNavigationButton.click()
    window.show()
    qtbot.wait(20)
    viewer = page.resultPreviewLabel
    assert viewer.pixmap().height() == size[1]  # original pixels stay available for inspection
    rect = viewer.image_rect()
    assert rect.width() / rect.height() == pytest.approx(size[0] / size[1])
    assert viewer.viewport().rect().contains(rect.toAlignedRect())
    assert viewer.horizontalScrollBar().maximum() == viewer.verticalScrollBar().maximum() == 0
    viewer.zoom(4)
    assert viewer.verticalScrollBar().maximum() > 0
    viewer.fit()
    assert viewer.verticalScrollBar().maximum() == 0
    page.generateButton.click()  # a new run clears the preview and the frame returns to its resting height
    assert page.previewHost.minimumHeight() == 180
    qtbot.waitUntil(settled(window), timeout=5000)  # deliver the owned result before tearing down the window


def test_canvas_zoom_anchor_expand_lifecycle_and_narrow_navigation(window, qtbot):
    from PySide6.QtCore import QPoint

    window.form.generationNavigationButton.click()
    window.show()
    window.form.resize(800, 680)
    qtbot.wait(30)
    page, form = window.generation_page, window.form
    assert not page.generationRightRail.isVisible()
    assert form.navigationFrame.isVisible()
    assert form.navigationFrame.parent() is page.generationLeftRail
    assert page.width() <= form.centralWidget().width()
    form.resize(1600, 1000)
    qtbot.wait(20)
    assert page.generationRightRail.isVisible()  # the inspector follows the layout; there is no manual toggle
    assert form.navigationFrame.isVisible()
    assert form.navigationFrame.parent() is page.generationLeftRail
    form.resize(800, 680)
    qtbot.wait(20)
    viewer = page.resultPreviewLabel
    window.generation._show_image(fixture_png(1024, 576, 0), "final", "fake")
    viewer.zoom(2)
    viewer.horizontalScrollBar().setValue(200)
    viewer.verticalScrollBar().setValue(100)
    anchor = QPoint(100, 100)
    before = viewer.image_rect()
    source = ((anchor.x() - before.x()) / viewer._scale, (anchor.y() - before.y()) / viewer._scale)
    viewer.zoom(3, anchor)
    after = viewer.image_rect()
    assert (anchor.x() - after.x()) / viewer._scale == pytest.approx(source[0], abs=1)
    assert (anchor.y() - after.y()) / viewer._scale == pytest.approx(source[1], abs=1)
    host = viewer.parentWidget()
    viewer.toggle_expanded()
    assert viewer.isWindow() and viewer._expanded
    viewer.close()
    assert not viewer._expanded and viewer.parentWidget() is host
    viewer.toggle_expanded()
    form.close()
    assert not viewer._expanded and viewer.parentWidget() is host


def test_saved_actions_use_attempt_identity_and_metadata_file(window, qtbot, fake):
    use, _ = fake
    use()
    page = window.generation_page
    page.generateButton.click()
    qtbot.waitUntil(settled(window), timeout=5000)
    window.form.backendCombo.setCurrentText("cloud")
    assert "Fixture" in page.resultPreviewLabel._badge.text()
    window.generation.metadataButton.click()
    assert window.inline.form.inlineTitleLabel.text() == "Saved generation metadata"
    assert json.loads(window.inline.form.inlineTextBrowser.toPlainText())["backend_id"] == "fake"
    window.inline.form.inlineDismissButton.click()
    window.generation._show_image(fixture_png(64, 64, 0), "final", "cloud")
    assert "Fixture" not in page.resultPreviewLabel._badge.text()


def test_form_fields_are_remembered_per_provider_but_prompts_are_shared(window, tmp_path):
    page = window.generation_page
    page.modelCombo.setCurrentText("fake-grid")
    page.stepsSpinBox.setValue(7)
    page.widthSpinBox.setValue(256)
    window.form.backendCombo.setCurrentText("cloud")
    assert page.promptEdit.toPlainText() == "a cat"  # prompts stay
    assert (page.modelCombo.currentText(), page.stepsSpinBox.value(), page.widthSpinBox.value()) != (
        "fake-grid",
        7,
        256,
    )
    page.modelCombo.setCurrentText("cloud-model.ckpt")
    page.stepsSpinBox.setValue(20)
    window.generation.lora.set_loras([LoRASelection("cloud-lora", 0.5)])
    window.form.backendCombo.setCurrentText("fake")
    assert (page.modelCombo.currentText(), page.stepsSpinBox.value(), page.widthSpinBox.value()) == (
        "fake-grid",
        7,
        256,
    )
    assert window.generation.lora.loras == ()
    window.form.backendCombo.setCurrentText("cloud")
    assert (page.modelCombo.currentText(), page.stepsSpinBox.value()) == ("cloud-model.ckpt", 20)
    assert [item.identifier for item in window.generation.lora.loras] == ["cloud-lora"]
    document = window.generation.forms_document()
    assert set(document) == {"fake", "cloud"} and document["fake"]["model"] == "fake-grid"

"""Qt shell lifecycle, background boundary, presentation wiring and headless separation."""

import asyncio
import os
import subprocess
import sys
import threading

import pytest
from support.network_guard import child_command

pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from support.fake_vault import Vault  # noqa: E402

from dtflow.build import display_identity  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.ui.inline_feedback import InlineNotice  # noqa: E402
from dtflow.ui.main_window import MainWindow  # noqa: E402
from dtflow.ui.runner import AsyncRunner  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path):
    shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=tmp_path / "state")
    qtbot.addWidget(shell.form)
    yield shell
    shell.runner.shutdown()


def test_lifecycle_from_designer_file_and_clean_shutdown(window, qtbot):
    assert window.form.objectName() == "MainWindow"
    assert not window.form.windowIcon().isNull()
    window.show()
    assert window.runner._thread.is_alive()
    window.queue._timer.start(1)
    delivered = []
    window.runner._done.emit(lambda result, error: delivered.append(result), "stale", None)
    window.runner.shutdown()
    assert window.runner.closed and not window.runner._thread.is_alive()
    assert not window.queue._timer.isActive()
    qtbot.wait(20)  # drain the callback queued before shutdown, without reviving polling
    assert delivered == [] and not window.queue._timer.isActive()


def test_feedback_child_deleted_before_activity_strip_remains_safe(window, qtbot):
    """A dying inline child must not leave the strip with a stale Qt pointer."""
    window.show()
    strip = window.progress_strip
    window.inline.show("Details", "Preview confirmation information")
    strip.start("fixture-progress", "Still working")
    assert strip.feedback is window.inline

    window.inline.form.deleteLater()
    qtbot.waitUntil(lambda: strip.feedback is None, timeout=2000)
    assert not strip.retired and "fixture-progress" in strip.rows
    strip._position()
    strip.update("fixture-progress", 1, 2)
    strip.finish("fixture-progress")


def test_overlay_notices_preserve_editor_and_show_distinct_lifetime(window, qtbot):
    from dtflow.ui.inline_feedback import edit_text

    window.show()
    strip = window.progress_strip
    edit_text(window.form, "Edit caption", "Caption", lambda value: None)
    editor = window.inline._editor
    editor.setText("Keep this text")
    InlineNotice.information(window.form, "Background information", "Unrelated notification")
    assert window.inline._editor is editor
    assert editor.text() == "Keep this text"
    assert any(row.activityLabel.text() == "Background information" for row in strip.rows.values())

    window.inline.dismiss()
    strip.complete("example", "Finished", "Saved successfully", "success")
    row = strip.rows["example"]
    assert row.activityProgress.isHidden()
    qtbot.waitUntil(row.activityLifetimeBar.isVisible, timeout=1000)
    assert row.activityLifetimeBar.value() == 6000
    assert strip._expiry["example"] == 6.0
    strip.finish("example")

    for state in ("stopped", "partial", "warning"):
        strip.complete("example", state.title(), "Detailed outcome stays in the page history.", state)
        row = strip.rows["example"]
        assert row.activityLifetime.text() == "6s"
        assert row.activityLifetimeBar.value() == 6000
        assert strip._expiry["example"] == 6.0
        assert row.property("feedbackState") == state
        strip.finish("example")

    # Nonfatal warning notices use the same amber lifetime row, not the persistent editor panel.
    InlineNotice.warning(window.form, "Busy", "Try again later")
    amber_notices = [(key, row) for key, row in strip.rows.items() if row.activityLabel.text() == "Busy"]
    assert len(amber_notices) == 1
    key, row = amber_notices[0]
    assert row.property("feedbackState") == "warning"
    assert strip._expiry[key] == 6.0
    strip.finish(key)

    # Red errors and informational inspection remain persistent.
    strip.complete("failure", "Export failed", "Review the file errors", "error")
    assert "failure" not in strip._expiry
    strip.finish("failure")
    InlineNotice.information(window.form, "Details", "Persistent metadata")
    assert window.inline.form.property("feedbackState") == "info"
    window.inline.escape.activated.emit()
    assert window.inline.form.isHidden()


def test_destructive_recycle_confirmation_uses_red_trash_icon(window):
    from dtflow.ui.theme import load_tokens

    window.show()
    button = window.gallery.delete_button
    window.inline.arm(
        button, "Move 2 selected originals to Recycle Bin?", "Confirm the scope", lambda: None, danger=True
    )
    symbol = window.inline.form.inlineSymbol
    icon = symbol.pixmap()
    assert icon is not None and not icon.isNull()
    assert symbol.text() == ""
    assert symbol.property("feedbackState") == "error"
    danger_rgb = load_tokens()["color.dangerText"].lower()
    pixels = icon.toImage()
    assert any(
        pixels.pixelColor(x, y).name().lower() == danger_rgb
        for y in range(pixels.height())
        for x in range(pixels.width())
    )
    window.inline.dismiss()


def test_shutdown_before_the_loop_starts_is_not_lost(qtbot):
    import warnings

    async def never():
        await asyncio.sleep(60)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # "coroutine was never awaited" would fail here
        for _ in range(20):
            runner = AsyncRunner()
            coroutine = never()
            runner.submit(coroutine, lambda result, failure: None)
            runner.shutdown()  # immediately, possibly before the worker loop is running
            assert not runner._thread.is_alive()
            assert coroutine.cr_frame is None  # closed or finished, never left pending
            runner.shutdown()  # idempotent
            with pytest.raises(RuntimeError):
                runner.submit(never(), lambda result, failure: None)


def test_background_work_leaves_event_loop_responsive_and_returns_on_gui_thread(qtbot):
    runner = AsyncRunner()
    results = []

    async def slow():
        await asyncio.sleep(0.2)
        return threading.current_thread().name

    runner.submit(slow(), lambda result, failure: results.append((result, failure, threading.current_thread().name)))
    qtbot.wait(20)
    assert results == []  # the GUI thread was free to process events while the coroutine ran
    qtbot.waitUntil(lambda: bool(results), timeout=3000)
    worker, failure, delivered_on = results[0]
    assert failure is None and worker == "dtflow-qt-async" and delivered_on != worker
    results.clear()

    async def boom():
        raise RuntimeError("x")

    runner.submit(boom(), lambda result, failure: results.append(failure))
    qtbot.waitUntil(lambda: bool(results), timeout=3000)
    assert isinstance(results[0], RuntimeError)
    runner.shutdown()
    assert not runner._thread.is_alive()


def test_connection_check_presents_fake_backend_and_closes(window, qtbot):
    window.form.backendCombo.setCurrentText("fake")
    window.form.checkButton.click()
    assert not window.form.checkButton.isEnabled()
    qtbot.waitUntil(lambda: window.form.checkButton.isEnabled(), timeout=5000)
    assert window.form.statusLabel.text().startswith("Connected")


def test_cloud_with_empty_saved_store_is_reported_not_raised(window, qtbot, monkeypatch):
    monkeypatch.delenv("DRAWTHINGS_API_KEY", raising=False)
    window.form.backendCombo.setCurrentText("cloud")
    window.form.checkButton.click()
    qtbot.waitUntil(lambda: window.form.checkButton.isEnabled(), timeout=5000)
    assert window.form.statusLabel.text().startswith(
        "Authentication failed"
    )  # missing credentials fail before any HTTP


def test_credential_entry_wiring_never_echoes_secret(window):
    form = window.form
    form.backendCombo.setCurrentText("cloud")
    assert form.keyEdit.echoMode().name == "Password"
    form.keyEdit.setText("private-key")
    form.saveKeyButton.click()
    assert form.keyEdit.text() == ""
    assert "saved" in form.credentialStatusLabel.text().lower()
    assert "private-key" not in form.credentialStatusLabel.text()
    form.removeKeyButton.click()
    assert form.removeKeyButton.property("confirmationPending") is True
    assert "saved" in form.credentialStatusLabel.text().lower()
    InlineNotice.information(form, "Background notice", "Unrelated status")
    assert form.removeKeyButton.property("confirmationPending") is True
    assert window.inline.form.inlineTitleLabel.text() == "Remove saved key"
    assert any(row.activityLabel.text() == "Background notice" for row in window.progress_strip.rows.values())
    window.inline.form.inlineDismissButton.click()  # declining keeps the key
    assert not form.removeKeyButton.property("confirmationPending")
    assert "saved" in form.credentialStatusLabel.text().lower() and form.removeKeyButton.isEnabled()
    form.removeKeyButton.click()
    assert "saved" in form.credentialStatusLabel.text().lower()
    form.removeKeyButton.click()
    assert form.credentialStatusLabel.text().startswith("No API key") and not form.removeKeyButton.isEnabled()
    form.saveKeyButton.click()  # empty entry is refused
    assert "not saved" in form.credentialStatusLabel.text()


def test_status_bar_uses_shared_identity(window):
    assert display_identity() in window.form.statusBar().currentMessage()


def test_cli_and_services_import_without_qt():
    code = (
        "import sys, dtflow.cli, dtflow.composition, dtflow.services.connection;"
        "bad=[m for m in sys.modules if m.startswith('PySide6')]; assert not bad, bad"
    )
    command = [sys.executable, "-c", code]
    with child_command(command):
        subprocess.run(command, check=True)

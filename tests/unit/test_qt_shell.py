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

from dtflow._diagnostic_notifications import DiagnosticNotice  # noqa: E402
from dtflow.build import display_identity  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.ui.diagnostic_notice import DiagnosticNoticeReceiver  # noqa: E402
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


@pytest.mark.parametrize("capability", [None, False, True])
def test_trash_capability_gates_startup_and_source_checks(capability, monkeypatch, qtbot, tmp_path):
    from dtflow.services.image_recycle import RecycleUnavailable
    from dtflow.ui import system_trash

    class FileCapability:
        pass

    if capability is not None:
        FileCapability.supportsMoveToTrash = staticmethod(lambda: capability)
    monkeypatch.setattr(system_trash, "QFile", FileCapability)
    assert system_trash.trash_supported() is bool(capability)
    if not capability:
        # Refuse before inspecting an original, let alone moving/deleting it.
        with pytest.raises(RecycleUnavailable, match="System Trash is unavailable"):
            system_trash.check_trash_source(tmp_path / "nonexistent.png")

    shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=tmp_path / "state")
    qtbot.addWidget(shell.form)
    try:
        assert (shell.gallery._recycle_callback is not None) is bool(capability)
        if not capability:
            assert not shell.gallery.delete_button.isEnabled()
    finally:
        shell.runner.shutdown()


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


def test_overlay_notices_preserve_editor_and_show_distinct_lifetime(window, qtbot, monkeypatch):
    from PySide6.QtCore import QRect

    from dtflow.ui.inline_feedback import edit_text

    window.show()
    strip = window.progress_strip
    monkeypatch.setattr(strip, "_bar_rect", lambda: QRect(12, 2, 400, 20))
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
    # Initial state is synchronous. Waiting for Qt visibility may legitimately
    # deliver a countdown tick before we inspect these initial values.
    assert row.activityLifetimeBar.value() == 6000
    assert strip._expiry["example"] == 6.0
    qtbot.waitUntil(strip.bar.isVisible, timeout=1000)
    assert row.isHidden() and row.property("inBar")
    assert strip._bar_key == "example" and strip.bar.strip.model.lifetime == 1
    strip.finish("example")

    for state in ("stopped", "partial", "warning"):
        strip.complete("example", state.title(), "Detailed outcome stays in the page history.", state)
        row = strip.rows["example"]
        assert row.activityLifetime.text() == "6s"
        assert row.activityLifetimeBar.value() == 6000
        assert strip._expiry["example"] == 6.0
        assert row.property("feedbackState") == state
        if state == "stopped":
            assert row.property("inBar") and strip.bar.strip.model.kind == "stopped"
        else:
            assert not row.property("inBar") and row.activityLifetimeBar.isVisible()
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


def test_activity_bar_progress_and_result_lifecycle(window, qtbot, monkeypatch):
    from PySide6.QtCore import QAbstractAnimation, QRect
    from PySide6.QtWidgets import QApplication

    from dtflow.ui.theme import load_tokens

    window.show()
    strip = window.progress_strip
    real_bar_rect = strip._bar_rect
    # Pin the available slot, not the physical screen/font metrics, for threshold checks.
    monkeypatch.setattr(strip, "_bar_rect", lambda: QRect(12, 2, 400, 20))
    strip.start("fixture", "Queue · settled items", stop=lambda: None, total=7)
    strip.update(
        "fixture",
        4,
        7,
        queued=2,
        stage="3 running",
        wide="Queue fixture · Running · 3 items running",
        elapsed="0:43",
        eta="~0:25 left",
    )
    model = strip.bar.strip.model
    assert model.count == "4/7" and model.queued == 2 and model.fraction == 4 / 7
    tokens = load_tokens()
    assert strip.bar.strip._fills["running"].name() == tokens["color.selectionBorder"]
    assert strip.bar.strip._stripe.name() == tokens["color.selectionHover"]
    strip.bar.button.click()
    caveat = "Stopping after the current item; completed work remains."
    assert strip.bar.strip.model.stopping and not strip.bar.button.isEnabled()
    assert strip.bar.strip.model.stage == caveat and not strip.bar.strip.model.wide
    # Later queue progress must not replace the caveat at either short or long widths.
    for width in (400, 800):
        monkeypatch.setattr(strip, "_bar_rect", lambda width=width: QRect(12, 2, width, 20))
        strip.update("fixture", 5, 7, stage="1 running", wide="Queue fixture · Running · 1 item running")
        assert strip.bar.strip.model.stage == caveat and not strip.bar.strip.model.wide
    # Exercise the real geometry gate separately from the pinned presentation slot.
    status = window.form.statusBar()
    status.clearMessage()
    # With no visible controls, the slot has a 12 px inset on each edge.
    monkeypatch.setattr(status, "findChildren", lambda *args, **kwargs: [])
    monkeypatch.setattr(status, "width", lambda: 263)
    assert real_bar_rect() is None
    monkeypatch.setattr(status, "width", lambda: 264)
    assert real_bar_rect().width() == 240
    strip.complete("fixture", "Queue", "Finished", "success")
    strip._notice_timer.stop()
    monkeypatch.setattr(strip.form, "underMouse", lambda: False)
    monkeypatch.setattr(strip.bar, "underMouse", lambda: True)
    strip._expiry["fixture"] = 0.05
    strip._tick_notices()
    assert strip._expiry["fixture"] == 0.05  # hover pauses the lifetime
    monkeypatch.setattr(strip.bar, "underMouse", lambda: False)
    window.form.clearFocus()
    monkeypatch.setattr(QApplication, "focusWidget", lambda: None)
    strip._tick_notices()
    row = strip.rows["fixture"]
    fade = row._lifetime_fade
    assert fade.duration() == 450 and fade.state() == QAbstractAnimation.State.Running
    fade.pause()
    fade.setCurrentTime(225)
    assert 0 < strip.bar.opacity_effect.opacity() < 1
    assert strip.bar.strip.model.lifetime == 0
    # Switching to a new task restores full opacity; the old result cannot fade that task.
    strip.start("new-fixture", "Generation")
    assert strip.bar.opacity_effect.opacity() == 1
    fade.setCurrentTime(450)
    strip._finish_if_row("fixture", row)
    assert "new-fixture" in strip.rows and strip.bar.strip.model.title == "Generation"


def test_destructive_recycle_confirmation_uses_red_trash_icon(window):
    from dtflow.ui.theme import load_tokens
    from dtflow.ui.trash_icon import trash_icon_pixmap

    window.show()
    button = window.gallery.delete_button
    window.inline.arm(
        button, "Move 2 selected originals to Recycle Bin?", "Confirm the scope", lambda: None, danger=True
    )
    symbol = window.inline.form.inlineSymbol
    icon = symbol.pixmap()
    assert icon is not None and not icon.isNull()
    assert icon.toImage() == trash_icon_pixmap().toImage()
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

    # Running Trash also uses the same canonical Delete pictogram, never an emoji.
    window.progress_strip.start("trash", "Moving originals to Recycle Bin")
    progress_symbol = window.progress_strip.rows["trash"].activitySymbol
    assert progress_symbol.pixmap() is not None
    assert progress_symbol.pixmap().toImage() == icon.toImage()
    assert progress_symbol.text() == ""
    window.progress_strip.finish("trash")


def test_diagnostic_warning_is_delivered_once_without_replacing_confirmation(window, qtbot):
    window.show()
    mailbox = DiagnosticNotice()

    def report():
        mailbox.post("Diagnostic evidence is incomplete; review the logs.")
        mailbox.post("Duplicate warning must not replace the first one.")

    worker = threading.Thread(target=report, daemon=True)
    worker.start()
    worker.join(3)
    assert not worker.is_alive()
    button = window.gallery.delete_button
    window.inline.arm(button, "Keep this confirmation", "Scope remains protected", lambda: None, danger=True)
    receiver = DiagnosticNoticeReceiver(window, mailbox)
    assert receiver.parent() is window.form
    qtbot.waitUntil(lambda: "diagnostics" in window.progress_strip.rows, timeout=2000)
    row = window.progress_strip.rows["diagnostics"]
    assert row.activityDetail.text() == "Diagnostic evidence is incomplete; review the logs."
    assert row.property("feedbackState") == "warning" and window.progress_strip._expiry["diagnostics"] == 6.0
    assert (
        button.property("confirmationPending")
        and window.inline.form.inlineTitleLabel.text() == "Keep this confirmation"
    )
    assert not receiver.timer.isActive() and mailbox.take() is None
    receiver.deliver()
    assert window.progress_strip.rows["diagnostics"] is row
    window.inline.dismiss()


def test_diagnostic_receiver_stops_at_shutdown_without_consuming_a_late_notice(window):
    mailbox = DiagnosticNotice()
    receiver = DiagnosticNoticeReceiver(window, mailbox)
    window.runner.shutdown()
    assert not receiver.timer.isActive()
    mailbox.post("Late diagnostic warning must not reopen a window.")
    receiver.deliver()
    assert "diagnostics" not in window.progress_strip.rows
    assert mailbox.take() == "Late diagnostic warning must not reopen a window."


def test_gallery_thumbnail_decode_is_fixed_600px_and_detail_uses_original(tmp_path, monkeypatch):
    """All thumbnail views share one 600 px decode; Detail retains original pixels."""
    from PySide6.QtGui import QImage

    from dtflow.ui.gallery_page import THUMBNAIL_SIDE, _image

    path = tmp_path / "saved-output.png"
    source = QImage(1200, 900, QImage.Format.Format_RGB32)
    source.fill(0xFF497AA3)
    assert source.save(str(path))
    assert THUMBNAIL_SIDE == 600

    thumb, issue = _image(str(path), THUMBNAIL_SIDE)
    assert issue == ""
    assert thumb is not None
    assert (thumb.width(), thumb.height()) == (600, 450)

    full, issue = _image(str(path), 0)
    assert issue == ""
    assert full is not None
    assert (full.width(), full.height()) == (1200, 900)
    assert path.read_bytes()  # No derivative is created or written beside the source.
    assert tuple(tmp_path.iterdir()) == (path,)

    # Native codec code may hold Qt's image-plugin mutex. Its raw byte reads
    # must not re-enter Python, which can invert that mutex with the GIL while
    # the GUI is painting/capturing another image.
    from PySide6.QtCore import QBuffer

    def python_read_forbidden(*_args):
        raise AssertionError("Buffered image codec re-entered Python I/O")

    monkeypatch.setattr(QBuffer, "readData", python_read_forbidden)
    state = tmp_path / "state"
    state.mkdir()
    for side, expected in ((600, (600, 450)), (0, (1200, 900))):
        buffered, issue = _image(str(path), side, state)
        assert issue == "" and buffered is not None
        assert (buffered.width(), buffered.height()) == expected

    # Exercise cache encoding/hits without starting a background writer.
    from types import SimpleNamespace

    from dtflow.persistence import thumbnail_cache
    from dtflow.ui import gallery_page

    monkeypatch.setattr(thumbnail_cache, "_enabled", True)
    monkeypatch.setattr(thumbnail_cache, "_format", "jpg")
    monkeypatch.setattr(gallery_page, "resolve_item_read", lambda *_args, **_kwargs: (str(path), None))
    writes = []

    def immediate_store(state, entry, write, done=None):
        writes.append(entry)
        assert thumbnail_cache.store(state, entry, write)
        return True

    monkeypatch.setattr(thumbnail_cache, "store_later", immediate_store)
    item = SimpleNamespace(identity="thumbnail-format-fixture")
    for selected, alpha in (("jpg", False), ("webp", False), ("jpg", True)):
        if alpha:
            source = source.convertToFormat(QImage.Format.Format_ARGB32)
            source.fill(0x80497AA3)
            assert source.save(str(path))
        thumbnail_cache.configure(no_cache=False, thumbnail_format=selected)
        thumb, issue = _image(str(path), 600, state, item=item, cache=True)
        assert not issue and thumb is not None
        expected_suffix = ".webp" if alpha or selected == "webp" else ".jpg"
        assert writes[-1].suffix == expected_suffix
        payload = writes[-1].read_bytes()
        assert payload.startswith(b"RIFF" if expected_suffix == ".webp" else b"\xff\xd8")
        count = len(writes)
        hit, issue = _image(str(path), 600, state, item=item, cache=True)
        assert not issue and hit is not None and len(writes) == count
        assert hit.hasAlphaChannel() == alpha
        assert thumbnail_cache.forget(state, [item.identity]) >= 1
    thumbnail_cache.configure(no_cache=True)
    assert thumbnail_cache.entry_path(state, item.identity, path.stat(), 600) is None


def test_images_grid_and_list_thumbnails_use_320px_standard_and_scale_column(window):
    """100% matches the former 200% Grid size; List scales image width, not only height."""
    gallery = window.gallery
    assert gallery._thumbnail_pixels(25) == 80
    assert gallery._thumbnail_pixels(100) == 320
    assert gallery._thumbnail_pixels(200) == 640
    assert gallery.slider.value() == 100

    gallery.show_view("List", persist=False)
    normal_row_height = gallery.table.verticalHeader().defaultSectionSize()
    normal_icon = gallery.table.iconSize()
    normal_column = gallery.table.columnWidth(1)
    assert normal_row_height == 112
    assert normal_icon.width() == 149
    assert normal_column >= normal_icon.width() + 16

    gallery.slider.setValue(200)
    enlarged = gallery.table.iconSize()
    assert gallery.table.verticalHeader().defaultSectionSize() == 224
    assert enlarged.width() == 299
    assert enlarged.height() > normal_icon.height()
    assert gallery.table.columnWidth(1) >= enlarged.width() + 16
    assert gallery.table.columnWidth(1) > normal_column

    gallery.slider.setValue(25)
    assert gallery.table.iconSize().width() == 48
    assert gallery.table.columnWidth(1) >= 64
    gallery.slider.setValue(100)
    assert gallery.table.columnWidth(1) == normal_column


def test_images_export_toggle_opens_without_selection_and_tracks_panel(window):
    """Opening the export card needs no checked image, but export submission still does."""
    from dtflow.core.models import ValidationError

    window.show()
    window.form.imagesNavigationButton.click()
    button = window.gallery.export_button
    assert not window.gallery.selection.checked
    assert button.isEnabled() and button.isCheckable() and not button.isChecked()
    assert window.gallery_detail.galleryExportScroll.isHidden()

    button.click()
    assert button.isChecked()
    assert button.accessibleName() == "Hide export panel"
    assert not window.gallery_detail.galleryExportScroll.isHidden()
    assert window.form.imageExportBox.parentWidget() is window.gallery_detail.galleryExportHost
    assert not window.image_export.running
    with pytest.raises(ValidationError, match="Check at least one saved image"):
        window._export_selection("checked")

    window.gallery_detail.galleryExportCloseButton.click()
    assert not button.isChecked()
    assert window.gallery_detail.galleryExportScroll.isHidden()

    button.click()
    assert button.isChecked()
    window.form.connectionNavigationButton.click()
    assert not button.isChecked()  # not visible here; shared card returns to Connection
    assert window._gallery_export_open  # reopen beside Images on return
    window.form.imagesNavigationButton.click()
    assert button.isChecked()
    assert not window.gallery_detail.galleryExportScroll.isHidden()
    assert not window.image_export.running


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

    from PySide6.QtCore import QTimer

    release = asyncio.Event()
    started = threading.Event()
    gui_events = []

    async def slow():
        started.set()
        await release.wait()
        return threading.current_thread().name

    runner.submit(slow(), lambda result, failure: results.append((result, failure, threading.current_thread().name)))
    QTimer.singleShot(0, lambda: gui_events.append(threading.current_thread().name))
    try:
        qtbot.waitUntil(lambda: started.is_set() and bool(gui_events), timeout=3000)
        assert gui_events == [threading.current_thread().name]
        assert results == []  # Qt processed its callback while worker completion was explicitly held.
    finally:
        runner._loop.call_soon_threadsafe(release.set)
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


def test_cloud_missing_credentials_are_reported_not_raised(window, qtbot, monkeypatch):
    from dtflow.core.models import ConnectionStatus
    from dtflow.ui import main_window

    async def missing_credentials(selection, *, saved):
        assert selection.name == "cloud"
        assert saved is window._saved
        # Deliver the service's missing-credentials outcome through the real
        # background/GUI boundary. Credential resolution and TLS have their own
        # lower-layer tests; cold backend setup can exceed this UI wait.
        return ConnectionStatus(
            "cloud",
            "offline://fixture",
            "2026-10-18T00:00:00+00:00",
            "unknown",
            "failed",
            "unknown",
            (("authentication_code", "missing_credentials"),),
        )

    monkeypatch.setattr(main_window, "check_connection", missing_credentials)
    window.form.backendCombo.setCurrentText("cloud")
    window.form.checkButton.click()
    assert not window.form.checkButton.isEnabled()
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

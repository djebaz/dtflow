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

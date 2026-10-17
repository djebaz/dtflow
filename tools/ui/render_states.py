"""Render real Qt widgets against offline fixtures. Never admits provider network traffic.

Run with the locked dev environment: uv run python tools/ui/render_states.py --out tmp/ui-renders
The calibration grid is a fixture, not a generated photograph. Cloud receipts use the
same in-memory API/transport as the asset tests; no real file upload occurs.
"""

import argparse
import asyncio
import faulthandler
import json
import os
import sys
import tempfile
import time
import traceback
from dataclasses import replace
from pathlib import Path
from unittest.mock import PropertyMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from support.network_guard import install  # noqa: E402

install()  # before provider/client imports; the guard rejects unregistered DNS/sockets
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ["QT_QPA_PLATFORM"] == "offscreen":
    # Qt 6.12 defaults to an 800x800 virtual screen. Fix the screen geometry
    # before QApplication starts so fullscreen renders match normal 1600x1000.
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={ROOT / 'tools/ui/offscreen-screen.json'}"
if sys.platform == "win32" and os.environ["QT_QPA_PLATFORM"] == "offscreen":
    # Offscreen Qt on Windows otherwise searches PySide6's empty font folder.
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit, QListWidget, QPushButton, QScrollArea  # noqa: E402
from support.cloud_assets import DtPlus, lora_file  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.core.export import ExportResult  # noqa: E402
from dtflow.core.gallery import GalleryDetail, GalleryItem  # noqa: E402
from dtflow.core.gallery import GalleryPage as GalleryResults  # noqa: E402
from dtflow.core.image_operations import ImageOperationSupport  # noqa: E402
from dtflow.core.models import ConnectionStatus, InventoryEntry, InventorySnapshot, LoRASelection, ValidationError  # noqa: E402
from dtflow.persistence.cache_coordination import CacheCleanupError  # noqa: E402
from dtflow.persistence.image_recycle import ReconcileResult  # noqa: E402
from dtflow.persistence.original_io import OriginalImagesBusyError, OriginalImagesUnavailableError  # noqa: E402
from dtflow.persistence.person_models import ModelRef, PersonModels  # noqa: E402
from dtflow.persistence.store import PersistenceError, StateBusyError  # noqa: E402
from dtflow.services import artifact_paths, session  # noqa: E402
from dtflow.services.artifact_paths import ArtifactReadPaths  # noqa: E402
from dtflow.services.cache_management import CacheCategory  # noqa: E402
from dtflow.services.credentials import KEY, SavedCredentials, SecureStorageUnavailable  # noqa: E402
from dtflow.services.gallery import MISSING_ISSUE, item_from_record, load_detail  # noqa: E402
from dtflow.services.image_recycle import RecyclePlan, RecycleResult, RecycleTarget, RecycleUnavailable  # noqa: E402
from dtflow.services.person_selection import PersonAnalysis, PersonLayers, colored_preview  # noqa: E402
from dtflow.ui import desktop_open, system_trash  # noqa: E402
from dtflow.ui import gallery_page as gallery_ui  # noqa: E402
from dtflow.ui import lora_panel as lora_ui  # noqa: E402
from dtflow.ui import main_window as window_ui  # noqa: E402
from dtflow.ui.activity_strip import activity_strip  # noqa: E402
from dtflow.ui.generation_page import GenerationPage  # noqa: E402
from dtflow.ui.inline_feedback import InlineNotice  # noqa: E402
from dtflow.ui.main_window import MainWindow  # noqa: E402
from dtflow.ui.system_trash import TRASH_NAME  # noqa: E402
from dtflow.ui.theme import apply_theme  # noqa: E402

RENDERED = {}  # state name -> {"seconds", "blank"}, in render order
STARTED = time.monotonic()


def report(target):
    """Print progress for one written state and remember it for the final summary."""
    image = QImage(str(target)).scaled(16, 16)
    colors = {image.pixel(x, y) for x in range(image.width()) for y in range(image.height())}
    name = Path(target).stem
    if name in RENDERED:
        raise RuntimeError(f"State {name} was rendered twice")
    seconds = round(time.monotonic() - STARTED, 1)
    RENDERED[name] = {"seconds": seconds, "blank": len(colors) <= 1}
    print(f"[{len(RENDERED):3}] +{seconds:5.1f}s {target}")
    # Dump Python stacks before CI's step timeout if the next state stops making progress.
    faulthandler.dump_traceback_later(30, repeat=True)


def summarize():
    """Print the run total and fail when any render is a single flat color."""
    blank = [name for name, entry in RENDERED.items() if entry["blank"]]
    print(f"Rendered {len(RENDERED)} states in {time.monotonic() - STARTED:.1f}s.")
    if blank:
        raise SystemExit(f"Blank renders: {', '.join(blank)}")


def wait_for(predicate, timeout=8):
    until = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= until:
            raise RuntimeError("Offline UI fixture timed out.")
        QTest.qWait(10)
    QTest.qWait(30)


def save(widget, out, name):
    target = out / f"{name}.png"
    if not widget.grab().save(str(target)):
        raise RuntimeError(f"Could not save {target.name}")
    report(target)


@patch.object(GenerationPage, "request_cached", new=lambda self, **_: None)
def render_connection_states(app, state, out):
    """Connection fixtures with synthetic credentials and intercepted provider/file mutations."""
    # Unused Generate inventory restoration can otherwise resolve the real process environment on Cloud selection.
    environ = {}
    saved = SavedCredentials(Vault())
    shell = MainWindow(saved=saved, environ=environ, state_dir=state / "connection-fixtures", no_cache=True)

    def snap(name, expected_status, remove_enabled):
        shell.refresh_credentials()
        assert not shell.form.keyEdit.text()
        assert shell.form.statusLabel.text().startswith("Not checked.")
        assert expected_status in shell.form.credentialStatusLabel.text()
        assert shell.form.removeKeyButton.isEnabled() == remove_enabled
        QTest.qWait(100)
        save(shell.form, out, name)

    try:
        form = shell.form
        form.resize(1600, 1000)
        shell.show()
        form.connectionNavigationButton.click()
        form.backendCombo.setCurrentText("local")
        assert form.endpointEdit.isEnabled() and not form.credentialBox.isEnabled()
        snap("connection-01-local-idle", "No API key available.", False)

        form.backendCombo.setCurrentText("cloud")
        assert not form.endpointEdit.isEnabled() and form.credentialBox.isEnabled()
        snap("connection-02-cloud-no-key", "No API key available.", False)

        # These are synthetic source observations, never the process environment or the OS vault.
        environ[KEY] = "offline-environment-key"
        snap("connection-03-cloud-env-key", "from the environment", True)

        environ.clear()
        saved.save("offline-saved-key")  # Writes only the injected in-memory Vault.
        snap("connection-04-cloud-saved-key", "Using the key saved", True)

        with patch.object(saved, "_store", side_effect=SecureStorageUnavailable):
            snap("connection-05-storage-unavailable", "Secure storage is unavailable", False)
        render_connection_outcomes(shell, out)
        render_connection_management(shell, out)
        render_generate_configuration_states(shell, out)
        render_active_queue_states(shell, out)
        render_gallery_composition_states(shell, state, out)
    finally:
        shell.runner.shutdown()
        shell.form.close()
        app.processEvents()


def render_connection_outcomes(shell, out):
    """Check/key/preset presentation through real handlers; all service work is intercepted."""
    form, saved = shell.form, shell._saved

    def snap(name):
        QTest.qWait(100)
        save(form, out, name)

    def hold_check(coroutine, callback):
        coroutine.close()  # Show Checking without starting a backend or leaving a gated task behind.

    with patch.object(shell.runner, "submit", side_effect=hold_check), patch.object(shell, "remember"):
        shell.check()
        assert not form.checkButton.isEnabled() and form.statusCard.property("state") == "warning"
        snap("connection-06-checking")
        status = ConnectionStatus(
            "cloud", "offline://connection-fixture", "2026-10-17T12:00:00+00:00", "ok", "ok", "unknown"
        )
        shell._checked(status, None)
        assert form.checkButton.isEnabled() and form.statusCard.property("state") == "ok"
        snap("connection-07-connected")
        shell._checked(None, TimeoutError())
        assert form.statusCard.property("state") == "error"
        snap("connection-08-check-failed")

    shell._selection_changed()
    shell.refresh_credentials()
    assert form.removeKeyButton.isEnabled()
    form.removeKeyButton.click()
    assert form.removeKeyButton.property("confirmationPending")
    assert not form.removeKeyButton.property("confirmationDanger")
    snap("connection-09-remove-key-confirmation")
    shell.inline.dismiss()
    with patch.object(saved, "save", side_effect=SecureStorageUnavailable):
        form.keyEdit.setText("offline-saved-key")
        form.saveKeyButton.click()
        assert form.credentialStatusLabel.text().startswith("Key not saved:") and not form.keyEdit.text()
        snap("connection-10-key-save-failed")
    shell.refresh_credentials()
    with patch.object(saved, "remove", side_effect=SecureStorageUnavailable):
        form.removeKeyButton.click()
        assert form.removeKeyButton.property("confirmationPending")
        form.removeKeyButton.click()
        assert form.credentialStatusLabel.text().startswith("Key not removed:")
        snap("connection-11-key-remove-failed")
    shell.refresh_credentials()

    # Native picker selections are synthetic; import/export services never read or write these paths.
    chosen = str(shell.state_dir / "offline-presets.json")
    library = [{"name": "Offline fixture", "configuration": {}}]
    with patch.object(window_ui, "load_presets", return_value=library):
        shell.refresh_presets()
    form.connectionWorkspaceScroll.ensureWidgetVisible(form.presetLibraryBox)
    with patch.object(window_ui.QFileDialog, "getOpenFileName", return_value=(chosen, "")):
        form.importPresetsButton.click()
        assert form.importPresetsButton.property("confirmationPending")
        assert not form.importPresetsButton.property("confirmationDanger")
        snap("connection-12-replace-presets-confirmation")
        shell.inline.dismiss()
    with patch.object(window_ui, "load_presets", side_effect=ValidationError("Offline fixture: invalid preset JSON.")):
        shell.refresh_presets()
        assert not form.exportPresetsButton.isEnabled()
        assert "unreadable" in form.presetLibraryStatusLabel.text()
        snap("connection-13-presets-unreadable")
    with patch.object(window_ui, "load_presets", return_value=library):
        shell.refresh_presets()
    with (
        patch.object(window_ui.QFileDialog, "getOpenFileName", return_value=(chosen, "")),
        patch.object(
            window_ui, "import_presets", side_effect=ValidationError("Offline fixture: preset import refused.")
        ),
    ):
        form.importPresetsButton.click()
        assert form.importPresetsButton.property("confirmationPending")
        form.importPresetsButton.click()
        assert shell.inline.form.inlineTitleLabel.text() == "Presets not imported"
        snap("connection-14-preset-import-failed")
        shell.inline.dismiss()
    with (
        patch.object(window_ui.QFileDialog, "getSaveFileName", return_value=(chosen, "")),
        patch.object(window_ui, "export_presets", side_effect=OSError("Offline fixture: preset destination refused.")),
    ):
        assert form.exportPresetsButton.isEnabled()
        form.exportPresetsButton.click()
        assert shell.inline.form.inlineTitleLabel.text() == "Presets not exported"
        snap("connection-15-preset-export-failed")
        shell.inline.dismiss()
    shell.refresh_presets()


def render_connection_management(shell, out):
    """Catalogue/cache/export presentation; mutations and queued workers never execute."""
    form, strip = shell.form, shell.progress_strip

    def snap(name):
        QTest.qWait(100)
        save(form, out, name)

    def clear():
        shell.inline.dismiss()
        for key in tuple(strip.rows):
            strip.finish(key)

    def hold_worker(coroutine, callback):
        coroutine.close()  # No purge, export, preference write or cache measurement is started.

    shell.shell.leftScroll.ensureWidgetVisible(form.customLorasBox)
    with patch.object(window_ui, "Catalogue") as catalogue_type:
        catalogue = catalogue_type.return_value
        catalogue.user_loras.return_value = ("offline-count-only-definition",)
        catalogue.user_loras_path.exists.return_value = True
        catalogue.user_loras_path.unlink.side_effect = OSError("Offline fixture: catalogue removal refused.")
        shell.refresh_custom_loras()
        assert form.clearCustomLorasButton.isEnabled()
        form.clearCustomLorasButton.click()
        assert form.clearCustomLorasButton.property("confirmationPending")
        assert form.clearCustomLorasButton.property("confirmationDanger")
        snap("connection-16-clear-custom-loras-confirmation")
        clear()
        with (
            patch.object(
                window_ui.QFileDialog,
                "getOpenFileName",
                return_value=(str(shell.state_dir / "offline-loras.json"), ""),
            ),
            patch.object(window_ui, "import_user_loras", side_effect=ValueError("Offline fixture: invalid LoRA JSON.")),
        ):
            form.importCustomLorasButton.click()
            assert shell.inline.form.inlineTitleLabel.text() == "Custom LoRAs not imported"
            snap("connection-17-custom-lora-import-failed")
            clear()
        form.clearCustomLorasButton.click()
        assert form.clearCustomLorasButton.property("confirmationPending")
        form.clearCustomLorasButton.click()
        assert shell.inline.form.inlineTitleLabel.text() == "Custom LoRAs not cleared"
        snap("connection-18-custom-lora-clear-failed")
        clear()
    shell.refresh_custom_loras()
    shell.shell.leftScroll.verticalScrollBar().setValue(0)

    with patch.object(shell.runner, "submit", side_effect=hold_worker):
        form.connectionWorkspaceScroll.ensureWidgetVisible(form.cacheManagementBox)
        # Presentation fixture: refresh_cache does not reset its label, so restore the canonical initial text.
        form.cacheStatusLabel.setText("Measuring cache…")
        shell.refresh_cache()
        snap("connection-19-cache-measuring")
        usage = (
            CacheCategory("inventory", 2, 4096),
            CacheCategory("thumbnails", 0, 0),
            CacheCategory("derived", 3, 8192),
        )
        shell._cache_measured(usage, None)
        assert "Inventory" in form.cacheStatusLabel.text() and "Derived" in form.cacheStatusLabel.text()
        snap("connection-20-cache-measured")
        shell._cache_measured(None, OSError("Offline fixture"))
        assert "unavailable" in form.cacheStatusLabel.text()
        snap("connection-21-cache-unavailable")
        shell._cache_measured(usage, None)
        form.clearAllCacheButton.click()
        assert form.clearAllCacheButton.property("confirmationPending")
        assert form.clearAllCacheButton.property("confirmationDanger")
        snap("connection-22-cache-clear-confirmation")
        clear()
        shell.activity.set(dispatching=True)
        try:
            form.clearAllCacheButton.click()
            assert any(row.activityLabel.text() == "Cache is busy" for row in strip.rows.values())
            snap("connection-23-cache-busy")
        finally:
            shell.activity.set(dispatching=False)
            clear()
        form.clearAllCacheButton.click()
        assert form.clearAllCacheButton.property("confirmationPending")
        form.clearAllCacheButton.click()
        assert strip.rows["cache"].activityLabel.text() == "Clearing disposable cache"
        snap("connection-24-cache-clearing")
        names = "inventory, thumbnails, derived"
        shell._cache_cleared(names, None)
        shell._cache_measured(tuple(CacheCategory(item.name, 0, 0) for item in usage), None)
        snap("connection-25-cache-cleared")
        clear()
        # Partial cleanup is an error in the existing presenter, never a fabricated amber success.
        shell._cache_cleared(
            names,
            CacheCleanupError(
                "Cache cleanup stopped at derived; completed categories: inventory, thumbnails. "
                "Partial cleanup may have occurred; refresh sizes and retry."
            ),
        )
        shell._cache_measured((CacheCategory("inventory", 0, 0), CacheCategory("thumbnails", 0, 0), usage[2]), None)
        snap("connection-26-cache-partial")
        clear()
        shell._cache_cleared(names, OSError("Offline fixture"))
        snap("connection-27-cache-clear-failed")
        clear()

        export = shell.image_export
        destination = str(shell.state_dir / "offline-export")
        form.connectionWorkspaceScroll.ensureWidgetVisible(form.imageExportBox)
        form.imageExportFolderEdit.setText(destination)
        form.imageExportScopeCombo.setCurrentIndex(form.imageExportScopeCombo.findData("all"))
        assert form.imageExportStartButton.isEnabled() and not form.imageExportCancelButton.isEnabled()
        snap("connection-28-export-idle")
        form.imageExportStartButton.click()
        assert export.running and not form.imageExportStartButton.isEnabled()
        assert form.imageExportCancelButton.isEnabled()
        snap("connection-29-export-collecting")
        export._progress(2, 5)
        snap("connection-30-export-progress")
        form.imageExportCancelButton.click()
        assert export._stop.is_set() and not form.imageExportCancelButton.isEnabled()
        snap("connection-31-export-stopping")
        export._done(ExportResult(destination, 5, 3, 0, False, noise=1, blank=1, skipped=2), None)
        assert not export.running and form.imageExportOpenButton.isEnabled()
        snap("connection-32-export-success")
        clear()
        form.imageExportStartButton.click()
        assert export.running
        export._done(
            ExportResult(
                destination,
                5,
                3,
                1,
                False,
                warnings=("Offline fixture: one source was unavailable.",),
                skipped=1,
            ),
            None,
        )
        snap("connection-33-export-partial")
        clear()
        form.imageExportStartButton.click()
        assert export.running
        export._progress(1, 5)
        export.cancel()
        export._done(ExportResult(destination, 5, 2, 0, True, skipped=1), None)
        snap("connection-34-export-stopped")
        clear()
        form.imageExportStartButton.click()
        assert export.running
        export._done(None, OSError("Offline fixture"))
        snap("connection-35-export-failed")
        clear()
        form.imageExportFolderEdit.clear()
        form.imageExportStartButton.click()
        assert not export.running and strip.rows["export"].activityLabel.text() == "Export not started"
        snap("connection-36-export-not-started")
        clear()
        form.imageExportFolderEdit.setText(destination)
        form.imageExportStartButton.click()
        assert export.running
        export._done(ExportResult(destination, 0, 0, 0, False), None)
        assert "No saved images match" in form.imageExportStatusLabel.text()
        snap("connection-37-export-no-matches")
        clear()
        form.imageExportFolderEdit.clear()
        form.imageExportPromptsCheck.setChecked(True)
        form.imageExportSidecarsCheck.setChecked(False)
        form.imageExportSeparateCheck.setChecked(True)
        export._preferences_dirty = False
        export._loaded(None, OSError("Offline fixture"))
        assert "could not be read" in form.imageExportStatusLabel.text()
        snap("connection-38-export-preferences-unavailable")
        form.resize(1000, 900)
        shell._responsive_workspace()
        assert shell.shell.rightRail.isHidden()
        snap("connection-39-narrow")
        form.resize(1600, 1000)
        shell._responsive_workspace()


def render_generate_configuration_states(shell, out):
    """Local editors/inventory/configuration plus asset progress; no provider or upload worker runs."""
    form, page, controller = shell.form, shell.generation_page, shell.generation
    strip = shell.progress_strip

    def snap(name):
        QTest.qWait(100)
        save(form, out, name)

    def clear():
        shell.inline.dismiss()
        for key in tuple(strip.rows):
            strip.finish(key)

    def hold_worker(coroutine, callback):
        coroutine.close()

    def reject_selection(parent, **options):
        # Defensive callback failure fixture; the real chooser normally prevents an empty identifier.
        options["callback"](("", 1.0))

    with patch.object(shell.runner, "submit", side_effect=hold_worker):
        form.backendCombo.setCurrentText("local")
        form.generationNavigationButton.click()
        shell.shell.leftScroll.verticalScrollBar().setValue(0)
        assert not page.includeCloudCheck.isHidden() and page.simulationBox.isHidden()
        snap("generate-20-local-controls")
        snapshot = InventorySnapshot(
            "local",
            "offline-fixture",
            "2026-10-17T12:00:00+00:00",
            False,
            (
                InventoryEntry(
                    "offline-checkpoint.ckpt", "model", "Offline checkpoint", None, "present", "offline-fixture"
                ),
                InventoryEntry(
                    "offline-style.ckpt",
                    "lora",
                    "Offline style",
                    None,
                    "present",
                    "offline-fixture",
                    default_weight=0.8,
                ),
            ),
            (("model_metadata", "present"),),
        )
        controller._show_inventory(snapshot, "cached")
        page.modelCombo.setCurrentText("offline-checkpoint.ckpt")
        page.modelCombo.showPopup()
        editor = shell.inline._editor
        assert editor is not None
        choices = editor.findChild(QListWidget, "inlineSelectionChoices")
        assert choices.count() > 0
        snap("generate-21-checkpoint-editor")
        editor.findChild(QLineEdit, "inlineSelectionSearch").setText("no-offline-match")
        assert choices.count() == 0
        snap("generate-22-checkpoint-no-matches")
        identifier = editor.findChild(QLineEdit, "inlineSelectionIdentifier")
        assert not identifier.isReadOnly()
        identifier.setText("offline-custom-checkpoint.ckpt")
        snap("generate-23-custom-checkpoint-editor")
        clear()  # Do not apply a different checkpoint or submit anything.

        lora = controller.lora
        with patch.object(lora, "_candidates", [("offline-style.ckpt", 0.8)]):
            page.addLoraButton.click()
            editor = shell.inline._editor
            assert editor is not None
            editor.findChild(QListWidget, "inlineSelectionChoices").setCurrentRow(0)
            snap("generate-24-lora-weight-editor")
            clear()
        with patch.object(lora, "_candidates", [("offline-style.ckpt", 4.2)]):
            page.addLoraButton.click()
            editor = shell.inline._editor
            assert editor is not None
            editor.findChild(QListWidget, "inlineSelectionChoices").setCurrentRow(0)
            assert any("outside 0.0" in label.text() for label in editor.findChildren(QLabel))
            snap("generate-25-lora-weight-hint")
            clear()
        with patch.object(lora_ui, "choose_searchable", side_effect=reject_selection):
            page.addLoraButton.click()
            assert "LoRA not added" in page.compatibilityStatusLabel.text()
            snap("generate-26-lora-not-added")
        assert controller._catalogue.view().unclassified_checkpoints
        controller._show_unclassified_models()
        assert shell.inline.form.inlineTitleLabel.text() == "Unclassified files"
        snap("generate-27-unclassified-models")
        clear()
        controller.refresh()
        assert not page.refreshInventoryButton.isEnabled()
        snap("generate-28-inventory-refreshing")
        controller._refreshed(None, OSError("Offline fixture"))
        assert "showing previous inventory" in page.inventoryEvidenceLabel.text()
        snap("generate-29-inventory-refresh-failed")
        controller._inventory_notice = controller._inventory_error = ""
        controller._show_inventory(snapshot, "cached")
        assert "cached" in page.inventoryEvidenceLabel.text()
        snap("generate-30-inventory-cache-only")

        page.modelCombo.setCurrentText("offline-checkpoint.ckpt")
        page.promptEdit.setPlainText("Offline configuration fixture; no generation submitted.")
        page.seedEdit.setText("123")
        request = controller.request_for_queue()[0]  # Pure form resolution, never queue creation/dispatch.
        config_path = shell.state_dir / "offline-config.json"
        controller._loaded(config_path, request, None)
        assert "Configuration loaded" in page.generationStatusLabel.text()
        snap("generate-31-config-loaded")
        previous_prompt = page.promptEdit.toPlainText()
        controller._loaded(config_path, None, ValidationError("Offline fixture: invalid configuration."))
        assert page.promptEdit.toPlainText() == previous_prompt
        assert "Configuration not loaded" in page.generationStatusLabel.text()
        snap("generate-32-config-load-failed")
        controller._metadata_path = shell.state_dir / "missing-metadata.json"
        controller.show_metadata()
        assert shell.inline.form.inlineTitleLabel.text() == "Metadata unavailable"
        snap("generate-33-metadata-unavailable")
        clear()
        # Synthetic saved-outcome presentation only: these paths are never written or exported.
        outcome = session.GenerationOutcome(
            "offline-partial-attempt",
            "local",
            (str(shell.state_dir / "offline-output.png"),),
            str(shell.state_dir / "offline-metadata.json"),
            ("Offline fixture: advisory warning.",),
            (),
            request,
        )
        controller._generated(outcome, None)
        assert "generation-result" in strip.rows
        snap("generate-34-partial-result")
        clear()
        form.backendCombo.setCurrentText("cloud")
        with patch.object(lora, "choose_asset_file", return_value=str(shell.state_dir / "offline-asset.ckpt")):
            page.prepareAssetButton.click()
            assert "asset" in strip.rows and not page.prepareAssetButton.isEnabled()
            snap("generate-35-asset-preparing")
        controller._set_busy(False)
        clear()
        # Receipt states are synthetic registry observations; no file, account or metadata worker is read.
        record = {
            "asset_id": "offline-receipt",
            "identifier": "offline-style.ckpt",
            "state": "unknown",
            "evidence": "offline synthetic receipt",
            "sha256": "0" * 64,
            "size": 1024,
            "origin": "offline-fixture",
        }
        page.detailsToggle.setChecked(True)
        lora._prepared(record, None)
        lora._listed([record], None)
        assert "Receipt unknown" in page.assetStatusLabel.text()
        shell.shell.rightScroll.ensureWidgetVisible(page.assetStatusLabel)
        snap("generate-36-asset-partial")
        clear()
        lora._prepared(None, OSError("Offline fixture"))
        assert strip.rows["asset"].property("feedbackState") == "error"
        snap("generate-37-asset-failed")
        clear()
        lora._listed(None, OSError("Offline fixture"))
        assert "Registry unavailable" in page.assetStatusLabel.text()
        snap("generate-38-asset-registry-unavailable")
        lora._start_reconcile(record["asset_id"])
        assert "Reconciling" in page.assetStatusLabel.text() and not page.prepareAssetButton.isEnabled()
        snap("generate-39-asset-reconciling")
        controller._set_busy(False)
        confirmed = dict(record, state="confirmed")
        lora._listed([confirmed], None)
        lora.set_loras((LoRASelection(record["identifier"], 0.8),))
        assert lora._bind_confirmed(record["identifier"])
        assert lora.receipt_ids() == (record["asset_id"],)
        snap("generate-40-asset-receipt-bound")
        clear()


def render_active_queue_states(shell, out):
    """Synthetic read views through the real controller; no queue is persisted, posted or dispatched."""
    form, queue, page = shell.form, shell.queue, shell.queue_page
    queue_id = "offline-queue-0001"
    stamp = "2026-10-17T12:00:00+00:00"

    def snap(name):
        QTest.qWait(100)
        # Keep this feature's review images together in the Actions archive.
        target_dir = out / "images" / "pagination" if "-pager-" in name else out
        target_dir.mkdir(parents=True, exist_ok=True)
        save(form, target_dir, name)

    def hold_worker(coroutine, callback):
        coroutine.close()

    def present(status, states, *, owned=False, started=True, edit=False):
        items = []
        for position, state in enumerate(states, 1):
            attempt = f"attempt-{position:04d}" if state not in ("pending", "cancelled") else None
            items.append(
                dict(
                    item_id=f"item-{position:04d}",
                    position=position,
                    state=state,
                    overridden=False,
                    attempt_id=attempt,
                    request_id=f"request-{position:04d}",
                    seed=100 + position,
                    artifacts=[
                        str(shell.state_dir / f"output-{position}.png"),
                        str(shell.state_dir / f"attempt-{position}" / "metadata.json"),
                    ]
                    if state == "succeeded"
                    else [],
                    failure={"code": f"offline_{state}"} if state in ("failed", "unknown", "save_failed") else None,
                    operation="inpaint" if edit else "txt2img",
                    strength=0.7 if edit else None,
                )
            )
        counts = {
            state: states.count(state)
            for state in ("pending", "running", "succeeded", "failed", "unknown", "save_failed", "cancelled")
        }
        events = [dict(kind="created", at_utc=stamp, detail={"items": len(items), "cap": 2})]
        if started:
            events.append(dict(kind="started", at_utc=stamp, detail={}))
        view = dict(
            queue_id=queue_id,
            status=status,
            backend={"name": "local", "endpoint": None},
            cap=2,
            summary=counts,
            items=items,
            blocked_by_uncertainty=counts["unknown"],
            pending_controls=[],
            events=events,
        )
        queue._dispatching = queue_id if owned else None
        queue._dispatch_started = owned and started
        queue._selected = queue_id
        queue._activity.set(dispatching=owned)
        queue._read_done(([view], view, 2), None)
        queue._timer.stop()  # No polling between presentation-only captures.
        assert queue.model.rowCount() == len(items)
        return view

    with patch.object(shell.runner, "submit", side_effect=hold_worker):
        form.backendCombo.setCurrentText("local")
        form.queueNavigationButton.click()
        request = shell.generation.request_for_queue(preview=True)[0]
        present("running", ["succeeded", "running", "pending"], owned=True)
        assert page.pauseQueueButton.isEnabled() and queue._activity.run_state.action == "pause"
        snap("queues-11-running")
        present("paused", ["succeeded", "pending", "pending"], owned=True)
        assert page.resumeQueueButton.isEnabled() and queue._activity.run_state.action == "resume"
        snap("queues-12-paused-after-start")
        present("running", ["succeeded", "running", "pending"])
        assert queue._activity.run_state.action is None
        snap("queues-13-foreign-running")
        present("blocked", ["unknown", "pending", "pending"])
        assert page.overrideReservationButton.isEnabled() and not page.resumeQueueButton.isEnabled()
        snap("queues-14-blocked-unknown")
        page.overrideReservationButton.click()
        assert shell.inline._armed and shell.inline._armed[0] is page.overrideReservationButton
        snap("queues-15-override-confirmation")
        shell.inline.dismiss()  # Never acknowledge or mutate a reservation.
        present("blocked", ["succeeded", "failed", "unknown", "save_failed", "cancelled", "running"], owned=True)
        snap("queues-16-item-states")
        page.queueItemsView.setCurrentIndex(queue.model.index(2, 0))
        assert "may have reached" in page.uncertaintyLabel.text()
        assert page.attemptIdLabel.toolTip() == "attempt-0003"
        snap("queues-17-item-inspection")
        present("paused", ["pending", "pending"], started=False)
        page.planBox.setChecked(True)
        page.planSeedsEdit.setText("not-a-seed")
        with patch.object(queue, "_request_source", return_value=(request, "local", None, ())):
            page.planDryRunButton.click()
        assert page.planSeedsEdit.property("invalid")
        shell.shell.leftScroll.ensureWidgetVisible(page.planSeedsEdit)
        snap("queues-18-plan-invalid")
        page.planSeedsEdit.clear()
        queue._mark_plan(None)
        with (
            patch.object(queue, "_editing", return_value=True),
            patch.object(queue, "_edit_source", return_value=object()),
        ):
            present("paused", ["pending", "pending"], started=False, edit=True)
            page.planStrengthsEdit.setText("0.5; 0.7")
            assert queue._plan_for(request, "Plan not valid") is not None
            assert not page.planStrengthsEdit.isHidden() and queue.model.row_at(0).edit_text == "Inpaint · 0.7"
            shell.shell.leftScroll.ensureWidgetVisible(page.planStrengthsEdit)
            snap("queues-19-inpaint-plan")
        page.planStrengthsEdit.clear()
        page.planBox.setChecked(False)
        queue._sync()
        shell.shell.leftScroll.verticalScrollBar().setValue(0)
        queue._syncing = True
        page.concurrencySpinBox.setValue(4)
        queue._syncing = False
        queue._set_level(4)
        # Defensive validator refusal: the bounded spin box normally prevents values outside 1..4.
        queue._cap_changed(5)
        assert "Concurrency not changed" in page.queueMessageLabel.text()
        assert page.concurrencySpinBox.property("level") == "elevated"
        snap("queues-20-concurrency-refused")
        queue._message(window_ui.Presentation("unknown", "Offline fixture", "No dispatch was performed."))
        present("finished", ["succeeded", "succeeded", "succeeded"])
        assert not page.startQueueButton.isEnabled() and page.queueProgressBar.value() == 3
        snap("queues-21-finished-table")
        present("cancelled", ["succeeded", "cancelled", "cancelled"])
        assert not page.resumeQueueButton.isEnabled() and page.queueProgressBar.value() == 3
        snap("queues-22-cancelled-table")
        form.resize(1000, 900)
        shell._responsive_workspace()
        assert shell.shell.rightRail.isHidden()
        snap("queues-23-narrow")
        form.resize(1600, 1000)
        shell._responsive_workspace()
        queue._dispatching = None
        queue._dispatch_started = False
        queue._selected = None
        queue._activity.set(dispatching=False)
        queue._read_done(([], None, 2), None)
        queue._timer.stop()


def render_gallery_composition_states(shell, state, out):
    """Synthetic gallery projection and GUI settlements only; no DB authority, export or output mutation."""
    form, gallery = shell.form, shell.gallery
    export = shell.image_export
    image = QImage(1024, 768, QImage.Format.Format_RGB32)
    image.fill(QColor("#8094a8"))
    root = state / "gallery-composition"
    item = GalleryItem(
        identity="offline-gallery:0",
        attempt_id="offline-gallery",
        request_id="offline-request",
        path=str(root / "output.png"),
        metadata_path=str(root / "metadata.json"),
        saved_at_utc="2026-10-17T12:00:00+00:00",
        backend_id="fake",
        model="offline-checkpoint.ckpt",
        prompt="Synthetic offline gallery projection; no image generation.",
        negative="",
        width=1024,
        height=768,
        loras=(),
        request_json=json.dumps(
            {"model_id": "offline-checkpoint.ckpt", "seed": 123, "operation": "txt2img", "steps": 20}
        ),
        file_size=None,
    )

    def snap(name):
        QTest.qWait(100)
        if "-pager-" in name:
            bar = gallery._widget("galleryPagerBar")
            for number, button in gallery.pager.buttons.items():
                origin = button.mapTo(bar, QPoint(0, 0))
                if not button.isVisibleTo(bar) or not (
                    8 <= origin.x()
                    and origin.x() + button.width() <= bar.width() - 8
                    and 0 <= origin.y()
                    and origin.y() + button.height() <= bar.height()
                ):
                    raise AssertionError(
                        f"Pager chip {number} clipped in {name}: "
                        f"x={origin.x()} width={button.width()} bar_width={bar.width()}"
                    )
        if name.startswith("images-") and name.endswith("-fullscreen"):
            actual = form.grab().size()
            if actual != QSize(1600, 1000):
                raise AssertionError(f"{name}: expected 1600x1000, got {actual.width()}x{actual.height()}")
        # Store the approved pager review states together in one artifact folder.
        # Other Images screenshots keep their existing paths.
        target_dir = out / "image-navigation" if "-pager-" in name else out
        target_dir.mkdir(parents=True, exist_ok=True)
        save(form, target_dir, name)

    def hold_worker(coroutine, callback):
        coroutine.close()

    def present(record):
        gallery.items = [record]
        gallery.total, gallery.offset = 1, 0
        gallery._render_page()
        gallery._rendered_page = (0, (record,))
        if not record.issue:
            gallery._thumbnail_done(gallery._thumb_versions[record.identity], record.identity, (image, ""), None)
        gallery.select(record.identity)
        if not record.issue:
            gallery._image_done(gallery._image_generation, record.identity, (image, ""), None)
        else:
            gallery._image_done(gallery._image_generation, record.identity, (None, record.issue), None)
        metadata = json.dumps({"fixture": True, "request": json.loads(record.request_json)}, indent=2)
        gallery._detail_done(gallery._detail_generation, record.identity, GalleryDetail(record, metadata), None)

    with (
        patch.object(shell.runner, "submit", side_effect=hold_worker),
        patch.object(gallery, "_schedule_save"),
    ):
        gallery.save_timer.stop()
        form.imagesNavigationButton.click()
        gallery.auto_button.setChecked(False)
        gallery.auto_timer.stop()
        gallery.filter_timer.stop()
        gallery._pending_refresh = None
        gallery._loading = False
        gallery._snapshot_at = None
        shell.inline.dismiss()
        for key in tuple(shell.progress_strip.rows):
            shell.progress_strip.finish(key)
        present(item)
        gallery.show_view("Grid")
        gallery._unselect_all()
        # The pager's static/fullscreen states are captured before live lazy loading is wired.
        # Synthetic identities and solid-color fixture pixels never call a provider.
        pager_records = [
            replace(item, identity=f"offline-gallery:{index}", attempt_id=f"offline-attempt:{index}")
            for index in range(81, 105)
        ]
        gallery.items, gallery.total, gallery.offset = pager_records, 342, 80
        gallery._render_page()
        gallery._rendered_page = (80, tuple(pager_records))
        gallery.pager.present(total=342, visible_start=81, visible_end=104, loaded=160, active_page=2)
        assert gallery.pager.buttons[2].isChecked()
        assert gallery._widget("galleryPagerBar").isVisible()
        snap("images-65-pager-grid-normal")
        gallery._widget("galleryFocusButton", QPushButton).click()
        try:
            assert gallery._widget("galleryPagerBar").isVisible()
            snap("images-66-pager-grid-fullscreen")
        finally:
            gallery._widget("galleryFocusButton", QPushButton).click()
        gallery.show_view("List")
        snap("images-67-pager-list-normal")
        gallery._widget("galleryFocusButton", QPushButton).click()
        try:
            assert gallery._widget("galleryPagerBar").isVisible()
            snap("images-68-pager-list-fullscreen")
        finally:
            gallery._widget("galleryFocusButton", QPushButton).click()
        gallery.select(pager_records[0].identity)
        gallery.show_view("Detail")
        snap("images-69-pager-detail-normal")
        gallery._widget("galleryFocusButton", QPushButton).click()
        try:
            assert gallery._widget("galleryPagerBar").isVisible()
            snap("images-70-pager-detail-fullscreen")
        finally:
            gallery._widget("galleryFocusButton", QPushButton).click()
        gallery.pager.present(total=342, visible_start=81, visible_end=104, loaded=160, active_page=2, loading=True)
        snap("images-71-pager-loading")
        # Empty-state screenshot must contain an actually empty gallery, not
        # an empty pager displayed over the previously selected Detail image.
        gallery.items, gallery.total, gallery.offset = [], 0, 0
        gallery.selected_id = None
        gallery._render_page()
        gallery._rendered_page = (0, ())
        gallery._clear_detail()
        gallery.show_view("Grid")
        gallery.pager.present(total=0, visible_start=0, visible_end=0, loaded=0, active_page=0)
        assert gallery._grid_empty is not None
        snap("images-72-pager-empty")

        # Multi-batch UX fixtures deliberately build distinct numbered records:
        # batch 1 (1-80), append batch 2 (81-160), then jump directly to the
        # short final batch (321-342). These are *offline projections*, not a
        # claim that production lazy-load orchestration is implemented yet.
        def multi_item(index):
            return replace(
                item,
                identity=f"offline-multipage:{index:04d}",
                attempt_id=f"offline-multipage-attempt:{index:04d}",
                path=str(root / f"image-{index:04d}.png"),
                metadata_path=str(root / f"metadata-{index:04d}.json"),
                prompt=f"Offline page fixture image {index:04d}; no generation.",
            )

        def paint_multi_thumbnail(record, index):
            # Distinct colored, numbered preview pixels make it possible to
            # inspect the actual viewport at a page boundary in the artifact.
            preview = QImage(240, 160, QImage.Format.Format_RGB32)
            palette = ("#526a83", "#64715d", "#7b6655", "#67627b", "#577879")
            preview.fill(QColor(palette[(index // 8) % len(palette)]))
            painter = QPainter(preview)
            painter.setPen(QColor("#f4f4f4"))
            painter.drawText(preview.rect(), Qt.AlignmentFlag.AlignCenter, f"IMAGE {index:04d}")
            painter.end()
            gallery._thumbnail_done(gallery._thumb_versions[record.identity], record.identity, (preview, ""), None)

        def multi_window(first, last, *, total=342, loaded=None, first_visible=None, last_visible=None):
            records = [multi_item(i) for i in range(first, last + 1)]
            gallery.items, gallery.total, gallery.offset = records, total, first - 1
            gallery._render_page()
            gallery._rendered_page = (gallery.offset, tuple(records))
            for index, record in zip(range(first, last + 1), records, strict=True):
                paint_multi_thumbnail(record, index)
            QTest.qWait(60)  # settle deferred Grid reflow before taking the screenshot
            shown_start = first if first_visible is None else first_visible
            shown_end = min(last, shown_start + 23) if last_visible is None else last_visible
            gallery.pager.present(
                total=total,
                visible_start=shown_start,
                visible_end=shown_end,
                loaded=len(records) if loaded is None else loaded,
                active_page=(shown_start - 1) // gallery_ui.PAGE_SIZE + 1,
            )
            assert len(gallery.items) == last - first + 1
            assert gallery.table.rowCount() == len(gallery.items)
            assert len(gallery._grid_buttons) == len(gallery.items)
            return records

        def grid_visible(*, loaded, loading=False):
            """Present the actual visible Grid card range, not a hardcoded fixture range."""
            QTest.qWait(60)
            viewport = gallery.grid_viewport
            visible = []
            for position, record in enumerate(gallery.items, gallery.offset + 1):
                card = gallery._grid_buttons[record.identity].parentWidget()
                y = card.mapTo(viewport, QPoint(0, 0)).y()
                if y < viewport.height() and y + card.height() > 0:
                    visible.append(position)
            assert visible, "Grid viewport has no visible synthetic cards"
            first, last = visible[0], visible[-1]
            gallery.pager.present(
                total=gallery.total,
                visible_start=first,
                visible_end=last,
                loaded=loaded,
                active_page=(first - 1) // gallery_ui.PAGE_SIZE + 1,
                loading=loading,
            )
            return first, last, set(visible)

        def list_visible(*, loaded):
            """Read displayed List rows from the real table viewport."""
            QTest.qWait(60)
            top = gallery.table.rowAt(0)
            assert top >= 0, "No visible List row"
            bottom = gallery.table.rowAt(gallery.table.viewport().height() - 1)
            if bottom < 0:
                bottom = gallery.table.rowCount() - 1
            first, last = gallery.offset + top + 1, gallery.offset + bottom + 1
            gallery.pager.present(
                total=gallery.total,
                visible_start=first,
                visible_end=last,
                loaded=loaded,
                active_page=(first - 1) // gallery_ui.PAGE_SIZE + 1,
            )
            return first, last

        gallery.show_view("Grid")
        first_batch = multi_window(1, 80, first_visible=1, last_visible=24)
        gallery.grid_scroll.verticalScrollBar().setValue(0)
        start, end, _ = grid_visible(loaded=80)
        assert start == 1 and end <= 80
        assert gallery.pager.buttons[1].isChecked()
        snap("images-73-pager-first-batch-grid")

        # Move the real Qt viewport near the end of the first batch.
        gallery.grid_scroll.ensureWidgetVisible(gallery._grid_buttons[first_batch[-1].identity])
        QTest.qWait(50)
        assert gallery.grid_scroll.verticalScrollBar().maximum() > 0
        at_edge, _end, positions = grid_visible(loaded=80, loading=True)
        assert at_edge <= 80 and 80 in positions
        snap("images-74-pager-prefetch-boundary-grid")

        # The second batch is appended to the *same mounted listing* and the
        # first 80 stable widgets retain identity. Show rows around 80/81.
        saved_first_button = gallery._grid_buttons[first_batch[0].identity]
        second_batch = multi_window(1, 160, loaded=160, first_visible=73, last_visible=96)
        assert gallery._grid_buttons[first_batch[0].identity] is saved_first_button
        assert second_batch[79].identity in gallery._grid_buttons
        assert second_batch[80].identity in gallery._grid_buttons
        # Position the actual viewport at the 80/81 seam, then measure it.
        # The extra QGridLayout reflow after append can reset an earlier scroll.
        card_81 = gallery._grid_buttons[second_batch[80].identity].parentWidget()
        gallery.grid_scroll.verticalScrollBar().setValue(max(0, card_81.y() - gallery.grid_viewport.height() // 2))
        _first, _last, visible = grid_visible(loaded=160)
        # At large thumbnail sizes, rows 80 and 81 may not fit together
        # vertically. Verify the next batch is actually visible while the
        # previous batch remains mounted, rather than requiring both rows
        # to share one viewport.
        assert 81 in visible, "The appended second Grid batch is not on screen"
        assert second_batch[79].identity in gallery._grid_buttons
        snap("images-75-pager-two-batches-grid")

        card_129 = gallery._grid_buttons[second_batch[128].identity].parentWidget()
        gallery.grid_scroll.verticalScrollBar().setValue(max(0, card_129.y() - gallery.grid_viewport.height() // 3))
        first, _last, visible = grid_visible(loaded=160)
        assert 129 in visible and first >= 81
        assert gallery.pager.buttons[2].isChecked()
        snap("images-76-pager-scrolled-second-batch-grid")
        gallery._widget("galleryFocusButton", QPushButton).click()
        try:
            snap("images-77-pager-two-batches-grid-fullscreen")
        finally:
            gallery._widget("galleryFocusButton", QPushButton).click()

        gallery.show_view("List")
        # The List really contains 160 distinct table rows and can scroll into batch 2.
        gallery.table.scrollToItem(gallery.table.item(100, 2))
        start, _end = list_visible(loaded=160)
        assert start >= 81 and gallery.pager.buttons[2].isChecked()
        assert gallery.table.item(79, 2).text() == "image-0080.png"
        assert gallery.table.item(80, 2).text() == "image-0081.png"
        snap("images-78-pager-two-batches-list")
        gallery._widget("galleryFocusButton", QPushButton).click()
        try:
            snap("images-79-pager-two-batches-list-fullscreen")
        finally:
            gallery._widget("galleryFocusButton", QPushButton).click()

        # Exercise the actual numbered Qt button: an offline-only callback
        # supplies the target batch without reading a DB or paging through 3/4.
        def fixture_jump(page_number):
            first = (page_number - 1) * gallery_ui.PAGE_SIZE + 1
            last = min(342, first + gallery_ui.PAGE_SIZE - 1)
            multi_window(first, last, first_visible=first, last_visible=min(last, first + 17))
            # Direct jumps should start at the selected batch, not inherit the
            # scroll offset of the previous 160-image listing.
            gallery.table.verticalScrollBar().setValue(0)
            gallery.grid_scroll.verticalScrollBar().setValue(0)
            list_visible(loaded=last - first + 1)

        gallery.pager.on_jump = fixture_jump
        gallery.pager.buttons[5].click()
        assert gallery.offset == 320 and gallery.total == 342 and len(gallery.items) == 22
        assert gallery.pager.buttons[5].isChecked()
        assert gallery.pager.range.text().startswith("321–")
        snap("images-80-pager-direct-jump-last-batch-list")
        gallery.show_view("Grid")
        first, _last, _visible = grid_visible(loaded=22)
        assert first == 321 and gallery.pager.buttons[5].isChecked()
        snap("images-81-pager-direct-jump-last-batch-grid")

        # A larger library forces ellipses and tests that 7+ chip controls
        # still remain inside the pager at a reduced window width.
        multi_window(961, 1040, total=1540, first_visible=961, last_visible=984)
        gallery.grid_scroll.verticalScrollBar().setValue(0)
        first, _last, _visible = grid_visible(loaded=80)
        assert first == 961 and gallery.pager.buttons[13].isChecked()
        assert len(gallery.pager._gaps) == 2
        snap("images-82-pager-many-pages-ellipsis")
        form.resize(800, 680)
        QTest.qWait(130)
        first, _last, _visible = grid_visible(loaded=80)
        assert first == 961
        snap("images-83-pager-many-pages-narrow")
        form.resize(1600, 1000)
        QTest.qWait(100)
        gallery.pager.on_jump = gallery._jump_to_page

        present(item)
        gallery._sync_pager()
        gallery.show_view("Grid")
        gallery._unselect_all()
        with patch.object(export, "start") as start:
            gallery.export_button.click()
            assert not start.called and not export.running and gallery.export_button.isEnabled()
            assert not shell.gallery_detail.galleryExportScroll.isHidden()
            assert form.imageExportScopeCombo.isHidden()
            snap("images-53-export-panel-empty-scope")
        gallery._set_checked(item.identity, True)
        assert shell._export_selection("checked") == (item.identity,)
        snap("images-54-export-panel-checked")
        destination = str(root / "offline-export")
        form.imageExportFolderEdit.setText(destination)
        form.imageExportStartButton.click()
        assert export.running and not form.imageExportStartButton.isEnabled()
        export._progress(0, 1)
        snap("images-55-export-panel-running")
        export._done(ExportResult(destination, 0, 0, 0, False), None)
        shell.progress_strip.finish("export")
        gallery.export_button.click()
        assert form.imageExportBox.parentWidget() is form.imageExportConnectionHost
        present(item)
        raw = gallery._widget("galleryRawMetadata")
        gallery._widget("galleryRawButton", QPushButton).setChecked(True)
        assert not raw.isHidden() and '"fixture": true' in raw.toPlainText()
        snap("images-56-detail-raw-metadata")
        gallery._widget("galleryRawButton", QPushButton).setChecked(False)
        gallery._widget("galleryFocusButton", QPushButton).click()
        try:
            assert gallery.page.property("imageFocus") and form.isFullScreen()
            snap("images-57-detail-fullscreen")
        finally:
            gallery._widget("galleryFocusButton", QPushButton).click()
            form.resize(1600, 1000)
        present(replace(item, issue=MISSING_ISSUE))
        assert gallery.surface_painter.placeholder == "File missing"
        snap("images-58-detail-file-missing")
        present(replace(item, issue="Offline fixture: original cannot be verified."))
        assert gallery.surface_painter.placeholder == "Image unavailable"
        snap("images-59-detail-unavailable")
        edited = replace(
            item, request_json=json.dumps({"model_id": item.model, "operation": "inpaint", "strength": 0.7})
        )
        present(edited)
        gallery._widget("galleryAdditionalButton", QPushButton).setChecked(True)
        assert not gallery.use_settings_button.isEnabled() and not gallery.regenerate_button.isEnabled()
        assert "edited images" in gallery.regenerate_button.toolTip()
        gallery._widget("galleryAdditionalScroll", QScrollArea).ensureWidgetVisible(gallery.regenerate_button)
        snap("images-60-detail-edited-image")
        present(replace(item, model="", request_json="{}"))
        assert gallery._widget("galleryModelValue", QLabel).text() == "Not recorded"
        assert not gallery.use_settings_button.isEnabled() and not gallery.regenerate_button.isEnabled()
        snap("images-61-detail-no-model")
        present(replace(item, loras=(("offline-style.ckpt", 0.8), ("offline-detail.ckpt", 0.4))))
        gallery._widget("galleryAdditionalButton", QPushButton).setChecked(False)
        assert gallery._widget("galleryLoRAStatus1", QLabel).text() == "Unknown"
        assert not gallery._widget("galleryLoRAChip2").isHidden()
        snap("images-62-detail-loras")
        present(item)
        gallery._widget("galleryActualSizeButton", QPushButton).click()
        assert gallery.surface_painter.mode == "100%" and gallery.surface_painter.zoom == 1
        snap("images-63-detail-zoom-native")
        gallery._unselect_all()
        gallery.show_view("Grid")
        gallery.prompt.setText("no-offline-match")
        gallery.filter_timer.stop()
        query = gallery._query(offset=0)
        gallery._query_done(
            gallery._query_generation, None, (query, GalleryResults((), 0, 0, query.limit), ((), ()), False), None
        )
        assert not gallery.items and gallery._grid_empty is not None
        snap("images-64-filter-no-matches")
        gallery.prompt.clear()
        gallery.filter_timer.stop()
        gallery.save_timer.stop()


def render_removal_and_selection_states(shell, item, plan, out):
    """Images consent, notices and result rows; every removal/query worker is bypassed."""
    recycle, gallery, strip = shell.image_recycle, shell.gallery, activity_strip(shell.form)

    def snap(name):
        QTest.qWait(100)
        save(shell.form, out, name)

    recycle._apply_mode("trash")
    with patch.object(recycle, "_execute", side_effect=RuntimeError("Preview cannot remove files")):
        recycle._confirmation_revision = gallery.recycle_scope_revision
        recycle._prepared(RecyclePlan(plan.targets, 0, 0, 0, 1, "trash"), None)
        snap("images-31-trash-confirmation")
        shell.inline.dismiss()
        recycle._confirmation_revision = gallery.recycle_scope_revision
        recycle._prepared(RecyclePlan((), 1, 2, 0, 3, "trash"), None)
        snap("images-32-nothing-to-remove")
        shell.inline.dismiss()
    # Busy source: request and the execution revalidation both refuse before any worker.
    with patch.object(recycle, "busy_source", return_value=True):
        recycle.request((item.identity,), 0, gallery.export_query())
        snap("images-33-removal-busy")
        shell.inline.dismiss()
        recycle._execute(plan)
        snap("images-34-removal-changed-or-busy")
        shell.inline.dismiss()
    recycle._mode_loaded("trash", OSError("Offline fixture: removal preferences are unreadable."))
    snap("images-35-removal-preferences-unavailable")
    shell.inline.dismiss()
    gallery.set_recycle_busy(True)
    snap("images-36-removal-control-busy")
    gallery.set_recycle_busy(False)

    # Presentation fixture of the running row; recycle_outputs is never called.
    strip.start("trash", f"Moving originals to {TRASH_NAME}", lambda: None, total=3)
    strip.update("trash", 1, 3)
    snap("images-37-trash-progress")
    recycle._action = "trash"
    with patch.object(gallery, "recycle_completed"):
        for name, result in (
            ("images-38-trash-result-success", RecycleResult((item.identity,), (), (), 0, 0, 0, False)),
            (
                "images-39-trash-result-partial",
                RecycleResult((), ("fixture:1",), (), 0, 1, 0, False, (("fixture:1", "source_changed"),)),
            ),
            ("images-40-trash-result-stopped", RecycleResult((item.identity,), (), (), 0, 0, 2, True)),
        ):
            recycle._completed(result, None)
            snap(name)
    for name, phase, processed, error in (
        ("images-41-removal-preflight-refused", "preflight", 0, RecycleUnavailable("Offline fixture: Trash refused.")),
        ("images-42-removal-not-started", "executing", 0, OriginalImagesBusyError("Offline fixture: originals busy.")),
        ("images-43-removal-needs-review", "executing", 1, RuntimeError("Offline fixture")),
    ):
        recycle._phase, recycle._processed_count = phase, processed
        recycle._finish_with_error(error)
        snap(name)
    strip.finish("trash")

    # Selection scan settlements, delivered directly; no identity query runs.
    gallery._cancel_selection_scan()
    snap("images-44-selection-stopped")
    gallery._selection_scan_done(gallery._selection_generation, "add", None, PersistenceError("Offline fixture"))
    snap("images-45-selection-failed")
    for name, omitted in (("images-46-selection-limit-partial", 3), ("images-47-selection-all-checked-hidden", 0)):
        # Same-filter settlement; one off-filter check shows the hidden count.
        gallery._selection_query = gallery.export_query()
        gallery.selection.checked.add("fixture:hidden")
        gallery._selection_scan_done(gallery._selection_generation, "add", ((), 1, omitted, (item.identity,)), None)
        snap(name)
    strip.finish("selection")
    assert gallery.select_filtered_button.text() == "Unselect all"
    assert not gallery.hidden_count_label.isHidden()
    gallery.selection.checked.discard("fixture:hidden")
    gallery._unselect_all()
    gallery._set_checked(item.identity, True)

    # Compact toolbar and its secondary-action menus.
    shell.form.resize(1180, 1000)
    QTest.qWait(150)
    assert not gallery.more_button.isHidden(), gallery.selection_bar.width()
    snap("images-48-compact-toolbar")
    for button, name in ((gallery.more_button, "images-49-more-menu"),):
        menu = button.menu()
        menu.popup(button.mapToGlobal(QPoint(0, button.height())))
        QTest.qWait(100)
        save(menu, out, name)
        menu.hide()
    shell.form.resize(1600, 1000)
    QTest.qWait(150)
    menu = gallery.clear_selection_button.menu()
    menu.popup(gallery.clear_selection_button.mapToGlobal(QPoint(0, gallery.clear_selection_button.height())))
    QTest.qWait(100)
    save(menu, out, "images-50-unselect-menu")
    menu.hide()
    gallery.show_view("Detail")
    gallery.select(item.identity)
    assert item.identity in gallery.selection.checked
    snap("images-51-detail-checked")
    gallery.show_view("Grid")


def render_initial_refresh_recovery(app, state, out):
    """Drive the real first-query failure/recovery before any successful render."""
    allow_success = False
    failures = 0

    async def query(_state, options):
        nonlocal failures
        if not allow_success:
            failures += 1
            raise PersistenceError("Offline first-query failure (SQLITE_IOERR_TRUNCATE).")
        return GalleryResults((), 0, options.offset, options.limit)

    async def choices(_state):
        return (), ()

    async def no_snapshot(*_args):
        raise PersistenceError("Offline fixture has no browsing publication.")

    with (
        patch.object(gallery_ui, "query_outputs", query),
        patch.object(gallery_ui, "filter_choices", choices),
        patch.object(gallery_ui, "query_snapshot", no_snapshot),
    ):
        shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=state / "initial-query-failure")
        try:
            shell.form.resize(1600, 1000)
            shell.show()
            shell.form.imagesNavigationButton.click()
            wait_for(lambda: shell.gallery._refresh_failing and not shell.gallery._loading)
            shell.gallery.auto_timer.stop()
            shell.gallery.filter_timer.stop()
            assert failures >= 1
            assert shell.gallery._rendered_page is None
            assert not shell.gallery._grid_buttons
            failed_label = shell.gallery._grid_empty
            assert failed_label is not None and "Offline first-query failure" in failed_label.text()
            disposed = []
            failed_label.destroyed.connect(lambda: disposed.append(True))
            target = out / "images-18-refresh-failed.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)

            allow_success = True
            shell.gallery.refresh()
            wait_for(lambda: shell.gallery._rendered_page is not None and not shell.gallery._loading)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            assert disposed and not shell.gallery._refresh_failing
            assert shell.gallery._grid_empty is not failed_label
            assert shell.gallery._grid_empty.text() == "No saved outputs match these filters."
            target = out / "images-19-refresh-recovered-empty.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            shell.gallery.show_view("Detail")
            QTest.qWait(100)
            save(shell.form, out, "images-52-detail-no-selection")
            shell.gallery.show_view("Grid")
            # Offline failure presentation only; no real desktop process is started.
            with patch.object(desktop_open, "_open_on_worker", return_value="the folder no longer exists."):
                shell.image_export._exported_folder = state / "missing-export-folder-fixture"
                shell.image_export.open_folder()
                wait_for(lambda: "export-open" in activity_strip(shell.form).rows)
            target = out / "images-26-export-folder-missing.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            activity_strip(shell.form).finish("export-open")
            shell.image_export._exported_folder = None
            with patch.object(
                desktop_open,
                "_open_on_worker",
                return_value="Windows could not open the file with its associated application; check its default viewer.",
            ):
                desktop_open.open_local_path(
                    str(state / "viewer-fixture.png"),
                    runner=shell.runner,
                    directory=False,
                    on_error=lambda reason: activity_strip(shell.form).complete(
                        "viewer-open", "External Viewer", f"Could not open the image: {reason}", "error"
                    ),
                )
                wait_for(lambda: "viewer-open" in activity_strip(shell.form).rows)
            target = out / "images-27-external-viewer-unavailable.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            activity_strip(shell.form).finish("viewer-open")
        finally:
            shell.runner.shutdown()
            shell.form.close()
            app.processEvents()


def render_mapped_gallery_states(app, state, out):
    """Synthetic drive mount/reference; never opens an owner DB or Windows image."""
    root = state / "mapped-fixture"
    root.mkdir()
    image = QImage(512, 384, QImage.Format.Format_RGB32)
    image.fill(QColor("#8094a8"))
    if not image.save(str(root / "saved.png")):
        raise RuntimeError("Could not save synthetic mapped-image fixture")
    stored = r"W:\saved.png"
    (root / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "attempt_id": "fixture",
                "artifacts": [{"path": stored, "designation": "final"}],
            }
        ),
        encoding="utf-8",
    )
    record = dict(
        identity="fixture:0",
        attempt_id="fixture",
        request_id="fixture-request",
        path=stored,
        metadata_path=r"W:\metadata.json",
        saved_at_utc="2026-10-17T00:00:00+00:00",
        backend_id="fake",
        model="offline-fixture",
        prompt="Synthetic mapped output; no inference",
        negative="",
        width=512,
        height=384,
        request_json=json.dumps({"model_id": "offline-fixture", "prompt": "Synthetic fixture"}),
    )
    paths = ArtifactReadPaths((("W", str(root)),))

    def foreign(value):
        return value.startswith(("W:\\", "W:/"))

    async def query(_state, options):
        return GalleryResults((item,), 1, options.offset, options.limit)

    async def choices(_state):
        return (), ()

    async def detail(_state, _identity):
        return load_detail(item)

    with (
        patch.object(artifact_paths, "foreign_windows_path", foreign),
        patch("dtflow.services.gallery.foreign_windows_path", foreign),
        patch.object(gallery_ui, "foreign_windows_path", foreign),
        patch.object(ArtifactReadPaths, "discover", return_value=paths),
        patch.object(gallery_ui, "query_outputs", query),
        patch.object(gallery_ui, "filter_choices", choices),
        patch.object(gallery_ui, "inspect_output", detail),
    ):
        item = item_from_record(record, paths)
        shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=state / "mapped-state")
        try:
            shell.form.resize(1600, 1000)
            shell.show()
            shell.form.imagesNavigationButton.click()
            wait_for(lambda: not shell.gallery._loading and bool(shell.gallery.items))
            for mode, name in (
                ("Grid", "images-03-mapped-grid-toggle-font"),
                ("List", "images-04-mapped-list-toggle-font"),
            ):
                shell.gallery.show_view(mode)
                QTest.qWait(150)
                target = out / f"{name}.png"
                if not shell.form.grab().save(str(target)):
                    raise RuntimeError(f"Could not save {target.name}")
                report(target)
            shell.gallery.select(item.identity)
            QTest.qWait(150)
            # Display only: mapped Edit eligibility never reads the fixture or calls a provider.
            target = out / "images-07-mapped-edit-eligible.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            target = out / "images-05-mapped-detail-metadata.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            shell.gallery._show_refresh_failure(PersistenceError("Offline refresh refusal fixture."), first=False)
            shell.gallery._refresh_failing = True
            shell.gallery.refresh()
            wait_for(lambda: not shell.gallery._loading)
            QTest.qWait(100)
            target = out / "images-20-refresh-recovered-populated.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)

            async def frozen_query(_state, options):
                return GalleryResults((item,), 1, options.offset, options.limit, "2026-10-17T07:00:00+00:00")

            # Synthetic publication presentation only; no owner database or mutation.
            live_query = gallery_ui.query_outputs
            with patch.object(gallery_ui, "query_outputs", frozen_query):
                shell.gallery.refresh()
                wait_for(lambda: not shell.gallery._loading and bool(shell.gallery._snapshot_at))
                for view, name in (("Grid", "images-21-snapshot-grid"), ("Detail", "images-22-snapshot-detail")):
                    shell.gallery.show_view(view)
                    if view == "Detail":
                        shell.gallery.select(item.identity)
                    QTest.qWait(150)
                    target = out / f"{name}.png"
                    if not shell.form.grab().save(str(target)):
                        raise RuntimeError(f"Could not save {target.name}")
                    report(target)
                warning = activity_strip(shell.form).rows["gallery-snapshot"]
                # One brief live success keeps the same slim row, with accurate
                # recovering wording rather than stale read-only authority.
                with (
                    patch.object(gallery_ui, "query_outputs", live_query),
                    patch.object(gallery_ui, "monotonic", return_value=100.0),
                ):
                    shell.gallery.refresh()
                    wait_for(lambda: not shell.gallery._loading)
                target = out / "images-24-live-recovery-stabilizing.png"
                if not shell.form.grab().save(str(target)):
                    raise RuntimeError(f"Could not save {target.name}")
                report(target)
                shell.gallery.refresh()
                wait_for(lambda: not shell.gallery._loading)
                warning.activityStatusDismissButton.click()
                # A brief live result must not reset voluntary dismissal.
                with (
                    patch.object(gallery_ui, "query_outputs", live_query),
                    patch.object(gallery_ui, "monotonic", return_value=200.0),
                ):
                    shell.gallery.refresh()
                    wait_for(lambda: not shell.gallery._loading)
                shell.gallery.refresh()
                wait_for(lambda: not shell.gallery._loading)
                assert "gallery-snapshot" not in activity_strip(shell.form).rows
                QTest.qWait(100)
                target = out / "images-23-snapshot-warning-dismissed.png"
                if not shell.form.grab().save(str(target)):
                    raise RuntimeError(f"Could not save {target.name}")
                report(target)
            # Drive real recovery callbacks at two controlled clock readings;
            # no wall-clock wait, provider work or direct sentinel mutation.
            for now in (300.0, 300.0 + gallery_ui.AUTO_REFRESH_MS / 1000 + 1):
                with patch.object(gallery_ui, "monotonic", return_value=now):
                    shell.gallery.refresh()
                    wait_for(lambda: not shell.gallery._loading and shell.gallery._snapshot_at is None)
            assert "gallery-snapshot" not in activity_strip(shell.form).rows
            # Auto-refresh Off: one manual successful live refresh clears the
            # warning, without another scheduled check or recovery timer.
            shell.gallery.auto_button.setChecked(False)
            with patch.object(gallery_ui, "query_outputs", frozen_query):
                shell.gallery.refresh()
                wait_for(lambda: not shell.gallery._loading and bool(shell.gallery._snapshot_at))
            shell.gallery.refresh()
            wait_for(lambda: not shell.gallery._loading and shell.gallery._snapshot_at is None)
            QTest.qWait(100)
            target = out / "images-25-manual-live-recovery.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            shell.gallery.auto_button.setChecked(True)
            # Presentation only: synthetic scope, with execution explicitly blocked.
            wait_for(lambda: shell.gallery.removal_ready)
            shell.gallery._set_checked(item.identity, True)
            # Offline two-choice popups: selecting a mode never executes removal.
            menu = shell.image_recycle.menu_button.menu()
            shell.image_recycle._apply_mode("trash")
            assert shell.gallery.delete_button.text() == "Trash"
            assert menu.actions()[0].text() == "Trash"
            assert menu.actions()[1].text() == "Delete"
            assert shell.gallery.delete_button.iconSize() == QSize(22, 16)
            assert not shell.gallery.delete_button.icon().isNull()
            QTest.qWait(100)
            target = out / "images-13-trash-mode.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            menu.popup(shell.image_recycle.menu_button.mapToGlobal(QPoint(0, shell.image_recycle.menu_button.height())))
            QTest.qWait(100)
            control = shell.gallery._widget("galleryRemovalControl", QFrame)
            anchor = control.mapToGlobal(QPoint(0, control.height() - 1))
            expected_left = anchor.x()
            # Mirror the menu: clamp only when the anchor lies on a screen
            # (the offscreen screen can be smaller than the rendered window).
            anchor_screen = QGuiApplication.screenAt(anchor)
            if anchor_screen is not None:
                screen = anchor_screen.availableGeometry()
                expected_left = min(
                    max(expected_left, screen.left()), max(screen.left(), screen.right() - menu.width() + 1)
                )
            assert menu.geometry().left() == expected_left
            target = out / "images-10-removal-menu-trash-selected.png"
            if not menu.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            menu.hide()
            shell.image_recycle._apply_mode("permanent")
            assert shell.gallery.delete_button.text() == "Delete"
            assert shell.gallery.delete_button.iconSize() == QSize(22, 16)
            assert not shell.gallery.delete_button.icon().isNull()
            menu.popup(shell.image_recycle.menu_button.mapToGlobal(QPoint(0, shell.image_recycle.menu_button.height())))
            QTest.qWait(100)
            target = out / "images-11-removal-menu-permanent-selected.png"
            if not menu.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            menu.hide()
            target = out / "images-06-permanent-mode.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            # Show the disabled icon beside the same disabled text shade.
            shell.gallery._set_checked(item.identity, False)
            assert not shell.gallery.delete_button.isEnabled()
            assert shell.gallery.delete_button.text() == "Delete"
            target = out / "images-14-delete-disabled.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            shell.gallery._set_checked(item.identity, True)
            plan = RecyclePlan(
                (RecycleTarget(item.identity, str(root / "saved.png"), 1, 1, 1, 1),),
                0,
                0,
                0,
                1,
                "permanent",
            )
            shell.image_recycle._confirmation_revision = shell.gallery.recycle_scope_revision
            with patch.object(shell.image_recycle, "_execute", side_effect=RuntimeError("Preview cannot remove files")):
                shell.image_recycle._prepared(plan, None)
                QTest.qWait(100)
                target = out / "images-08-permanent-confirmation.png"
                if not shell.form.grab().save(str(target)):
                    raise RuntimeError(f"Could not save {target.name}")
                report(target)
                shell.inline.dismiss()
            render_removal_and_selection_states(shell, item, plan, out)
            # Synthetic inaccessible-original state: short card text, full hover details.
            shell.gallery.show_view("Grid")
            generation = shell.gallery._thumb_versions[item.identity]
            shell.gallery._thumbnail_done(
                generation,
                item.identity,
                (None, "The Windows-drive mount has no verified shared-lock support. Use WSL ext4."),
                None,
            )
            target = out / "images-12-original-unavailable-thumbnail.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            # Confirmed-missing original: file-missing pictogram in Grid and List, distinct from unavailable.
            shell.gallery._show_unavailable(item.identity, MISSING_ISSUE)
            for view, name in (("Grid", "images-16-file-missing-grid"), ("List", "images-17-file-missing-list")):
                shell.gallery.show_view(view)
                QTest.qWait(50)
                target = out / f"{name}.png"
                if not shell.form.grab().save(str(target)):
                    raise RuntimeError(f"Could not save {target.name}")
                report(target)
            shell.gallery.show_view("Grid")
            # Present a safe unsupported-alias status without attempting a system launch.
            shell.gallery._read_action_done(
                "folder", item.identity, None, OSError("Recorded output is not under this shared Windows state.")
            )
            target = out / "images-15-read-alias-unavailable.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
        finally:
            shell.runner.shutdown()
            shell.form.close()
            app.processEvents()


def render_queue_states(shell, capture):
    """A real paused queue in the temporary state; dispatch settlements are delivered directly, never run."""
    queue, page = shell.queue, shell.queue_page
    shell.form.queueNavigationButton.click()
    page.queueCountSpinBox.setValue(3)
    page.createQueueButton.click()
    wait_for(lambda: queue._view() is not None)
    capture("queues-02-created-paused")
    page.planBox.setChecked(True)
    page.planDryRunButton.click()
    capture("queues-03-plan-dry-run")
    page.planBox.setChecked(False)
    for button, name in (
        (page.cancelQueueButton, "queues-04-cancel-confirmation"),
        (page.clearQueuesButton, "queues-05-clear-confirmation"),
    ):
        button.click()
        capture(name)
        shell.inline.dismiss()
    view = queue._view()
    counts = dict.fromkeys(view["summary"], 0)
    strip = activity_strip(page)
    for name, status, summary, failure in (
        ("queues-06-result-finished", "finished", {"succeeded": 3}, None),
        ("queues-07-result-partial", "finished", {"succeeded": 1, "failed": 1, "unknown": 1}, None),
        ("queues-08-result-cancelled", "cancelled", {"succeeded": 1, "cancelled": 2}, None),
        ("queues-09-dispatch-owned-elsewhere", None, None, StateBusyError("Offline fixture: state is owned.")),
        ("queues-10-dispatch-error", None, None, RuntimeError("Offline fixture")),
    ):
        final = None if failure else dict(view, status=status, summary={**counts, **summary})
        queue._dispatched(final, failure)
        capture(name)
        strip.finish("queue-result")
    # Leave no queue behind for the later Generate/Inpaint renders.
    page.clearQueuesButton.click()
    page.clearQueuesButton.click()
    wait_for(lambda: queue._view() is None)


def render_shell_states(shell, capture):
    """Window-level notices, close consent and header run-control phases, driven through the real handlers."""
    strip = activity_strip(shell.form)

    def clear():
        shell.inline.dismiss()
        for key in list(strip.rows):
            strip.finish(key)

    shell.about()
    capture("shell-01-about")
    clear()
    with patch.object(type(shell.queue), "dispatching_here", new_callable=PropertyMock, return_value=True):
        shell._confirm_close()
        capture("shell-02-close-consent-dispatch")
        shell.inline.dismiss()
    shell.image_recycle._running = True
    try:
        shell._confirm_close()
        capture("shell-03-close-removal-in-progress")
    finally:
        shell.image_recycle._running = False
        shell.image_recycle._stop.clear()
    clear()
    for name, error, outcome in (
        (
            "shell-04-originals-unavailable",
            OriginalImagesUnavailableError("Offline fixture: bridge unsupported."),
            None,
        ),
        ("shell-05-reconciliation-deferred", OriginalImagesBusyError("Offline fixture"), None),
        ("shell-06-reconciliation-failed", RuntimeError("Offline fixture"), None),
        ("shell-07-permanent-deletion-review", None, ReconcileResult(0, 2, 0)),
    ):
        if name == "shell-04-originals-unavailable":
            shell._originals_ready_checked(None, error)
        else:
            shell._images_reconciled(outcome, error)
        capture(name)
        clear()
    # Several labelled rows at once, then an unrelated notice arriving while a confirmation is armed.
    InlineNotice.warning(shell.form, "Removal reconciliation deferred", "Offline fixture warning.")
    strip.complete("export-result", "Export finished", "3 images exported.")
    strip.start("trash-run", "Moving images to Trash", stop=lambda: None, total=4)
    strip.update("trash-run", 1, 4)
    capture("shell-08-overlay-several-rows")
    clear()
    queue, page = shell.queue, shell.queue_page
    shell.form.queueNavigationButton.click()
    page.createQueueButton.click()
    wait_for(lambda: queue._view() is not None and page.clearQueuesButton.isEnabled())
    page.clearQueuesButton.click()  # arms the Clear confirmation
    assert page.clearQueuesButton.property("confirmationPending")
    InlineNotice.warning(shell.form, "Original images unavailable", "Offline fixture notice during a confirmation.")
    assert page.clearQueuesButton.property("confirmationPending")
    capture("shell-09-overlay-notice-during-confirmation")
    page.clearQueuesButton.click()  # confirms, leaving no queue for later renders
    wait_for(lambda: queue._view() is None)
    clear()
    strip.complete("info-result", "Nothing to export", "All checked images already exist at the destination.", "info")
    capture("shell-10-overlay-info-result")
    clear()
    control = shell.shell.runControl
    for name, phase, kwargs in (
        ("shell-11-run-control-ready", "ready", {}),
        ("shell-12-run-control-paused", "paused", {"completed": 1, "total": 3}),
        ("shell-13-run-control-finishing", "complete", {"detail": "Finishing."}),
        ("shell-14-run-control-queue-running", "running", {"action": "queue", "completed": 2, "total": 5}),
    ):
        control.set_presentation(phase, **kwargs)
        capture(name)
    shell._sync_run_control()


def render_inpaint_states(shell, state, capture):
    """Listed editor states only; analysis/dispatch rows are presentation fixtures."""
    page, editor = shell.generation_page, shell.generation.inpaint
    shell.form.generationNavigationButton.click()
    wait_for(lambda: page.inpaintModeButton.isEnabled())
    shell.inline.dismiss()
    page.inpaintModeButton.click()
    capture("inpaint-01-empty")

    # Calibration source, not a user photo or model-generated result.
    source = QImage(512, 384, QImage.Format.Format_RGB32)
    source.fill(QColor("#8094a8"))
    painter = QPainter(source)
    painter.fillRect(64, 48, 384, 288, QColor("#d9c19c"))
    painter.fillRect(192, 96, 128, 240, QColor("#668c73"))
    painter.end()
    source_path = state / "inpaint-fixture-source.png"
    if not source.save(str(source_path)):
        raise RuntimeError("Could not write offline source fixture")
    editor.replace_source(str(source_path))
    wait_for(lambda: editor._source is not None and not editor._loading)
    capture("inpaint-02-source-loaded")

    width, height = editor._source.width, editor._source.height
    mask = bytes(
        255 if width // 3 <= x < 2 * width // 3 and height // 4 <= y < 3 * height // 4 else 0
        for y in range(height)
        for x in range(width)
    )
    editor.canvas.replace_mask(mask)
    wait_for(lambda: editor._coverage > 0)
    capture("inpaint-03-mask-painted")
    for name, view in (
        ("inpaint-04-source-view", "Source"),
        ("inpaint-05-mask-view", "Mask"),
        ("inpaint-06-result-no-result", "Result"),
    ):
        # No result exists yet, so Result still shows the source: nothing is fabricated or dispatched.
        editor.canvas.set_view(view)
        capture(name)

    editor.canvas.set_view("Source")
    editor.form.clearMaskButton.click()
    capture("inpaint-07-clear-confirmation")
    shell.inline.dismiss()  # Keep the fixture mask.

    # Presentation fixtures: a probe-status line, the consent step, and a calibration image as the "result".
    editor._support = ImageOperationSupport("owner_probe", "Offline presentation fixture.")
    editor._render_support()
    capture("inpaint-10-capability-probe")
    editor.canvas.set_result(QPixmap.fromImage(source.flipped(Qt.Orientation.Horizontal)))
    editor.canvas.set_view("Result")
    capture("inpaint-11-result-fixture")
    editor.canvas.set_view("Source")

    # Model badges use fake file references only; no ONNX file is read or loaded.
    editor.form.selectModeButton.setChecked(True)
    capture("inpaint-12-select-no-models")
    fake = ModelRef("offline-only.onnx", "0" * 64)
    for name, models, issues, checking, check_error in (
        ("inpaint-16-model-readiness-checking", PersonModels(fake), {}, True, None),
        ("inpaint-17-one-model", PersonModels(fake), {"segmentation": None}, False, None),
        ("inpaint-18-two-models", PersonModels(fake, fake), {"segmentation": None, "clothes": None}, False, None),
        (
            "inpaint-19-three-models",
            PersonModels(fake, fake, fake),
            {"segmentation": None, "clothes": None, "faces": None},
            False,
            None,
        ),
        (
            "inpaint-15-model-unreadable",
            PersonModels(fake, fake),
            {
                "segmentation": None,
                "clothes": "The saved clothes model cannot be read (permission denied); choose it again with Models….",
            },
            False,
            None,
        ),
        ("inpaint-20-model-unknown", PersonModels(fake), {}, False, "Availability could not be checked."),
    ):
        editor._models = models
        editor._model_readiness = issues
        editor._model_checking = checking
        editor._model_check_error = check_error
        editor._models_issue = (
            "Checking saved model read access…"
            if checking
            else next((issue for issue in issues.values() if issue), check_error)
        )
        editor._update_models_badge()
        editor._people_status()
        capture(name)
    editor._models = PersonModels()
    editor._model_readiness = {}
    editor._model_checking = False
    editor._model_check_error = None
    editor._models_issue = None
    editor._update_models_badge()
    editor._people_status()
    people = PersonAnalysis(
        width,
        height,
        (
            PersonLayers(
                1,
                0.93,
                (width // 3, height // 4, 2 * width // 3, 3 * height // 4),
                bytes([255]) * (((2 * width // 3) - width // 3) * ((3 * height // 4) - height // 4)),
                (bytes([4 + 1]) * (((2 * width // 3) - width // 3) * ((3 * height // 4) - height // 4) // 2)).ljust(
                    ((2 * width // 3) - width // 3) * ((3 * height // 4) - height // 4), bytes([6 + 1])
                ),
            ),
        ),
        True,
    )
    editor._set_analysis(people)
    capture("inpaint-13-select-people-found")
    person = people.people[0]
    editor.canvas.set_hover_colors(
        person.box, colored_preview(person, frozenset({"person", "upper clothes", "pants"}), "pants")
    )
    capture("inpaint-14-select-hover-preview")
    editor.canvas.clear_hover()
    editor.form.paintModeButton.setChecked(True)

    strip = activity_strip(page)
    for key, label, stop_text, done, total, detail, name in (
        (
            "inpaint-find-fixture",
            "Find people",
            "Stop after current",
            1,
            3,
            "OFFLINE PRESENTATION FIXTURE · analysis step 1 of 3; no ONNX execution",
            "inpaint-08-find-people-overlay",
        ),
        (
            "inpaint-edit-fixture",
            "Edit image",
            "Stop waiting locally",
            4,
            12,
            "OFFLINE PRESENTATION FIXTURE · sampling step 4 of 12; no generation submitted",
            "inpaint-09-edit-image-overlay",
        ),
    ):
        strip.start(key, label, stop=lambda key=key: strip.finish(key), stop_text=stop_text, total=total)
        strip.update(key, done, total, detail=detail)
        capture(name)
        strip.finish(key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tmp" / "ui-renders", help="Screenshot directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_theme(app)
    cloud = DtPlus()
    scenario, gate = "success", None

    def backend(selection, **_):
        if selection.name == "cloud":
            return cloud.backend()
        return FakeBackend(scenario=scenario, gate=gate)

    with (
        tempfile.TemporaryDirectory(prefix="dtflow-ui-") as temporary,
        patch.object(session, "create_backend", backend),
    ):
        state = Path(temporary)
        render_connection_states(app, state, args.out)
        render_mapped_gallery_states(app, state, args.out)
        # Missing Qt capability API: startup succeeds with Trash unavailable.
        with patch.object(system_trash, "QFile", object):
            unavailable = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=state / "no-trash")
        try:
            unavailable.form.resize(1600, 1000)
            unavailable.show()
            unavailable.form.imagesNavigationButton.click()
            wait_for(lambda: unavailable.gallery.removal_ready)
            unavailable.image_recycle.menu_button.menu().popup(
                unavailable.image_recycle.menu_button.mapToGlobal(
                    QPoint(0, unavailable.image_recycle.menu_button.height())
                )
            )
            QTest.qWait(100)
            target = args.out / "images-02-trash-unavailable.png"
            if not unavailable.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            target = args.out / "images-09-removal-mode-menu.png"
            if not unavailable.image_recycle.menu_button.menu().grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            unavailable.image_recycle.menu_button.menu().hide()
        finally:
            unavailable.runner.shutdown()
            unavailable.form.close()
            app.processEvents()
        render_initial_refresh_recovery(app, state, args.out)
        shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=state)
        shell.form.resize(1600, 1000)
        shell.show()
        shell.form.backendCombo.setCurrentText("fake")
        shell.form.generationNavigationButton.click()
        page, controller = shell.generation_page, shell.generation

        def capture(name):
            app.processEvents()
            preview = controller.inpaint.canvas if controller.inpaint.active else page.resultPreviewLabel
            preview._show_controls()
            QTest.qWait(30)
            target = args.out / f"{name}.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)

        def settled():
            return page.generateButton.isEnabled()

        try:
            QTest.qWait(100)
            capture("generate-01-empty")
            page.refreshInventoryButton.click()
            wait_for(lambda: page.refreshInventoryButton.isEnabled())
            page.modelCombo.setCurrentText("fake-grid")
            page.promptEdit.setPlainText(
                "Offline calibration grid — verify aspect ratio, edges and zoom. No model inference."
            )
            page.negativePromptEdit.setText("Not used by the offline fixture")
            controller.lora.set_loras((LoRASelection("fake-soft", 0.8),))
            page.widthSpinBox.setValue(512)
            page.heightSpinBox.setValue(1024)
            page.generateButton.click()
            wait_for(settled, timeout=60)
            assert page.resultList.count() == 2, page.generationStatusLabel.text()
            capture("generate-02-portrait")
            QTest.mouseMove(page.generateButton, QPoint(20, 15))
            capture("generate-03-hover")
            page.generateButton.setDown(True)
            capture("generate-04-pressed")
            page.generateButton.setDown(False)
            page.resultPreviewLabel.zoom(2)
            capture("generate-05-zoom")
            page.widthSpinBox.setValue(1024)
            page.heightSpinBox.setValue(576)
            page.generateButton.click()
            wait_for(settled, timeout=60)
            capture("generate-06-landscape")
            shell.form.resize(800, 680)
            capture("generate-07-narrow")
            shell.form.resize(1600, 1000)
            page.promptEdit.clear()
            page.seedEdit.setText("invalid")
            page.generateButton.click()
            page.seedEdit.setFocus()
            capture("generate-08-invalid-focus")
            page.promptEdit.setPlainText("Offline fixture")
            page.seedEdit.setText("0")
            controller.lora.set_loras((LoRASelection("fake-mismatch", 0.7), LoRASelection("fake-unknown", 0.5)))
            page.checkLorasButton.click()
            wait_for(settled, timeout=60)
            capture("generate-09-incompatible")
            controller.lora.set_loras(())
            gate = asyncio.Event()
            page.generateButton.click()
            wait_for(lambda: not page.generateButton.isEnabled())
            capture("generate-10-busy-disabled")
            shell.runner._loop.call_soon_threadsafe(gate.set)
            wait_for(settled, timeout=60)
            gate, scenario = None, "lost"
            page.generateButton.click()
            wait_for(settled, timeout=60)
            capture("generate-11-outcome-unknown")
            strip = shell.progress_strip
            strip.finish("generation-result")
            page.expandPromptButton.setChecked(True)
            capture("generate-13-prompt-expanded")
            page.expandPromptButton.setChecked(False)
            scenario = "reject"
            page.generateButton.click()
            wait_for(settled, timeout=60)
            capture("generate-14-failed")
            strip.finish("generation-result")
            gate, scenario = asyncio.Event(), "success"
            page.generateButton.click()
            wait_for(lambda: "run" in strip.rows)
            strip.rows["run"].activityStopButton.click()
            capture("generate-15-stop-requested")
            wait_for(settled, timeout=60)
            capture("generate-16-interrupted")
            gate = None
            strip.finish("generation-result")
            shell.activity.set(dispatching=True)
            capture("generate-17-queue-dispatch-running")
            shell.activity.set(dispatching=False)
            page.savePresetButton.click()
            capture("generate-18-save-preset-editor")
            shell.inline.dismiss()

            # Real asset-service composition, in-memory transport. Never generates through cloud.
            shell.form.backendCombo.setCurrentText("cloud")
            panel = controller.lora
            panel.confirm_upload = lambda *_: True
            for name in ("fixture-style.ckpt", "fixture-soft.ckpt"):
                fixture = lora_file(state, name, data=name.encode() * 200)
                panel.choose_asset_file = lambda *_, fixture=fixture: str(fixture)
                page.prepareAssetButton.click()
                wait_for(settled, timeout=60)
                wait_for(lambda name=name: page.assetList.count() >= (1 if "style" in name else 2))
            page.assetList.setCurrentRow(0)
            controller.lora.set_loras((LoRASelection("fixture-soft.ckpt", 0.8),))
            page.assetNoteLabel.setText(
                "OFFLINE FIXTURE · in-memory cloud receipts. No provider traffic or real upload."
            )
            page.detailsToggle.setChecked(True)
            capture("generate-12-assets-details")
            shell.form.backendCombo.setCurrentText("fake")
            for name, tab in (
                ("queues-01-empty", "queueNavigationButton"),
                ("images-01-grid", "imagesNavigationButton"),
            ):
                getattr(shell.form, tab).click()
                if tab == "imagesNavigationButton":
                    # The fake generations above are saved outputs; show them loaded, not a mid-refresh frame.
                    shell.gallery.show_view("Grid")
                    wait_for(
                        lambda: (
                            not shell.gallery._loading
                            and bool(shell.gallery.items)
                            and not shell.gallery._thumb_waiting
                            and not shell.gallery._thumb_active
                        )
                    )
                QTest.qWait(300)
                capture(name)
            # Detail of a fake-backend output with Additional details open, then the real
            # Regenerate consent and its confirmed fake-provider submission.
            gallery = shell.gallery
            wait_for(lambda: not gallery._loading and bool(gallery.items))
            gallery.show_view("Detail")
            gallery.select(gallery.items[0].identity)
            gallery._widget("galleryAdditionalButton", QPushButton).setChecked(True)
            QTest.qWait(150)
            capture("images-28-detail-additional")
            assert gallery.regenerate_button.isEnabled()
            # Regenerate lives at the end of Additional details; show the armed Confirm in place.
            gallery._widget("galleryAdditionalScroll", QScrollArea).ensureWidgetVisible(gallery.regenerate_button)
            gallery.regenerate_button.click()
            QTest.qWait(150)
            capture("images-29-regenerate-confirmation")
            scenario = "success"
            gallery.regenerate_button.click()
            assert page.resultList.count() == 0  # submitted: Generate cleared its previous result
            wait_for(lambda: page.resultList.count() > 0 and settled())
            capture("images-30-regenerate-confirmed")
            shell.form.generationNavigationButton.click()
            controller.show_metadata()
            capture("generate-19-metadata-inspection")
            shell.inline.dismiss()
            render_queue_states(shell, capture)
            render_shell_states(shell, capture)
            render_inpaint_states(shell, state, capture)
        except BaseException:
            # Print before shutdown: a stuck shutdown would otherwise hide the original failure.
            traceback.print_exc()
            sys.stderr.flush()
            raise
        finally:
            shell.runner.shutdown()
            shell.form.close()
            app.processEvents()
    summarize()


if __name__ == "__main__":
    faulthandler.dump_traceback_later(30, repeat=True)
    try:
        main()
    finally:
        faulthandler.cancel_dump_traceback_later()

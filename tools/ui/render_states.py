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
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from support.network_guard import install  # noqa: E402

install()  # before provider/client imports; the guard rejects unregistered DNS/sockets
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
platform_name = os.environ["QT_QPA_PLATFORM"].split(":", 1)[0]
if os.environ["QT_QPA_PLATFORM"] == "offscreen":
    # Qt 6.12 defaults to an 800x800 virtual screen. Fix the screen geometry
    # before QApplication starts so fullscreen renders match normal 1600x1000.
    # Qt splits platform arguments at every colon, including a Windows drive
    # separator. A path relative to the launch directory avoids that ambiguity.
    try:
        screen_config = os.path.relpath(ROOT / "tools/ui/offscreen-screen.json")
    except ValueError as exc:
        raise SystemExit(
            "Run the offscreen renderer from the repository drive or set an explicit QT_QPA_PLATFORM."
        ) from exc
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={screen_config}"
if sys.platform == "win32" and platform_name == "offscreen":
    # Offscreen Qt on Windows otherwise searches PySide6's empty font folder.
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap, QTextDocument, QTextOption  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QToolButton,
    QWidget,
)
from support.cloud_assets import DtPlus, lora_file  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow import gallery_limits  # noqa: E402
from dtflow._diagnostic_notifications import DiagnosticNotice  # noqa: E402
from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.core.compute_units import ComputeUnitEstimate  # noqa: E402
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
from dtflow.ui.diagnostic_notice import DiagnosticNoticeReceiver  # noqa: E402
from dtflow.ui.generation_page import COMPUTE_UNITS_METER_STEPS, GenerationPage, compute_units_meter_value  # noqa: E402
from dtflow.ui.inline_feedback import InlineNotice  # noqa: E402
from dtflow.ui.main_window import MainWindow  # noqa: E402
from dtflow.ui.queue_item_delegate import COL_LORAS, COL_PROMPT, COL_STATUS, wrapped_lines  # noqa: E402
from dtflow.ui.queue_model import LEVEL_COLORS  # noqa: E402
from dtflow.ui.system_trash import TRASH_NAME  # noqa: E402
from dtflow.ui.theme import apply_theme  # noqa: E402

RENDERED = {}  # state name -> {"seconds", "blank"}, in render order
STARTED = time.monotonic()
SELECTED_CATEGORY = "all"


def wants(category):
    """Return whether a render category should execute."""
    return SELECTED_CATEGORY in ("all", category) or (category == "images" and SELECTED_CATEGORY == "image-navigation")


RENDER_CATEGORIES = (
    "all",
    "connection",
    "generate",
    "images",
    "image-navigation",
    "queues",
    "shell",
    "inpaint",
    "logs",
)


def render_category(target):
    """Return the review category for a screenshot path."""
    path = Path(target)
    if "image-navigation" in path.parts:
        return "image-navigation"
    return path.stem.split("-", 1)[0]


def category_selected(target):
    """Whether this screenshot belongs to the requested render category."""
    category = render_category(target)
    if SELECTED_CATEGORY == "all":
        return True
    if SELECTED_CATEGORY == "images":
        return category in {"images", "image-navigation"}
    return category == SELECTED_CATEGORY


def report(target):
    """Print progress for one written state and remember it for the final summary."""
    if not category_selected(target):
        Path(target).unlink(missing_ok=True)
        return
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


def render_inspector_fit_states(shell, out, category):
    """Exercise the real inspector layout at the rail minimum, including a formerly wider hidden page."""
    form, chrome, page = shell.form, shell.shell, shell.queue_page
    rail, scroll, stack = chrome.rightRail, chrome.rightScroll, chrome.rightStack
    minimum, maximum = rail.minimumWidth(), rail.maximumWidth()
    sizes = chrome.workspaceSplitter.sizes()
    selected = form.workspaceStack.currentIndex()
    backend = page.itemBackendLabel.text()
    position = scroll.verticalScrollBar().value()

    def fits(expected):
        QTest.qWait(100)
        assert rail.isVisible() and rail.width() == minimum
        assert stack.currentWidget() is expected
        viewport = scroll.viewport().width()
        assert stack.width() <= viewport, (stack.width(), viewport)
        assert expected.width() <= viewport, (expected.width(), viewport)
        assert stack.minimumSizeHint().width() <= viewport, (stack.minimumSizeHint().width(), viewport)
        assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert scroll.horizontalScrollBar().maximum() == 0  # No offscreen content, not merely a hidden scrollbar.
        for index in range(stack.count()):
            widget = stack.widget(index)
            policy = widget.sizePolicy().horizontalPolicy()
            assert policy == (QSizePolicy.Policy.Preferred if widget is expected else QSizePolicy.Policy.Ignored)

    def actions_fit():
        scroll.ensureWidgetVisible(page.itemRetryButton)
        QTest.qWait(30)
        for button in (page.itemOpenFolderButton, page.itemRetryButton):
            assert button.isVisible()
            left = button.mapTo(scroll.viewport(), QPoint(0, 0)).x()
            assert 0 <= left and left + button.width() <= scroll.viewport().width()

    try:
        rail.setFixedWidth(minimum)
        form.queueNavigationButton.click()
        fits(page.queueInspector)
        if category == "queues":
            actions_fit()
            save(form, out, "queues-50-inspector-minimum-width")
        long_backend = (
            "Draw Things local backend with a deliberately long offline fixture name and endpoint description"
        )
        page.itemBackendLabel.setText(long_backend)
        fits(page.queueInspector)
        if category == "queues":
            assert page.itemBackendLabel.wordWrap() and page.itemBackendLabel.text() == long_backend
            assert page.itemBackendLabel.height() > page.itemBackendLabel.fontMetrics().height()
            actions_fit()
            save(form, out, "queues-51-inspector-long-backend")
            # An unbroken model/LoRA identifier must wrap inside the actual New queue summary card.
            shell.queue.open_composer()
            shell.queue._preview_timer.stop()  # Freeze the offline fixture while inspecting layout.
            summary_names = ("Backend", "Model", "Loras", "Items", "Varies", "Seeds", "Cap", "Note")
            summary_labels = [getattr(page, f"composerSummary{name}Label") for name in summary_names]
            previous_text = [label.text() for label in summary_labels]
            try:
                model = "z_image_turbo_" + "long_checkpoint_identifier_" * 5 + ".ckpt"
                lora = "realisticsnapshot_zimage_turbov5_" + "rank64_variant_" * 5 + ".ckpt"
                page.composerSummaryBackendLabel.setText("cloud · offline.test:7859")
                page.composerSummaryModelLabel.setText(model)
                page.composerSummaryLorasLabel.setText(f"{lora}: 0.94\n{lora}: 1.00")
                fits(page.queueInspector)
                for label in summary_labels:
                    assert label.wordWrap() and label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
                    left = label.mapTo(scroll.viewport(), QPoint(0, 0)).x()
                    right = label.mapTo(scroll.viewport(), QPoint(label.width(), 0)).x()
                    assert 0 <= left < right <= scroll.viewport().width(), (label.objectName(), left, right)
                assert page.composerSummaryModelLabel.height() > page.composerSummaryModelLabel.fontMetrics().height()
                assert page.composerSummaryLorasLabel.height() > page.composerSummaryLorasLabel.fontMetrics().height()
                # Check every rendered line, not just that the label grew beyond a single line.
                lora_label = page.composerSummaryLorasLabel
                document = QTextDocument()
                document.setDefaultFont(lora_label.font())
                document.setDocumentMargin(0)
                option = document.defaultTextOption()
                option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
                document.setDefaultTextOption(option)
                document.setPlainText(lora_label.text())
                document.setTextWidth(lora_label.contentsRect().width())
                assert lora_label.height() >= document.size().height(), (lora_label.height(), document.size().height())
                shell.queue._summary_fit.refit()  # the label heights are fitted on a deferred pass; let it settle first
                QTest.qWait(150)
                lora_bottom = lora_label.mapTo(page.composerSummaryBoxContents, QPoint(0, lora_label.height())).y()
                items_top = page.composerSummaryItemsCaption.mapTo(page.composerSummaryBoxContents, QPoint(0, 0)).y()
                assert items_top >= lora_bottom, (items_top, lora_bottom)
                card = page.composerSummaryBox
                assert card.mapTo(scroll.viewport(), QPoint(card.width(), 0)).x() <= scroll.viewport().width()
                save(form, out, "queues-52-composer-long-identifiers")
                # The owner Windows screenshot shows the second LoRA's text cut off by Items.
                realistic = "zimage_turbo_lora_rank64_5: 0.94\nrealisticsnapshot_zimage_turbov5_lora_f16.ckpt: 0.6"
                page.composerSummaryLorasLabel.setText(realistic)
                shell.queue._summary_fit.refit()
                QTest.qWait(100)
                assert page.composerSummaryLorasLabel.text() == realistic
                document.setPlainText(realistic)
                document.setTextWidth(lora_label.contentsRect().width())
                assert lora_label.height() >= document.size().height()
                lora_bottom = lora_label.mapTo(page.composerSummaryBoxContents, QPoint(0, lora_label.height())).y()
                items_top = page.composerSummaryItemsCaption.mapTo(page.composerSummaryBoxContents, QPoint(0, 0)).y()
                assert items_top >= lora_bottom
                save(form, out, "queues-55-inspector-two-loras")
            finally:
                for label, previous in zip(summary_labels, previous_text, strict=True):
                    label.setText(previous)
                shell.queue.close_composer()
        # Keep the previously widest inspector mounted and verify that it cannot widen Generate after navigation.
        form.generationNavigationButton.click()
        fits(shell.generation_page.generationInspector)
        if category == "generate":
            save(form, out, "generate-50-inspector-after-queues")
        form.queueNavigationButton.click()
        fits(page.queueInspector)
    finally:
        page.itemBackendLabel.setText(backend)
        rail.setMaximumWidth(maximum)
        rail.setMinimumWidth(minimum)
        form.workspaceStack.setCurrentIndex(selected)
        chrome.workspaceSplitter.setSizes(sizes)
        scroll.verticalScrollBar().setValue(position)
        QTest.qWait(30)


@patch.object(GenerationPage, "request_cached", new=lambda self, **_: None)
def render_workspace_states(app, state, out):
    """Route shared workspace fixtures by category, with synthetic credentials and intercepted mutations."""
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
        if wants("connection"):
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
        if wants("generate"):
            render_generate_configuration_states(shell, out)
        if wants("queues"):
            render_active_queue_states(shell, out)
            render_item_table_states(shell, out, state)
        if wants("images"):
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

        about = form.findChild(QToolButton, "aboutButton")
        assert about.size() == QSize(20, 20) and about.iconSize() == QSize(16, 16)
        QTest.mouseMove(about, about.rect().center())
        snap("connection-40-about-status-button")


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
        options["callback"]([("", 1.0)])

    with patch.object(shell.runner, "submit", side_effect=hold_worker):
        form.backendCombo.setCurrentText("local")
        form.generationNavigationButton.click()
        shell.shell.leftScroll.verticalScrollBar().setValue(0)
        assert not page.includeCloudCheck.isHidden() and page.simulationBox.isHidden()
        assert not page.computeUnitsMeter.isHidden()
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

        with patch(
            "dtflow.services.checkpoint_metadata.exact_model_spec",
            return_value={"file": "offline-checkpoint.ckpt", "name": "Offline model", "version": "flux2"},
        ):
            page.checkpointDetailsButton.click()
            assert shell.inline.form.inlineTitleLabel.text() == "Checkpoint details"
            assert "Technical / version: flux2" in shell.inline.form.inlineTextBrowser.toPlainText()
            snap("generate-23a-checkpoint-details-published")
            clear()
        page.modelCombo.setCurrentText(
            "offline-checkpoint-with-a-very-long-identifier-q6p-unknown-technical-description.ckpt"
        )
        snap("generate-23aa-checkpoint-icon-long-name")
        form.resize(800, 680)
        snap("generate-23ab-checkpoint-icon-narrow")
        form.resize(1600, 1000)
        page.checkpointDetailsButton.click()
        assert "Not available for this exact checkpoint" in shell.inline.form.inlineTextBrowser.toPlainText()
        snap("generate-23b-checkpoint-details-missing")
        clear()
        page.modelCombo.setCurrentText("offline-checkpoint.ckpt")

        lora = controller.lora
        with patch.object(lora, "_candidates", [("offline-style.ckpt", 0.8), ("offline-detail.ckpt", None)]):
            page.addLoraButton.click()
            editor = shell.inline._editor
            assert editor is not None
            rows = {r.property("identifier"): r for r in editor.findChildren(QWidget, "loraSelectionRow")}
            rows["offline-style.ckpt"].findChild(QSlider, "loraSelectionSlider").setValue(16)  # 0.8; checks it too
            snap("generate-24-lora-selection-editor")
            clear()
        with patch.object(lora, "_candidates", [("offline-style.ckpt", 4.2)]):
            page.addLoraButton.click()
            editor = shell.inline._editor
            assert editor is not None
            assert editor.findChildren(QWidget, "loraSelectionRow")
            snap("generate-25-lora-selection-single")
            clear()
        with patch.object(lora_ui, "edit_lora_selection", side_effect=reject_selection):
            page.addLoraButton.click()
            assert "LoRAs not changed" in page.compatibilityStatusLabel.text()
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

        # Multi-file controller composition with held workers and synthetic receipts only.
        files = [str(shell.state_dir / f"offline-prepare-{number}.ckpt") for number in range(3)]
        with patch.object(lora, "choose_asset_file", return_value=files):
            page.prepareAssetButton.click()
        assert lora.preparing and "Preparing 1 of 3" in page.assetStatusLabel.text()
        snap("generate-43-prepare-multiple-start")
        lora._prepared(dict(confirmed, asset_id="offline-first", identifier="offline-first.ckpt"), None)
        assert lora.preparing and "Preparing 2 of 3" in page.assetStatusLabel.text()
        snap("generate-44-prepare-multiple-progress")
        lora._prepared(record, None)
        lora._prepared(None, OSError("Offline synthetic unresolved result"))
        assert not lora.preparing and "1 confirmed, 0 failed, 2 unresolved" in page.assetStatusLabel.text()
        snap("generate-45-prepare-multiple-partial")
        clear()
        with (
            patch.object(lora, "choose_asset_file", return_value=files[:1]),
            patch("dtflow.ui.lora_panel.load_prepare_folder", side_effect=OSError("Offline preference failure")),
        ):
            page.prepareAssetButton.click()
        assert "asset-folder" in strip.rows and lora.preparing
        snap("generate-46-prepare-folder-unavailable")
        lora._prepared(record, None)
        clear()
        # Compute Units meter (Cloud and Local): calculated fixture value never reads a provider.
        meter = page.computeUnitsMeter
        units_path = "dtflow.ui.generation_page.estimate_compute_units"
        form.backendCombo.setCurrentText("cloud")
        for value, name, state in (
            (1500, "generate-47a-compute-units-meter-typical", "ok"),
            (14250, "generate-47-compute-units-meter", "ok"),
            (100000, "generate-48-compute-units-over-limit", "over"),
            (None, "generate-49-compute-units-unavailable", "unavailable"),
        ):
            with patch(units_path, return_value=ComputeUnitEstimate(value, "Offline fixture estimate.")):
                controller._update_compute_units()
            assert not meter.isHidden() and meter.property("state") == state
            expected = compute_units_meter_value(value) if value else 0
            assert meter.maximum() == COMPUTE_UNITS_METER_STEPS and meter.value() == expected
            assert meter.toolTip() == ""  # no hover overlay
            assert (value is None) or str(value)[:2] in meter.format().replace("\u202f", "")
            shell.shell.leftScroll.verticalScrollBar().setValue(0)
            snap(name)
        form.backendCombo.setCurrentText("local")
        for include_cloud, name in (
            (False, "generate-49b-compute-units-local"),
            (True, "generate-49c-compute-units-local-include-cloud"),
        ):
            page.includeCloudCheck.setChecked(include_cloud)
            # The catalogue toggle may rebuild the selector; restore the known
            # fixture checkpoint before checking both meter and inspector.
            page.modelCombo.setCurrentText("offline-checkpoint.ckpt")
            assert page.checkpointDetailsButton.isEnabled()
            with patch(units_path, return_value=ComputeUnitEstimate(14250, "Offline fixture estimate.")):
                controller._update_compute_units()
                assert not meter.isHidden() and meter.property("state") == "ok"
                assert meter.value() == compute_units_meter_value(14250)
                page.checkpointDetailsButton.click()
            assert "Compute Units: 14\u202f250 CU" in shell.inline.form.inlineTextBrowser.toPlainText()
            snap(name)
            clear()
        # Checkpoint previous/next: both enabled mid-list; Next stops at the last entry.
        controller._fill_models(["offline-a.ckpt", "offline-b.ckpt", "offline-c.ckpt"])
        page.modelCombo.setCurrentIndex(-1)
        assert page.previousCheckpointButton.isEnabled() and page.nextCheckpointButton.isEnabled()
        page.previousCheckpointButton.click()  # nothing selected: Previous takes the last entry
        assert page.modelCombo.currentIndex() == 2
        page.modelCombo.setCurrentIndex(1)
        assert page.previousCheckpointButton.isEnabled() and page.nextCheckpointButton.isEnabled()
        shell.shell.leftScroll.verticalScrollBar().setValue(0)
        snap("generate-49d-checkpoint-steppers")
        page.nextCheckpointButton.click()
        assert page.modelCombo.currentIndex() == 2 and not page.nextCheckpointButton.isEnabled()
        snap("generate-49e-checkpoint-steppers-last")
        controller._step_settle.stop()
        form.backendCombo.setCurrentText("fake")
        controller._update_compute_units()
        assert meter.isHidden()
        form.backendCombo.setCurrentText("cloud")
        # Backend switches can rebuild the checkpoint selector and clear the local-only
        # selection. Restore a named offline checkpoint before clicking its details.
        page.modelCombo.setCurrentText("offline-checkpoint.ckpt")
        assert page.checkpointDetailsButton.isEnabled(), "Checkpoint details button needs a selected model."
        with patch(units_path, return_value=ComputeUnitEstimate(14250, "Offline fixture estimate.")):
            controller._update_compute_units()
            page.checkpointDetailsButton.click()
        detail = shell.inline.form.inlineTextBrowser.toPlainText()
        assert "Compute Units: 14\u202f250 CU" in detail and "Supported models:" in detail, (
            f"Unexpected checkpoint details for {page.modelCombo.currentText()!r}: {detail!r}"
        )
        assert "5.14816e-12" not in detail and "Source: Draw Things ComputeUnits.swift" not in detail
        snap("generate-49a-compute-units-details")
        clear()
        render_inspector_fit_states(shell, out, "generate")


def render_active_queue_states(shell, out):
    """Synthetic read views through the real controller; no queue is persisted, posted or dispatched."""
    form, queue, page = shell.form, shell.queue, shell.queue_page
    queue_id = "offline-queue-0001"
    stamp = "2026-10-17T12:00:00+00:00"
    PROMPT = "A lighthouse on a basalt cliff at dusk, long exposure, oil painting with heavy impasto"
    strip = activity_strip(page)

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
                    prompt=PROMPT,
                    negative_prompt="Blurry, text, watermark.",
                    model="flux_1_schnell_q8p.ckpt",
                    loras=[("offline-style.safetensors", 0.8)],
                    steps=20,
                    guidance=4.5,
                    sampler="DPM++ 2M SDE Karras",
                    duration=12.3 if state == "succeeded" else None,
                    width=1024,
                    height=1024,
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
            created_at_utc=stamp,
            up_next=None,
            cap=2,
            summary=counts,
            first=dict(
                prompt=PROMPT,
                model="flux_1_schnell_q8p.ckpt",
                operation="inpaint" if edit else "txt2img",
                loras=[("offline-style.safetensors", 0.8)],
            ),
            varied={"steps": 2} if len(items) > 3 else {},
            items=items,
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
        # Category processes start with a fresh form; do not depend on the Generate fixtures running first.
        shell.generation_page.modelCombo.setCurrentText("offline-checkpoint.ckpt")
        shell.generation_page.promptEdit.setPlainText(PROMPT)
        shell.generation_page.seedEdit.setText("123")
        shell.generation.lora.set_loras(())
        request = shell.generation.request_for_queue(preview=True)[0]
        present("running", ["succeeded", "running", "pending"], owned=True)
        # #121: one status chip and one state-dependent primary action in the queue header.
        assert page.queuePrimaryButton.text() == "Pause" and queue._activity.run_state.action == "pause"
        assert page.queueStateLabel.text() == "Running" and not page.removeQueueButton.isEnabled()
        assert page.createQueueButton.isEnabled()  # #133: a new queue can be prepared while one runs
        # The header lists model and LoRAs on their own labelled lines, during and after the run.
        assert page.queueFactModelLabel.text() == "flux_1_schnell_q8p.ckpt"
        assert page.queueFactLorasLabel.text() == "offline-style.safetensors 0.8"
        snap("queues-11-running")
        # #123: while this window dispatches, the composer offers Add to queue; Start now waits for the run.
        page.newQueueButton.click()
        assert queue.composing and page.addQueueButton.isEnabled() and not page.startQueueButton.isEnabled()
        snap("queues-40-composer-while-running")
        queue.close_composer()
        timed = present("running", ["succeeded", "running", "pending"], owned=True)
        timed.update(successful_durations=[45], started_at_utc=datetime.now(UTC).isoformat())
        queue._read_done(([timed], timed, 2), None)
        queue._timer.stop()
        assert "Elapsed" in queue._activity.run_state.detail and "estimate" in queue._activity.run_state.detail
        assert (queue._activity.run_state.completed, queue._activity.run_state.total) == (1, 3)
        snap("queues-33-elapsed-eta")
        with patch("dtflow.ui.queue_page.monotonic", return_value=queue._countdown.deadline - 89):
            queue._timing_tick()
        assert "~1:29 left" in queue._activity.run_state.detail
        assert (queue._activity.run_state.completed, queue._activity.run_state.total) == (1, 3)
        snap("queues-34-eta-countdown")
        resumed = dict(timed, resume_event_id=1)
        queue._read_done(([resumed], resumed, 2), None)
        queue._timer.stop()
        assert "~1:30 left" in queue._activity.run_state.detail
        snap("queues-35-resume-between-polls")
        present("paused", ["succeeded", "pending", "pending"], owned=True)
        assert page.queuePrimaryButton.text() == "Resume" and queue._activity.run_state.action == "resume"
        snap("queues-12-paused-after-start")
        present("running", ["succeeded", "running", "pending"])
        assert queue._activity.run_state.action is None and "another process" in page.queueStateLabel.toolTip()
        snap("queues-13-foreign-running")
        stale = present("running", ["succeeded", "running", "pending"])
        stale["owner_active"] = False
        queue._read_done(([stale], stale, 2), None)
        queue._timer.stop()
        assert page.queuePrimaryButton.text() == "Resume" and queue._activity.run_state.action == "start"
        assert "No dispatch owner" in page.queueStateLabel.toolTip()
        snap("queues-31-interrupted-dispatch")
        present("running", ["unknown", "running", "pending"], owned=True)
        assert page.queuePrimaryButton.isEnabled() and page.cancelQueueButton.isEnabled()  # unknown blocks nothing
        snap("queues-14-unknown-not-blocking")
        present("running", ["succeeded", "failed", "unknown", "save_failed", "cancelled", "running"], owned=True)
        snap("queues-16-item-states")
        page.queueItemsView.setCurrentIndex(queue.model.index(2, 0))
        assert "most likely failed" in page.uncertaintyLabel.text()
        assert page.attemptIdLabel.toolTip() == "attempt-0003"
        # #132/#126: an unknown item offers Retry item; the header offers Relaunch with its four choices.
        assert page.itemRetryButton.isEnabled() and page.relaunchQueueButton.isEnabled()
        relaunch_menu = QMenu(page)
        queue._relaunch_actions(relaunch_menu, queue._selected)
        assert [action.isEnabled() for action in relaunch_menu.actions()] == [True] * 4
        snap("queues-17-item-inspection")
        # #125: a paused queue offers Start next while this window dispatches; an up-next queue can move or leave.
        paused = dict(
            present("paused", ["pending", "pending"], started=False),
            queue_id="offline-queue-0002",
            created_at_utc="2026-10-17T12:05:00+00:00",
        )
        running = present("running", ["succeeded", "running", "pending"], owned=True)
        queue._selected = paused["queue_id"]
        queue._read_done(([running, paused], paused, 2), None)
        queue._timer.stop()
        assert page.queuePrimaryButton.text() == "Start next" and page.queuePrimaryButton.isEnabled()
        snap("queues-24-start-next")
        queued = dict(paused, status="queued", up_next=1)
        queue._read_done(([running, queued], queued, 2), None)
        queue._timer.stop()
        assert page.earlierQueueButton.isVisible() and page.queuePrimaryButton.text() == "Pause"
        assert page.queueStateLabel.text() == "Up next 1" and "1 up next" in queue._activity.run_state.detail
        assert "up next 1" in queue.queue_items()[1].text()
        snap("queues-25-up-next")
        # #122: a long, grouped list (Running, Up next, Paused, History), its filter, Remove and an item filter.
        history = []
        for number, (status, states, prompt) in enumerate(
            (
                ("finished", ["succeeded"] * 4, "Portrait of an old fisherman, rembrandt lighting"),
                ("finished", ["succeeded", "failed", "unknown", "succeeded"], "Isometric tiny city block at night"),
                ("cancelled", ["succeeded", "cancelled", "cancelled"], "Red fox in fresh snow, telephoto"),
                ("finished", ["succeeded"] * 6, "Brutalist museum interior, soft daylight"),
                ("paused", ["pending"] * 8, "Studio product shot of a ceramic teapot"),
                ("finished", ["succeeded", "succeeded"], "Watercolor map of an imaginary archipelago"),
                ("finished", ["save_failed", "succeeded", "succeeded"], "Macro photo of dew on a spider web"),
            ),
            3,
        ):
            view = present(status, states, started=status != "paused")
            history.append(
                dict(
                    view,
                    queue_id=f"offline-queue-{number:04d}",
                    created_at_utc=f"2026-10-1{number % 7}T0{number}:15:00+00:00",
                    first=dict(view["first"], prompt=prompt, model="sdxl_base_1.0_f16.ckpt"),
                    varied={"prompt": 2, "steps": 2} if number % 2 else {},
                )
            )
        everything = [running, queued, *history]
        mixed = history[1]
        queue._selected = mixed["queue_id"]
        queue._read_done((everything, mixed, 2), None)
        queue._timer.stop()
        headings = [page.queueList.item(i).text() for i in range(page.queueList.count())]
        assert [h for h in headings if h.isupper()] == ["RUNNING", "UP NEXT", "PAUSED", "HISTORY"]
        assert page.failedFilterButton.text() == "✗ 1" and page.removeQueueButton.isEnabled()
        snap("queues-26-list-groups")
        # The queue list, not the shared left QScrollArea, owns vertical scrolling.
        form.resize(1182, 792)
        shell._responsive_workspace()
        QTest.qWait(100)
        left_scroll = shell.shell.leftScroll
        assert left_scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert left_scroll.verticalScrollBar().maximum() == 0
        assert page.queueList.verticalScrollBar().maximum() > 0
        filter_top = page.queueFilterEdit.mapTo(left_scroll.viewport(), QPoint(0, 0)).y()
        clear_top = page.clearQueuesButton.mapTo(left_scroll.viewport(), QPoint(0, 0)).y()
        page.queueList.verticalScrollBar().setValue(page.queueList.verticalScrollBar().maximum())
        QTest.qWait(30)
        assert page.queueFilterEdit.mapTo(left_scroll.viewport(), QPoint(0, 0)).y() == filter_top
        assert page.clearQueuesButton.mapTo(left_scroll.viewport(), QPoint(0, 0)).y() == clear_top
        assert 0 <= clear_top < left_scroll.viewport().height()
        assert clear_top + page.clearQueuesButton.height() <= left_scroll.viewport().height()
        snap("queues-56-single-left-scroll")
        form.resize(1600, 1000)
        shell._responsive_workspace()
        page.queueList.verticalScrollBar().setValue(0)
        page.queueFilterEdit.setText("fox")
        assert len(queue.queue_items()) == 1
        snap("queues-27-list-filter")
        page.queueFilterEdit.setText("nothing like this")
        assert not queue.queue_items()
        snap("queues-28-list-filter-empty")
        page.queueFilterEdit.clear()
        # #126: with queues listed but none selected, the empty state offers Relaunch last.
        selected, detail = queue._selected, queue._detail
        queue._set_selected(None)
        queue._render()
        assert page.emptyStateCard.isVisible() and page.emptyRelaunchButton.isVisible()
        snap("queues-44-empty-relaunch-last")
        queue._set_selected(selected)
        queue._detail = detail
        queue._render()
        page.failedFilterButton.setChecked(True)
        assert [page.queueItemsView.isRowHidden(row) for row in range(4)] == [True, False, True, True]
        snap("queues-29-item-filter")
        page.failedFilterButton.setChecked(False)
        page.removeQueueButton.click()  # arms the inline Remove confirmation; nothing is removed here
        assert page.removeQueueButton.property("confirmationPending")
        snap("queues-30-remove-confirmation")
        shell.inline.dismiss()
        page.failedFilterButton.setChecked(True)
        fresh = dict(history[4], queue_id="offline-queue-new")
        queue._select_after = fresh["queue_id"]
        queue._read_done(([fresh], fresh, 2), None)
        queue._timer.stop()
        assert not page.failedFilterButton.isChecked()
        assert not any(page.queueItemsView.isRowHidden(row) for row in range(len(fresh["items"])))
        snap("queues-32-new-queue-filter-reset")
        queue._selected = queue_id
        present("paused", ["pending", "pending"], started=False)
        # #123 / #140: New queue (top of the center) swaps the selected queue for the composer in the center.
        page.newQueueButton.click()
        assert queue.composing and not page.queueHeader.isVisible() and not page.queueTableHost.isVisible()
        assert not page.planTableView.isVisible()
        assert page.planCountLabel.text().startswith("1 item")
        snap("queues-37-composer-repeat")
        page.planModeCombo.setCurrentText("Cross")
        page.planModelsEdit.setText("offline-checkpoint.ckpt")
        page.planStepsEdit.setText("20; 30")
        page.planSamplersEdit.setText("euler_a; ddim")
        page.planSizesEdit.setText("768x768; 1024x1024")
        shell.generation.lora.set_loras((LoRASelection("offline-style.safetensors", 0.8),))
        queue.preview()
        assert queue.plan_model.rowCount() == 8 and page.startQueueButton.text() == "Start now (8 items)"
        assert page.planTableView.isVisible()
        assert page.composerSummaryBox.isVisible() and page.composerSummaryItemsLabel.text() == "8"
        assert "offline-style" in page.composerSummaryLorasLabel.text()
        assert "model" not in page.composerSummaryVariesLabel.text()  # single-value dimensions do not vary
        snap("queues-38-composer-cross")
        shell.generation.lora.set_loras(())
        page.planModelsEdit._add.click()  # the shared searchable editor, filled from the Generate inventory
        snap("queues-39-composer-model-search")
        shell.inline.dismiss()
        # #124: LoRA sets vary which LoRAs each item uses (None, or a LoRA with its weight).
        page.planLoraSetsEdit.setText("None; offline-style.safetensors: 0.8")
        queue.preview()
        assert queue.plan_model.rowCount() == 16
        assert page.composerSummaryLorasLabel.text() == "2 LoRA sets, per item"
        assert "2 LoRA sets" in page.composerSummaryVariesLabel.text()
        page.queueComposerScroll.ensureWidgetVisible(page.planLoraSetsEdit)
        snap("queues-42-composer-lora-sets")
        page.planLoraSetsEdit._add.click()  # the shared LoRA checkbox+slider editor, starting from Generate's LoRAs
        snap("queues-43-composer-lora-set-search")
        shell.inline.dismiss()
        page.planLoraSetsEdit.clear()
        # LoRA weights to cross: one chip per checked LoRA and weight, grouped per LoRA for the planner.
        page.planLorasEdit.setText("offline-style.safetensors: 0.6; offline-style.safetensors: 1")
        queue.preview()
        assert queue._lora_weight_lines() == "offline-style.safetensors: 0.6; 1"
        page.queueComposerScroll.ensureWidgetVisible(page.planLorasEdit)
        snap("queues-42a-composer-lora-weights")
        page.planLorasEdit._add.click()  # the same checkbox+slider editor, not limited to Generate's LoRAs
        weight_editor = shell.inline._editor
        weight_editor.findChild(QLineEdit, "loraSelectionCustomIdentifier").setText("offline-style.safetensors")
        weight_editor.findChild(QPushButton, "loraSelectionCustomAddButton").click()
        row = next(r for r in weight_editor.findChildren(QWidget, "loraSelectionRow"))
        for tick in (12, 16, 20):  # 0.6, 0.8, 1.0: several weights for one LoRA, collected with +
            row.findChild(QSlider, "loraSelectionSlider").setValue(tick)
            row.findChild(QToolButton, "loraSelectionAddWeightButton").click()
        assert len(row.findChildren(QPushButton)) == 3
        snap("queues-43a-composer-lora-weights-editor")
        shell.inline.dismiss()
        page.planLorasEdit.clear()
        # #128: random draws from the listed values under one recorded plan seed (the same seed gives the same items).
        page.planStepsEdit.setText("20; 30; 40; 50")
        page.planDrawStepsSpin.setValue(2)
        page.planRngSeedEdit.setText("12345")
        queue.preview()
        assert queue.plan_model.rowCount() == 2 * 2 * 2 and page.planRngSeedEdit.isVisible()
        page.queueComposerScroll.ensureWidgetVisible(page.planDrawStepsSpin)
        snap("queues-48-composer-random-draws")
        # This intermediate width leaves both rails visible but gives the composer
        # substantially less space than a desktop-wide fixture.
        form.resize(1182, 792)
        shell._responsive_workspace()
        QTest.qWait(100)
        assert shell.shell.rightRail.isVisible()
        scroll = page.queueComposerScroll
        viewport = scroll.viewport()
        assert viewport.width() < 680
        assert page.queueComposer.width() <= viewport.width(), (page.queueComposer.width(), viewport.width())
        assert scroll.horizontalScrollBar().maximum() == 0
        for widget in (
            page.newQueueCapLabel,
            page.newQueueCapSpinBox,
            page.createQueueButton,
            page.addQueueButton,
            page.startQueueButton,
            page.closeComposerButton,
            page.editInGenerateButton,
            page.planDrawModelsLabel,
            page.planDrawModelsSpin,
            page.planDrawStepsLabel,
            page.planDrawStepsSpin,
            page.planDrawLorasLabel,
            page.planDrawLorasSpin,
        ):
            assert widget.isVisible(), widget.objectName()
            left = widget.mapTo(viewport, QPoint(0, 0)).x()
            right = widget.mapTo(viewport, QPoint(widget.width(), 0)).x()
            assert 0 <= left < right <= viewport.width(), (widget.objectName(), left, right, viewport.width())
        assert queue._actions_fit._wrapped
        assert page.closeComposerButton.parentWidget() is queue._actions_fit._host
        assert page.planDrawGrid.getItemPosition(page.planDrawGrid.indexOf(page.planDrawModelsSpin))[:2] == (0, 1)
        assert page.planDrawGrid.getItemPosition(page.planDrawGrid.indexOf(page.planDrawSeedsSpin))[:2] == (0, 3)
        assert page.planDrawGrid.getItemPosition(page.planDrawGrid.indexOf(page.planDrawStepsSpin))[:2] == (1, 1)
        # Text-to-image has no Strength control: its hidden slot must not leave a gap.
        assert page.planDrawStrengthsSpin.isHidden()
        assert page.planDrawGrid.getItemPosition(page.planDrawGrid.indexOf(page.planDrawLoraSetsSpin))[:2] == (3, 1)
        assert page.planDrawGrid.getItemPosition(page.planDrawGrid.indexOf(page.planDrawLorasSpin))[:2] == (3, 3)
        snap("queues-53-composer-two-rails-narrow-center")
        form.resize(1600, 1000)
        shell._responsive_workspace()
        QTest.qWait(50)
        assert page.closeComposerButton.parentWidget() is page.queueComposer
        assert page.planDrawGrid.getItemPosition(page.planDrawGrid.indexOf(page.planDrawStepsSpin))[:2] == (0, 5)
        page.planDrawStepsSpin.setValue(0)
        # #129: dynamic prompt variation of the Generate prompt, with wildcards and seeded edits.
        shell.generation_page.promptEdit.setPlainText("a {red|blue} fox, __place__, golden light, film grain")
        page.planVariationCombo.setCurrentText("Random sample")
        page.planVariationCountSpin.setValue(4)
        page.planWildcardsEdit.setPlainText("place: forest; meadow; harbour")
        page.planShuffleCombo.setCurrentText("Shuffle parts")
        page.planDropSpin.setValue(1)
        page.planAddPartsEdit.setText("bokeh; mist")
        page.planAddSpin.setValue(1)
        for name in ("planStepsEdit", "planModelsEdit", "planSamplersEdit", "planSizesEdit"):
            getattr(page, name).clear()
        real_submit = type(shell.runner).submit.__get__(shell.runner)  # this block holds submits; this one must run
        with patch.object(shell.runner, "submit", side_effect=real_submit):
            queue.preview()
            for _ in range(100):  # prompt variation resolves on a worker; wait for the preview to land
                if not queue.previewing:
                    break
                QTest.qWait(50)
        assert queue.plan_model.rowCount() == 4, page.planCountLabel.text()
        token = queue._preview_token
        for _ in range(3):  # unchanged polls keep the shown preview and never restart its computation
            queue.sync_edit_fields()
            queue.preview()
        assert queue._preview_token == token and not queue.previewing and queue.plan_model.rowCount() == 4
        page.queueComposerScroll.ensureWidgetVisible(page.planVariationCombo)
        snap("queues-49-composer-prompt-variation")
        form.resize(1182, 792)
        shell._responsive_workspace()
        QTest.qWait(100)
        assert page.planMutationBox.isVisible() and page.planRngSeedEdit.isVisible()
        viewport = page.queueComposerScroll.viewport()
        assert page.queueComposer.width() <= viewport.width()
        assert page.queueComposerScroll.horizontalScrollBar().maximum() == 0
        for widget in (
            page.planVariationCombo,
            page.planVariationCountSpin,
            page.planShuffleCombo,
            page.planDropLabel,
            page.planDropSpin,
            page.planAddLabel,
            page.planAddSpin,
            page.planRngSeedEdit,
            page.planRerollButton,
        ):
            assert widget.isVisible(), widget.objectName()
            left = widget.mapTo(viewport, QPoint(0, 0)).x()
            right = widget.mapTo(viewport, QPoint(widget.width(), 0)).x()
            assert 0 <= left < right <= viewport.width(), (widget.objectName(), left, right, viewport.width())
        snap("queues-54-composer-narrow-prompt-edit")
        form.resize(1600, 1000)
        shell._responsive_workspace()
        QTest.qWait(50)
        page.planVariationCombo.setCurrentText("Off")
        page.planShuffleCombo.setCurrentText("No shuffle")
        page.planDropSpin.setValue(0)
        page.planAddSpin.setValue(0)
        page.planAddPartsEdit.clear()
        page.planWildcardsEdit.clear()
        page.planRngSeedEdit.clear()
        for name in ("planModelsEdit", "planStepsEdit", "planSamplersEdit", "planSizesEdit"):
            getattr(page, name).clear()
        page.planSeedsEdit.setText("not-a-seed")
        queue.preview()
        assert page.planSeedsEdit.property("invalid")
        assert not page.planTableView.isVisible()  # no tall, blank table underneath the validation message
        page.queueComposerScroll.ensureWidgetVisible(page.planSeedsEdit)
        snap("queues-18-plan-invalid")
        page.planSeedsEdit.clear()
        page.planStepsEdit.setText("20; 30")
        queue.preview()
        assert page.planTableView.isVisible()  # valid settings restore the preview automatically
        page.planStepsEdit.clear()
        queue._mark_plan(None)
        with (
            patch.object(queue, "_editing", return_value=True),
            patch.object(queue, "_edit_source", return_value=object()),
        ):
            present("paused", ["pending", "pending"], started=False, edit=True)
            page.planStrengthsEdit.setText("0.5; 0.7")
            assert queue._plan_for(request) is not None
            assert not page.planStrengthsEdit.isHidden() and queue.model.row_at(0).edit_text == "Inpaint · 0.7"
            assert page.planDrawGrid.indexOf(page.planDrawStrengthsSpin) >= 0
            page.queueComposerScroll.ensureWidgetVisible(page.planStrengthsEdit)
            snap("queues-19-inpaint-plan")
        page.planStrengthsEdit.clear()
        page.planModeCombo.setCurrentText("Repeat")
        queue.close_composer()
        queue._sync()
        queue._syncing = True
        page.concurrencySpinBox.setValue(10)
        page.newQueueCapSpinBox.setValue(10)
        queue._syncing = False
        queue._set_level(page.concurrencySpinBox, 10)
        assert page.concurrencySpinBox.value() == page.newQueueCapSpinBox.value() == 10
        snap("queues-36-max-at-once-ten")
        queue._syncing = True
        page.concurrencySpinBox.setValue(4)
        page.newQueueCapSpinBox.setValue(4)
        queue._syncing = False
        queue._set_level(page.concurrencySpinBox, 4)
        # Defensive validator refusal: the bounded spin box normally prevents values outside 1..10.
        queue._cap_changed(11)
        assert strip.rows["queue-message"].activityLabel.text() == "Concurrency not changed"
        assert page.concurrencySpinBox.property("level") == "elevated"
        snap("queues-20-concurrency-refused")
        strip.finish("queue-message")
        present("finished", ["succeeded", "succeeded", "succeeded"])
        assert not page.queuePrimaryButton.isEnabled() and page.queueProgressBar.settled == 3
        assert not page.concurrencySpinBox.isEnabled()  # a finished queue's maximum is fixed
        snap("queues-21-finished-table")
        present("cancelled", ["succeeded", "cancelled", "cancelled"])
        assert not page.queuePrimaryButton.isEnabled() and page.queueProgressBar.settled == 3
        snap("queues-22-cancelled-table")
        render_inspector_fit_states(shell, out, "queues")
        form.resize(1000, 900)
        shell._responsive_workspace()
        assert shell.shell.rightRail.isHidden()
        assert page.newQueueButton.isVisible()  # #140: New queue sits in the center, reachable at every width
        snap("queues-23-narrow")
        page.newQueueButton.click()
        assert queue.composing and page.startQueueButton.isVisible()
        snap("queues-41-composer-narrow")
        queue.close_composer()
        form.resize(1600, 1000)
        shell._responsive_workspace()
        queue._dispatching = None
        queue._dispatch_started = False
        queue._selected = None
        queue._activity.set(dispatching=False)
        queue._read_done(([], None, 2), None)
        queue._timer.stop()


def header_end_visible(table):
    """The last column (duration) must sit fully inside the viewport, not behind a horizontal scroll."""
    header = table.horizontalHeader()
    last = header.count() - 1
    return header.sectionViewportPosition(last) + header.sectionSize(last) <= table.viewport().width()


def render_item_table_states(shell, out, state):
    """The seven-column items table (#127) from synthetic fixtures; thumbnails are generated gradients, not photographs.

    Rows follow devdocs/mockups/2026-10-21-queues-item-table-text-preview.md. Nothing is persisted, posted or dispatched.
    """
    form, queue, page = shell.form, shell.queue, shell.queue_page
    real_submit = shell.runner.submit  # the patch below holds every submit; thumbnail decoding must really run
    queue_id = "offline-queue-table"
    stamp = "2026-10-21T19:40:00+00:00"
    long_lora = "ultra_realistic_characters_style_detail_v12.safetensors"
    assert len(long_lora) == 55
    fixtures = state / "item-table"
    fixtures.mkdir(parents=True, exist_ok=True)

    def thumbnail(name, hue):
        image = QImage(256, 256, QImage.Format.Format_RGB32)
        for y in range(256):
            for x in range(256):
                image.setPixelColor(x, y, QColor.fromHsv((hue + x // 8) % 360, 120, 70 + y // 2))
        path = fixtures / f"{name}.png"
        assert image.save(str(path))
        return str(path)

    traveler = "Portrait of a traveler in a blue coat at dawn, soft light, mountains in the distance, realistic fabric details, shallow depth of field."
    negative = "Blurry, distorted hands, duplicate faces, text, watermark."
    z_model, photo_model = "Z-Image-Turbo_v2.ckpt", "PhotoReal_XL-v2.ckpt"
    karras, exponential = "DPM++ 2M SDE Karras", "DPM++ 3M SDE Exponential"
    specs = (
        (
            "succeeded",
            traveler,
            z_model,
            [("portrait_style_v1.safetensors", 0.9), ("cinematic_rim_light_v3.safetensors", 1.2)],
            28,
            4.5,
            karras,
            31.2,
            "a",
        ),
        (
            "succeeded",
            traveler,
            z_model,
            [
                ("portrait_style_v1.safetensors", 1.2),
                ("cinematic_rim_light_v3.safetensors", 0.6),
                (long_lora, 1.5),
                ("background_bokeh_v2.safetensors", 0.9),
            ],
            32,
            5.0,
            exponential,
            36.8,
            "b",
        ),
        (
            "running",
            "A red house beside a mountain lake, with cloud reflections spreading across the water, still morning air and pale light on the far ridge.",
            photo_model,
            [
                ("landscape_style_v2.safetensors", 0.9),
                ("cloud_mood_v4.safetensors", 1.2),
                ("texture_detail_v1.safetensors", 0.6),
            ],
            24,
            4.0,
            karras,
            None,
            None,
        ),
        (
            "unknown",
            "Studio photo of a ceramic cup on a table, with warm light against a pale background and a soft shadow beneath.",
            photo_model,
            [],
            24,
            4.0,
            karras,
            None,
            None,
        ),
        (
            "pending",
            "An illustrated old town square after rainfall, with lamps reflected in wet cobblestones, evening haze between the buildings.",
            z_model,
            [("portrait_style_v1.safetensors", 0.9)],
            28,
            4.5,
            exponential,
            None,
            None,
        ),
        (
            "cancelled",
            "A lighthouse on a rocky coast at dusk, with waves breaking against dark stone and a warm lamp glowing above.",
            photo_model,
            [],
            24,
            4.0,
            karras,
            None,
            None,
        ),
    )
    items = []
    for position, (item_state, prompt, model, loras, steps, guidance, sampler, seconds, image) in enumerate(specs, 1):
        artifacts = (
            [thumbnail(f"item-{image}", 200 + position * 40), str(fixtures / f"attempt-{position}" / "metadata.json")]
            if image
            else []
        )
        items.append(
            dict(
                item_id=f"d8293dd{position}",
                position=position,
                state=item_state,
                attempt_id=f"a71b71c{position}" if item_state not in ("pending", "cancelled") else None,
                request_id=f"request-{position:04d}",
                seed=295625550 + position,
                artifacts=artifacts,
                failure=None,
                operation="txt2img",
                strength=None,
                prompt=prompt,
                negative_prompt=negative,
                model=model,
                loras=loras,
                steps=steps,
                guidance=guidance,
                sampler=sampler,
                duration=seconds,
                width=1024,
                height=1024,
            )
        )
    states = [item["state"] for item in items]
    counts = {
        name: states.count(name)
        for name in ("pending", "running", "succeeded", "failed", "unknown", "save_failed", "cancelled")
    }
    view = dict(
        queue_id=queue_id,
        status="running",
        backend={"name": "local", "endpoint": None},
        created_at_utc=stamp,
        up_next=None,
        cap=2,
        summary=counts,
        first=dict(prompt=traveler, model=z_model, operation="txt2img", loras=items[0]["loras"]),
        varied={"prompt": 4, "model": 2, "loras": 4},
        items=items,
        pending_controls=[],
        events=[dict(kind="created", at_utc=stamp, detail={"items": len(items), "cap": 2})],
    )

    def snap(name):
        QTest.qWait(100)
        save(form, out, name)

    def select(position):
        page.queueItemsView.setCurrentIndex(queue.model.index(position, 0))
        QTest.qWait(30)

    def hold_worker(coroutine, callback):
        coroutine.close()

    def fixture_decode(path, identity, _state):
        # Synthetic render identities are not live database authority. Intercept only this offline boundary.
        from dtflow.ui.gallery_page import THUMBNAIL_SIDE, _image

        return _image(path, THUMBNAIL_SIDE, state)

    with (
        patch.object(shell.runner, "submit", side_effect=hold_worker),
        patch("dtflow.ui.queue_thumbnails._decode", side_effect=fixture_decode),
    ):
        form.backendCombo.setCurrentText("local")
        form.queueNavigationButton.click()
        form.resize(1600, 1000)
        shell._responsive_workspace()
        queue._dispatching = queue_id
        queue._dispatch_started = True
        queue._selected = queue_id
        queue._activity.set(dispatching=True)
        queue._read_done(([view], view, 2), None)
        queue._timer.stop()
        model, table = queue.model, page.queueItemsView
        model.thumbnails._runner = SimpleNamespace(submit=real_submit, closed=False)
        model.thumbnails.clear()
        model.thumbnails._active = 0  # fixture-only jobs were closed by hold_worker, not actually submitted
        assert model.columnCount() == 7 and model.rowCount() == len(items)
        wait_for(
            lambda: model.thumbnails.pixmap(items[0]["artifacts"][0], identity="a71b71c1:0") is not None
        )  # async, bounded decode through the intercepted fixture boundary
        assert model.gauge_scale == 1.5  # the largest recorded weight in this fixture; a 0.94 LoRA fills 63%
        assert model.headerData(6, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole) == "Backend duration"
        assert table.verticalHeader().defaultSectionSize() == 76
        assert header_end_visible(table)
        header = table.horizontalHeader()
        # The stopwatch is painted by ItemTableHeader; a model decoration would draw a second one at the left edge.
        assert type(header).__name__ == "ItemTableHeader"
        assert model.headerData(6, Qt.Orientation.Horizontal, Qt.ItemDataRole.DecorationRole) is None
        assert (
            header.sectionSize(COL_LORAS) >= 190 and header.sectionSize(COL_PROMPT) >= table.viewport().width() * 0.29
        )
        # Statuses use the shared semantic colors as a single text label, and duration is "—" when unavailable.
        for row, word in enumerate(("succeeded", "succeeded", "running", "no result", "pending", "cancelled")):
            assert model.index(row, COL_STATUS).data() == word
        assert model.index(0, COL_STATUS).data(Qt.ItemDataRole.ForegroundRole).name() == LEVEL_COLORS["ok"]
        assert [model.index(row, 6).data() for row in (0, 2, 3)] == ["31.2 s", "—", "—"]
        # The prompt wraps naturally over two lines; the first line never carries an ellipsis.
        lines = wrapped_lines(traveler, table.font(), header.sectionSize(COL_PROMPT) - 14, 2)
        assert len(lines) == 2 and not lines[0].endswith("…")
        # Hover text is read-only: all LoRAs (including the 55-character name) and both prompts.
        tip = model.index(1, COL_LORAS).data(Qt.ItemDataRole.ToolTipRole)
        assert long_lora.removesuffix(".safetensors") in tip and "background_bokeh_v2" in tip  # all four LoRAs
        assert ".safetensors" not in tip  # extensions are hidden in the hover too
        prompt_tip = model.index(1, COL_PROMPT).data(Qt.ItemDataRole.ToolTipRole)
        assert "Positive" in prompt_tip and negative.rstrip(".") in prompt_tip
        assert model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole) == ""  # the preview is a bounded overlay, not <img>
        assert model.index(2, 0).data(Qt.ItemDataRole.ToolTipRole) == "No saved image"
        assert model.index(3, COL_LORAS).data(Qt.ItemDataRole.ToolTipRole) == ""
        opened = []
        queue.set_images_hook(opened.append)
        select(0)
        wait_for(lambda: page.itemOpenImagesButton.isEnabled())
        assert page.itemOpenImagesButton.isEnabled() and page.itemOutcomeLabel.text() == "No error recorded"
        assert model.row_at(0).gallery_identity == "a71b71c1:0"  # attempt id and the image's index in its artifacts
        page.itemOpenImagesButton.click()
        assert opened == ["a71b71c1:0"]  # the hook receives the gallery identity, never a path
        snap("queues-60-item-table")
        preview = queue._hover_preview
        preview.show_row(0, QPoint(600, 300))
        wait_for(lambda: not preview.label.pixmap().isNull())
        assert preview.label.windowFlags() & Qt.WindowType.ToolTip and preview.label.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        save(preview.label, out, "queues-64-item-table-thumbnail-preview")
        preview.hide()
        select(1)  # more than three LoRAs and the 55-character filename
        assert page.itemSeedLabel.text() == str(items[1]["seed"]) and page.itemDimensionsLabel.text() == "1024 × 1024"
        assert page.itemOperationLabel.text() == "Text to image" and page.itemIdLabel.text() == "d8293dd2"
        snap("queues-61-item-table-long-lora")
        select(3)  # no result
        assert page.itemOutcomeLabel.text() == "No result received" and not page.itemOpenImagesButton.isEnabled()
        snap("queues-62-item-table-no-result")
        queue.open_composer()
        assert page.itemDetailsBox.isHidden() and page.itemIdentityBox.isHidden()
        snap("queues-65-composer-without-old-item-evidence")
        queue.close_composer()
        select(0)
        model.thumbnails.clear()
        with patch("dtflow.ui.queue_thumbnails._decode", return_value=(None, "Saved image is no longer available")):
            page.queueItemsView.viewport().update()
            queue._show_item()
            wait_for(lambda: model.thumbnails.is_failed(items[0]["artifacts"][0], identity="a71b71c1:0"))
            assert not page.itemOpenImagesButton.isEnabled()
            snap("queues-66-item-table-unavailable-output")
        form.resize(1000, 900)
        shell._responsive_workspace()
        QTest.qWait(100)
        assert shell.shell.rightRail.isHidden() and header.sectionSize(COL_LORAS) >= 190
        snap("queues-63-item-table-narrow")
        form.resize(1600, 1000)
        shell._responsive_workspace()
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
        # Existing stress/continuity screenshots intentionally retain 100/300.
        patch.object(gallery_ui, "PAGE_SIZE", 100),
        patch.object(gallery_ui, "MAX_MOUNTED_IMAGES", 300),
        # Synthetic projections intentionally drive exact row/cell contents;
        # do not submit real neighbor workers during screenshot assembly.
        patch.object(gallery, "_queue_neighbor_prefetch"),
    ):
        gallery._neighbor_prefetch_timer.stop()
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
        # The actual circular buttons use SVGs so font baselines cannot shift
        # their chevrons. Check painted ink, not just the icon rectangle.
        chevron_records = [replace(item, identity=f"offline-chevron:{i}") for i in range(3)]
        gallery.items, gallery.total, gallery.offset = chevron_records, 3, 0
        gallery._render_page()
        gallery._rendered_page = (0, tuple(chevron_records))
        for record in chevron_records:
            gallery._thumbnail_done(gallery._thumb_versions[record.identity], record.identity, (image, ""), None)
        gallery.canvas_loading.clear()
        gallery.select(chevron_records[1].identity)
        gallery._image_done(gallery._image_generation, gallery.selected_id, (image, ""), None)
        QTest.qWait(60)
        for button in (gallery.previous_button, gallery.next_button):
            assert button.isVisible() and button.isEnabled() and not button.text() and not button.icon().isNull()
            assert button.iconSize() == QSize(24, 24)
            pixels = button.grab().toImage()
            center_x, center_y = (pixels.width() - 1) / 2, (pixels.height() - 1) / 2
            ink = []
            for y in range(pixels.height() // 4, pixels.height() * 3 // 4):
                for x in range(pixels.width() // 4, pixels.width() * 3 // 4):
                    color = pixels.pixelColor(x, y)
                    channels = (color.red(), color.green(), color.blue())
                    if min(channels) > 180 and max(channels) - min(channels) < 8:
                        ink.append((x, y))
            assert ink, f"Missing Detail chevron: {button.objectName()}"
            assert abs((min(x for x, _ in ink) + max(x for x, _ in ink)) / 2 - center_x) <= 1
            assert abs((min(y for _, y in ink) + max(y for _, y in ink)) / 2 - center_y) <= 1
        snap("images-97-detail-navigation-centered")
        gallery.previous_button.click()
        assert gallery.selected_id == chevron_records[0].identity
        assert not gallery.previous_button.isEnabled() and gallery.next_button.isEnabled()
        gallery.next_button.click()
        assert gallery.selected_id == chevron_records[1].identity
        gallery.previous_button.click()
        assert gallery.selected_id == chevron_records[0].identity
        gallery._image_done(gallery._image_generation, gallery.selected_id, (image, ""), None)
        snap("images-98-detail-navigation-first-image")

        present(item)
        gallery.show_view("Grid")
        gallery._unselect_all()
        # The pager's static/fullscreen states are captured before live lazy loading is wired.
        # Synthetic identities and solid-color fixture pixels never call a provider.
        pager_records = [
            replace(item, identity=f"offline-gallery:{index}", attempt_id=f"offline-attempt:{index}")
            for index in range(101, 125)
        ]
        gallery.items, gallery.total, gallery.offset = pager_records, 342, 100
        gallery._render_page()
        gallery._rendered_page = (100, tuple(pager_records))
        gallery.pager.present(total=342, visible_start=101, visible_end=124, loaded=200, active_page=2)
        assert gallery.pager.buttons[2].isChecked()
        assert gallery._widget("galleryPagerBar").isVisible()
        # Global status-bar index is intentionally understated and Images-only.
        shell._sync_grid_index_status(101, 342)
        assert shell.grid_index_group.isVisible()
        assert shell.grid_index_label.text() == "101/342"
        assert not shell.grid_index_label.hasFrame()
        snap("images-65a-status-position-subtle")
        form.generationNavigationButton.click()
        assert not shell.grid_index_group.isVisible()
        snap("generation-65b-status-position-hidden")
        form.imagesNavigationButton.click()
        assert shell.grid_index_group.isVisible()
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
        gallery.pager.present(total=342, visible_start=101, visible_end=124, loaded=200, active_page=2, loading=True)
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

        # One centered presentation is shared by initial live reads, snapshot
        # fallback and page jumps. No provider calls; these are GUI states.
        gallery.canvas_loading.loading(immediate=True)
        QTest.qWait(110)
        assert gallery.canvas_loading.ring.timer.isActive()
        assert gallery.canvas_loading.ring.angle != 0
        assert gallery.canvas_loading.form.isVisible()
        snap("images-84-pager-initial-database-loading")

        gallery.canvas_loading.loading(immediate=True)
        snap("images-85-pager-snapshot-read-loading")

        gallery.total, gallery.offset = 342, 300
        gallery.pager.present(total=342, visible_start=0, visible_end=0, loaded=0, active_page=4)
        gallery.canvas_loading.loading(immediate=True, text="Loading page 4…")
        assert gallery.canvas_loading.status.text() == "Loading page 4…"
        snap("images-86-pager-direct-jump-loading")

        gallery.canvas_loading.failed()
        assert not gallery.canvas_loading.ring.timer.isActive()
        assert gallery.canvas_loading.retry.isVisible()
        snap("images-87-pager-empty-load-failed")
        with patch.object(gallery, "refresh") as retry:
            gallery.canvas_loading.retry.click()
            retry.assert_called_once_with(preserve=True, trigger="retry")
        gallery.canvas_loading.clear()
        assert not gallery.canvas_loading.form.isVisible()
        assert not gallery.canvas_loading.ring.timer.isActive()
        gallery.total, gallery.offset = 0, 0
        gallery.pager.present(total=0, visible_start=0, visible_end=0, loaded=0, active_page=0)

        # Multi-batch UX fixtures deliberately build distinct numbered records:
        # batch 1 (1-100), append batch 2 (101-200), then jump directly to the
        # short final batch (301-342). These are *offline projections*, not a
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
            wait_for(
                lambda: gallery._grid_mount_keys is None and not gallery.reflow_timer.isActive()
            )  # wait for all incremental Grid insertions before capturing geometry
            shown_start = first if first_visible is None else first_visible
            shown_end = min(last, shown_start + 23) if last_visible is None else last_visible
            gallery.pager.present(
                total=total,
                visible_start=shown_start,
                visible_end=shown_end,
                loaded=len(records) if loaded is None else loaded,
                active_page=(shown_start - 1) // gallery_ui.PAGE_SIZE + 1,
                batch_size=gallery_ui.PAGE_SIZE,
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
        first_batch = multi_window(1, 100, first_visible=1, last_visible=24)
        gallery.grid_scroll.verticalScrollBar().setValue(0)
        start, end, _ = grid_visible(loaded=100)
        assert start == 1 and end <= 100
        assert gallery.pager.buttons[1].isChecked()
        snap("images-73-pager-first-batch-grid")

        # Move the real Qt viewport near the end of the first batch.
        gallery.grid_scroll.ensureWidgetVisible(gallery._grid_buttons[first_batch[-1].identity])
        QTest.qWait(50)
        assert gallery.grid_scroll.verticalScrollBar().maximum() > 0
        at_edge, _end, positions = grid_visible(loaded=100, loading=True)
        assert at_edge <= 100 and 100 in positions
        snap("images-74-pager-prefetch-boundary-grid")

        # The second batch is appended to the *same mounted listing* and the
        # first 100 stable widgets retain identity. Show rows around 100/101.
        saved_first_button = gallery._grid_buttons[first_batch[0].identity]
        second_batch = multi_window(1, 200, loaded=200, first_visible=93, last_visible=116)
        assert gallery._grid_buttons[first_batch[0].identity] is saved_first_button
        assert second_batch[99].identity in gallery._grid_buttons
        assert second_batch[100].identity in gallery._grid_buttons
        # QGridLayout reflows asynchronously after cards are appended. Force
        # the pending layout to settle before using QScrollArea's widget-based
        # navigation (a stale card.y() is not a reliable scroll coordinate).
        gallery.reflow_timer.stop()
        gallery._layout_cards()
        gallery.cards_layout.activate()
        QTest.qWait(100)

        def show_grid_image(index):
            button = gallery._grid_buttons[second_batch[index - 1].identity]
            gallery.grid_scroll.ensureWidgetVisible(button, 0, 0)
            first, last, visible = grid_visible(loaded=200)
            assert index in visible, (
                f"Image {index} is not visible after ensureWidgetVisible: "
                f"visible={first}-{last}, scroll={gallery.grid_scroll.verticalScrollBar().value()}, "
                f"maximum={gallery.grid_scroll.verticalScrollBar().maximum()}, "
                f"button_y={button.parentWidget().y()}, "
                f"viewport_height={gallery.grid_viewport.height()}"
            )
            return first, last, visible

        _first, _last, visible = show_grid_image(101)
        assert second_batch[99].identity in gallery._grid_buttons
        assert 101 in visible
        snap("images-75-pager-two-batches-grid")

        first, _last, visible = show_grid_image(159)
        assert first >= 101
        assert gallery.pager.buttons[2].isChecked()
        snap("images-76-pager-scrolled-second-batch-grid")
        gallery._widget("galleryFocusButton", QPushButton).click()
        try:
            snap("images-77-pager-two-batches-grid-fullscreen")
        finally:
            gallery._widget("galleryFocusButton", QPushButton).click()

        gallery.show_view("List")
        # The List really contains 200 distinct table rows and can scroll into batch 2.
        gallery.table.scrollToItem(gallery.table.item(125, 2))
        start, _end = list_visible(loaded=200)
        assert start >= 101 and gallery.pager.buttons[2].isChecked()
        assert gallery.table.item(99, 2).text() == "image-0100.png"
        assert gallery.table.item(100, 2).text() == "image-0101.png"
        snap("images-78-pager-two-batches-list")
        gallery._widget("galleryFocusButton", QPushButton).click()
        try:
            snap("images-79-pager-two-batches-list-fullscreen")
        finally:
            gallery._widget("galleryFocusButton", QPushButton).click()

        # Changing size must keep the same saved-output identity on screen,
        # even when Grid changes column count or List changes row height. No
        # programmatic geometry update may initiate a neighboring batch read.
        gallery.show_view("Grid")
        gallery.reflow_timer.stop()
        gallery._layout_cards()
        gallery.cards_layout.activate()
        gallery.grid_scroll.ensureWidgetVisible(gallery._grid_buttons[second_batch[149].identity], 0, 0)
        QTest.qWait(60)
        grid_anchor = gallery._browsing_anchor()
        assert grid_anchor in {item.identity for item in second_batch[100:]}
        initial_ids = [item.identity for item in gallery.items]
        with patch.object(gallery, "_load_adjacent") as fetch:
            gallery.slider.setValue(190)
            QTest.qWait(130)
            assert not gallery.density_anchor_timer.isActive()
            assert [item.identity for item in gallery.items] == initial_ids
            card = gallery._grid_buttons[grid_anchor].parentWidget()
            y = card.mapTo(gallery.grid_viewport, QPoint(0, 0)).y()
            assert y < gallery.grid_viewport.height() and y + card.height() > 0
            gallery._sync_pager()
            assert gallery.pager.buttons[2].isChecked()
            snap("images-89-pager-resized-grid")
            gallery.slider.setValue(65)
            QTest.qWait(130)
            card = gallery._grid_buttons[grid_anchor].parentWidget()
            y = card.mapTo(gallery.grid_viewport, QPoint(0, 0)).y()
            assert y < gallery.grid_viewport.height() and y + card.height() > 0
            assert gallery.pager.buttons[2].isChecked()
            fetch.assert_not_called()

        gallery.show_view("List")
        row_for_anchor = 145
        gallery.table.scrollToItem(
            gallery.table.item(row_for_anchor, 0),
            gallery_ui.QAbstractItemView.ScrollHint.PositionAtTop,
        )
        QTest.qWait(60)
        top_row = gallery.table.rowAt(0)
        assert top_row >= 100
        list_anchor = gallery.items[top_row].identity
        with patch.object(gallery, "_load_adjacent") as fetch:
            gallery.slider.setValue(200)
            QTest.qWait(130)
            actual_row = gallery.table.rowAt(0)
            assert actual_row >= 0
            assert gallery.items[actual_row].identity == list_anchor
            assert gallery.pager.buttons[2].isChecked()
            snap("images-90-pager-resized-list")
            gallery.slider.setValue(100)
            QTest.qWait(130)
            assert gallery.items[gallery.table.rowAt(0)].identity == list_anchor
            fetch.assert_not_called()

        # A five-second background refresh must leave populated cards and the
        # pager's active chip visually unchanged. Reconciliation of identical
        # records must not trigger another full density repaint.
        gallery._adjacent_loading = False
        gallery._loading = True
        gallery._sync_pager(loading=True)
        assert gallery.pager.status.text() == "200 buffered"
        assert gallery.pager.buttons[2].isEnabled()
        with patch.object(gallery, "_density_changed") as repaint_all:
            gallery._render_page()
            repaint_all.assert_not_called()
        snap("images-88-pager-auto-refresh-no-flash")
        gallery._loading = False
        gallery._sync_pager()

        # A programmatic position restore must update the pager but never
        # schedule more gallery reads. Qt valueChanged fires in this case,
        # whereas a user Wheel or scrollbar action is a separate input.
        with patch.object(gallery, "_maybe_load_boundary") as prefetch:
            bar = gallery.table.verticalScrollBar()
            if bar.maximum() > 0:
                bar.setValue(0 if bar.value() else min(3, bar.maximum()))
                QTest.qWait(30)
                prefetch.assert_not_called()
            gallery._scroll_changed("list", bar.value())
            QTest.qWait(30)
            prefetch.assert_not_called()

        # Exercise the real third contiguous 100-image batch and the bounded
        # forward eviction: 1–300 buffered -> fetch 301–400 -> keep 101–400.
        # No provider or SQLite access is involved in this UI-only fixture.
        gallery.show_view("Grid")
        # Real preparation keeps the committed 100-image projection usable
        # until the next 100 cards/cells/filmstrip entries are ready. These
        # synthetic records confer no durable output or provider authority.
        gallery.show_view("Grid")
        multi_window(1, 100)
        retained = gallery._grid_buttons[multi_item(1).identity]
        gallery._adjacent_loading = True
        gallery._pending_refresh = None
        gallery._adjacent_done(
            gallery._query_generation,
            1,
            gallery._query(offset=100),
            gallery._snapshot_at,
            GalleryResults(tuple(multi_item(i) for i in range(101, 201)), 342, 100, gallery_ui.PAGE_SIZE),
            None,
        )
        assert gallery.preparation.active and len(gallery.items) == 100
        assert gallery._grid_buttons[multi_item(1).identity] is retained
        save(form, out / "image-navigation", "images-95-pager-preparing-next-100")
        wait_for(
            lambda: (
                not gallery.preparation.active and not gallery._adjacent_loading and not gallery._suppress_batch_scroll
            )
        )
        assert len(gallery.items) == gallery.table.rowCount() == len(gallery._film_entries) == 200
        assert gallery._grid_buttons[multi_item(1).identity] is retained
        snap("images-96-pager-prepared-200")

        # First-show geometry must already include metadata while pixels wait.
        # Use the production hidden-preparation path, with workers still held.
        pending_cards = [replace(multi_item(i), identity=f"offline-pending-thumb:{i}") for i in range(1, 7)]
        gallery._prepared_entries = {record.identity: gallery._build_prepared_entry(record) for record in pending_cards}
        reserved_heights = {
            identity: entry["button"].parentWidget().height() for identity, entry in gallery._prepared_entries.items()
        }
        gallery.items, gallery.total, gallery.offset = pending_cards, len(pending_cards), 0
        gallery._render_page()
        gallery._rendered_page = (0, tuple(pending_cards))
        wait_for(lambda: gallery._grid_mount_keys is None and not gallery.reflow_timer.isActive())
        gallery.grid_scroll.verticalScrollBar().setValue(0)

        def pending_geometry():
            return {
                record.identity: (
                    gallery._grid_buttons[record.identity].parentWidget().size(),
                    gallery._grid_buttons[record.identity].geometry(),
                    *(label.geometry() for label in gallery._grid_labels[record.identity]),
                )
                for record in pending_cards
            }

        pending_layout = pending_geometry()
        for record in pending_cards:
            button = gallery._grid_buttons[record.identity]
            caption, model = gallery._grid_labels[record.identity]
            assert button.parentWidget().height() == reserved_heights[record.identity]
            assert caption.y() > button.geometry().bottom()
            assert model.y() > caption.geometry().bottom()
            assert model.geometry().bottom() < button.parentWidget().height()
            assert gallery._grid_painters[record.identity].pixmap.isNull()
        snap("images-103-grid-metadata-before-thumbnails")
        assert pending_geometry() == pending_layout
        for index, record in enumerate(pending_cards, 1):
            paint_multi_thumbnail(record, index)
        snap("images-104-grid-metadata-after-thumbnails")
        assert pending_geometry() == pending_layout

        three_batches = multi_window(1, 300, total=1540)
        gallery.grid_scroll.verticalScrollBar().setValue(0)
        assert len(gallery.items) == gallery_ui.MAX_MOUNTED_IMAGES == 300
        gallery.reflow_timer.stop()
        gallery._layout_cards()
        gallery.cards_layout.activate()
        gallery.grid_cards.adjustSize()
        QTest.qWait(80)
        # A middle 100-image zone has both neighboring zones already
        # resident, with no further worker read needed for either direction.
        centered = gallery._grid_buttons[three_batches[149].identity].parentWidget()
        gallery.grid_scroll.verticalScrollBar().setValue(centered.y())
        middle_first, _middle_last, middle_visible = grid_visible(loaded=300)
        assert 150 in middle_visible and 101 <= middle_first <= 200
        assert gallery.pager.buttons[2].isChecked()
        snap("images-101-pager-neighbor-buffer-centered")
        target_card = gallery._grid_buttons[three_batches[249].identity].parentWidget()
        gallery.grid_scroll.verticalScrollBar().setValue(target_card.y())
        first, last, visible = grid_visible(loaded=300)
        preserved_identity = gallery._browsing_anchor()
        intended_first = first
        intended_scroll = gallery.grid_scroll.verticalScrollBar().value()
        assert 250 in visible, (
            f"300-buffer Grid target not visible: {first}–{last}, "
            f"scroll={gallery.grid_scroll.verticalScrollBar().value()}, card_y={target_card.y()}"
        )
        assert gallery.pager.status.text() == "300 buffered"
        snap("images-93-pager-three-batches-buffered-grid")

        gallery._adjacent_loading = True
        gallery._pending_refresh = None
        gallery._adjacent_failed_offset = None
        gallery._adjacent_done(
            gallery._query_generation,
            1,
            gallery._query(offset=300),
            gallery._snapshot_at,
            GalleryResults(
                items=tuple(multi_item(i) for i in range(301, 401)),
                total=1540,
                offset=300,
                limit=gallery_ui.PAGE_SIZE,
            ),
            None,
        )
        wait_for(
            lambda: (
                not gallery.preparation.active and not gallery._adjacent_loading and not gallery._suppress_batch_scroll
            )
        )
        assert len(gallery.items) == gallery_ui.MAX_MOUNTED_IMAGES
        assert gallery.offset == 100
        assert gallery.items[0].identity == multi_item(101).identity
        assert gallery.items[-1].identity == multi_item(400).identity
        assert multi_item(100).identity not in gallery._grid_buttons
        assert all(button.parentWidget().isVisible() for button in gallery._grid_buttons.values())
        assert gallery.pager.status.text() == "300 buffered"
        first, last, visible = grid_visible(loaded=300)
        assert 250 in visible, (
            f"Eviction lost viewport around image 250: {first}–{last}; "
            f"before={intended_first}, target_scroll={intended_scroll}, "
            f"preserved_anchor={preserved_identity}, actual_anchor={gallery._browsing_anchor()}, "
            f"after_scroll={gallery.grid_scroll.verticalScrollBar().value()}, "
            f"anchor_y={gallery._grid_buttons[preserved_identity].parentWidget().y() if preserved_identity in gallery._grid_buttons else -1}, "
            f"viewport_h={gallery.grid_viewport.height()}"
        )
        snap("images-94-pager-eviction-keeps-300-grid")

        # Exercise the actual numbered Qt button: an offline-only callback
        # supplies the target batch without reading a DB or paging through 3/4.
        def fixture_jump(page_number):
            first = (page_number - 1) * gallery_ui.PAGE_SIZE + 1
            last = min(342, first + gallery_ui.PAGE_SIZE - 1)
            multi_window(first, last, first_visible=first, last_visible=min(last, first + 17))
            # Direct jumps should start at the selected batch, not inherit the
            # scroll offset of the previous 200-image listing.
            gallery.table.verticalScrollBar().setValue(0)
            gallery.grid_scroll.verticalScrollBar().setValue(0)
            list_visible(loaded=last - first + 1)

        gallery.pager.on_jump = fixture_jump
        gallery.pager.buttons[4].click()
        assert gallery.offset == 300 and gallery.total == 342 and len(gallery.items) == 42
        assert gallery.pager.buttons[4].isChecked()
        assert gallery.pager.range.text().startswith("301–")
        snap("images-80-pager-direct-jump-last-batch-list")
        gallery.show_view("Grid")
        first, _last, _visible = grid_visible(loaded=42)
        assert first == 301 and gallery.pager.buttons[4].isChecked()
        snap("images-81-pager-direct-jump-last-batch-grid")

        # A larger library forces ellipses and tests that 7+ chip controls
        # still remain inside the pager at a reduced window width.
        multi_window(1201, 1300, total=2000, first_visible=1201, last_visible=1224)
        gallery.grid_scroll.verticalScrollBar().setValue(0)
        first, _last, _visible = grid_visible(loaded=100)
        assert first == 1201 and gallery.pager.buttons[13].isChecked()
        assert len(gallery.pager._gaps) == 2
        # Neighbor buffering is no longer driven by distance to the edge:
        # a resident 100-card zone requests the immediate next batch even
        # when its first image is visible. Actual reads remain synthetic here.
        assert gallery.mode == "Grid"
        assert not gallery._loading and not gallery._adjacent_loading
        with (
            patch.object(gallery, "_load_adjacent") as fetch,
            patch.object(gallery, "_visible_range", return_value=(1201, 1210)),
        ):
            gallery._prefetch_neighbors()
            fetch.assert_called_once_with(1)
        with (
            patch.object(gallery, "_load_adjacent") as fetch,
            patch.object(gallery, "_visible_range", return_value=(1291, 1300)),
        ):
            gallery._prefetch_neighbors()
            fetch.assert_called_once_with(1)
        with patch.object(gallery, "_queue_neighbor_prefetch") as schedule:
            gallery._maybe_load_boundary(-1)
            gallery._maybe_load_boundary(1)
            assert schedule.call_count == 2
        # Input handlers must retain scroll direction when queuing a
        # deferred fetch, rather than rerunning an undirected proximity test.
        with patch.object(gallery, "_maybe_load_boundary") as boundary:
            gallery._request_boundary_prefetch(1, "fixture")
            QTest.qWait(30)
            boundary.assert_called_once_with(1)

        class SyntheticWheel:
            def __init__(self, delta):
                self.delta = delta

            def type(self):
                return QEvent.Type.Wheel

            def angleDelta(self):
                return QPoint(0, self.delta)

        with patch.object(gallery, "_request_boundary_prefetch") as request:
            gallery.eventFilter(gallery.grid_viewport, SyntheticWheel(-120))
            request.assert_called_once_with(1, "wheel")
        with patch.object(gallery, "_request_boundary_prefetch") as request:
            gallery.eventFilter(gallery.grid_viewport, SyntheticWheel(120))
            request.assert_called_once_with(-1, "wheel")
        with patch.object(gallery, "_request_boundary_prefetch") as request:
            gallery._scrollbar_action("grid", gallery_ui.QAbstractSlider.SliderAction.SliderPageStepAdd.value)
            request.assert_called_once_with(1, "scrollbar")
        with patch.object(gallery, "_request_boundary_prefetch") as request:
            gallery._scrollbar_action("grid", gallery_ui.QAbstractSlider.SliderAction.SliderPageStepSub.value)
            request.assert_called_once_with(-1, "scrollbar")
        snap("images-82-pager-many-pages-ellipsis")
        form.resize(800, 680)
        QTest.qWait(130)
        first, _last, _visible = grid_visible(loaded=100)
        assert first == 1201
        snap("images-83-pager-many-pages-narrow")
        form.resize(1600, 1000)
        QTest.qWait(100)
        gallery.pager.on_jump = gallery._jump_to_page

        # Exercise the real bounded-prepend completion. From the top of batch 2,
        # scrolling up reveals image 100 directly above 101, never image 1.
        # These records are synthetic; the worker is closed by hold_worker.
        def prepended_page():
            return GalleryResults(
                items=tuple(multi_item(i) for i in range(1, 101)),
                total=342,
                offset=0,
                limit=gallery_ui.PAGE_SIZE,
            )

        def prepend_from_page_two():
            gallery._adjacent_loading = True
            gallery._pending_refresh = None
            gallery._adjacent_failed_offset = None
            gallery._adjacent_done(
                gallery._query_generation,
                -1,
                gallery._query(offset=0),
                gallery._snapshot_at,
                prepended_page(),
                None,
            )
            wait_for(
                lambda: (
                    not gallery.preparation.active
                    and not gallery._adjacent_loading
                    and not gallery._suppress_batch_scroll
                )
            )
            assert gallery.offset == 0
            assert len(gallery.items) == 200
            assert [record.identity for record in gallery.items[:100]] == [
                multi_item(i).identity for i in range(1, 101)
            ]
            assert [record.identity for record in gallery.items[100:]] == [
                multi_item(i).identity for i in range(101, 201)
            ]
            for i in range(1, 101):
                record = gallery.items[i - 1]
                paint_multi_thumbnail(record, i)

        gallery.show_view("Grid")
        multi_window(101, 200)
        gallery.grid_scroll.verticalScrollBar().setValue(0)
        QTest.qWait(60)
        assert gallery._browsing_anchor() == multi_item(101).identity
        bar = gallery.grid_scroll.verticalScrollBar()
        values = []
        bar.valueChanged.connect(values.append)
        try:
            prepend_from_page_two()
        finally:
            bar.valueChanged.disconnect(values.append)
        first, last, visible = grid_visible(loaded=200)
        prev_card = gallery._grid_buttons[multi_item(100).identity].parentWidget()
        anchor_card = gallery._grid_buttons[multi_item(101).identity].parentWidget()
        assert 100 in visible, (
            f"Upward Grid prepend jumped to {first}–{last}, not image 100; "
            f"scroll={bar.value()}/{bar.maximum()} prev_y={prev_card.y()} "
            f"anchor_y={anchor_card.y()} canvas={gallery.grid_cards.height()} "
            f"viewport={gallery.grid_viewport.height()} columns={gallery._grid_layout_columns} "
            f"scroll_history={values[-30:]}"
        )
        assert first >= 90, f"Upward Grid prepend jumped to first of previous page: {first}"
        assert gallery.pager.buttons[1].isChecked()
        snap("images-91-pager-prepend-previous-last-grid")

        gallery.show_view("List")
        multi_window(101, 200)
        gallery.table.verticalScrollBar().setValue(0)
        QTest.qWait(60)
        assert gallery._browsing_anchor() == multi_item(101).identity
        prepend_from_page_two()
        first, last = list_visible(loaded=200)
        assert first == 100, f"Upward List prepend jumped to image {first}, expected 100"
        assert last >= 101
        assert gallery.table.item(99, 2).text() == "image-0100.png"
        assert gallery.table.item(100, 2).text() == "image-0101.png"
        assert gallery.pager.buttons[1].isChecked()
        snap("images-92-pager-prepend-previous-last-list")

        # Early prefetch is different from an edge hit: retain the anchor
        # instead of pulling the viewport back to the previous batch.
        multi_window(101, 200)
        gallery.table.scrollToItem(
            gallery.table.item(12, 0),
            gallery_ui.QAbstractItemView.ScrollHint.PositionAtTop,
        )
        QTest.qWait(60)
        before = gallery._browsing_anchor()
        assert before == multi_item(113).identity
        prepend_from_page_two()
        assert gallery._browsing_anchor() == before
        assert gallery.pager.buttons[2].isChecked()
        snap("images-99-pager-list-prefetch-keeps-anchor")

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
        present(replace(item, file_size=123_456_789))
        snap("images-100-detail-thumbnail-badges")
        gallery.slider.setValue(25)
        QTest.qWait(130)
        assert gallery._film_entries[item.identity][0].width() == 88
        snap("images-101-detail-thumbnail-badges-small")
        gallery.slider.setValue(100)
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

        # Resolve the real launch defaults rather than duplicating their values
        # in the fixture. Keep filenames stable; changing defaults needs review.
        default_batch, default_cards = gallery_limits.resolve(None, None)
        assert (default_batch, default_cards) == (20, 100), "Update the default preview names and guide together."
        with (
            patch.object(gallery_ui, "PAGE_SIZE", default_batch),
            patch.object(gallery_ui, "MAX_MOUNTED_IMAGES", default_cards),
        ):
            gallery.show_view("Grid")
            multi_window(1, default_batch)
            gallery.grid_scroll.verticalScrollBar().setValue(0)
            assert gallery._query(offset=0).limit == default_batch
            assert gallery.pager.status.text() == "20 buffered"
            assert gallery.pager.buttons[2].toolTip() == "Jump to images 21–40"
            snap("images-105-pager-default-20-first-batch")
            multi_window(1, default_cards)
            gallery.grid_scroll.verticalScrollBar().setValue(0)
            assert len(gallery.items) == gallery_ui.MAX_MOUNTED_IMAGES == default_cards
            assert gallery.pager.status.text() == "100 buffered"
            snap("images-106-pager-default-100-buffered")


def render_removal_and_selection_states(shell, item, plan, out):
    """Images consent, notices and result rows; every removal/query worker is bypassed."""
    recycle, gallery, strip = shell.image_recycle, shell.gallery, activity_strip(shell.form)
    # These states drive synthetic callbacks explicitly. A real five-second
    # poll must not overwrite that projection halfway through a slow capture.
    # Live/snapshot auto-refresh behavior is exercised before this catalogue.
    gallery.auto_button.setChecked(False)
    assert not gallery.auto_timer.isActive()

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
            wait_for(lambda: not shell.gallery.surface_painter.pixmap.isNull())
            QTest.qWait(150)
            save(shell.form, out, "images-102-buffered-detail-decoded")
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
    page.newQueueButton.click()
    page.planModeCombo.setCurrentText("Cross")
    page.planStepsEdit.setText("20; 30")
    queue.preview()
    capture("queues-03-composer-preview")
    page.planStepsEdit.clear()
    page.planModeCombo.setCurrentText("Repeat")
    queue.close_composer()
    # Cancel posts immediately without confirmation (#120); removing a queue record asks inline first (#122).
    page.removeQueueButton.click()
    capture("queues-05-remove-confirmation")
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
    page.removeQueueButton.click()
    page.removeQueueButton.click()
    wait_for(lambda: not queue._views)
    render_queue_popover_states(shell, capture)


def render_queue_popover_states(shell, capture):
    """The one-click Queue popover (#135) on Generate, Images and Queues; Add to queue is shown while a queue runs."""
    queue, page, popover = shell.queue, shell.queue_page, shell.queue_popover
    header = shell.shell.quickQueueButton
    shell.form.generationNavigationButton.click()
    trigger = shell.generation_page.queueGenerateButton
    popover.repeat_spin.setValue(4)
    assert trigger.toolTip().startswith("Queue ×4:"), trigger.toolTip()
    popover.open_at(trigger)
    assert popover.start_button.isEnabled() and not popover.add_button.isEnabled()
    assert not popover.plan_option.isEnabled()  # the composer is in Repeat mode: no plan to reuse
    capture("queues-45-popover-generate", (popover, trigger))
    popover.hide()
    # The composer's plan becomes a choice; the trigger's tooltip follows it.
    page.planModeCombo.setCurrentText("Cross")
    page.planStepsEdit.setText("20; 30")
    page.planSeedsEdit.setText("1; 2")
    shell.form.imagesNavigationButton.click()
    QTest.qWait(150)
    popover.open_at(header)
    assert popover.plan_option.isEnabled()
    popover.plan_option.setChecked(True)
    assert trigger.toolTip().startswith("Queue ×4:") and popover.label() == "Queue ×4", popover.label()
    capture("queues-46-popover-images-composer-plan", (popover, header))
    popover.hide()
    page.planStepsEdit.setText("20; 30; 40")  # edited while the popover is closed: the count stays true
    assert trigger.toolTip().startswith("Queue ×6:"), trigger.toolTip()
    page.planStepsEdit.setText("20; 30")
    shell.form.queueNavigationButton.click()
    with patch.object(type(queue), "dispatching_here", new_callable=PropertyMock, return_value=True):
        popover.open_at(header)
        assert popover.add_button.isEnabled() and not popover.start_button.isEnabled()
        capture("queues-47-popover-queues-while-running", (popover, header))
        popover.hide()
    with patch.object(type(queue), "_live_run", return_value=True):
        popover.open_at(header)  # another process dispatches: the new queue waits behind it
        assert popover.add_button.isEnabled() and not popover.start_button.isEnabled()
        popover.hide()
    with patch.object(type(shell.activity), "generating", new_callable=PropertyMock, return_value=True):
        popover.open_at(header)
        assert popover.add_button.isEnabled() and not popover.start_button.isEnabled()
        popover.hide()
    popover.repeat_option.setChecked(True)
    popover.repeat_spin.setValue(1)
    for name in ("planStepsEdit", "planSeedsEdit"):
        getattr(page, name).clear()
    page.planModeCombo.setCurrentText("Repeat")


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
    mailbox = DiagnosticNotice()
    mailbox.post("Offline fixture: diagnostic evidence is incomplete; logging failed or records were rejected.")
    receiver = DiagnosticNoticeReceiver(shell, mailbox)
    receiver.deliver()
    capture("shell-21-diagnostic-warning")
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
    wait_for(lambda: queue._view() is not None and page.removeQueueButton.isEnabled())
    page.removeQueueButton.click()  # arms the Remove confirmation
    assert page.removeQueueButton.property("confirmationPending")
    InlineNotice.warning(shell.form, "Original images unavailable", "Offline fixture notice during a confirmation.")
    assert page.removeQueueButton.property("confirmationPending")
    capture("shell-09-overlay-notice-during-confirmation")
    page.removeQueueButton.click()  # confirms, leaving no queue for later renders
    wait_for(lambda: not queue._views)
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
        if key == "inpaint-edit-fixture":
            # #133: while an edit runs, the editor stays usable; only another Edit image waits.
            shell.generation._generating = True
            shell.generation._sync_generate()
            assert editor.canvas.isEnabled() and editor.form.chooseSourceButton.isEnabled()
            assert page.generationModeSwitch.isEnabled() and not page.generateButton.isEnabled()
        capture(name)
        if key == "inpaint-edit-fixture":
            shell.generation._generating = False
            shell.generation._sync_generate()
        strip.finish(key)


def render_logs_states(shell, capture):
    """Real Qt Logs presentations, including bounded paging and Follow inserts."""
    from datetime import datetime

    from dtflow._diagnostic_reader import RecordRef
    from dtflow.ui.logs_workspace import row_preview, session_label

    shell._toggle_logs()
    shell.logs.stop()  # no background file reader/polling in offline screenshots
    # Earlier states leave timed outcome rows; clear them so they do not cover the Logs footer.
    for key in tuple(shell.progress_strip.rows):
        shell.progress_strip.finish(key)
    panel = shell.logs_form
    from dtflow.ui.logs_workspace import EMPTY_SESSION, ICON_GAP, NO_MATCHES

    shell.logs.model.replace([])
    shell.logs._set_empty(EMPTY_SESSION)
    panel.logsCounter.setText("0 records · pending")
    capture("logs-01-empty")
    shell.logs._set_empty(NO_MATCHES)
    panel.logsCounter.setText("0 of 5 matching records · complete")
    capture("logs-08-no-matches")
    shell.logs._set_empty("")

    def record(seq, level, event, component):
        return {
            "v": 1,
            "seq": seq,
            "ts": f"2026-10-19T09:12:{seq % 60:02d}.000Z",
            "level": level,
            "component": component,
            "event": event,
        }

    rows = [
        (record(1, "INFO", "app.window_ready", "app"), RecordRef("fixture", 1, 0, 0, 0, 0, "fixture.jsonl")),
        (record(2, "DEBUG", "gallery.gui_gap", "gallery"), RecordRef("fixture", 2, 0, 0, 0, 0, "fixture.jsonl")),
        (record(3, "WARNING", "diagnostics.loss", "diagnostics"), RecordRef("fixture", 3, 0, 0, 0, 0, "fixture.jsonl")),
        (record(4, "ERROR", "generation.failed", "generation"), RecordRef("fixture", 4, 0, 0, 0, 0, "fixture.jsonl")),
        (record(5, "INFO", "logging.message", "backend"), RecordRef("fixture", 5, 0, 0, 0, 0, "fixture.jsonl")),
    ]
    rows[1][0]["data"] = {"elapsed_ms": 117.4, "sql_cpu_ms": 0.4, "max_ms": 118.6, "mounted": 100, "mode": "grid"}
    shell.logs.model.replace(
        rows,
        [
            ((("state", "ready"),), 0),
            row_preview(rows[1][0]),
            ((("reason", "queue_bytes"), ("count", "1")), 0),
            ((("category", "backend"), ("retryable", "false")), 0),
            ((), 0, "Generic library message without characteristics (fixture)"),
        ],
    )
    panel.logsCounter.setText("5 of 5 matching records · complete · page 1 · showing 5")
    panel.logsSessionCombo.blockSignals(True)
    panel.logsSessionCombo.clear()
    for number, sid, written, current in (
        (1, "3f2a9c1e-fixture", datetime(2026, 10, 19, 9, 12), True),
        (2, "81bd04aa-fixture", datetime(2026, 10, 18, 22, 40), False),
    ):
        panel.logsSessionCombo.addItem(session_label(number, sid, written, current=current), sid)
    panel.logsSessionCombo.blockSignals(False)
    capture("logs-02-mixed-severity")

    # Exercise the shared projection: integer timing chips beside exact raw JSON.
    shell.logs._show_detail(rows[1][0], None)
    capture("logs-07-whole-milliseconds")
    panel.logsDetail.hide()

    paging_rows = [
        (record(seq, "DEBUG", "gallery.query_done", "gallery"), RecordRef("fixture", seq, 0, 0, 0, 0, "fixture.jsonl"))
        for seq in range(501, 1001)
    ]
    shell.logs.model.replace(paging_rows, [((("returned", "20"),), 0)] * len(paging_rows))
    panel.logsPreviousButton.setEnabled(True)
    panel.logsNextButton.setEnabled(True)
    panel.logsCounter.setText("1204 of 1204 matching records · pending · page 2 · showing 500")
    shell.logs._set_pending(True, 3, detail="3 new records match the current filters")
    panel.logsFollowButton.blockSignals(True)
    panel.logsFollowButton.setChecked(False)
    panel.logsFollowButton.setText(ICON_GAP + "Follow: Off")
    panel.logsFollowButton.blockSignals(False)
    panel.logsOperationStatus.setText(
        "Logs exported: diagnostic-excerpt.csv · 1204 records · source pending (synthetic presentation)"
    )
    capture("logs-05-paging")

    shell.logs._set_pending(True, 2, incomplete=True, detail="Synthetic incomplete-evidence count")
    capture("logs-09-refresh-count-incomplete")
    shell.logs._set_pending(True, 0, incomplete=True, detail="No matching arrivals; source evidence incomplete")
    capture("logs-14-zero-arrivals-incomplete")

    shell.logs._set_pending(False)
    # Component sort from the left target of the split COMPONENT . EVENT header.
    by_component = sorted(rows, key=lambda row: (row[0]["component"], row[0]["seq"]))
    shell.logs.model.replace(by_component, [((("state", "fixture"),), 0)] * len(rows))
    panel.logsOperationStatus.clear()
    shell.logs.model.set_sort_state("component", False)
    panel.logsSortNote.setText("Component · A → Z")
    panel.logsCounter.setText("5 of 5 matching records · complete · page 1 · showing 5")
    panel.logsPreviousButton.setEnabled(False)
    panel.logsNextButton.setEnabled(False)
    capture("logs-10-component-sort")
    # One spinner frame of the Refresh icon during a page read; no reader runs offline.
    shell.logs.active, shell.logs._query_busy, shell.logs._spin_angle = True, True, 15
    shell.logs._spin()
    capture("logs-11-refreshing")
    # Export of the live session: progress with Cancel, then a disclosed incomplete-source outcome.
    strip = shell.progress_strip
    strip.start("logs-export", "Exporting diagnostic-excerpt.jsonl", lambda: None, stop_text="Cancel export")
    strip.update("logs-export", 640, 1204, detail="640 of 1204 records written (synthetic presentation)")
    capture("logs-12-export-progress")
    strip.complete(
        "logs-export",
        "Logs exported",
        "diagnostic-excerpt.jsonl · 1204 records · source incomplete (seal_missing) (synthetic presentation)",
        "warning",
    )
    capture("logs-13-export-incomplete-source")
    strip.finish("logs-export")
    shell.logs.active, shell.logs._query_busy = False, False
    shell.logs._spin()
    shell.logs.model.set_sort_state("ts", True)
    panel.logsSortNote.setText("Datetime · newest first")
    panel.logsTable.selectRow(1)
    added = (record(6, "INFO", "gallery.query_done", "gallery"), RecordRef("fixture", 6, 0, 0, 0, 0, "fixture.jsonl"))
    previews = list(shell.logs.model.previews)
    shell.logs.model.reconcile([added, *rows], [((("returned", "20"),), 0), *previews])
    panel.logsPreviousButton.setEnabled(False)
    panel.logsNextButton.setEnabled(False)
    panel.logsCounter.setText("6 of 6 matching records · pending · page 1 · showing 6")
    capture("logs-06-follow-selection")
    panel.logsOperationStatus.clear()

    shell.logs._show_detail(
        {
            "v": 1,
            "seq": 4,
            "ts": "2026-10-19T09:12:04.000Z",
            "level": "ERROR",
            "component": "generation",
            "event": "generation.failed",
            "msg": "Backend rejected the request (fixture)",
            "data": {"category": "backend", "retryable": False},
        },
        None,
    )
    capture("logs-03-details")

    panel.logsDetail.hide()
    from dtflow.ui.logs_workspace import INCOMPLETE_NOTICE

    panel.logsNotice.setText(INCOMPLETE_NOTICE.format(reasons="missing_segment"))
    panel.logsNotice.show()
    capture("logs-04-reader-error")
    panel.logsNotice.hide()
    shell._close_logs()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tmp" / "ui-renders", help="Screenshot directory")
    parser.add_argument(
        "--category",
        choices=RENDER_CATEGORIES,
        default="all",
        help="Render only one UI category (default: all). Images includes Image Navigation.",
    )
    args = parser.parse_args()
    global SELECTED_CATEGORY
    SELECTED_CATEGORY = args.category
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
        if any(wants(category) for category in ("connection", "generate", "queues", "images")):
            render_workspace_states(app, state, args.out)
        if wants("images"):
            render_mapped_gallery_states(app, state, args.out)
        if wants("images"):
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
        if wants("images"):
            render_initial_refresh_recovery(app, state, args.out)
        shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=state)
        shell.form.resize(1600, 1000)
        shell.show()
        shell.form.backendCombo.setCurrentText("fake")
        shell.form.generationNavigationButton.click()
        page, controller = shell.generation_page, shell.generation

        def capture(name, popup=None):
            """``popup`` is (widget, anchor): a separate popup window drawn under its anchor, as the user sees it."""
            category = name.split("-", 1)[0]
            if not wants(category):
                return
            app.processEvents()
            preview = controller.inpaint.canvas if controller.inpaint.active else page.resultPreviewLabel
            preview._show_controls()
            QTest.qWait(30)
            target = args.out / f"{name}.png"
            image = shell.form.grab()
            if popup is not None:
                widget, anchor = popup
                at = widget.placement(anchor)
                painter = QPainter(image)
                painter.drawPixmap(at, widget.grab())
                painter.end()
            if not image.save(str(target)):
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
            # #133: only another Generate waits; the form and the LoRA panel stay editable for the next request.
            assert page.promptEdit.isEnabled() and page.addLoraButton.isEnabled()
            assert page.refreshInventoryButton.isEnabled()
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
            assert page.generateButton.isEnabled()  # #125: Generate queues behind the running queue
            capture("generate-17-queue-dispatch-running")
            behind = []
            controller.queue_behind = lambda *args: behind.append(args)
            page.generateButton.click()
            assert behind and "Queuing behind" in page.generationStatusLabel.text()
            capture("generate-42-queued-behind")
            controller.queue_behind = shell.queue.queue_behind
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
            if wants("queues"):
                render_queue_states(shell, capture)
            if wants("shell"):
                render_shell_states(shell, capture)
            if wants("inpaint"):
                render_inpaint_states(shell, state, capture)
            if wants("logs"):
                render_logs_states(shell, capture)
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

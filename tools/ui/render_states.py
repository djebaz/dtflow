"""Render real Qt widgets against offline fixtures. Never admits provider network traffic.

Run with the locked dev environment: uv run python tools/ui/render_states.py --out tmp/ui-renders
The calibration grid is a fixture, not a generated photograph. Cloud receipts use the
same in-memory API/transport as the asset tests; no real file upload occurs.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from support.network_guard import install  # noqa: E402

install()  # before provider/client imports; the guard rejects unregistered DNS/sockets
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32" and os.environ["QT_QPA_PLATFORM"] == "offscreen":
    # Offscreen Qt on Windows otherwise searches PySide6's empty font folder.
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QScrollArea  # noqa: E402
from support.cloud_assets import DtPlus, lora_file  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.core.gallery import GalleryPage as GalleryResults  # noqa: E402
from dtflow.core.image_operations import ImageOperationSupport  # noqa: E402
from dtflow.core.models import LoRASelection  # noqa: E402
from dtflow.persistence.original_io import OriginalImagesBusyError  # noqa: E402
from dtflow.persistence.person_models import ModelRef, PersonModels  # noqa: E402
from dtflow.persistence.store import PersistenceError, StateBusyError  # noqa: E402
from dtflow.services import artifact_paths, session  # noqa: E402
from dtflow.services.artifact_paths import ArtifactReadPaths  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.services.gallery import MISSING_ISSUE, item_from_record, load_detail  # noqa: E402
from dtflow.services.image_recycle import RecyclePlan, RecycleResult, RecycleTarget, RecycleUnavailable  # noqa: E402
from dtflow.services.person_selection import PersonAnalysis, PersonLayers, colored_preview  # noqa: E402
from dtflow.ui import desktop_open, system_trash  # noqa: E402
from dtflow.ui import gallery_page as gallery_ui  # noqa: E402
from dtflow.ui.activity_strip import activity_strip  # noqa: E402
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
        snap("63-images-trash-confirmation")
        shell.inline.dismiss()
        recycle._confirmation_revision = gallery.recycle_scope_revision
        recycle._prepared(RecyclePlan((), 1, 2, 0, 3, "trash"), None)
        snap("64-images-nothing-to-remove")
        shell.inline.dismiss()
    # Busy source: request and the execution revalidation both refuse before any worker.
    with patch.object(recycle, "busy_source", return_value=True):
        recycle.request((item.identity,), 0, gallery.export_query())
        snap("65-images-removal-busy")
        shell.inline.dismiss()
        recycle._execute(plan)
        snap("66-images-removal-changed-or-busy")
        shell.inline.dismiss()
    recycle._mode_loaded("trash", OSError("Offline fixture: removal preferences are unreadable."))
    snap("67-images-removal-preferences-unavailable")
    shell.inline.dismiss()
    gallery.set_recycle_busy(True)
    snap("68-images-removal-control-busy")
    gallery.set_recycle_busy(False)

    # Presentation fixture of the running row; recycle_outputs is never called.
    strip.start("trash", f"Moving originals to {TRASH_NAME}", lambda: None, total=3)
    strip.update("trash", 1, 3)
    snap("69-images-trash-progress")
    recycle._action = "trash"
    with patch.object(gallery, "recycle_completed"):
        for name, result in (
            ("70-images-trash-result-success", RecycleResult((item.identity,), (), (), 0, 0, 0, False)),
            (
                "71-images-trash-result-partial",
                RecycleResult((), ("fixture:1",), (), 0, 1, 0, False, (("fixture:1", "source_changed"),)),
            ),
            ("72-images-trash-result-stopped", RecycleResult((item.identity,), (), (), 0, 0, 2, True)),
        ):
            recycle._completed(result, None)
            snap(name)
    for name, phase, processed, error in (
        ("73-images-removal-preflight-refused", "preflight", 0, RecycleUnavailable("Offline fixture: Trash refused.")),
        ("74-images-removal-not-started", "executing", 0, OriginalImagesBusyError("Offline fixture: originals busy.")),
        ("75-images-removal-needs-review", "executing", 1, RuntimeError("Offline fixture")),
    ):
        recycle._phase, recycle._processed_count = phase, processed
        recycle._finish_with_error(error)
        snap(name)
    strip.finish("trash")

    # Selection scan settlements, delivered directly; no identity query runs.
    gallery._cancel_selection_scan()
    snap("76-images-selection-stopped")
    gallery._selection_scan_done(gallery._selection_generation, "add", None, PersistenceError("Offline fixture"))
    snap("77-images-selection-failed")
    for name, omitted in (("78-images-selection-limit-partial", 3), ("79-images-selection-all-checked-hidden", 0)):
        # Same-filter settlement; one off-filter check shows the hidden count.
        gallery._selection_query = gallery.export_query()
        gallery.selection.checked.add("fixture:hidden")
        gallery._selection_scan_done(gallery._selection_generation, "add", ((), 1, omitted, (item.identity,)), None)
        snap(name)
    strip.finish("selection")
    assert gallery.select_filtered_button.text() == "Unselect all"
    assert not gallery.hidden_count_label.isHidden()

    # Compact toolbar and its secondary-action menus.
    shell.form.resize(1180, 1000)
    QTest.qWait(150)
    assert not gallery.more_button.isHidden(), gallery.selection_bar.width()
    snap("80-images-compact-toolbar")
    for button, name in ((gallery.more_button, "81-images-more-menu"),):
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
    save(menu, out, "82-images-unselect-menu")
    menu.hide()
    gallery.show_view("Detail")
    gallery.select(item.identity)
    snap("83-images-detail-checked")
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
            target = out / "44-images-refresh-failed.png"
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
            target = out / "45-images-refresh-recovered-empty.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            shell.gallery.show_view("Detail")
            QTest.qWait(100)
            save(shell.form, out, "84-images-detail-no-selection")
            shell.gallery.show_view("Grid")
            # Offline failure presentation only; no real desktop process is started.
            with patch.object(desktop_open, "_open_on_worker", return_value="the folder no longer exists."):
                shell.image_export._exported_folder = state / "missing-export-folder-fixture"
                shell.image_export.open_folder()
                wait_for(lambda: "export-open" in activity_strip(shell.form).rows)
            target = out / "52-export-folder-missing.png"
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
            target = out / "54-external-viewer-unavailable.png"
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
            for mode, name in (("Grid", "31-mapped-grid-toggle-font"), ("List", "32-mapped-list-toggle-font")):
                shell.gallery.show_view(mode)
                QTest.qWait(150)
                target = out / f"{name}.png"
                if not shell.form.grab().save(str(target)):
                    raise RuntimeError(f"Could not save {target.name}")
                report(target)
            shell.gallery.select(item.identity)
            QTest.qWait(150)
            # Display only: mapped Edit eligibility never reads the fixture or calls a provider.
            target = out / "34-mapped-edit-eligible.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            target = out / "33-mapped-detail-metadata.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            shell.gallery._show_refresh_failure(PersistenceError("Offline refresh refusal fixture."), first=False)
            shell.gallery._refresh_failing = True
            shell.gallery.refresh()
            wait_for(lambda: not shell.gallery._loading)
            QTest.qWait(100)
            target = out / "46-images-refresh-recovered-populated.png"
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
                for view, name in (("Grid", "47-images-snapshot-grid"), ("Detail", "48-images-snapshot-detail")):
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
                target = out / "50-images-live-recovery-stabilizing.png"
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
                target = out / "49-images-snapshot-warning-dismissed.png"
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
            target = out / "51-images-manual-live-recovery.png"
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
            target = out / "39-images-trash-mode.png"
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
            target = out / "37-images-removal-menu-trash-selected.png"
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
            target = out / "38-images-removal-menu-permanent-selected.png"
            if not menu.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            menu.hide()
            target = out / "34-images-permanent-mode.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            # Show the disabled icon beside the same disabled text shade.
            shell.gallery._set_checked(item.identity, False)
            assert not shell.gallery.delete_button.isEnabled()
            assert shell.gallery.delete_button.text() == "Delete"
            target = out / "40-images-delete-disabled.png"
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
                target = out / "35-images-permanent-confirmation.png"
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
            target = out / "39-images-original-unavailable-thumbnail.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            # Confirmed-missing original: file-missing pictogram in Grid and List, distinct from unavailable.
            shell.gallery._show_unavailable(item.identity, MISSING_ISSUE)
            for view, name in (("Grid", "42-images-file-missing-grid"), ("List", "43-images-file-missing-list")):
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
            target = out / "41-images-read-alias-unavailable.png"
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
    capture("85-queues-created-paused")
    page.planBox.setChecked(True)
    page.planDryRunButton.click()
    capture("86-queues-plan-dry-run")
    page.planBox.setChecked(False)
    for button, name in (
        (page.cancelQueueButton, "87-queues-cancel-confirmation"),
        (page.clearQueuesButton, "88-queues-clear-confirmation"),
    ):
        button.click()
        capture(name)
        shell.inline.dismiss()
    view = queue._view()
    counts = dict.fromkeys(view["summary"], 0)
    strip = activity_strip(page)
    for name, status, summary, failure in (
        ("89-queues-result-finished", "finished", {"succeeded": 3}, None),
        ("90-queues-result-partial", "finished", {"succeeded": 1, "failed": 1, "unknown": 1}, None),
        ("91-queues-result-cancelled", "cancelled", {"succeeded": 1, "cancelled": 2}, None),
        ("92-queues-dispatch-owned-elsewhere", None, None, StateBusyError("Offline fixture: state is owned.")),
        ("93-queues-dispatch-error", None, None, RuntimeError("Offline fixture")),
    ):
        final = None if failure else dict(view, status=status, summary={**counts, **summary})
        queue._dispatched(final, failure)
        capture(name)
        strip.finish("queue-result")
    # Leave no queue behind for the later Generate/Inpaint renders.
    page.clearQueuesButton.click()
    page.clearQueuesButton.click()
    wait_for(lambda: queue._view() is None)


def render_inpaint_states(shell, state, capture):
    """Listed editor states only; analysis/dispatch rows are presentation fixtures."""
    page, editor = shell.generation_page, shell.generation.inpaint
    shell.form.generationNavigationButton.click()
    wait_for(lambda: page.inpaintModeButton.isEnabled())
    shell.inline.dismiss()
    page.inpaintModeButton.click()
    capture("15-inpaint-empty")

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
    capture("16-inpaint-source-loaded")

    width, height = editor._source.width, editor._source.height
    mask = bytes(
        255 if width // 3 <= x < 2 * width // 3 and height // 4 <= y < 3 * height // 4 else 0
        for y in range(height)
        for x in range(width)
    )
    editor.canvas.replace_mask(mask)
    wait_for(lambda: editor._coverage > 0)
    capture("17-inpaint-mask-painted")
    for name, view in (
        ("18-inpaint-source-view", "Source"),
        ("19-inpaint-mask-view", "Mask"),
        ("20-inpaint-result-no-result", "Result"),
    ):
        # No result exists yet, so Result still shows the source: nothing is fabricated or dispatched.
        editor.canvas.set_view(view)
        capture(name)

    editor.canvas.set_view("Source")
    editor.form.clearMaskButton.click()
    capture("21-inpaint-clear-confirmation")
    shell.inline.dismiss()  # Keep the fixture mask.

    # Presentation fixtures: a probe-status line, the consent step, and a calibration image as the "result".
    editor._support = ImageOperationSupport("owner_probe", "Offline presentation fixture.")
    editor._render_support()
    capture("24-inpaint-capability-probe")
    editor.canvas.set_result(QPixmap.fromImage(source.flipped(Qt.Orientation.Horizontal)))
    editor.canvas.set_view("Result")
    capture("26-inpaint-result-fixture")
    editor.canvas.set_view("Source")

    # Model badges use fake file references only; no ONNX file is read or loaded.
    editor.form.selectModeButton.setChecked(True)
    capture("27-inpaint-select-no-models")
    fake = ModelRef("offline-only.onnx", "0" * 64)
    for name, models, issues, checking, check_error in (
        ("55-inpaint-model-readiness-checking", PersonModels(fake), {}, True, None),
        ("56-inpaint-one-model", PersonModels(fake), {"segmentation": None}, False, None),
        ("57-inpaint-two-models", PersonModels(fake, fake), {"segmentation": None, "clothes": None}, False, None),
        (
            "58-inpaint-three-models",
            PersonModels(fake, fake, fake),
            {"segmentation": None, "clothes": None, "faces": None},
            False,
            None,
        ),
        (
            "53-inpaint-model-unreadable",
            PersonModels(fake, fake),
            {
                "segmentation": None,
                "clothes": "The saved clothes model cannot be read (permission denied); choose it again with Models….",
            },
            False,
            None,
        ),
        ("59-inpaint-model-unknown", PersonModels(fake), {}, False, "Availability could not be checked."),
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
    capture("28-inpaint-select-people-found")
    person = people.people[0]
    editor.canvas.set_hover_colors(
        person.box, colored_preview(person, frozenset({"person", "upper clothes", "pants"}), "pants")
    )
    capture("29-inpaint-select-hover-preview")
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
            "22-inpaint-find-people-overlay",
        ),
        (
            "inpaint-edit-fixture",
            "Edit image",
            "Stop waiting locally",
            4,
            12,
            "OFFLINE PRESENTATION FIXTURE · sampling step 4 of 12; no generation submitted",
            "23-inpaint-edit-image-overlay",
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
            target = args.out / "30-images-trash-unavailable.png"
            if not unavailable.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            report(target)
            target = args.out / "36-images-removal-mode-menu.png"
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
            capture("01-empty")
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
            capture("02-portrait")
            QTest.mouseMove(page.generateButton, QPoint(20, 15))
            capture("03-hover")
            page.generateButton.setDown(True)
            capture("04-pressed")
            page.generateButton.setDown(False)
            page.resultPreviewLabel.zoom(2)
            capture("05-zoom")
            page.widthSpinBox.setValue(1024)
            page.heightSpinBox.setValue(576)
            page.generateButton.click()
            wait_for(settled, timeout=60)
            capture("06-landscape")
            shell.form.resize(800, 680)
            capture("07-narrow")
            shell.form.resize(1600, 1000)
            page.promptEdit.clear()
            page.seedEdit.setText("invalid")
            page.generateButton.click()
            page.seedEdit.setFocus()
            capture("08-invalid-focus")
            page.promptEdit.setPlainText("Offline fixture")
            page.seedEdit.setText("0")
            controller.lora.set_loras((LoRASelection("fake-mismatch", 0.7), LoRASelection("fake-unknown", 0.5)))
            page.checkLorasButton.click()
            wait_for(settled, timeout=60)
            capture("09-incompatible")
            controller.lora.set_loras(())
            gate = asyncio.Event()
            page.generateButton.click()
            wait_for(lambda: not page.generateButton.isEnabled())
            capture("10-busy-disabled")
            shell.runner._loop.call_soon_threadsafe(gate.set)
            wait_for(settled, timeout=60)
            gate, scenario = None, "lost"
            page.generateButton.click()
            wait_for(settled, timeout=60)
            capture("11-outcome-unknown")

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
            capture("12-assets-details")
            shell.form.backendCombo.setCurrentText("fake")
            for name, tab in (("13-queues", "queueNavigationButton"), ("14-images", "imagesNavigationButton")):
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
            capture("60-images-detail-additional")
            assert gallery.regenerate_button.isEnabled()
            # Regenerate lives at the end of Additional details; show the armed Confirm in place.
            gallery._widget("galleryAdditionalScroll", QScrollArea).ensureWidgetVisible(gallery.regenerate_button)
            gallery.regenerate_button.click()
            QTest.qWait(150)
            capture("61-images-regenerate-confirmation")
            scenario = "success"
            gallery.regenerate_button.click()
            assert page.resultList.count() == 0  # submitted: Generate cleared its previous result
            wait_for(lambda: page.resultList.count() > 0 and settled())
            capture("62-images-regenerate-confirmed")
            render_queue_states(shell, capture)
            render_inpaint_states(shell, state, capture)
        finally:
            shell.runner.shutdown()
            shell.form.close()
            app.processEvents()
    summarize()


if __name__ == "__main__":
    main()

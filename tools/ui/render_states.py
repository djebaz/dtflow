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

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from support.cloud_assets import DtPlus, lora_file  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.core.gallery import GalleryPage as GalleryResults  # noqa: E402
from dtflow.core.image_operations import ImageOperationSupport  # noqa: E402
from dtflow.core.models import LoRASelection  # noqa: E402
from dtflow.services import artifact_paths, session  # noqa: E402
from dtflow.services.artifact_paths import ArtifactReadPaths  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.services.gallery import item_from_record, load_detail  # noqa: E402
from dtflow.services.image_recycle import RecyclePlan, RecycleTarget  # noqa: E402
from dtflow.services.person_selection import PersonAnalysis, PersonLayers, colored_preview  # noqa: E402
from dtflow.ui import gallery_page as gallery_ui  # noqa: E402
from dtflow.ui import system_trash  # noqa: E402
from dtflow.ui.activity_strip import activity_strip  # noqa: E402
from dtflow.ui.main_window import MainWindow  # noqa: E402
from dtflow.ui.theme import apply_theme  # noqa: E402


def wait_for(predicate, timeout=8):
    until = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= until:
            raise RuntimeError("Offline UI fixture timed out.")
        QTest.qWait(10)
    QTest.qWait(30)


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
                print(target)
            shell.gallery.select(item.identity)
            QTest.qWait(150)
            target = out / "33-mapped-detail-metadata.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            print(target)
            # Presentation only: synthetic scope, with execution explicitly blocked.
            wait_for(lambda: shell.gallery.removal_ready)
            shell.gallery._set_checked(item.identity, True)
            # Offline two-choice popups: selecting a mode never executes removal.
            menu = shell.image_recycle.menu_button.menu()
            shell.image_recycle._apply_mode("trash")
            menu.popup(shell.image_recycle.menu_button.mapToGlobal(QPoint(0, shell.image_recycle.menu_button.height())))
            QTest.qWait(100)
            target = out / "37-images-removal-menu-trash-selected.png"
            if not menu.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            print(target)
            menu.hide()
            shell.image_recycle._apply_mode("permanent")
            menu.popup(shell.image_recycle.menu_button.mapToGlobal(QPoint(0, shell.image_recycle.menu_button.height())))
            QTest.qWait(100)
            target = out / "38-images-removal-menu-permanent-selected.png"
            if not menu.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            print(target)
            menu.hide()
            target = out / "34-images-permanent-mode.png"
            if not shell.form.grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            print(target)
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
                print(target)
                shell.inline.dismiss()
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
            print(target)
        finally:
            shell.runner.shutdown()
            shell.form.close()
            app.processEvents()


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
    editor.canvas.set_result(QPixmap.fromImage(source.mirrored(True, False)))
    editor.canvas.set_view("Result")
    capture("26-inpaint-result-fixture")
    editor.canvas.set_view("Source")

    # Select mode: offline fixtures only (a synthetic silhouette; no ONNX model is loaded).
    editor.form.selectModeButton.setChecked(True)
    capture("27-inpaint-select-no-models")
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
            print(target)
            target = args.out / "36-images-removal-mode-menu.png"
            if not unavailable.image_recycle.menu_button.menu().grab().save(str(target)):
                raise RuntimeError(f"Could not save {target.name}")
            print(target)
            unavailable.image_recycle.menu_button.menu().hide()
        finally:
            unavailable.runner.shutdown()
            unavailable.form.close()
            app.processEvents()
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
            print(target)

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
            wait_for(settled)
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
            wait_for(settled)
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
            wait_for(settled)
            capture("09-incompatible")
            controller.lora.set_loras(())
            gate = asyncio.Event()
            page.generateButton.click()
            wait_for(lambda: not page.generateButton.isEnabled())
            capture("10-busy-disabled")
            shell.runner._loop.call_soon_threadsafe(gate.set)
            wait_for(settled)
            gate, scenario = None, "lost"
            page.generateButton.click()
            wait_for(settled)
            capture("11-outcome-unknown")

            # Real asset-service composition, in-memory transport. Never generates through cloud.
            shell.form.backendCombo.setCurrentText("cloud")
            panel = controller.lora
            panel.confirm_upload = lambda *_: True
            for name in ("fixture-style.ckpt", "fixture-soft.ckpt"):
                fixture = lora_file(state, name, data=name.encode() * 200)
                panel.choose_asset_file = lambda *_, fixture=fixture: str(fixture)
                page.prepareAssetButton.click()
                wait_for(settled)
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
                QTest.qWait(300)
                capture(name)
            render_inpaint_states(shell, state, capture)
        finally:
            shell.runner.shutdown()
            shell.form.close()
            app.processEvents()


if __name__ == "__main__":
    main()

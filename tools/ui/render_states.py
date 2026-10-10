"""Render real Qt widgets against offline fixtures. Never admits provider network traffic.

Run with the locked dev environment: uv run python tools/ui/render_states.py --out tmp/ui-renders
The calibration grid is a fixture, not a generated photograph. Cloud receipts use the
same in-memory API/transport as the asset tests; no real file upload occurs.
"""

import argparse
import asyncio
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
from dtflow.core.image_operations import ImageOperationSupport  # noqa: E402
from dtflow.core.models import LoRASelection  # noqa: E402
from dtflow.services import session  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.services.person_selection import PersonAnalysis, PersonLayers  # noqa: E402
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
                bytes([255]) * ((width // 3) * (height // 2)),
                bytes([255]) * ((width // 3) * (height // 2)),
                bytes([255]) * ((width // 3) * (height // 2)),
            ),
        ),
        True,
    )
    editor._set_analysis(people)
    capture("28-inpaint-select-people-found")
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

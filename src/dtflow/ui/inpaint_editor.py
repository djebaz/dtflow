"""Offline manual editor checkpoint; all decoding and mask file I/O use headless workers."""

import asyncio
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, Slot
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QFileDialog

from dtflow.core.model_profiles import profile_for
from dtflow.core.tasks import settle
from dtflow.services.image_preparation import (
    image_content_pixels,
    load_mask_file,
    mask_coverage,
    prepare_image_files,
    save_mask_pixels_file,
)
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.inline_feedback import InlineNotice, confirm_action
from dtflow.ui.inpaint_canvas import InpaintCanvas


class InpaintEditor(QObject):
    def __init__(self, page, runner):
        super().__init__(page)
        self.page, self.runner = page, runner
        self._revision = 0
        self._busy = False
        self._loading = False
        self._source = None
        self._coverage = 0.0
        file = QFile(str(Path(__file__).with_name("forms") / "inpaint_editor.ui"))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise OSError("Inpaint editor form could not be opened.")
        try:
            self.form = QUiLoader().load(file, page)
        finally:
            file.close()
        if self.form is None:
            raise OSError("Inpaint editor form could not be loaded.")
        self.form.hide()
        page.generationContextLayout.insertWidget(0, self.form.sourceCard)
        page.generationInspectorLayout.insertWidget(0, self.form.maskCard)
        self.canvas = InpaintCanvas(page.previewHost)
        page.previewHostLayout.addWidget(self.canvas)
        self.form.chooseSourceButton.clicked.connect(self.choose_source)
        self.form.removeSourceButton.clicked.connect(self.remove_source)
        self.form.toolCombo.currentTextChanged.connect(self._tool)
        self.form.brushSize.valueChanged.connect(lambda value: setattr(self.canvas, "brush_size", value))
        self.form.viewCombo.currentTextChanged.connect(self.canvas.set_view)
        self.form.undoButton.clicked.connect(self.canvas.undo)
        self.form.redoButton.clicked.connect(self.canvas.redo)
        self.form.clearMaskButton.clicked.connect(self.clear_mask)
        self.form.loadMaskButton.clicked.connect(self.load_mask)
        self.form.saveMaskButton.clicked.connect(self.save_mask)
        self.canvas.sourceDropped.connect(self.replace_source)
        self.canvas.maskEdited.connect(self._edited)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._measure)
        runner.shutdown_started.connect(self.shutdown)
        page.generationModeLayout.setContentsMargins(0, 0, 0, 0)
        page.generationModeLayout.setSpacing(0)
        page.textToImageModeButton.toggled.connect(self.sync_mode)
        page.inpaintModeButton.toggled.connect(self.sync_mode)
        page.stepsSpinBox.valueChanged.connect(self._steps_note)
        page.modelCombo.currentTextChanged.connect(self._steps_note)
        self.sync_mode()

    @property
    def active(self):
        return self.page.inpaintModeButton.isChecked()

    def sync_mode(self):
        active = self.active
        self.canvas.cancel_stroke()
        for widget in (self.form.sourceCard, self.form.maskCard, self.canvas):
            widget.setVisible(active)
        self.page.resultPreviewLabel.setVisible(not active)
        self.page.generateButton.setText("Edit image" if active else "Generate image")
        if active:
            self.page.generateButton.setEnabled(False)
            self.page.generateButton.setToolTip("Offline editor checkpoint: permit-backed generation is not wired yet.")
        else:
            self.page.generateButton.setToolTip("")
        for name in ("configurationCombo", "loadConfigurationButton", "saveConfigurationButton", "savePresetButton"):
            widget = getattr(self.page, name)
            widget.setEnabled(not active and not self._busy)
            widget.setToolTip("Image operations are single-shot only." if active else "")
        self._steps_note()
        self._controls()

    def set_busy(self, busy):
        self._busy = busy
        self.page.generationModeSwitch.setEnabled(not busy)
        self._controls()
        if self.active:
            self.page.generateButton.setEnabled(False)

    def _controls(self):
        available = not self._busy and not self._loading
        self.canvas.setEnabled(available)
        self.form.chooseSourceButton.setEnabled(available)
        for name in ("removeSourceButton", "toolCombo", "brushSize", "viewCombo", "clearMaskButton", "loadMaskButton"):
            getattr(self.form, name).setEnabled(available and self._source is not None)
        self.form.saveMaskButton.setEnabled(available and self._source is not None and self._coverage > 0)
        self.form.undoButton.setEnabled(available and bool(self.canvas._undo))
        self.form.redoButton.setEnabled(available and bool(self.canvas._redo))

    def _tool(self, value):
        self.canvas.cancel_stroke()
        self.canvas.tool = value

    def _steps_note(self):
        profile = profile_for(self.page.modelCombo.currentText())
        low = profile is not None and self.page.stepsSpinBox.value() < profile.steps
        self.form.stepsNote.setText(
            f"Below the bundled {profile.steps}-step preset; output quality may suffer. This is advisory, not a minimum."
            if low
            else ""
        )

    def _edited(self):
        self._revision += 1
        self._debounce.start()
        self.form.historyLabel.setText(
            f"Undo: {len(self.canvas._undo)} · Redo: {len(self.canvas._redo)} (bounded history)"
        )
        self._controls()

    def _measure(self):
        if self.runner.closed or self._source is None:
            return
        revision = self._revision
        pixels = self.canvas.mask_pixels()
        width, height = self._source.width, self._source.height

        async def work():
            return await settle(asyncio.to_thread(mask_coverage, pixels, width, height))

        def done(value, error):
            if revision != self._revision or self.runner.closed:
                return
            if error is None:
                self._coverage = value
                self.form.coverageLabel.setText(f"Covers {value:.2f}% of the image · working mask, before grow/feather")
                self._controls()

        self.runner.submit(work(), done)

    def _task(self, title, worker, apply):
        if self.runner.closed or self._busy or self._loading:
            return
        self._revision += 1
        revision = self._revision
        self._loading = True
        self._controls()
        strip = activity_strip(self.page)
        key = "inpaint-editor-io"
        strip.start(key, title)

        async def work():
            return await settle(asyncio.to_thread(worker))

        def done(value, error):
            if self.runner.closed:
                return
            self._loading = False
            if revision != self._revision:
                strip.finish(key)
                self._controls()
                return
            if error is not None:
                if isinstance(error, asyncio.CancelledError):
                    strip.complete(key, "Stopped", "No generation was submitted.", "stopped")
                else:
                    self.form.sourceError.setText(str(error))
                    strip.complete(key, title, str(error), "error")
            else:
                apply(value)
                self.form.sourceError.clear()
                strip.complete(key, title, "Completed locally. No generation was submitted.")
            self._controls()

        self.runner.submit(work(), done)

    def choose_source(self):
        if self._busy or self._loading:
            return
        path = QFileDialog.getOpenFileName(
            self.page, "Choose source image", "", "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)"
        )[0]
        if path:
            self.replace_source(path)

    def _discard_then(self, button, title, callback):
        if self.canvas._undo or self.canvas._redo:
            confirm_action(
                self.page,
                button,
                title,
                "The mask and Undo history belong to this source and will be discarded.",
                callback,
                danger=True,
            )
        else:
            callback()

    def replace_source(self, path):
        if self._busy or self._loading:
            return

        def load():
            def worker():
                prepared = prepare_image_files(Path(path), 512, 512)
                return prepared.source, image_content_pixels(prepared.source)

            def apply(value):
                self._source, pixels = value
                self._coverage = 0
                self.canvas.set_source(pixels, self._source.width, self._source.height)
                self.form.sourceThumbnail.setPixmap(self.canvas.pixmap().scaledToHeight(80))
                self.form.sourceFacts.setText(
                    f"{Path(path).name}\n{self._source.width} × {self._source.height} · EXIF-oriented · RGB8 · alpha flattened onto white"
                )

            self._task("Load source image", worker, apply)

        self._discard_then(self.form.chooseSourceButton, "Replace source image", load)

    def remove_source(self):
        if self._busy or self._loading:
            return

        def remove():
            self._source = None
            self._coverage = 0
            self.canvas.remove_source()
            self.form.sourceThumbnail.clear()
            self.form.sourceFacts.setText("Choose a source image…")
            self.form.sourceError.clear()
            self.form.coverageLabel.setText("Covers 0% of the image")

        self._discard_then(self.form.removeSourceButton, "Remove source image", remove)

    def clear_mask(self):
        if not self._busy and not self._loading:
            confirm_action(
                self.page,
                self.form.clearMaskButton,
                "Clear mask",
                "Removes the whole mask. Undo remains available.",
                self.canvas.clear_mask,
                danger=True,
            )

    def load_mask(self):
        if self._source is None or self._busy or self._loading:
            return
        path = QFileDialog.getOpenFileName(
            self.page, "Load mask", "", "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)"
        )[0]
        if path:
            width, height = self._source.width, self._source.height
            self._task(
                "Load mask",
                lambda: image_content_pixels(load_mask_file(Path(path), width, height)),
                self.canvas.replace_mask,
            )

    def save_mask(self):
        if self._source is None or self._busy or self._loading or self._coverage <= 0:
            return
        path = QFileDialog.getSaveFileName(
            self.page, "Save new mask (existing files are refused)", "mask.png", "PNG (*.png)"
        )[0]
        if path:
            pixels = self.canvas.mask_pixels()
            width, height = self._source.width, self._source.height
            self._task("Save mask", lambda: save_mask_pixels_file(Path(path), pixels, width, height), lambda _: None)

    def generate(self):
        InlineNotice.warning(
            self.page,
            "Editor dispatch pending",
            "This checkpoint is offline only. Continue using dtflow edit for explicit owner probes.",
        )

    @Slot()
    def shutdown(self):
        self._revision += 1
        self._debounce.stop()

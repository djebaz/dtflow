"""Inline manual Inpaint editor; decoding, preparation and mask I/O run on workers, dispatch stays in the page."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QTimer, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QFileDialog, QLabel

from dtflow.core.image_inputs import ImageContent, max_mask_grow, suggest_generation_size
from dtflow.core.model_profiles import profile_for
from dtflow.core.tasks import settle
from dtflow.persistence.person_models import PersonModels, load_person_models, save_person_models
from dtflow.services.image_preparation import (
    image_content_pixels,
    load_mask_file,
    load_source_file,
    mask_coverage,
    save_mask_pixels_file,
)
from dtflow.services.person_selection import analyze_people, apply_selection, classify_models, layers_union, person_at
from dtflow.services.session import image_edit_capability, selection_for
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.inline_feedback import confirm_action, feedback
from dtflow.ui.inpaint_canvas import InpaintCanvas


@dataclass(frozen=True, slots=True)
class EditJob:
    """Immutable snapshot taken on the GUI thread when the user confirms one edit."""

    source: ImageContent
    mask: bytes
    grow: int
    feather: float
    strength: float
    keep_outside: bool


class InpaintEditor(QObject):
    def __init__(self, page, runner):
        super().__init__(page)
        self.page, self.runner = page, runner
        self._revision = 0
        self._busy = False
        self._loading = False
        self._source = None
        self._coverage = 0.0
        self.controller = None
        self._support = None
        self._support_revision = 0
        self._models = PersonModels()
        self._analysis = None
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
        page.generationInspectorLayout.insertWidget(1, self.form.liveMaskCard)
        # Strength and Keep-outside are backend parameters: they live in the Generate Parameters card.
        grid = page.parametersGrid
        self.form.maskCardLayout.removeWidget(self.form.strengthSpinBox)
        self.form.maskCardLayout.removeWidget(self.form.keepOutsideCheck)
        self.form.strengthSpinBox.setPrefix("")
        self._strength_label = QLabel("Strength")
        self._strength_label.setProperty("textRole", "caption")
        row = grid.rowCount()
        grid.addWidget(self._strength_label, row, 0)
        grid.addWidget(self.form.strengthSpinBox, row, 1)
        grid.addWidget(self.form.keepOutsideCheck, row + 1, 0, 1, 2)
        self._parameter_widgets = (self._strength_label, self.form.strengthSpinBox, self.form.keepOutsideCheck)
        self.form.toolLayout.setContentsMargins(0, 0, 0, 0)
        self.form.toolLayout.setSpacing(0)
        self.canvas = InpaintCanvas(page.previewHost)
        page.previewHostLayout.addWidget(self.canvas)
        self.form.chooseSourceButton.clicked.connect(self.choose_source)
        self.form.removeSourceButton.clicked.connect(self.remove_source)
        for name, tool in (
            ("toolBrush", "Brush"),
            ("toolErase", "Erase"),
            ("toolRect", "Rectangle"),
            ("toolCircle", "Circle"),
        ):
            getattr(self.form, name).clicked.connect(lambda _=False, tool=tool: self._tool(tool))
        self.form.brushSize.valueChanged.connect(self._brush)
        self.form.advancedToggle.toggled.connect(self.form.advancedBox.setVisible)
        self.form.selectModeButton.toggled.connect(self._mode)
        self.form.modelsButton.clicked.connect(self.choose_models)
        self.form.findPeopleButton.clicked.connect(self.find_people)
        self.form.peopleList.itemActivated.connect(self._person_item)
        self.form.peopleList.itemClicked.connect(self._person_item)
        self.canvas.pointPicked.connect(self._pick)
        self.canvas.pointHovered.connect(self._hover)
        self.canvas.hoverLeft.connect(self.canvas.clear_hover)
        self._hovered = None
        for box in (self.form.layerPerson, self.form.layerBody, self.form.layerClothes):
            box.toggled.connect(lambda _=False: self._reset_hover())
        self.form.modeLayout.setContentsMargins(0, 0, 0, 0)
        self.form.modeLayout.setSpacing(0)
        self.canvas.viewChanged.connect(self._view_changed)
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
        page.modelCombo.currentTextChanged.connect(self.refresh_capability)
        self.sync_mode()

    def attach(self, controller):
        """The generation page owns backend selection and dispatch; the editor only asks it."""
        self.controller = controller
        try:
            self._models = load_person_models(controller._state)
        except Exception as exc:  # unreadable settings never block manual painting
            self.form.peopleStatus.setText(str(exc))
        self._people_status()
        self.refresh_capability()

    @property
    def active(self):
        return self.page.inpaintModeButton.isChecked()

    def sync_mode(self):
        active = self.active
        self.canvas.cancel_stroke()
        for widget in (self.form.sourceCard, self.form.maskCard, self.form.liveMaskCard, self.canvas):
            widget.setVisible(active)
        for widget in self._parameter_widgets:
            widget.setVisible(active)
        self.page.resultPreviewLabel.setVisible(not active)
        self.page.generateButton.setText("Edit image" if active else "Generate image")
        for name in ("configurationCombo", "loadConfigurationButton", "saveConfigurationButton", "savePresetButton"):
            widget = getattr(self.page, name)
            widget.setEnabled(not active and not self._busy)
            widget.setToolTip("Image operations are single-shot only." if active else "")
        self._steps_note()
        self._controls()
        self.refresh_capability()

    def set_busy(self, busy):
        self._busy = busy
        self.page.generationModeSwitch.setEnabled(not busy)
        self._controls()

    def refresh_capability(self, *_):
        """Read-only evidence line, computed on a worker; stale answers are dropped."""
        if self.controller is None or not self.active or self.runner.closed:
            return
        name, endpoint = self.controller._backend()
        model = self.page.modelCombo.currentText().strip()
        state = self.controller._state
        self._support_revision += 1
        revision = self._support_revision

        async def work():
            return await settle(
                asyncio.to_thread(lambda: image_edit_capability(selection_for(name, endpoint, state), model))
            )

        def done(value, error):
            if revision != self._support_revision or self.runner.closed:
                return
            self._support = value if error is None else None
            self._render_support(error)

        self.runner.submit(work(), done)

    def _render_support(self, error=None):
        label = self.form.editorCapability
        support = self._support
        if support is None:
            text, state = f"Capability could not be checked. {error or ''}".strip(), "error"
        elif support.status == "owner_probe":
            text, state = support.reason, "warning"
        elif support.status == "owner_verified":
            text, state = support.reason, "ok"
        else:
            text, state = support.reason, "error"
        label.setText(text)
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)
        self._controls()

    def _ready(self):
        support = self._support
        return (
            self._source is not None
            and self._coverage > 0
            and support is not None
            and support.status in ("owner_probe", "owner_verified")
        )

    def _sync_button(self):
        button = self.page.generateButton
        if not self.active:
            button.setEnabled(not self._busy)
            button.setToolTip("")
            return
        button.setEnabled(self._ready() and not self._busy and not self._loading)
        if self._source is None:
            button.setToolTip("Choose a source image first.")
        elif self._coverage <= 0:
            button.setToolTip("Paint the area to change first.")
        else:
            button.setToolTip(self._support.reason if self._support is not None else "Checking capability…")

    def _controls(self):
        available = not self._busy and not self._loading
        self.canvas.setEnabled(available)
        self.form.chooseSourceButton.setEnabled(available)
        has_source = available and self._source is not None
        for name in (
            "removeSourceButton",
            "toolBrush",
            "toolErase",
            "toolRect",
            "toolCircle",
            "brushSize",
            "clearMaskButton",
            "loadMaskButton",
        ):
            getattr(self.form, name).setEnabled(has_source)
        for name in ("growSpin", "featherSpin", "strengthSpinBox", "keepOutsideCheck"):
            getattr(self.form, name).setEnabled(available)
        self.form.saveMaskButton.setEnabled(available and self._source is not None and self._coverage > 0)
        self.form.findPeopleButton.setEnabled(has_source and self._models.segmentation is not None)
        self.form.modelsButton.setEnabled(available)
        self.form.peopleList.setEnabled(has_source and self._analysis is not None)
        self.form.layerBody.setEnabled(self._analysis is not None and self._analysis.has_clothes)
        self.form.layerClothes.setEnabled(self._analysis is not None and self._analysis.has_clothes)
        self.canvas.undoButton.setEnabled(has_source and self.canvas.can_undo())
        self.canvas.redoButton.setEnabled(has_source and self.canvas.can_redo())
        for button in self.canvas._view_buttons.values():
            button.setEnabled(has_source)
        self._sync_button()

    def _tool(self, value):
        self.canvas.cancel_stroke()
        self.canvas.tool = value

    def _brush(self, value):
        self.canvas.brush_size = value
        self.form.brushValue.setText(f"{value} px")

    def _view_changed(self, view):
        self.form.liveMaskCard.setToolTip("")

    def _steps_note(self):
        profile = profile_for(self.page.modelCombo.currentText())
        low = profile is not None and self.page.stepsSpinBox.value() < profile.steps
        self.form.stepsNote.setText(
            f"Below this model's {profile.steps}-step preset; quality may suffer." if low else ""
        )

    def _edited(self):
        self._revision += 1
        self._debounce.start()
        edits = self.form.editsList
        edits.clear()
        labels = self.canvas.edit_labels()
        edits.addItems(labels[-20:] or ["No edits yet."])
        edits.scrollToBottom()
        mask = self.canvas._mask
        if mask.isNull():
            self.form.maskThumb.clear()
        else:
            self.form.maskThumb.setPixmap(QPixmap.fromImage(mask.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio)))
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
                delta = value - self._coverage
                self._coverage = value
                change = f" ({delta:+.2f}%)" if abs(delta) >= 0.005 else ""
                self.form.coverageLabel.setText(f"Covers {value:.2f}% of the image{change}")
                self._controls()

        self.runner.submit(work(), done)

    def _task(self, title, worker, apply, *, quiet=False):
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
                if quiet:
                    strip.finish(key)
                else:
                    strip.complete(key, title, "Completed locally. No generation was submitted.")
            self._controls()

        self.runner.submit(work(), done)

    def _commit(self, button):
        """A pending Confirm lives on the original button: its next click commits instead of repeating the action."""
        return feedback(self.page).commit(button)

    def choose_source(self):
        if self._commit(self.form.chooseSourceButton) or self._busy or self._loading:
            return
        path = QFileDialog.getOpenFileName(
            self.page, "Choose source image", "", "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)"
        )[0]
        if path:
            self.replace_source(path)

    def _discard_then(self, button, title, callback):
        if self.canvas.can_undo() or self.canvas.can_redo():
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
                source = load_source_file(Path(path))
                return source, image_content_pixels(source)

            def apply(value):
                self._source, pixels = value
                self._coverage = 0
                self._set_analysis(None)
                self.canvas.set_source(pixels, self._source.width, self._source.height)
                self.form.sourceThumbnail.setPixmap(self.canvas.pixmap().scaledToHeight(80))
                self.form.sourceFacts.setText(
                    f"{Path(path).name}\n{self._source.width} × {self._source.height} · EXIF-oriented · RGB8 · alpha flattened onto white"
                )
                self._fit_to_source()

            self._task("Load source image", worker, apply)

        self._discard_then(self.form.chooseSourceButton, "Replace source image", load)

    def _fit_to_source(self):
        """Follow the new source: sensible brush, grow budget and a 64-multiple generation box of the same aspect."""
        source = self._source
        longest = max(source.width, source.height)
        self.form.brushSize.setRange(1, max(32, min(512, longest // 4)))
        self.form.brushSize.setValue(max(4, min(256, longest // 30)))
        self.form.growSpin.setRange(
            -max_mask_grow(source.width, source.height), max_mask_grow(source.width, source.height)
        )
        current = max(self.page.widthSpinBox.value(), self.page.heightSpinBox.value())
        edge = min(2048, max(128, round(current / 64) * 64))
        width, height = suggest_generation_size(source.width, source.height, max_edge=edge)
        self.page.widthSpinBox.setValue(width)
        self.page.heightSpinBox.setValue(height)
        self.canvas.set_result(QPixmap())
        self.canvas.set_view("Source")

    def remove_source(self):
        if self._commit(self.form.removeSourceButton) or self._busy or self._loading:
            return

        def remove():
            self._source = None
            self._coverage = 0
            self._set_analysis(None)
            self.canvas.remove_source()
            self.form.sourceThumbnail.clear()
            self.form.sourceFacts.setText("Choose a source image…")
            self.form.sourceError.clear()
            self.form.coverageLabel.setText("Covers 0% of the image")
            self._controls()

        self._discard_then(self.form.removeSourceButton, "Remove source image", remove)

    def clear_mask(self):
        if self._commit(self.form.clearMaskButton):
            return
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

    def edit_saved_image(self, path):
        """Images handoff: load a saved output as the source, after the usual discard consent."""
        self.page.inpaintModeButton.setChecked(True)
        self.replace_source(path)

    def generate(self):
        """Edit image: the click is the per-run probe consent; the capability line discloses what is sent."""
        if self._busy or self._loading or not self._ready() or self.controller is None:
            return
        self._dispatch()

    def _dispatch(self):
        if self._source is None or self._busy or self._loading or self.controller is None:
            return
        form = self.form
        job = EditJob(
            self._source,
            self.canvas.mask_pixels(),
            form.growSpin.value(),
            float(form.featherSpin.value()),
            form.strengthSpinBox.value(),
            form.keepOutsideCheck.isChecked(),
        )
        self.canvas.set_result(QPixmap())
        self.controller.generate(edit=job)

    # -- Select mode: local ONNX person selection ------------------------------------------------------------------
    def _mode(self, select):
        self.form.selectBox.setVisible(select)
        self.form.paintBox.setVisible(not select)
        self.canvas.set_select_mode(select)

    def _people_status(self):
        models = self._models
        if self._analysis is None and models.segmentation is None:
            text = "Choose a YOLO26 segmentation model (Models…) to find people. Painting works without it."
        elif self._analysis is None:
            text = "Press Find people." + (
                "" if models.clothes else " Add a clothes model for Body and Clothes layers."
            )
        elif not self._analysis.people:
            text = "No people found. Paint the area instead."
        else:
            text = (
                "Click a person on the image (Alt removes) or in the list. Suggestions can be wrong; refine in Paint."
            )
        self.form.peopleStatus.setText(text)

    def _reset_hover(self):
        self._hovered = None
        self.canvas.clear_hover()

    def _hover(self, x, y):
        analysis = self._analysis
        if analysis is None or self._busy or self._loading:
            return
        person = person_at(analysis, x, y)
        layers = self._layers()
        key = None if person is None or not layers else (person.index, layers)
        if key == self._hovered:
            return
        self._hovered = key
        if key is None:
            self.canvas.clear_hover()
        else:
            self.canvas.set_hover(person.box, layers_union(person, layers))

    def _set_analysis(self, analysis):
        self._reset_hover()
        self._analysis = analysis
        self.form.peopleList.clear()
        if analysis is not None:
            self.form.peopleList.addItems([f"Person {p.index} · {p.score:.0%}" for p in analysis.people])
        self._people_status()
        self._controls()

    def choose_models(self):
        if self._busy or self._loading or self.controller is None:
            return
        paths = QFileDialog.getOpenFileNames(
            self.page,
            "Choose the YOLO26 segmentation model and, optionally, the clothes model (ONNX)",
            "",
            "ONNX (*.onnx)",
        )[0]
        if not paths:
            return
        state = self.controller._state

        def worker():
            segmentation, clothes = classify_models([Path(path) for path in paths])
            models = PersonModels(segmentation, clothes)
            save_person_models(state, models)
            return models

        def apply(models):
            self._models = models
            self._set_analysis(None)

        self._task("Choose person models", worker, apply)

    def find_people(self):
        if self._source is None or self._models.segmentation is None or self._busy or self._loading:
            return
        source, models = self._source, self._models
        self._task(
            "Find people",
            lambda: analyze_people(source, models.segmentation, models.clothes),
            self._set_analysis,
        )

    def _layers(self):
        form = self.form
        return frozenset(
            name
            for name, box in (("person", form.layerPerson), ("body", form.layerBody), ("clothes", form.layerClothes))
            if box.isChecked() and box.isEnabled()
        )

    def _person_item(self, item):
        row = self.form.peopleList.row(item)
        if self._analysis is not None and 0 <= row < len(self._analysis.people):
            self._add_person(self._analysis.people[row], remove=False)

    def _pick(self, x, y, remove):
        if self._analysis is None:
            self.form.peopleStatus.setText("Press Find people first.")
            return
        person = person_at(self._analysis, x, y)
        if person is None:
            self.form.peopleStatus.setText("No person at that point.")
            return
        self._add_person(person, remove)

    def _add_person(self, person, remove):
        layers, analysis = self._layers(), self._analysis
        self._reset_hover()
        if not layers:
            self.form.peopleStatus.setText("Tick Person, Body or Clothes first.")
            return
        current = self.canvas.mask_pixels()
        names = " + ".join(name.title() for name in ("person", "body", "clothes") if name in layers)
        label = f"{'Remove' if remove else 'Add'} Person {person.index} · {names}"
        self._task(
            "Select person",
            lambda: apply_selection(current, analysis, person, layers, remove=remove),
            lambda pixels: self.canvas.replace_mask(pixels, label),
            quiet=True,
        )

    def show_result(self, encoded):
        pixmap = QPixmap()
        if pixmap.loadFromData(encoded):
            self.canvas.set_result(pixmap)
            self.canvas.set_view("Result")

    @Slot()
    def shutdown(self):
        self._revision += 1
        self._debounce.stop()

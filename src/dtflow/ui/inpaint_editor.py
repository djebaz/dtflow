"""Inline manual Inpaint editor; decoding, preparation and mask I/O run on workers, dispatch stays in the page."""

import asyncio
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QTimer, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLabel, QListWidgetItem, QSizePolicy

from dtflow.ui.file_dialogs import QFileDialog
from dtflow.core.image_inputs import ImageContent, max_mask_grow, suggest_generation_size
from dtflow.core.model_profiles import profile_for
from dtflow.core.models import ValidationError
from dtflow.core.tasks import settle
from dtflow.persistence.person_models import PersonModels, load_person_models, save_person_models
from dtflow.services.gallery import GalleryEditSource, load_edit_source
from dtflow.services.image_preparation import (
    adjusted_mask_alpha,
    image_content_pixels,
    load_mask_file,
    load_source_file,
    mask_coverage,
    save_mask_pixels_file,
)
from dtflow.services.person_selection import (
    LAYERS,
    PART_COLORS,
    analyze_people,
    apply_selection,
    classify_models,
    colored_preview,
    model_issues,
    part_at,
    person_at,
)
from dtflow.services.model_provisioning import missing_entries
from dtflow.services.session import image_edit_capability, selection_for
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.inline_feedback import confirm_action, feedback
from dtflow.ui.model_download import LatestRelay, confirm_and_download, is_downloading
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
        self._source_path = None
        self._result_path = None
        self._run_source = None  # the source the running edit was taken from
        self.script_handoff = None  # injected UI-only navigation; never generation dispatch
        self._models = PersonModels()
        self._models_issue = None
        self._model_readiness: dict[str, str | None] = {}
        self._model_check_error = None
        self._model_checking = False
        self._model_check_revision = 0
        self._badge_phase = 0
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
        self.form.sourceFacts.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.form.sourceFacts.setMinimumWidth(0)
        self.form.chooseSourceButton.clicked.connect(self.choose_source)
        self.form.removeSourceButton.clicked.connect(self.remove_source)
        self.form.runScriptOnImageButton.clicked.connect(self._open_scripts_preview)
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
        self.form.downloadModelsButton.clicked.connect(self.download_models)
        self._models_tooltip = self.form.modelsButton.toolTip()
        self.form.modelsBadge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.form.modelsBadge.raise_()
        self._badge_timer = QTimer(self)
        self._badge_timer.setInterval(400)
        self._badge_timer.timeout.connect(self._advance_badge)
        self._update_models_badge()
        self.form.findPeopleButton.clicked.connect(self.find_people)
        self.form.peopleList.currentRowChanged.connect(self._list_selected)
        self.form.peopleList.itemActivated.connect(lambda _item: self._add_selected(remove=False))
        self.form.useResultButton.clicked.connect(self.use_result)
        self.form.addPersonButton.clicked.connect(lambda: self._add_selected(remove=False))
        self.form.removePersonButton.clicked.connect(lambda: self._add_selected(remove=True))
        self.canvas.pointPicked.connect(self._pick)
        self.canvas.set_adjuster(self._adjusted_alpha)
        self.form.growSpin.valueChanged.connect(lambda _v: self.canvas.refresh_adjustment())
        self.form.featherSpin.valueChanged.connect(lambda _v: self.canvas.refresh_adjustment())
        self.canvas.pointHovered.connect(self._hover)
        self.canvas.hoverLeft.connect(self._hover_left)
        self._hovered = None
        for name in LAYERS:
            item = QListWidgetItem(name.capitalize())
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if name == "person" else Qt.CheckState.Unchecked)
            swatch = QPixmap(12, 12)
            swatch.fill(QColor(*PART_COLORS[name]))
            item.setIcon(QIcon(swatch))
            self.form.layerList.addItem(item)
        self.form.layerList.itemChanged.connect(self._layer_changed)
        for row in range(self.form.layerList.count()):
            self._style_layer(self.form.layerList.item(row))
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
        page.modelCombo.currentTextChanged.connect(lambda _text: self._sync_button())
        self.sync_mode()

    def attach(self, controller):
        """The generation page owns backend selection and dispatch; the editor only asks it."""
        self.controller = controller
        try:
            self._models = load_person_models(controller._state)
        except Exception as exc:  # unreadable settings never block manual painting
            self._model_check_error = str(exc)
            self._models_issue = str(exc)
        if self._model_check_error is None:
            self._check_models()
        else:
            self._update_models_badge()
            self._people_status()
        self.refresh_capability()

    def _advance_badge(self):
        """Animate only the compact status glyph while saved-file access is checked."""
        if not self._model_checking:
            self._badge_timer.stop()
            return
        self._badge_phase = (self._badge_phase + 1) % 3
        self.form.modelsBadge.setText(("·", "··", "···")[self._badge_phase])

    def _update_models_badge(self):
        """Show verified file access, never claim ONNX sessions are already loaded."""
        models = self._models
        configured = (
            ("Segmentation", "segmentation", models.segmentation),
            ("Clothes", "clothes", models.clothes),
            ("Faces", "faces", models.faces),
        )
        issues = self._model_readiness
        if self._model_checking:
            state, glyph, summary = "checking", "·", "Checking saved model files"
        elif self._model_check_error:
            state, glyph, summary = "unknown", "?", "Model availability could not be checked"
        elif any(issue for issue in issues.values()):
            state, glyph, summary = "error", "!", "One or more configured model files are unavailable"
        elif models.segmentation is None:
            state, glyph, summary = "missing", "0", "Segmentation model not configured"
        elif not issues:
            state, glyph, summary = "unknown", "?", "Saved model files not yet checked"
        else:
            count = sum(1 for _label, role, ref in configured if ref is not None and issues.get(role) is None)
            state, glyph, summary = "ok", str(count), f"{count} model file(s) accessible"

        details = [summary, "File access only; ONNX compatibility is verified when Find people runs."]
        for label, role, ref in configured:
            if ref is None:
                status = "not configured"
            elif self._model_checking:
                status = "checking"
            elif self._model_check_error or role not in issues:
                status = "not verified"
            elif issues[role]:
                status = issues[role]
            else:
                status = "file accessible (not loaded)"
            detail = f"{PureWindowsPath(ref.path).name} — {status}" if ref is not None else status
            details.append(f"{label}: {detail}")
        tooltip = "\n".join(details)
        badge = self.form.modelsBadge
        badge.setText(glyph)
        badge.setProperty("readiness", state)
        badge.setAccessibleName(summary)
        badge.setToolTip(tooltip)
        badge.style().unpolish(badge)
        badge.style().polish(badge)
        self.form.modelsButton.setToolTip(f"{self._models_tooltip}\n\n{tooltip}")
        if self._model_checking and not self._badge_timer.isActive():
            self._badge_phase = 0
            self._badge_timer.start()
        elif not self._model_checking:
            self._badge_timer.stop()
        self._sync_download()

    def _check_models(self):
        """Check saved model read access off Qt; full model verification stays at Find people."""
        self._model_check_revision += 1
        revision = self._model_check_revision
        models = self._models
        self._model_readiness = {}
        self._model_check_error = None
        self._model_checking = False
        self._models_issue = None
        if models.segmentation is None or self.runner.closed:
            self._update_models_badge()
            self._people_status()
            return
        self._model_checking = True
        self._models_issue = "Checking saved model read access…"
        self._update_models_badge()
        self._people_status()

        async def work():
            return await asyncio.to_thread(model_issues, models)

        def done(issues, error):
            if self.runner.closed or self._models is not models or revision != self._model_check_revision:
                return
            self._model_checking = False
            if error is not None:
                self._model_check_error = "Saved model availability could not be checked; choose it again with Models…."
                self._models_issue = self._model_check_error
                self._model_readiness = {}
            else:
                self._model_readiness = issues
                self._models_issue = next((issue for issue in issues.values() if issue), None)
            self._update_models_badge()
            self._people_status()

        self.runner.submit(work(), done)

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
            widget.setEnabled(not active)
            widget.setToolTip("Configurations and presets cover text-to-image only." if active else "")
        self._steps_note()
        self._controls()
        self.refresh_capability()

    def set_busy(self, busy):
        """Another run cannot start now. The editor itself stays editable: a running edit keeps its own snapshot."""
        self._busy = busy
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
        no_model = self.controller is not None and self.controller._model_missing()
        if not self.active:
            button.setEnabled(not self._busy and not no_model)
            button.setToolTip("Choose a model first: Generate needs a checkpoint." if no_model else "")
            return
        button.setEnabled(self._ready() and not self._busy and not self._loading and not no_model)
        if no_model:
            button.setToolTip("Choose a model first: Edit image needs a checkpoint.")
        elif self._source is None:
            button.setToolTip("Choose a source image first.")
        elif self._coverage <= 0:
            button.setToolTip("Paint the area to change first.")
        else:
            button.setToolTip(self._support.reason if self._support is not None else "Checking capability…")

    def _controls(self):
        available = not self._loading
        self.canvas.setEnabled(available)
        self.form.chooseSourceButton.setEnabled(available)
        self.form.useResultButton.setEnabled(available and self._result_path is not None)
        has_source = available and self._source is not None
        self.form.runScriptOnImageButton.setEnabled(has_source and not self._busy and self.script_handoff is not None)
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
        self.form.downloadModelsButton.setEnabled(available and not is_downloading(self.page))
        self.form.peopleList.setEnabled(has_source and self._analysis is not None)
        can_add = has_source and self._selected_person() is not None and bool(self._layers())
        self.form.addPersonButton.setEnabled(can_add)
        self.form.removePersonButton.setEnabled(can_add and self._coverage > 0)
        has_clothes = self._analysis is not None and self._analysis.has_clothes
        for row in range(1, self.form.layerList.count()):
            item = self.form.layerList.item(row)
            flags = item.flags()
            wanted = flags | Qt.ItemFlag.ItemIsEnabled if has_clothes else flags & ~Qt.ItemFlag.ItemIsEnabled
            if wanted != flags:  # setFlags emits itemChanged, which re-enters _controls
                item.setFlags(wanted)
        self.canvas.undoButton.setEnabled(has_source and self.canvas.can_undo())
        self.canvas.redoButton.setEnabled(has_source and self.canvas.can_redo())
        for button in self.canvas._view_buttons.values():
            button.setEnabled(has_source)
        self._sync_button()

    def _open_scripts_preview(self):
        """Copy the current input into a detached UI preview, without changing the editor."""
        if self._source is None or self._loading or self._busy or self.script_handoff is None:
            return
        self.script_handoff(self._source, self.canvas.mask_pixels(), self.canvas.pixmap().copy())

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

    def _task(self, title, worker, apply, *, quiet=False, on_end=None):
        if self.runner.closed or self._loading:
            if on_end is not None:
                on_end()
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
            if on_end is not None:
                on_end()
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
        if self._commit(self.form.chooseSourceButton) or self._loading:
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

    def replace_source(self, path, *, quiet=False, button=None):
        if self._loading:
            return
        recorded_path = path.item.path if isinstance(path, GalleryEditSource) else str(path)
        windows_path = PureWindowsPath(recorded_path)
        filename = windows_path.name if windows_path.drive or "\\" in recorded_path else Path(recorded_path).name

        def load():
            def worker():
                source = load_edit_source(path) if isinstance(path, GalleryEditSource) else load_source_file(Path(path))
                return source, image_content_pixels(source)

            def apply(value):
                self._source, pixels = value
                self._source_path = recorded_path
                self._result_path = None
                self._coverage = 0
                self._set_analysis(None)
                self.canvas.set_source(pixels, self._source.width, self._source.height)
                self.form.sourceThumbnail.setPixmap(self.canvas.pixmap().scaledToHeight(80))
                self.form.sourceFacts.setText(
                    f"{self._short_name(filename)}\n{self._source.width} × {self._source.height} · EXIF-oriented · RGB8 · alpha flattened onto white"
                )
                self.form.sourceFacts.setToolTip(filename)
                self._fit_to_source()

            self._task("Load source image", worker, apply, quiet=quiet)

        self._discard_then(button or self.form.chooseSourceButton, "Replace source image", load)

    def _short_name(self, path):
        """File name elided in the middle to the card width: very long names must never widen the left rail."""
        label = self.form.sourceFacts
        width = max(120, min(260, label.width() - 8)) if label.width() > 0 else 220
        return label.fontMetrics().elidedText(Path(path).name, Qt.TextElideMode.ElideMiddle, width)

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
        if self._commit(self.form.removeSourceButton) or self._loading:
            return

        def remove():
            self._source = None
            self._source_path = None
            self._result_path = None
            self._coverage = 0
            self._set_analysis(None)
            self.canvas.remove_source()
            self.form.sourceThumbnail.clear()
            self.form.sourceFacts.setText("Choose a source image…")
            self.form.sourceFacts.setToolTip("")
            self.form.sourceError.clear()
            self.form.coverageLabel.setText("Covers 0% of the image")
            self._controls()

        self._discard_then(self.form.removeSourceButton, "Remove source image", remove)

    def clear_mask(self):
        if self._commit(self.form.clearMaskButton):
            return
        if not self._loading:
            confirm_action(
                self.page,
                self.form.clearMaskButton,
                "Clear mask",
                "Removes the whole mask. Undo remains available.",
                self.canvas.clear_mask,
                danger=True,
            )

    def load_mask(self):
        if self._source is None or self._loading:
            return
        path = QFileDialog.getOpenFileName(
            self.page, "Load mask", "", "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)", remember_as="inpaint_mask"
        )[0]
        if path:
            width, height = self._source.width, self._source.height
            self._task(
                "Load mask",
                lambda: image_content_pixels(load_mask_file(Path(path), width, height)),
                self.canvas.replace_mask,
            )

    def save_mask(self):
        if self._source is None or self._loading or self._coverage <= 0:
            return
        path = QFileDialog.getSaveFileName(
            self.page,
            "Save new mask (existing files are refused)",
            "mask.png",
            "PNG (*.png)",
            remember_as="inpaint_mask",
        )[0]
        if path:
            pixels = self.canvas.mask_pixels()
            width, height = self._source.width, self._source.height
            self._task("Save mask", lambda: save_mask_pixels_file(Path(path), pixels, width, height), lambda _: None)

    def session_document(self):
        """What the next launch restores: mode, last source image path and edit settings (never the mask)."""
        form = self.form
        return {
            "mode": "inpaint" if self.active else "text",
            "select": form.selectModeButton.isChecked(),
            "source": self._source_path,
            "layers": [
                form.layerList.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(form.layerList.count())
                if form.layerList.item(row).checkState() == Qt.CheckState.Checked
            ],
            "strength": form.strengthSpinBox.value(),
            "keep_outside": form.keepOutsideCheck.isChecked(),
            "grow": form.growSpin.value(),
            "feather": float(form.featherSpin.value()),
        }

    def restore(self, document):
        """Apply a remembered document; anything missing or unusable keeps the defaults."""
        if not isinstance(document, dict):
            return
        form = self.form
        strength, grow, feather = document.get("strength"), document.get("grow"), document.get("feather")
        if isinstance(strength, (int, float)) and not isinstance(strength, bool):
            form.strengthSpinBox.setValue(float(strength))
        if type(grow) is int:
            form.growSpin.setValue(grow)
        if isinstance(feather, (int, float)) and not isinstance(feather, bool):
            form.featherSpin.setValue(float(feather))
        if isinstance(document.get("keep_outside"), bool):
            form.keepOutsideCheck.setChecked(document["keep_outside"])
        layers = document.get("layers")
        if isinstance(layers, list):
            for row in range(form.layerList.count()):
                item = form.layerList.item(row)
                wanted = item.data(Qt.ItemDataRole.UserRole) in layers
                item.setCheckState(Qt.CheckState.Checked if wanted else Qt.CheckState.Unchecked)
        if document.get("select") is True:
            form.selectModeButton.setChecked(True)
        source = document.get("source")
        if isinstance(source, str) and Path(source).is_file():
            self.replace_source(source, quiet=True)
        if document.get("mode") == "inpaint":
            self.page.inpaintModeButton.setChecked(True)

    def edit_saved_image(self, path):
        """Images handoff: load a saved output as the source, after the usual discard consent."""
        self.page.inpaintModeButton.setChecked(True)
        self.replace_source(path)

    def generate(self):
        """Edit image: the click is the per-run probe consent; the capability line discloses what is sent."""
        if self._busy or self._loading or not self._ready() or self.controller is None:
            return
        self._dispatch()

    def queue_job(self):
        """The current edit for the Queues tab: None until a source and a mask are ready (the capability line says why)."""
        if self._loading or not self._ready():
            return None
        return self._edit_job()

    def _edit_job(self):
        form = self.form
        return EditJob(
            self._source,
            self.canvas.mask_pixels(),
            form.growSpin.value(),
            float(form.featherSpin.value()),
            form.strengthSpinBox.value(),
            form.keepOutsideCheck.isChecked(),
        )

    def _dispatch(self):
        if self._source is None or self._busy or self._loading or self.controller is None:
            return
        job, source = self._edit_job(), self._source
        if self.controller.generate(edit=job):
            self._run_source = source
            self.canvas.set_result(QPixmap())

    # -- Select mode: local ONNX person selection ------------------------------------------------------------------
    def _mode(self, select):
        self.form.selectBox.setVisible(select)
        self.form.paintBox.setVisible(not select)
        self.canvas.set_select_mode(select)

    def _people_status(self):
        models = self._models
        if self._analysis is None and self._models_issue:
            text = self._models_issue
        elif self._analysis is None and models.segmentation is None and self._downloadable():
            text = (
                "Press Download models… to install the detection models, or choose your own files (Models…). "
                "Painting works without them."
            )
        elif self._analysis is None and models.segmentation is None:
            text = "Choose a YOLO26 segmentation model (Models…) to find people. Painting works without it."
        elif self._analysis is None:
            text = (
                "Press Find people."
                + ("" if models.clothes else " Add a clothes model for Body and Clothes layers.")
                + ("" if not models.clothes or models.faces else " Add the 320 face model to limit Face to the head.")
            )
        elif not self._analysis.people:
            text = "No people found. Paint the area instead."
        elif not self._analysis.has_clothes:
            text = (
                "Only Person is available: no clothes model is set. Press Models… and select "
                "segformer_b2_clothes.onnx (Ctrl-click to pick both files) to enable the part layers."
            )
        else:
            text = (
                "Click a person on the image (Alt removes) or in the list. Suggestions can be wrong; refine in Paint."
            )
        self.form.peopleStatus.setText(text)

    def _adjusted_alpha(self, pixels, width, height):
        """Overlay alpha with Grow and Edge feather applied; None when neither is set (the raw mask shows)."""
        grow, feather = self.form.growSpin.value(), float(self.form.featherSpin.value())
        if self._source is None or not (grow or feather):
            return None
        return adjusted_mask_alpha(
            pixels,
            width,
            height,
            source_width=self._source.width,
            source_height=self._source.height,
            grow=grow,
            feather=feather,
        )

    def _layer_changed(self, item):
        self._style_layer(item)
        self._reset_hover()
        self._controls()

    def _style_layer(self, item):
        # Ticked layers are bold on a tint of their colour; restyling must not re-enter itemChanged.
        checked = item.checkState() == Qt.CheckState.Checked
        name = item.data(Qt.ItemDataRole.UserRole)
        font = QFont(item.font())
        font.setBold(checked)
        tint = QColor(*PART_COLORS[name])
        tint.setAlpha(70 if checked else 0)
        blocked = self.form.layerList.blockSignals(True)
        try:
            item.setFont(font)
            item.setBackground(QBrush(tint))
        finally:
            self.form.layerList.blockSignals(blocked)

    def _reset_hover(self):
        self._hovered = None
        self.canvas.clear_hover()

    def _hover(self, x, y):
        analysis = self._analysis
        if analysis is None or self._loading:
            return
        person = person_at(analysis, x, y)
        layers = self._layers()
        part = None if person is None or not layers else part_at(person, x, y, frozenset(layers))
        key = None if person is None or not layers else (person.index, layers, part)
        if key == self._hovered:
            return
        self._hovered = key
        if key is None:
            self.canvas.clear_hover()
        else:
            self.canvas.set_hover_colors(person.box, colored_preview(person, frozenset(layers), part))
            self.form.peopleStatus.setText(f"{part.capitalize()}: click to add, Alt-click to remove." if part else "")

    def _set_analysis(self, analysis):
        self._reset_hover()
        self._analysis = analysis
        self.form.peopleList.clear()
        if analysis is not None:
            self.form.peopleList.addItems([f"Person {p.index} · {p.score:.0%}" for p in analysis.people])
        self._people_status()
        self._controls()

    def choose_models(self):
        if self._loading or self.controller is None:
            return
        paths = QFileDialog.getOpenFileNames(
            self.page,
            "Choose the YOLO26 segmentation model and, optionally, the clothes model (ONNX)",
            "",
            "ONNX (*.onnx)",
        )[0]
        if not paths:
            return
        state, previous = self.controller._state, self._models

        def worker():
            segmentation, clothes, faces = classify_models([Path(path) for path in paths])
            models = PersonModels(
                segmentation or previous.segmentation, clothes or previous.clothes, faces or previous.faces
            )
            if models.segmentation is None:
                raise ValidationError("Also choose the YOLO26 segmentation model (yolo26n-seg.onnx).")
            save_person_models(state, models)
            return models

        def apply(models):
            self._models = models
            self._models_issue = None
            self._set_analysis(None)
            # Also check optional models retained from the previous selection.
            self._check_models()

        self._task("Choose person models", worker, apply)

    def find_people(self):
        if self._source is None or self._models.segmentation is None or self._loading:
            return
        source, models = self._source, self._models
        strip = activity_strip(self.page)

        def stage(text):
            # Truthful activity text: each stage is reported by the worker as it really starts.
            if strip is not None:
                strip.update("inpaint-editor-io", 0, 0, detail=text)

        relay = LatestRelay(stage, self)

        def worker():
            return analyze_people(source, models.segmentation, models.clothes, models.faces, on_stage=relay)

        self._task("Find people", worker, self._set_analysis, on_end=lambda: (relay.close(), relay.deleteLater()))

    def _downloadable(self):
        """Catalogue entries for roles that are not configured or whose saved file is unavailable (GUI-side, no I/O)."""
        if self.controller is None or self._model_checking or self._model_check_error:
            return ()
        try:
            return missing_entries(self._models, self._model_readiness)
        except ValidationError:
            return ()

    def _sync_download(self):
        entries = self._downloadable()
        button = self.form.downloadModelsButton
        button.setVisible(bool(entries) or is_downloading(self.page))
        if self._analysis is None:
            self._people_status()
        if entries:
            button.setToolTip(
                "Download from the publishers after one confirmation that lists each licence: "
                + ", ".join(entry.name for entry in entries)
                + ". Your own files chosen with Models… are kept."
            )

    def download_models(self):
        button = self.form.downloadModelsButton
        if self._commit(button):
            self._controls()  # the confirmed download is running now
            return
        if self._loading or self.controller is None or is_downloading(self.page):
            return
        entries = self._downloadable()
        state = self.controller._state

        def done(models, error):
            self._controls()
            if models is None:
                self._model_check_error = str(error) if error is not None else "Model settings could not be read."
                self._models_issue = self._model_check_error
                self._update_models_badge()
                self._people_status()
                return
            # Reload what is recorded now: downloaded roles plus any user-selected file, untouched.
            self._models = models
            self._models_issue = None
            self._set_analysis(None)
            self._check_models()

        confirm_and_download(self.page, state, entries, self.runner, done, button=button)
        self._controls()

    def _layers(self):
        """Ticked layers, in list order; garment layers are unavailable without the clothes model."""
        layers = self.form.layerList
        return tuple(
            layers.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(layers.count())
            if layers.item(row).checkState() == Qt.CheckState.Checked
            and layers.item(row).flags() & Qt.ItemFlag.ItemIsEnabled
        )

    def _selected_person(self):
        row = self.form.peopleList.currentRow()
        if self._analysis is not None and 0 <= row < len(self._analysis.people):
            return self._analysis.people[row]
        return None

    def _list_selected(self, _row):
        self._reset_hover()
        self._preview_selected()
        self._controls()

    def _preview_selected(self):
        person, layers = self._selected_person(), self._layers()
        if person is not None and layers:
            self.canvas.set_hover_colors(person.box, colored_preview(person, frozenset(layers)))

    def _hover_left(self):
        self._hovered = None
        self._people_status()
        self.canvas.clear_hover()
        self._preview_selected()

    def _add_selected(self, *, remove):
        person = self._selected_person()
        if person is not None:
            self._add_person(person, remove)

    def _pick(self, x, y, remove):
        if self._analysis is None:
            self.form.peopleStatus.setText("Press Find people first.")
            return
        person = person_at(self._analysis, x, y)
        if person is None:
            self.form.peopleStatus.setText("No person at that point.")
            return
        part = part_at(person, x, y, frozenset(self._layers())) if self._layers() else None
        self._add_person(person, remove, (part,) if part else None)

    def _add_person(self, person, remove, only=None):
        layers, analysis = only or self._layers(), self._analysis
        self._reset_hover()
        if not layers:
            self.form.peopleStatus.setText("Tick Person, Body or Clothes first.")
            return
        current = self.canvas.mask_pixels()
        names = " + ".join(name.capitalize() for name in layers)
        label = f"{'Remove' if remove else 'Add'} Person {person.index} · {names}"
        self._task(
            "Select person",
            lambda: apply_selection(current, analysis, person, frozenset(layers), remove=remove),
            lambda pixels: self.canvas.replace_mask(pixels, label),
            quiet=True,
        )

    def use_result(self):
        """Chain edits: the latest saved result becomes the source (after the usual discard consent)."""
        if self._commit(self.form.useResultButton):
            return
        path = self._result_path
        if path is not None and Path(path).is_file():
            self.replace_source(path, button=self.form.useResultButton)

    def show_result(self, encoded, path=None):
        self._result_path = str(path) if path else None
        self._controls()
        source, self._run_source = self._run_source, None
        if source is None or self._source is not source:
            return  # the source changed while the edit ran: Use result still chains from it, the canvas keeps the new one
        pixmap = QPixmap()
        if pixmap.loadFromData(encoded):
            self.canvas.set_result(pixmap)
            self.canvas.set_view("Result")

    @Slot()
    def shutdown(self):
        self._revision += 1
        self._debounce.stop()
        self._badge_timer.stop()

"""Controlnet mode of the Generate page: the Pose source and Pose guidance cards and the read-only pose canvas.

``ControlnetEditor`` owns the mode: ``present`` applies one ``PoseView`` to the real widgets, extraction, the detector
download and map fitting run on workers, and Generate follows the reason line the view carries."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLabel, QSizePolicy

from dtflow.core.image_inputs import ImageContent, ImageInputSnapshot
from dtflow.core.pose import (
    INPUT_LABELS,
    POSE_FAMILY_LABEL,
    PoseControl,
    desktop_admits,
    desktop_control,
    desktop_controls,
    retired_controls,
)
from dtflow.core.tasks import settle
from dtflow.services import image_preparation as pixels
from dtflow.services.body_pose import (
    body_map,
    detect_people,
    depth_ready,
    detector_ready,
    extract_control_map,
    fit_control_map,
    load_control_map,
    load_prepared_map,
)
from dtflow.services.image_inputs import restore_image_inputs
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.file_dialogs import QFileDialog
from dtflow.ui.model_download import is_downloading, start_depth_download, start_pose_download
from dtflow.ui.pose_canvas import PoseCanvas

CONFIGURATION_NOTE = "Saving a configuration or preset covers text-to-image only: it would drop the control map."
CONSENT = "Generate or Start sends the control map to DT+ Cloud and may consume credits. Saving a queue stays local."
DETECTOR_NOTE = "YOLO26n-pose · about 11.6 MiB · AGPL-3.0 · runs on this computer"
DEPTH_NOTE = "Depth Anything V2 Small · about 94.5 MiB · Apache-2.0 · runs on this computer"
NO_BACKEND = "Controlnet needs DT+ Cloud. Choose DT+ Cloud under Provider."
NO_CHECKPOINT = (
    f"Controlnet needs a {POSE_FAMILY_LABEL} checkpoint that a desktop control supports; the selected one is not."
)
UNVERIFIED_CHECKPOINT = "Not verified with this checkpoint: the owner runs used {models}."
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)"
RETIRED_CONTROL_NOTE = "The saved control, Union Pro 1.0, is retired. Choose Union Pro 2.0 to continue."
# What a prepared map of each type looks like; a pose map is drawn as OpenPose limbs on black.
MAP_HINTS = {
    "pose": "An OpenPose body map: black background, coloured limbs. Used as it is; nothing is detected.",
    "canny": "A Canny edge map: white edges on black. Used as it is; nothing is detected.",
    "depth": "A grayscale depth map: near is light, far is dark. Used as it is; nothing is detected.",
    "seg": "A segmentation map in ADE20K colours, unknown areas black. Used as it is; nothing is detected.",
}
PHOTO_HINTS = {
    "canny": "Edges are found in the photo on this computer (OpenCV Canny), at the size you generate.",
    "depth": "Depth is estimated on this computer with Depth Anything V2 Small, when it is installed.",
    "seg": "People and common objects are found with the Inpaint YOLO segmentation model, on this computer.",
}


def control_choices(
    snapshot, model="", base_model=None, modes=None, input_type=None
) -> tuple["PoseControlChoice", ...]:
    """The desktop controls (``controlnet_desktop.json``) the DT+ inventory lists that admit this checkpoint and,
    when given, this input type, in table order (verified variant first). The table is read once and cached."""
    if snapshot is None:
        return ()
    listed = {entry.identifier for entry in snapshot.entries}
    return tuple(
        PoseControlChoice(c.label, c.verified, c.checkpoint, c.inputs, c.evidence, c.verified_models)
        for c in desktop_controls()
        if c.checkpoint in listed
        and desktop_admits(c, model, base_model, modes)
        and (input_type is None or input_type in c.inputs)
    )


def input_choices(model, base_model=None, modes=None) -> tuple[str, ...]:
    """The input types some desktop control offers for this checkpoint, whatever the inventory lists."""
    offered = {kind for c in desktop_controls() if desktop_admits(c, model, base_model, modes) for kind in c.inputs}
    return tuple(kind for kind in INPUT_LABELS if kind in offered)


@dataclass(frozen=True, slots=True)
class PoseJob:
    """Immutable snapshot taken on the GUI thread when the user presses Generate: the map and its evidence."""

    content: ImageContent
    evidence: tuple[tuple[str, str], ...]
    control: PoseControl


@dataclass(frozen=True, slots=True)
class PoseControlChoice:
    label: str
    verified: bool
    checkpoint: str = ""
    inputs: tuple[str, ...] = ("pose",)
    evidence: str = ""
    verified_models: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PosePerson:
    label: str  # "Person 2 · 88 %"
    box: tuple[int, int, int, int]  # source pixels


@dataclass(frozen=True, slots=True)
class PoseView:
    """Everything the mode shows. Defaults are the empty state."""

    kind: str = "photo"  # "photo" | "map"
    source: QPixmap | None = None
    source_facts: str = ""
    source_error: str = ""
    detector: str = "ready"  # "ready" | "missing" | "downloading" | "unknown"
    extracting: bool = False
    people: tuple[PosePerson, ...] = ()
    selected: int | None = None
    pose: QPixmap | None = None
    result: QPixmap | None = None
    map_facts: str = ""
    map_note: str = ""
    extraction_error: str = ""
    controls: tuple[PoseControlChoice, ...] = (
        PoseControlChoice("Union Pro 2.0 q5p", True),
        PoseControlChoice("Union Pro 2.0 q8p", False),
    )
    control_index: int = 0
    input_type: str = "pose"
    inputs: tuple[str, ...] = ("pose",)
    strength: float = 0.7
    blocker: str = ""  # why Generate is unavailable; empty means ready
    notice: str = ""
    view: str = "Source"
    extras: dict = field(default_factory=dict)


class ControlnetEditor(QObject):
    def __init__(self, page, runner):
        super().__init__(page)
        self.page, self.runner = page, runner
        self.controller = None
        self._revision = 0
        self._busy = False
        self._kind = "photo"
        self._input_type = "pose"
        self._content = None  # the chosen photo or body map
        self._filename = ""
        self._source_pixmap = None
        self._source_evidence = {}  # extraction evidence that came with a prepared map file
        self._detection = None
        self._selected = None
        self._map = None  # the body map at source size: extracted from the photo, or the chosen map
        self._map_evidence = {}
        self._pose_pixmap = None  # the map as it will be submitted (fitted to the current size)
        self._map_facts = ""
        self._source_error = ""
        self._extraction_error = ""
        self._extracting = False
        self._downloading = False
        self._result = None
        self._controls = ()
        self._inputs = ()  # input types offered for the selected checkpoint
        self._control_index = 0
        self._generate_tip = False  # Generate's tooltip is ours while in this mode and cleared on leaving
        self._wanted_control = None  # a remembered control, applied once the inventory lists it
        self._paths = {}  # the last photo and the last body map, kept apart so switching between them restores each
        self._restore_source_pending = False  # no saved source work until visible Generate → ControlNet
        self._generate_visible = False  # the legacy generationPage stays hidden in the workspace shell
        self._pending_person = None
        self._strength = 0.7
        self._notice = ""
        file = QFile(str(Path(__file__).with_name("forms") / "controlnet_editor.ui"))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise OSError("Controlnet editor form could not be opened.")
        try:
            self.form = QUiLoader().load(file, page)
        finally:
            file.close()
        if self.form is None:
            raise OSError("Controlnet editor form could not be loaded.")
        self.form.hide()
        page.generationContextLayout.insertWidget(0, self.form.poseSourceCard)
        page.generationInspectorLayout.insertWidget(0, self.form.poseGuidanceCard)
        self.canvas = PoseCanvas(page.previewHost)
        page.previewHostLayout.addWidget(self.canvas)
        self.facts = QLabel()
        self.facts.setWordWrap(True)
        self.facts.setProperty("textRole", "supporting")
        self.facts.setAccessibleName("Pose map details")
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        self.reason.setProperty("textRole", "supporting")
        self.reason.setAccessibleName("Why Generate is unavailable")
        page.previewHostLayout.addWidget(self.facts)
        page.previewHostLayout.addWidget(self.reason)
        for label in (self.facts, self.reason):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setMinimumWidth(0)
        self._extras = (self.canvas, self.facts, self.reason)
        self.form.poseSourceFacts.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.form.poseSourceFacts.setMinimumWidth(0)
        self.form.poseKindLayout.setContentsMargins(0, 0, 0, 0)
        self.form.poseKindLayout.setSpacing(0)
        self._view = PoseView()
        self._wired = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._refit)
        self.form.choosePoseButton.clicked.connect(self.choose_source)
        self.form.removePoseButton.clicked.connect(self.remove_source)
        self.form.extractPoseButton.clicked.connect(self.extract)
        self.form.downloadDetectorButton.clicked.connect(self.download_detector)
        self.form.photoKindButton.clicked.connect(lambda: self._set_kind("photo"))
        self.form.mapKindButton.clicked.connect(lambda: self._set_kind("map"))
        self.form.posePeopleList.currentRowChanged.connect(self._list_selected)
        self.canvas.personPicked.connect(lambda row: self.form.posePeopleList.setCurrentRow(row))
        self.canvas.sourceDropped.connect(self.load_source)
        self.form.poseControlCombo.currentIndexChanged.connect(self._control_changed)
        self.form.poseInputCombo.currentIndexChanged.connect(self._input_changed)
        self.form.poseStrengthSpin.valueChanged.connect(self._strength_changed)
        self.form.poseStartSpin.valueChanged.connect(self._range_changed)
        self.form.poseEndSpin.valueChanged.connect(self._range_changed)
        page.textToImageModeButton.toggled.connect(self.sync_mode)
        page.inpaintModeButton.toggled.connect(self.sync_mode)
        page.controlnetModeButton.toggled.connect(self.sync_mode)
        runner.shutdown_started.connect(self.shutdown)
        self.present(self._view)
        self.sync_mode()

    def attach(self, controller):
        """The generation page owns backend selection and dispatch; the editor only asks it."""
        self.controller = controller
        page = self.page
        page.modelCombo.currentTextChanged.connect(self.refresh_controls)
        page.generationBackendCombo.currentTextChanged.connect(self.refresh)
        for spin in (page.widthSpinBox, page.heightSpinBox):
            spin.valueChanged.connect(self._size_changed)
        self._wired = True
        self.refresh_controls()

    @property
    def active(self):
        return self.page.controlnetModeButton.isChecked()

    def set_generate_visible(self, visible: bool) -> None:
        """Track the active workspace; Generate content is reparented out of the hidden legacy page."""
        self._generate_visible = visible
        self.activate_if_visible()

    def activate_if_visible(self) -> None:
        """Load a remembered pose source once, only while Generate → ControlNet is active in the workspace."""
        if not (self._restore_source_pending and self.active and self._generate_visible):
            return
        self._restore_source_pending = False
        remembered = self._paths.get(self._path_key())
        if remembered and Path(remembered).is_file():
            self.load_source(remembered)

    def sync_mode(self, *_):
        active = self.active
        for widget in (self.form.poseSourceCard, self.form.poseGuidanceCard, *self._extras):
            widget.setVisible(active)
        if active:
            self.page.resultPreviewLabel.setVisible(False)
            self.page.generateButton.setText(
                "Generate with pose" if self._input_type == "pose" else "Generate with Controlnet"
            )
        elif not self.page.inpaintModeButton.isChecked():
            self.page.resultPreviewLabel.setVisible(True)
            self.page.generateButton.setText("Generate image")
            self.page.imagesSpinBox.setEnabled(True)
            self.page.imagesSpinBox.setToolTip("")
        if not active and self._generate_tip:
            self._generate_tip = False
            self.page.generateButton.setToolTip("")
        for name in ("saveConfigurationButton", "savePresetButton"):
            widget = getattr(self.page, name)
            if active:
                widget.setEnabled(False)
                widget.setToolTip(CONFIGURATION_NOTE)
            elif not self.page.inpaintModeButton.isChecked():
                widget.setEnabled(True)
                widget.setToolTip("")
        if active:  # choosing a saved configuration only fills the settings; the pose stays as it is
            for name in ("configurationCombo", "loadConfigurationButton"):
                getattr(self.page, name).setEnabled(True)
        if active and self._wired:
            self.activate_if_visible()
            self.refresh_controls()
        else:
            self.sync_buttons()

    def set_busy(self, busy):
        """Another run cannot start now; the editor itself stays editable (a run keeps its own snapshot)."""
        self._busy = busy
        self.sync_buttons()

    def sync_buttons(self):
        """Pose uses the same Generate, Images and Queue submission actions as text-to-image."""
        if not self.active:
            return
        page, controller = self.page, self.controller
        generate = page.generateButton
        reason = self._view.blocker
        no_model = controller is not None and controller._model_missing()
        dispatching = controller is not None and controller._activity.dispatching
        self._generate_tip = True
        if reason:
            generate.setEnabled(False)
            generate.setToolTip(reason)
        elif dispatching:
            generate.setEnabled(not self._busy and not no_model)
            generate.setToolTip("Adds the pose as one up-next queue item; Start sends the map to DT+.")
        else:
            generate.setEnabled(not self._busy and not no_model)
            generate.setToolTip("Choose a model first: Generate needs a checkpoint." if no_model else "")
        page.queueGenerateButton.setEnabled(not self._busy and not no_model and not reason)
        page.queueGenerateButton.setToolTip(reason or "Create a pose Repeat or plan; Start sends its map to DT+.")
        page.imagesSpinBox.setEnabled(True)
        page.imagesSpinBox.setToolTip("Images above 1 create a Repeat queue with one pose-guided image per item.")

    def _list_selected(self, row):
        self.canvas.select(row if row >= 0 else None)
        if row >= 0 and row != self._selected and self._detection is not None and not self._extracting:
            self._build_map(row)

    @staticmethod
    def _state(widget, state):
        widget.setProperty("state", state)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def present(self, view: PoseView):
        """Show ``view``. Pure presentation: no worker, file or network access."""
        self._view = view
        form, canvas = self.form, self.canvas
        form.photoKindButton.setChecked(view.kind == "photo")
        form.mapKindButton.setChecked(view.kind == "map")
        form.mapKindButton.setText("Body map" if view.input_type == "pose" else "Map")
        has_source = view.source is not None
        busy = view.extracting or view.detector == "downloading"
        if has_source:
            form.poseThumbnail.setPixmap(view.source.scaledToHeight(80))
            canvas.set_source(view.source)
        else:
            form.poseThumbnail.clear()
            canvas.clear_all()
        pose = view.input_type == "pose"
        kind_label = INPUT_LABELS.get(view.input_type, view.input_type)
        form.poseSourceFacts.setText(
            view.source_facts
            or ("Choose a photo…" if view.kind == "photo" else "Choose a body map…" if pose else "Choose a map…")
        )
        form.poseSourceError.setText(view.source_error)
        form.poseSourceError.setVisible(bool(view.source_error))
        form.removePoseButton.setEnabled(has_source and not busy)
        form.choosePoseButton.setEnabled(not busy)
        limits = "Up to 64 MiB / 16 MP. You can also drop an image on the canvas."
        if view.kind == "photo" and not pose:
            limits = f"{limits} {PHOTO_HINTS.get(view.input_type, '')}"
        form.poseSourceLimits.setText(limits if view.kind == "photo" else MAP_HINTS.get(view.input_type, ""))
        photo = view.kind == "photo"
        pose_photo = photo and pose
        extracted = photo and view.input_type in ("pose", "depth", "seg")  # Canny is found while fitting
        for widget in (form.poseStep1Label, form.extractPoseButton):
            widget.setVisible(extracted)
        needs_model = photo and view.input_type in ("pose", "depth")
        for widget in (form.detectorBadge, form.detectorStatus):
            widget.setVisible(needs_model)
        form.downloadDetectorButton.setVisible(needs_model and view.detector in ("missing", "downloading"))
        if pose:
            form.downloadDetectorButton.setText("Download detector")
        form.poseStep2Label.setVisible(pose_photo)
        form.posePeopleList.setVisible(pose_photo)
        form.poseStep3Label.setText(f"{3 if pose_photo else 2} · Control and strength")
        self._detector(view)
        form.extractPoseButton.setText(
            "Extracting…" if view.extracting else "Extract pose" if pose else f"Extract {kind_label.lower()}"
        )
        form.extractPoseButton.setEnabled(has_source and (view.detector == "ready" or not needs_model) and not busy)
        form.poseInputCombo.blockSignals(True)
        form.poseInputCombo.clear()
        for kind in view.inputs:
            form.poseInputCombo.addItem(INPUT_LABELS.get(kind, kind), kind)
        if view.input_type not in view.inputs:  # a restored type the checkpoint does not offer stays visible
            form.poseInputCombo.addItem(f"{kind_label} · not offered for this checkpoint", view.input_type)
        form.poseInputCombo.setCurrentIndex(form.poseInputCombo.findData(view.input_type))
        form.poseInputCombo.blockSignals(False)
        form.poseInputCombo.setEnabled(not busy and form.poseInputCombo.count() > 1)
        form.downloadDetectorButton.setEnabled(view.detector == "missing")
        form.posePeopleList.blockSignals(True)
        form.posePeopleList.clear()
        form.posePeopleList.addItems([person.label for person in view.people])
        if view.selected is not None:
            form.posePeopleList.setCurrentRow(view.selected)
        form.posePeopleList.blockSignals(False)
        form.posePeopleList.setEnabled(len(view.people) > 1 and not busy)
        canvas.set_people([(person.label, person.box) for person in view.people], view.selected)
        canvas.set_pose(view.pose if view.pose is not None else QPixmap())
        canvas.set_result(view.result if view.result is not None else QPixmap())
        shown = {"Pose": view.pose, "Result": view.result}.get(view.view, True)
        canvas.set_view(view.view if shown is not None else "Source")
        canvas._view_buttons["Pose"].setEnabled(view.pose is not None)
        canvas._view_buttons["Pose"].setText("Pose" if pose else "Map")
        canvas._view_buttons["Result"].setEnabled(view.result is not None)
        canvas.setEnabled(not busy)
        form.poseControlCombo.blockSignals(True)
        form.poseControlCombo.clear()
        for choice in view.controls:
            form.poseControlCombo.addItem(f"{choice.label} · {'verified' if choice.verified else 'not verified'}")
        form.poseControlCombo.setCurrentIndex(view.control_index if view.controls else -1)
        form.poseControlCombo.blockSignals(False)
        form.poseControlCombo.setEnabled(bool(view.controls))
        chosen = view.controls[view.control_index] if view.controls and view.control_index >= 0 else None
        if chosen is None:
            note = (
                "Choose an available control, or refresh the inventory to restore the saved control."
                if view.controls
                else f"No {kind_label} control for this checkpoint is in your DT+ inventory. Generate cannot start "
                "without one."
            )
        else:
            note = chosen.evidence
        form.poseControlCombo.setToolTip(note)
        form.poseStrengthSpin.blockSignals(True)
        form.poseStrengthSpin.setValue(view.strength)
        form.poseStrengthSpin.blockSignals(False)
        for widget in (form.poseStrengthSpin, form.poseModeCombo, form.poseStartSpin, form.poseEndSpin):
            widget.setEnabled(chosen is not None)
        self.facts.setText(view.extraction_error or view.map_facts)
        self.facts.setToolTip(view.map_note)
        self._state(self.facts, "error" if view.extraction_error else "unknown")
        self.facts.setVisible(self.active and bool(self.facts.text()))
        self.reason.setText(" ".join(text for text in (view.blocker, view.notice) if text))
        self._state(self.reason, "warning" if view.blocker else "unknown")
        self.reason.setVisible(self.active and bool(self.reason.text()))
        self.sync_buttons()

    def _detector(self, view):
        form = self.form
        if view.input_type == "depth":
            self._depth_detector(view)
            return
        text, state, glyph, summary = {
            "ready": (DETECTOR_NOTE, "ok", "✓", "Pose detector installed"),
            "missing": (
                "The pose detector is not installed. Download reads poses from photos; a body map works without it. "
                "Pressing Download accepts the AGPL-3.0 licence of YOLO26n-pose (about 11.6 MiB, from its publisher).",
                "missing",
                "0",
                "Pose detector not installed",
            ),
            "downloading": ("Downloading the pose detector…", "checking", "·", "Downloading the pose detector"),
            "unknown": ("Detector availability could not be checked.", "unknown", "?", "Detector availability unknown"),
        }[view.detector]
        form.detectorStatus.setText("" if view.detector == "ready" else text)
        form.detectorStatus.setVisible(view.detector != "ready")
        form.detectorBadge.setText(glyph)
        form.detectorBadge.setProperty("readiness", state)
        form.detectorBadge.setAccessibleName(summary)
        form.detectorBadge.setToolTip(summary)
        form.detectorBadge.style().unpolish(form.detectorBadge)
        form.detectorBadge.style().polish(form.detectorBadge)

    def _depth_detector(self, view):
        form = self.form
        text, state, glyph, summary = {
            "ready": (DEPTH_NOTE, "ok", "✓", "Depth model installed"),
            "missing": (
                "The depth model is not installed. Download estimates depth from photos; a depth map works without "
                "it. Pressing Download accepts the Apache-2.0 licence of Depth Anything V2 Small (about 94.5 MiB, from "
                "Hugging Face).",
                "missing",
                "0",
                "Depth model not installed",
            ),
            "downloading": ("Downloading the depth model…", "checking", "·", "Downloading the depth model"),
            "unknown": ("Model availability could not be checked.", "unknown", "?", "Model availability unknown"),
        }[view.detector]
        form.detectorStatus.setText("" if view.detector == "ready" else text)
        form.detectorStatus.setVisible(view.detector != "ready")
        form.downloadDetectorButton.setText("Download depth model")
        form.detectorBadge.setText(glyph)
        form.detectorBadge.setProperty("readiness", state)
        form.detectorBadge.setAccessibleName(summary)
        form.detectorBadge.setToolTip(summary)
        form.detectorBadge.style().unpolish(form.detectorBadge)
        form.detectorBadge.style().polish(form.detectorBadge)

    def _model_ready(self):
        """The local model the current photo extraction needs: the pose detector, or the depth model for Depth."""
        if self.controller is None:
            return False
        state = self.controller._state
        return depth_ready(state) if self._input_type == "depth" else detector_ready(state)

    # -- behaviour --------------------------------------------------------------------------------------------------
    def _backend(self):
        return self.controller._backend()[0] if self.controller is not None else ""

    def _checkpoint(self):
        """The selected checkpoint with its catalogue family and modes (None when the catalogue does not know it)."""
        model = self.page.modelCombo.currentText().strip()
        entry = (
            self.controller._catalogue.checkpoint(model, include_unavailable=True)
            if self.controller is not None and model
            else None
        )
        return model, (entry.base_model if entry else None), (entry.modes if entry else None)

    def _choice(self):
        if self._controls and 0 <= self._control_index < len(self._controls):
            choice = self._controls[self._control_index]
            if not self._wanted_control or choice.checkpoint == self._wanted_control:
                return choice
        return None

    def blocker(self) -> str:
        """Why Generate cannot start now; empty when it can. Backend and checkpoint come first, as they are the
        mode's own requirement."""
        label = INPUT_LABELS.get(self._input_type, self._input_type)
        pose = self._input_type == "pose"
        if self._backend() != "cloud":
            return NO_BACKEND
        if not self._inputs:
            return NO_CHECKPOINT
        if self._input_type not in self._inputs:
            return f"{label} is not offered for this checkpoint. Choose another control type or checkpoint."
        if self._extracting:
            return "Extracting the pose…" if pose else f"Preparing the {label.lower()} map…"
        if self._downloading:
            return "Downloading the pose detector…"
        if self._content is None:
            return "Choose a photo or a body map first." if pose else "Choose a photo or a map first."
        if pose and self._kind == "photo" and not detector_ready(self.controller._state):
            return "Install the pose detector, or use a body map."
        if self._input_type == "depth" and self._kind == "photo" and not self._map and not self._model_ready():
            return "Install the depth model, or use a depth map."
        if pose and self._kind == "photo" and self._detection is not None and not self._detection.candidates:
            return "No person was found. Choose another photo or use a body map."
        if pose and self._kind == "photo" and self._detection is not None and self._selected is None:
            return "Pick the person the pose is taken from."
        if self._map is None:
            return "No pose to send yet." if pose else f"No {label.lower()} map to send yet."
        if self._pose_pixmap is None:
            return "Preparing the map for this size…"
        if self._wanted_control in retired_controls():
            return RETIRED_CONTROL_NOTE
        if not self._controls:
            return f"No {label} control for this checkpoint is in your DT+ inventory. Refresh the models."
        if self._choice() is None:
            return "The saved control is not available here. Refresh models or choose another control."
        return ""

    def _notice_text(self):
        choice = self._choice()
        model = self._checkpoint()[0]
        unverified = ""
        if choice is not None and choice.verified_models and model not in choice.verified_models:
            names = ", ".join(name.removesuffix(".ckpt") for name in choice.verified_models)
            unverified = UNVERIFIED_CHECKPOINT.format(models=names)
        elif choice is not None and not choice.verified:
            unverified = choice.evidence
        return " ".join(text for text in (CONSENT if self._backend() == "cloud" else "", unverified) if text)

    def refresh(self, *_):
        """Rebuild the presentation from the editor's state; the single place the widgets are driven from."""
        if self.runner.closed or self.controller is None:
            return
        detector = "ready" if self._model_ready() else "missing"
        if self._downloading:
            detector = "downloading"
        people = ()
        if self._detection is not None:
            people = tuple(PosePerson(f"Person {c.index} · {c.score:.0%}", c.box) for c in self._detection.candidates)
        view = PoseView(
            kind=self._kind,
            source=self._source_pixmap,
            source_facts=self._filename,
            source_error=self._source_error,
            detector=detector,
            extracting=self._extracting,
            people=people,
            selected=self._selected,
            pose=self._pose_pixmap,
            result=self._result,
            map_facts=self._map_facts,
            map_note=self._map_note(),
            extraction_error=self._extraction_error,
            controls=self._controls,
            control_index=-1 if self._choice() is None else self._control_index,
            input_type=self._input_type,
            inputs=self._inputs,
            strength=self._strength,
            blocker=self.blocker(),
            notice=self._notice_text(),
            view=self.canvas._view,
        )
        self.present(view)

    def queue_control_choices(self, model_id: str, input_type: str) -> tuple[PoseControlChoice, ...]:
        """The controls a queue draft for this checkpoint and input type may pick, from the cached inventory."""
        if self.controller is None:
            return ()
        entry = self.controller._catalogue.checkpoint(model_id, include_unavailable=True) if model_id else None
        return control_choices(
            self.controller._inventory_snapshot,
            model_id,
            entry.base_model if entry else None,
            entry.modes if entry else None,
            input_type,
        )

    def refresh_controls(self, *_):
        """The input types and controls for the selected checkpoint that the active DT+ inventory lists. Only while
        Controlnet is shown: the other modes never read the control table (#635)."""
        if self.controller is None or not self.active:
            return
        if self._wanted_control and not (
            desktop_control(self._wanted_control) or self._wanted_control in retired_controls()
        ):
            self._wanted_control = None  # a malformed remembered value never blocks Generate
        chosen = self._wanted_control or (self._controls[self._control_index].checkpoint if self._controls else None)
        model, base_model, modes = self._checkpoint()
        self._inputs = input_choices(model, base_model, modes)
        self._controls = control_choices(
            self.controller._inventory_snapshot, model, base_model, modes, self._input_type
        )
        self._control_index = next((i for i, c in enumerate(self._controls) if c.checkpoint == chosen), 0)
        self.refresh()

    def _input_changed(self, index):
        """A new control type needs a map of that type: a photo is prepared again, a map of another type is cleared."""
        kind = self.form.poseInputCombo.itemData(index)
        if not isinstance(kind, str) or kind == self._input_type:
            return
        self._input_type = kind
        self._clear_source()
        self._pending_person = None
        self.sync_mode()
        self.refresh_controls()
        remembered = self._paths.get(self._path_key())
        if remembered and Path(remembered).is_file():
            self.load_source(remembered)

    def _path_key(self, kind=None):
        """Photos are shared by every control type; maps are kept per type (a pose map is no depth map)."""
        kind = kind or self._kind
        return kind if kind == "photo" or self._input_type == "pose" else f"{self._input_type}_map"

    def _control_changed(self, index):
        if 0 <= index < len(self._controls):
            self._control_index = index
            self._wanted_control = self._controls[index].checkpoint  # the user's last pick wins over a restored one
            self.refresh()

    def _set_range(self, mode, start, end):
        """Apply a remembered control force and range; anything unusable keeps the current values."""
        form = self.form
        if isinstance(mode, str) and form.poseModeCombo.findText(mode) >= 0:
            form.poseModeCombo.setCurrentText(mode)
        numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (start, end))
        if numbers and 0 <= start < end <= 1:
            form.poseStartSpin.setMaximum(0.95)
            form.poseEndSpin.setMinimum(0.05)
            form.poseStartSpin.setValue(float(start))
            form.poseEndSpin.setValue(float(end))
            self._range_changed()

    def _range_changed(self, *_):
        """Keep the start before the end: each spin box stops one step short of the other."""
        form = self.form
        form.poseStartSpin.setMaximum(round(form.poseEndSpin.value() - 0.05, 2))
        form.poseEndSpin.setMinimum(round(form.poseStartSpin.value() + 0.05, 2))

    def _strength_changed(self, value):
        self._strength = float(value)
        self.refresh()

    def _set_kind(self, kind):
        if kind == self._kind:
            return
        self._clear_source()
        self._pending_person = None
        self._kind = kind
        remembered = self._paths.get(self._path_key(kind))
        if remembered and Path(remembered).is_file():
            self.load_source(remembered)
            return
        self.refresh()

    def _clear_source(self):
        self._restore_source_pending = False
        self._revision += 1
        self._extracting = False
        self._content, self._filename, self._source_pixmap = None, "", None
        self._source_evidence, self._detection, self._selected = {}, None, None
        self._map, self._map_evidence, self._pose_pixmap, self._map_facts = None, {}, None, ""
        self._source_error = self._extraction_error = ""

    def _pixmap(self, content):
        image = QImage(
            pixels.image_content_pixels(content),
            content.width,
            content.height,
            content.width * 3,
            QImage.Format.Format_RGB888,
        )
        return QPixmap.fromImage(image.copy())

    def choose_source(self):
        if self._extracting or self._downloading:
            return
        caption = (
            "Choose photo"
            if self._kind == "photo"
            else "Choose body map"
            if self._input_type == "pose"
            else f"Choose {INPUT_LABELS[self._input_type].lower()} map"
        )
        path = QFileDialog.getOpenFileName(self.page, caption, "", IMAGE_FILTER)[0]
        if path:
            self.load_source(path)

    def load_source(self, path):
        """Decode the chosen file on a worker; a photo then runs the detector, a body map is used as it is."""
        if self.runner.closed:
            return
        if self._extracting or self._downloading:
            self._source_error = "Wait for the current extraction or download to finish, then choose the image again."
            self.refresh()
            return
        self._restore_source_pending = False
        path = Path(path)
        kind, input_type = self._kind, self._input_type
        self._revision += 1
        revision = self._revision
        self._extracting = True
        self.refresh()

        def worker():
            if kind == "map":
                content, evidence = (
                    load_prepared_map(path) if input_type == "pose" else load_control_map(path, input_type)
                )
            else:
                content, evidence = pixels.load_source_file(path), {}
            return content, evidence

        async def work():
            return await settle(asyncio.to_thread(worker))

        def done(value, error):
            if self.runner.closed or revision != self._revision:
                return
            self._extracting = False
            if error is not None:
                self._source_error = str(error)
                self.refresh()
                return
            content, evidence = value
            self._clear_source()
            self._revision = revision + 1
            self._paths[self._path_key(kind)] = str(path)
            self._content, self._source_evidence = content, evidence
            self._source_pixmap = self._pixmap(content)
            name = path.name
            self._filename = f"{name}\n{content.width} × {content.height} · EXIF-oriented · RGB8"
            if kind == "map":
                self._map, self._map_evidence = content, dict(evidence)
                self._schedule_refit()
            elif input_type == "canny":  # edges are found inside the fitted photo, at each size
                self._map = content
                self._map_evidence = {"origin": "canny_photo", "source_sha256": content.sha256}
                self._schedule_refit()
            elif input_type == "seg" or (input_type == "depth" and depth_ready(self.controller._state)):
                self.refresh()
                self.extract()
                return
            elif detector_ready(self.controller._state):
                self.refresh()
                self.extract()
                return
            self.refresh()

        self.runner.submit(work(), done)

    def remove_source(self):
        if self._extracting or self._downloading:
            return
        self._clear_source()
        self._paths.pop(self._path_key(), None)
        self._pending_person = None
        self.refresh()

    def extract(self):
        """Find the people in the photo on a worker (local ONNX); one person is taken, several wait for a pick."""
        if self._content is None or self._kind != "photo" or self.runner.closed or self._extracting:
            return
        if self._input_type == "seg" or (self._input_type == "depth" and self._model_ready()):
            self._extract_map()
            return
        if self._input_type != "pose":
            return
        if not detector_ready(self.controller._state):
            self.refresh()
            return
        content, state = self._content, self.controller._state
        self._revision += 1
        revision = self._revision
        self._extracting = True
        self._extraction_error = ""
        self._detection, self._selected, self._map, self._pose_pixmap, self._map_facts = None, None, None, None, ""
        strip = activity_strip(self.page)
        strip.start("controlnet-extract", "Extract pose")
        self.refresh()

        async def work():
            return await settle(asyncio.to_thread(detect_people, content, state))

        def done(detection, error):
            strip.finish("controlnet-extract")
            if self.runner.closed or revision != self._revision:
                return
            self._extracting = False
            if error is not None:
                self._extraction_error = str(error)
                self.refresh()
                return
            self._detection = detection
            if not detection.candidates:
                self._extraction_error = (
                    "No person was found with enough confidence. Choose another photo or a body map."
                )
                self.refresh()
            elif len(detection.candidates) == 1:
                self._selected = 0
                self._build_map(0)
            elif self._pending_person is not None and self._pending_person < len(detection.candidates):
                row, self._pending_person = self._pending_person, None
                self._build_map(row)
            else:
                self.refresh()

        self.runner.submit(work(), done)

    def _extract_map(self):
        """The depth or segmentation map of the photo, on a worker (local ONNX, nothing downloaded or sent)."""
        content, state, input_type = self._content, self.controller._state, self._input_type
        self._revision += 1
        revision = self._revision
        self._extracting = True
        self._extraction_error = ""
        self._map, self._pose_pixmap, self._map_facts = None, None, ""
        label = INPUT_LABELS[input_type]
        strip = activity_strip(self.page)
        strip.start("controlnet-extract", f"Extract {label.lower()}")
        self.refresh()

        async def work():
            return await settle(asyncio.to_thread(extract_control_map, content, input_type, state))

        def done(value, error):
            strip.finish("controlnet-extract")
            if self.runner.closed or revision != self._revision:
                return
            self._extracting = False
            if error is not None:
                self._extraction_error = str(error)
                self.refresh()
                return
            self._map, evidence = value
            self._map_evidence = dict(evidence)
            self._schedule_refit()

        self.runner.submit(work(), done)

    def _build_map(self, row):
        """The OpenPose map of the picked person, on a worker (drawing only, no model run)."""
        detection, content = self._detection, self._content
        if detection is None or content is None or self.runner.closed:
            return
        self._revision += 1
        revision = self._revision
        self._selected = row
        self._map, self._pose_pixmap, self._map_facts, self._extraction_error = None, None, "", ""
        person = detection.candidates[row].index
        self.refresh()

        async def work():
            return await settle(asyncio.to_thread(body_map, content, detection, person))

        def done(value, error):
            if self.runner.closed or revision != self._revision:
                return
            if error is not None:
                self._extraction_error = str(error)
                self.refresh()
                return
            self._map, evidence = value
            self._map_evidence = dict(evidence)
            self._schedule_refit()

        self.runner.submit(work(), done)

    def _size_changed(self, *_):
        if self._map is not None and self.active:
            self._schedule_refit()

    def _schedule_refit(self):
        self._pose_pixmap = None
        self._debounce.start()
        self.refresh()

    def _refit(self):
        """Fit the map to the current size on a worker, so the Pose view is exactly what Generate will send."""
        if self._map is None or self.runner.closed or self.controller is None:
            return
        content, evidence, input_type = self._map, dict(self._map_evidence), self._input_type
        width, height = self.page.widthSpinBox.value(), self.page.heightSpinBox.value()
        self._revision += 1
        revision = self._revision

        async def work():
            return await settle(asyncio.to_thread(fit_control_map, content, evidence, input_type, width, height))

        def done(value, error):
            if self.runner.closed or revision != self._revision or self._map is not content:
                return
            if error is not None:
                self._extraction_error = str(error)
                self._pose_pixmap = None
                self.refresh()
                return
            prepared, _ = value
            pixmap = QPixmap()
            pixmap.loadFromData(prepared.image.encoded)
            self._pose_pixmap = pixmap
            self._map_facts = self._facts(width, height)
            self.refresh()

        self.runner.submit(work(), done)

    def _map_note(self):
        """The tooltip of the map details: which joints were left out, why, and what that means for the result."""
        evidence = self._map_evidence
        if self._map is None or self._input_type != "pose" or evidence.get("origin") == "prepared_map":
            return ""
        omitted = evidence.get("omitted_joints", "")
        if not omitted:
            return "Every joint was found with enough confidence (0.5 or more) and lies inside the image."
        return (
            f"Omitted joints: {omitted}.\n"
            "A joint is omitted when the detector's confidence is under 0.5 or it lies outside the image. Omitted "
            "joints and the limbs that end on them are not drawn, so the control gives no guidance there and the "
            "model decides freely. Shoulders and hips must be found or the person is refused."
        )

    def _facts(self, width, height):
        evidence = self._map_evidence
        if self._input_type != "pose":
            label = INPUT_LABELS[self._input_type]
            fit = "depth continued to the edges" if self._input_type == "depth" else "fitted with black padding"
            origin = evidence.get("origin", "")
            source = "found in the photo on this computer" if origin.endswith("_photo") else "map used as it is"
            return f"{label} map: {width} × {height}, {fit}, no crop · {source}"
        parts = [f"Map: {width} × {height}, fitted with black padding, no crop"]
        if evidence.get("origin") == "prepared_map":
            parts.append("body map used as it is · nothing detected")
        else:
            try:
                parts.append(f"Extracted in {float(evidence.get('seconds', 'nan')):.1f} s")
            except ValueError:
                pass
            missing = evidence.get("missing_joints")
            if missing is not None:
                omitted = evidence.get("omitted_joints", "")
                names = ", ".join(item.split(" (")[0] for item in omitted.split(", ")) if omitted else ""
                parts.append(
                    f"{missing} joint{'s' if missing != '1' else ''} omitted" + (f": {names}" if names else "")
                    if missing != "0"
                    else "all 18 joints found"
                )
        return " · ".join(parts)

    def download_detector(self):
        """Pressing Download is the accept: the licence (AGPL-3.0 for the pose detector, Apache-2.0 for the depth
        model) is shown beside the button."""
        if self.controller is None or self._downloading or is_downloading(self.page) or self._model_ready():
            return
        depth = self._input_type == "depth"
        self._downloading = True
        self.refresh()

        def done(error):
            self._downloading = False
            self.refresh()
            if error is None and self._content is not None and self._kind == "photo":
                if depth and self._input_type == "depth" and self._map is None:
                    self.extract()
                elif not depth and self._input_type == "pose" and self._detection is None:
                    self.extract()

        start = start_depth_download if depth else start_pose_download
        start(self.page, self.controller._state, self.runner, done)

    def job(self):
        """The pose request input for Generate, or None while the reason line says it cannot start."""
        choice = self._choice()
        if self.blocker() or self._map is None or choice is None:
            return None
        form = self.form
        control = PoseControl(
            choice.checkpoint,
            self._strength,
            input_type=self._input_type,
            control_mode=form.poseModeCombo.currentText(),
            guidance_start=round(form.poseStartSpin.value(), 2),
            guidance_end=round(form.poseEndSpin.value(), 2),
        )
        evidence = tuple(sorted((str(k), str(v)) for k, v in self._map_evidence.items()))
        return PoseJob(self._map, evidence, control)

    def show_result(self, encoded):
        pixmap = QPixmap()
        if pixmap.loadFromData(encoded):
            self._result = pixmap
            self.refresh()
            self.canvas.set_view("Result")

    def session_document(self):
        """What the next launch restores: mode, the photo and the body map paths, the person, control and strength.
        The map is rebuilt from the files, as Inpaint reloads its source."""
        chosen = self._wanted_control or (self._controls[self._control_index].checkpoint if self._controls else None)
        return {
            "mode": "controlnet" if self.active else "text",
            "kind": self._kind,
            "paths": dict(self._paths),
            "person": self._selected if self._selected is not None else self._pending_person,
            "control": chosen,
            "input_type": self._input_type,
            "strength": self._strength,
            "control_mode": self.form.poseModeCombo.currentText(),
            "start": self.form.poseStartSpin.value(),
            "end": self.form.poseEndSpin.value(),
        }

    def restore(self, document):
        """Apply a remembered document; anything missing or unusable keeps the defaults."""
        if not isinstance(document, dict):
            return
        strength = document.get("strength")
        if isinstance(strength, (int, float)) and not isinstance(strength, bool) and 0 < strength <= 2:
            self._strength = float(strength)
            self.form.poseStrengthSpin.setValue(self._strength)
        self._set_range(document.get("control_mode"), document.get("start"), document.get("end"))
        paths = document.get("paths")
        if isinstance(paths, dict):
            keys = ("photo", "map", *(f"{kind}_map" for kind in INPUT_LABELS if kind != "pose"))
            self._paths = {k: v for k, v in paths.items() if k in keys and isinstance(v, str)}
        control = document.get("control")
        if isinstance(control, str):  # checked against the table when Controlnet opens (refresh_controls)
            self._wanted_control = control
            self._control_index = next((i for i, c in enumerate(self._controls) if c.checkpoint == control), 0)
        kind = document.get("kind")
        if kind in ("photo", "map"):
            self._kind = kind
        input_type = document.get("input_type")
        if input_type in INPUT_LABELS:
            self._input_type = input_type
        person = document.get("person")
        self._pending_person = person if type(person) is int and person >= 0 else None
        # Restore preferences now, but postpone source I/O and ONNX detection until ControlNet is visible.
        self._restore_source_pending = bool(self._paths.get(self._path_key()))
        self.refresh()
        if document.get("mode") == "controlnet":
            self.page.controlnetModeButton.setChecked(True)

    def restore_problem(self, document):
        """Refuse controls and types the desktop does not offer before modifying the form. Checked against the
        bundled control table, never the live inventory, so a record restores offline or with Local selected. A
        retired Union Pro 1.0 pose record restores as recorded and Generate waits for an explicit replacement."""
        pose = document.get("pose_control")
        if not isinstance(pose, dict):
            return "This saved control type is not supported in desktop Controlnet."
        checkpoint, input_type = pose.get("checkpoint"), pose.get("input_type", "pose")
        control = desktop_control(checkpoint)
        if checkpoint in retired_controls():
            if input_type != "pose":
                return "This saved control type is not supported in desktop Controlnet."
        elif control is None:
            return "The saved control checkpoint is not supported in desktop Controlnet."
        elif input_type not in control.inputs:
            return f"The saved control type ({input_type}) is not offered with this control in desktop Controlnet."
        if not document.get("image_inputs"):
            return "The saved control map is missing."
        return ""

    def restore_record(self, document):
        """Gallery Reuse of a pose image: bring its control, strength and retained map back into this mode. The map is
        read from the managed inputs, not from the original file, and nothing is submitted."""
        pose = document.get("pose_control")
        record = document.get("image_inputs")
        if self.controller is None or not pose or not record or self.runner.closed:
            return
        if problem := self.restore_problem(document):
            self._clear_source()
            self._source_error = problem
            self.refresh()
            return
        state = self.controller._state
        self.page.controlnetModeButton.setChecked(True)
        self._clear_source()
        self._input_type = pose.get("input_type", "pose")
        self._revision += 1
        revision = self._revision
        self._kind = "map"
        self._extracting = True
        self.sync_mode()  # the control type, its controls and the Generate label follow the record

        async def work():
            def read():
                prepared = restore_image_inputs(state, ImageInputSnapshot.from_record(record))
                return prepared.image

            return await settle(asyncio.to_thread(read))

        def done(content, error):
            if self.runner.closed or revision != self._revision:
                return
            self._extracting = False
            if error is not None:
                self._source_error = f"The retained map could not be restored: {error}"
                self.refresh()
                return
            self._content, self._map = content, content
            self._source_pixmap = self._pixmap(content)
            self._filename = f"Retained map\n{content.width} × {content.height} · restored from the saved image"
            self._map_evidence = {str(k): str(v) for k, v in pose.get("evidence", ())}
            if self._input_type == "canny" and self._map_evidence.get("origin") == "canny_photo":
                # The retained map already holds the edges; fitting must not detect edges in it again.
                self._map_evidence["origin"] = "retained_canny_map"
            self._strength = float(pose["strength"])
            start, end = pose.get("guidance_start"), pose.get("guidance_end")
            self._set_range(
                pose.get("control_mode") or "Balanced", 0.0 if start is None else start, 1.0 if end is None else end
            )
            self._wanted_control = pose["checkpoint"]
            self.refresh_controls()
            self._schedule_refit()

        self.runner.submit(work(), done)

    def shutdown(self):
        self._revision += 1
        self._debounce.stop()

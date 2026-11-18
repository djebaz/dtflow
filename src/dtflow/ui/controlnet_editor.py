"""Controlnet mode of the Generate page: the Pose source and Pose guidance cards and the read-only pose canvas.

Presentation only. ``present`` applies one ``PoseView`` to the real widgets; nothing here extracts, downloads or
generates, and the Generate action stays disabled until that behaviour exists."""

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject
from PySide6.QtGui import QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLabel, QSizePolicy

from dtflow.ui.pose_canvas import PoseCanvas

NOT_WIRED = "Controlnet generation is not available yet: this mode is a layout preview."
CONFIGURATION_NOTE = "Configurations and presets cover text-to-image only."
QUEUE_NOTE = "Controlnet requests cannot be queued."
CONSENT = "The pose map is sent to DT+ Cloud and may use credits. Generate asks before the first use."
DETECTOR_NOTE = "YOLO26n-pose · about 11.6 MiB · AGPL-3.0 · runs on this computer"


@dataclass(frozen=True, slots=True)
class PoseControlChoice:
    label: str
    verified: bool


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
    map_facts: str = ""
    extraction_error: str = ""
    controls: tuple[PoseControlChoice, ...] = (
        PoseControlChoice("Union Pro 1.0 q5p", True),
        PoseControlChoice("Union Pro 1.0 q8p", False),
    )
    control_index: int = 0
    strength: float = 0.7
    blocker: str = NOT_WIRED  # why Generate is disabled; "" would mean ready
    notice: str = ""
    view: str = "Source"
    extras: dict = field(default_factory=dict)


class ControlnetEditor(QObject):
    def __init__(self, page, runner):
        super().__init__(page)
        self.page, self.runner = page, runner
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
        self.form.posePeopleList.currentRowChanged.connect(self._list_selected)
        self.canvas.personPicked.connect(self.form.posePeopleList.setCurrentRow)
        self._present_depth = 0
        self._view = PoseView()
        page.textToImageModeButton.toggled.connect(self.sync_mode)
        page.inpaintModeButton.toggled.connect(self.sync_mode)
        page.controlnetModeButton.toggled.connect(self.sync_mode)
        self.present(self._view)
        self.sync_mode()

    @property
    def active(self):
        return self.page.controlnetModeButton.isChecked()

    def sync_mode(self, *_):
        active = self.active
        for widget in (self.form.poseSourceCard, self.form.poseGuidanceCard, *self._extras):
            widget.setVisible(active)
        if active:
            self.page.resultPreviewLabel.setVisible(False)
            self.page.generateButton.setText("Generate with pose")
        elif not self.page.inpaintModeButton.isChecked():
            self.page.resultPreviewLabel.setVisible(True)
            self.page.generateButton.setText("Generate image")
        for name in ("configurationCombo", "loadConfigurationButton", "saveConfigurationButton", "savePresetButton"):
            widget = getattr(self.page, name)
            if active:
                widget.setEnabled(False)
                widget.setToolTip(CONFIGURATION_NOTE)
            elif not self.page.inpaintModeButton.isChecked():
                widget.setEnabled(True)
                widget.setToolTip("")
        self.sync_buttons()

    def sync_buttons(self):
        """Controlnet never submits yet: Generate and Add to queue are held with the reason shown under the canvas."""
        if not self.active:
            return
        self.page.generateButton.setEnabled(False)
        self.page.generateButton.setToolTip(self._view.blocker or NOT_WIRED)
        self.page.queueGenerateButton.setEnabled(False)
        self.page.queueGenerateButton.setToolTip(QUEUE_NOTE)

    def _list_selected(self, row):
        self.canvas.select(row if row >= 0 else None)

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
        has_source = view.source is not None
        busy = view.extracting or view.detector == "downloading"
        if has_source:
            form.poseThumbnail.setPixmap(view.source.scaledToHeight(80))
            canvas.set_source(view.source)
        else:
            form.poseThumbnail.clear()
            canvas.clear_all()
        form.poseSourceFacts.setText(
            view.source_facts or ("Choose a photo…" if view.kind == "photo" else "Choose a body map…")
        )
        form.poseSourceError.setText(view.source_error)
        form.poseSourceError.setVisible(bool(view.source_error))
        form.removePoseButton.setEnabled(has_source and not busy)
        form.choosePoseButton.setEnabled(not busy)
        form.poseSourceLimits.setText(
            "Up to 64 MiB / 16 MP. You can also drop an image on the canvas."
            if view.kind == "photo"
            else "An OpenPose body map: black background, coloured limbs. Used as it is; nothing is detected."
        )
        photo = view.kind == "photo"
        for widget in (form.poseStep1Label, form.extractPoseButton, form.detectorBadge, form.detectorStatus):
            widget.setVisible(photo)
        form.downloadDetectorButton.setVisible(photo and view.detector in ("missing", "downloading"))
        form.poseStep2Label.setVisible(photo)
        form.posePeopleList.setVisible(photo)
        self._detector(view)
        form.extractPoseButton.setText("Extracting…" if view.extracting else "Extract pose")
        form.extractPoseButton.setEnabled(has_source and view.detector == "ready" and not busy)
        form.downloadDetectorButton.setEnabled(view.detector == "missing")
        form.posePeopleList.blockSignals(True)
        form.posePeopleList.clear()
        form.posePeopleList.addItems([person.label for person in view.people])
        if view.selected is not None:
            form.posePeopleList.setCurrentRow(view.selected)
        form.posePeopleList.blockSignals(False)
        form.posePeopleList.setEnabled(len(view.people) > 1 and not busy)
        canvas.set_people([(person.label, person.box) for person in view.people], view.selected)
        if view.pose is not None:
            canvas.set_pose(view.pose)
        canvas.set_view(view.view if view.pose is not None or view.view == "Source" else "Source")
        canvas._view_buttons["Pose"].setEnabled(view.pose is not None)
        canvas.setEnabled(not busy)
        form.poseControlCombo.blockSignals(True)
        form.poseControlCombo.clear()
        for choice in view.controls:
            form.poseControlCombo.addItem(f"{choice.label} · {'verified' if choice.verified else 'not verified'}")
        form.poseControlCombo.setCurrentIndex(view.control_index if view.controls else -1)
        form.poseControlCombo.blockSignals(False)
        form.poseControlCombo.setEnabled(bool(view.controls))
        chosen = view.controls[view.control_index] if view.controls else None
        if chosen is None:
            note, state = (
                "No Union Pro 1.0 control is in your DT+ inventory. Generate cannot start without one.",
                "error",
            )
        elif chosen.verified:
            note, state = "Verified together with FLUX.1 Dev q5p on DT+ Cloud.", "ok"
        else:
            note, state = "Not verified: only Union Pro 1.0 q5p with FLUX.1 Dev q5p has been confirmed.", "warning"
        form.poseControlNote.setText(note)
        self._state(form.poseControlNote, state)
        form.poseStrengthSpin.setValue(view.strength)
        form.poseStrengthSpin.setEnabled(chosen is not None)
        self.facts.setText(view.extraction_error or view.map_facts)
        self._state(self.facts, "error" if view.extraction_error else "unknown")
        self.facts.setVisible(self.active and bool(self.facts.text()))
        self.reason.setText(" ".join(text for text in (view.blocker, view.notice) if text))
        self._state(self.reason, "warning" if view.blocker else "unknown")
        self.reason.setVisible(self.active and bool(self.reason.text()))
        self.sync_buttons()

    def _detector(self, view):
        form = self.form
        text, state, glyph, summary = {
            "ready": (DETECTOR_NOTE, "ok", "✓", "Pose detector installed"),
            "missing": (
                "The pose detector is not installed. Download it to read poses from photos; "
                "a prepared body map works without it.",
                "missing",
                "0",
                "Pose detector not installed",
            ),
            "downloading": ("Downloading the pose detector…", "checking", "·", "Downloading the pose detector"),
            "unknown": ("Detector availability could not be checked.", "unknown", "?", "Detector availability unknown"),
        }[view.detector]
        form.detectorStatus.setText(text)
        form.detectorBadge.setText(glyph)
        form.detectorBadge.setProperty("readiness", state)
        form.detectorBadge.setAccessibleName(summary)
        form.detectorBadge.setToolTip(summary)
        form.detectorBadge.style().unpolish(form.detectorBadge)
        form.detectorBadge.style().polish(form.detectorBadge)

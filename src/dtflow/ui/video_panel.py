"""Video mode of the Generate page: frames, duration, one start image and the cost note.

Selecting a video checkpoint switches the page to video mode (``model_changed``): the model's own profile fills steps,
guidance and sampler, frames and size get video defaults, image-only controls are disabled with the reason, and this
panel appears. The panel only reads and writes plain values; the request, wire, receiving, encoding and storage layers
do the work (``core.video``, ``backends.drawthings.video_stream``, ``services.video_artifacts``).
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout

from dtflow.core import video as rules
from dtflow.core.models import ResolvedGenerationRequest
from dtflow.services import video_encode
from dtflow.ui.file_dialogs import QFileDialog

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)"
IMAGE_ONLY_REASON = "Not available for video: a video is one clip from text or one start image."
COST_NOTE = (
    "A video takes minutes and can use a large part of the monthly allowance. DT+ does not report what a run costs, "
    "so no cost is shown."
)
MAX_START_IMAGE_BYTES = 32 * 1024 * 1024
LOCKED = ("inpaintModeButton", "controlnetModeButton", "scriptsModeButton", "imagesSpinBox", "queueGenerateButton")


class VideoPanel(QObject):
    def __init__(self, page, state_dir: Path):
        super().__init__(page)
        self.page, self._state = page, Path(state_dir)
        self.model = ""
        self._start: Path | None = None
        self._sha = ""
        self._locked_tips: dict[str, str] = {}
        self.frame = QFrame(page)
        self.frame.setObjectName("videoPanel")
        self.frame.setProperty("role", "card")
        column = QVBoxLayout(self.frame)
        column.setContentsMargins(10, 8, 10, 8)
        row = QHBoxLayout()
        row.addWidget(QLabel("Frames", self.frame))
        self.frames = QSpinBox(self.frame)
        self.frames.setObjectName("videoFramesSpinBox")
        self.frames.setRange(rules.MIN_FRAMES, rules.MAX_FRAMES)
        self.frames.setAccessibleName("Number of video frames")
        self.frames.editingFinished.connect(self._snap_frames)
        self.frames.valueChanged.connect(self._sync)
        row.addWidget(self.frames)
        self.duration = QLabel(self.frame)
        self.duration.setProperty("role", "secondary")
        row.addWidget(self.duration, 1)
        column.addLayout(row)
        start = QHBoxLayout()
        start.addWidget(QLabel("Start image", self.frame))
        self.choose = QPushButton("Choose…", self.frame)
        self.choose.setToolTip("One optional start image. Without it the video is made from the prompt alone.")
        self.choose.clicked.connect(self._choose)
        self.clear = QPushButton("Clear", self.frame)
        self.clear.clicked.connect(self._clear)
        self.start_label = QLabel("None (text to video)", self.frame)
        self.start_label.setProperty("role", "secondary")
        start.addWidget(self.choose)
        start.addWidget(self.clear)
        start.addWidget(self.start_label, 1)
        column.addLayout(start)
        self.note = QLabel(COST_NOTE, self.frame)
        self.note.setWordWrap(True)
        self.note.setProperty("role", "secondary")
        column.addWidget(self.note)
        self.problem = QLabel(self.frame)
        self.problem.setWordWrap(True)
        self.problem.setProperty("state", "warning")
        column.addWidget(self.problem)
        page.generationWorkspaceLayout.insertWidget(3, self.frame)
        self.frame.hide()
        self._sync()

    # -- mode ------------------------------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return rules.is_video_model(self.model)

    def model_changed(self, text: str) -> None:
        """Called when the checkpoint changes; fills the video defaults when the page enters video mode."""
        was = self.active
        self.model = text.strip()
        now = self.active
        self.frame.setVisible(now)
        if now and (not was or rules.family_of(text) is not None):
            self._apply_defaults()
        if now != was:
            self._lock(now)
            if not now:
                self._clear()
        self._sync()

    def _apply_defaults(self) -> None:
        page, family = self.page, rules.family_of(self.model)
        profile = rules.video_profile(self.model)
        if profile is not None:
            page.stepsSpinBox.setValue(profile.steps)
            page.guidanceSpinBox.setValue(profile.guidance)
            index = page.samplerCombo.findText(profile.sampler.value)
            if index >= 0:
                page.samplerCombo.setCurrentIndex(index)
        self.frames.setSingleStep(family.frame_step)
        self.frames.setValue(family.default_frames)
        page.widthSpinBox.setValue(family.default_size[0])
        page.heightSpinBox.setValue(family.default_size[1])
        if not rules.accepts_start_image(self.model):
            self._clear()

    def _lock(self, locked: bool) -> None:
        page = self.page
        for name in LOCKED:
            widget = getattr(page, name)
            if locked:
                self._locked_tips.setdefault(name, widget.toolTip())
                widget.setEnabled(False)
                widget.setToolTip(IMAGE_ONLY_REASON)
            else:
                widget.setEnabled(True)
                widget.setToolTip(self._locked_tips.pop(name, ""))
        if locked:
            page.textToImageModeButton.setChecked(True)
            page.generateButton.setText("Generate video")
        else:
            page.generateButton.setText("Generate image")

    # -- values ----------------------------------------------------------------------------------------------------
    def _snap_frames(self) -> None:
        if self.active:
            self.frames.setValue(rules.valid_frames(self.model, self.frames.value()))

    def _sync(self, *_args) -> None:
        if not self.active:
            return
        seconds = rules.duration_seconds(self.model, self.frames.value())
        audio = rules.audio_rate(self.model)
        self.duration.setText(
            f"{rules.duration_text(seconds)} at {rules.playback_fps(self.model)} fps"
            + (" · with sound" if audio else " · no sound")
        )
        accepts = rules.accepts_start_image(self.model)
        self.choose.setEnabled(accepts)
        self.choose.setToolTip(
            "One optional start image. Without it the video is made from the prompt alone."
            if accepts
            else "A start image is only verified with LTX-2.3 (LTX-2 froze on it)."
        )
        self.clear.setEnabled(self._start is not None)
        problem = video_encode.tools_problem() or rules.frame_problem(self.model, self.frames.value()) or ""
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))

    def blocker(self) -> str:
        """Why a video cannot start now (empty when it can); checked before submit so no paid run is wasted."""
        if not self.active:
            return ""
        return video_encode.tools_problem() or rules.frame_problem(self.model, self.frames.value()) or ""

    def _choose(self) -> None:
        path = QFileDialog.getOpenFileName(self.page, "Choose start image", "", IMAGE_FILTER)[0]
        if not path:
            return
        try:
            data = Path(path).read_bytes() if Path(path).stat().st_size <= MAX_START_IMAGE_BYTES else b""
        except OSError:
            data = b""
        if not data:
            self.problem.setText("The start image could not be read or is larger than 32 MiB.")
            self.problem.setVisible(True)
            return
        self._start, self._sha = Path(path), hashlib.sha256(data).hexdigest()
        self.start_label.setText(Path(path).name)
        self._sync()

    def _clear(self) -> None:
        self._start, self._sha = None, ""
        self.start_label.setText("None (text to video)")
        self.clear.setEnabled(False)

    def apply(self, request: ResolvedGenerationRequest) -> ResolvedGenerationRequest:
        """The resolved image request plus the video fields; the request model checks the frame rule and the image."""
        if request.loras:
            from dtflow.core.models import ValidationError

            raise ValidationError("LoRAs are not supported for video yet.")
        return replace(
            request,
            num_frames=self.frames.value(),
            start_image=str(self._start) if self._start else None,
            start_image_sha256=self._sha or None,
            resolution_evidence=(
                *request.resolution_evidence,
                ("video.playback_fps", str(rules.playback_fps(self.model))),
            ),
        )

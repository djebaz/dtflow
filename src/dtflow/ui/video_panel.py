"""Video mode of the Generate page: frames, duration, one start image and the cost note.

Selecting a video checkpoint switches the page to video mode (``model_changed``): the model's own profile fills steps,
guidance and sampler, frames and size get video defaults, image-only controls are disabled with the reason, and this
panel appears. The panel only reads and writes plain values; the request, wire, receiving, encoding and storage layers
do the work (``core.video``, ``backends.drawthings.video_stream``, ``services.video_artifacts``).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLabel, QPushButton, QSpinBox

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
FORM = Path(__file__).parent / "forms" / "video_panel.ui"


def _read_start_image(path: Path) -> str:
    """Read and hash a bounded file away from Qt; keep only its digest in the controller."""
    if path.stat().st_size > MAX_START_IMAGE_BYTES:
        raise ValueError("The start image is larger than 32 MiB.")
    with path.open("rb") as source:
        data = source.read(MAX_START_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_START_IMAGE_BYTES:
        raise ValueError("The start image is empty or larger than 32 MiB.")
    return hashlib.sha256(data).hexdigest()


LOCKED = ("inpaintModeButton", "controlnetModeButton", "scriptsModeButton", "imagesSpinBox", "queueGenerateButton")


class VideoPanel(QObject):
    def __init__(self, page, state_dir: Path, runner, backend_source):
        super().__init__(page)
        self.page, self._state = page, Path(state_dir)
        self._runner, self._backend_source = runner, backend_source
        self.model = ""
        self._start: Path | None = None
        self._sha = ""
        self._start_source: tuple | None = None
        self._choice_revision = 0
        self._loading = False
        self._choice_problem = ""
        self._locked_tips: dict[str, str] = {}
        file = QFile(str(FORM))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise OSError("Video controls form could not be opened.")
        try:
            self.frame = QUiLoader().load(file, page)
        finally:
            file.close()
        if self.frame is None:
            raise RuntimeError("Video controls form could not be loaded.")
        self.frames = self.frame.findChild(QSpinBox, "videoFramesSpinBox")
        self.duration = self.frame.findChild(QLabel, "videoDurationLabel")
        self.choose = self.frame.findChild(QPushButton, "videoChooseStartButton")
        self.clear = self.frame.findChild(QPushButton, "videoClearStartButton")
        self.start_label = self.frame.findChild(QLabel, "videoStartLabel")
        self.note = self.frame.findChild(QLabel, "videoCostNote")
        self.problem = self.frame.findChild(QLabel, "videoProblemLabel")
        self.note.setText(COST_NOTE)
        self.frames.setRange(rules.MIN_FRAMES, rules.MAX_FRAMES)
        self.frames.editingFinished.connect(self._snap_frames)
        self.frames.valueChanged.connect(self._sync)
        self.choose.clicked.connect(self._choose)
        self.clear.clicked.connect(self._clear)
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
        if text.strip() != self.model:
            self._clear()
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
        self.clear.setEnabled(self._start is not None or self._loading)
        problem = (
            (
                "This exact checkpoint is not verified for video yet. "
                "Select LTX-2.3 distilled 1.1 q6p or q8p."
                if not rules.supported_video_model(self.model) else ""
            )
            or self._choice_problem
            or (
                "Connection changed; choose the start image again."
                if self._start is not None and self._start_source != self._backend_source()
                else ""
            )
            or video_encode.tools_problem()
            or rules.frame_problem(self.model, self.frames.value())
            or ""
        )
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))

    def blocker(self) -> str:
        """Why a video cannot start now (empty when it can); checked before submit so no paid run is wasted."""
        if not self.active:
            return ""
        if not rules.supported_video_model(self.model):
            return "This exact checkpoint is not verified for video yet; select LTX-2.3 distilled 1.1 q6p or q8p."
        if self._loading:
            return "Checking the selected start image; generation cannot begin yet."
        if self._choice_problem:
            return self._choice_problem
        if self._start is not None and self._start_source != self._backend_source():
            return "Connection changed; choose the start image again."
        return video_encode.tools_problem() or rules.frame_problem(self.model, self.frames.value()) or ""

    def _choose(self) -> None:
        path = QFileDialog.getOpenFileName(self.page, "Choose start image", "", IMAGE_FILTER)[0]
        if not path:
            return
        self._clear()
        self._choice_revision += 1
        token, model, source = self._choice_revision, self.model, self._backend_source()
        chosen = Path(path)
        self._loading = True
        self.start_label.setText("Checking start image…")
        self._sync()

        def done(digest, failure) -> None:
            if token != self._choice_revision or model != self.model or self._runner.closed:
                return
            if source != self._backend_source():
                self._clear()
                self._sync()
                return
            self._loading = False
            if failure is not None:
                self._choice_problem = (
                    str(failure) if isinstance(failure, ValueError)
                    else "The start image could not be read."
                )
                self.start_label.setText("None (text to video)")
            else:
                self._start, self._sha, self._start_source = chosen, digest, source
                self.start_label.setText(chosen.name)
            self._sync()

        self._runner.submit(asyncio.to_thread(_read_start_image, chosen), done)

    def _clear(self) -> None:
        self._choice_revision += 1
        self._start, self._sha, self._start_source = None, "", None
        self._loading = False
        self._choice_problem = ""
        self.start_label.setText("None (text to video)")
        self.clear.setEnabled(False)
        self._sync()

    def apply(self, request: ResolvedGenerationRequest) -> ResolvedGenerationRequest:
        """The resolved image request plus the video fields; the request model checks the frame rule and the image."""
        from dtflow.core.models import ValidationError

        if not rules.supported_video_model(self.model):
            raise ValidationError(self.blocker())
        if request.loras:
            raise ValidationError("LoRAs are not supported for video yet.")
        if self._loading:
            raise ValidationError("The start image is still being checked.")
        if self._choice_problem:
            raise ValidationError(self._choice_problem)
        if self._start is not None and self._start_source != self._backend_source():
            raise ValidationError("Connection changed; choose the start image again.")
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

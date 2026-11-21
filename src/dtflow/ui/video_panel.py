"""Video mode of the Generate page: duration and one start image.

Selecting a video checkpoint switches the page to video mode (``model_changed``): the model's own profile fills steps,
guidance and sampler, frames and size get video defaults, image-only controls are disabled with the reason, and this
panel appears. The panel only reads and writes plain values; the request, wire, receiving, encoding and storage layers
do the work (``core.video``, ``backends.drawthings.video_stream``, ``services.video_artifacts``).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QDoubleSpinBox, QLabel, QPushButton

from dtflow.core import video as rules
from dtflow.core.models import ResolvedGenerationRequest
from dtflow.services import video_encode
from dtflow.ui.file_dialogs import QFileDialog

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)"
IMAGE_ONLY_REASON = "Not available for video: a video is one clip from text or one start image."


def _unverified(model: str) -> str:
    """Name the selected file and the exact verified ones, so a near-identical name is not mistaken for them."""
    return f"{model} is not verified for video yet. Verified: " + ", ".join(sorted(rules.SUPPORTED_VIDEO_MODELS)) + "."


MAX_START_IMAGE_BYTES = 32 * 1024 * 1024
FORM = Path(__file__).parent / "forms" / "video_panel.ui"


THUMB_BOX = (128, 96)  # twice the 64 x 48 label, so the preview stays sharp on a scaled display


@dataclass(frozen=True, slots=True)
class StartImageInfo:
    """What the controller keeps of the start image: its digest, its own size and a small PNG preview."""

    digest: str
    width: int
    height: int
    thumbnail_png: bytes


def _read_start_image(path: Path) -> StartImageInfo:
    """Read, hash and decode a bounded file away from Qt; only the digest, size and a small preview are kept."""
    from PIL import Image

    if path.stat().st_size > MAX_START_IMAGE_BYTES:
        raise ValueError("The start image is larger than 32 MiB.")
    with path.open("rb") as source:
        data = source.read(MAX_START_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_START_IMAGE_BYTES:
        raise ValueError("The start image is empty or larger than 32 MiB.")
    # The request path (``video_wire.start_image_tensor``) decodes the same bytes without an EXIF turn, so the size
    # and the preview shown here are the ones that will be submitted.
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        image.draft("RGB", THUMB_BOX)
        preview = image.convert("RGB")
        preview.thumbnail(THUMB_BOX, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        preview.save(out, format="PNG")
    return StartImageInfo(hashlib.sha256(data).hexdigest(), width, height, out.getvalue())


def _short_name(name: str, limit: int = 48) -> str:
    """A long file name cut in the middle (the extension stays) so the Choose and Clear buttons keep their room."""
    if len(name) <= limit:
        return name
    keep = limit - 1
    return name[: keep // 2] + "…" + name[len(name) - (keep - keep // 2) :]


def crop_note(source: tuple[int, int], target: tuple[int, int]) -> str:
    """How the start image is cover-fitted to the clip size (``ImageOps.fit`` in ``video_wire``): the part that does
    not fit the clip's shape is trimmed, nothing is stretched."""
    (sw, sh), (tw, th) = source, target
    size = f"{tw} × {th}"
    if abs(sw * th - sh * tw) * 100 <= sh * tw:  # within 1 % of the same shape
        return f"Fits {size} without cropping."
    return f"Cover-fitted to {size}: the {'sides are' if sw * th > sh * tw else 'top and bottom are'} trimmed."


LOCKED = ("inpaintModeButton", "controlnetModeButton", "scriptsModeButton", "imagesSpinBox", "queueGenerateButton")


class VideoPanel(QObject):
    def __init__(self, page, state_dir: Path, runner, backend_source):
        super().__init__(page)
        self.page, self._state = page, Path(state_dir)
        self._runner, self._backend_source = runner, backend_source
        self.model = ""
        self._start: Path | None = None
        self._sha = ""
        self._start_info: StartImageInfo | None = None
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
        self.seconds = self.frame.findChild(QDoubleSpinBox, "videoDurationSpinBox")
        self.summary = self.frame.findChild(QLabel, "videoDurationLabel")
        self.choose = self.frame.findChild(QPushButton, "videoChooseStartButton")
        self.clear = self.frame.findChild(QPushButton, "videoClearStartButton")
        self.thumb = self.frame.findChild(QLabel, "videoStartThumb")
        self.start_label = self.frame.findChild(QLabel, "videoStartLabel")
        self.start_note = self.frame.findChild(QLabel, "videoStartNote")
        self.problem = self.frame.findChild(QLabel, "videoProblemLabel")
        self.seconds.valueChanged.connect(self._sync)
        page.widthSpinBox.valueChanged.connect(self._sync)
        page.heightSpinBox.valueChanged.connect(self._sync)
        self.choose.clicked.connect(self._choose)
        self.clear.clicked.connect(self._clear)
        page.generationWorkspaceLayout.insertWidget(3, self.frame)
        self.frame.hide()
        self._sync()

    # -- mode ------------------------------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return rules.is_video_model(self.model)

    @property
    def frame_count(self) -> int:
        """The frame count for the entered duration: the one value shown beside it and sent in the request."""
        return rules.frames_for_duration(self.model, self.seconds.value())

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
        low, high = rules.duration_limits(self.model)
        self.seconds.setRange(round(low, 2), round(high, 2))
        self.seconds.setValue(round(family.default_frames / family.fps, 2))
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
    def _sync(self, *_args) -> None:
        if not self.active:
            return
        audio = rules.audio_rate(self.model)
        self.summary.setText(
            rules.effective_text(self.model, self.seconds.value()) + (" · with sound" if audio else " · no sound")
        )
        if self._start_info is not None:
            self.start_note.setText(
                crop_note(
                    (self._start_info.width, self._start_info.height),
                    (self.page.widthSpinBox.value(), self.page.heightSpinBox.value()),
                )
            )
            self.start_note.show()
        accepts = rules.accepts_start_image(self.model)
        self.choose.setEnabled(accepts)
        self.choose.setToolTip(
            "One optional start image. Without it the video is made from the prompt alone."
            if accepts
            else "A start image is only verified with LTX-2.3 (LTX-2 froze on it)."
        )
        self.clear.setEnabled(self._start is not None or self._loading)
        problem = (
            (_unverified(self.model) if not rules.supported_video_model(self.model) else "")
            or self._choice_problem
            or (
                "Connection changed; choose the start image again."
                if self._start is not None and self._start_source != self._backend_source()
                else ""
            )
            or video_encode.tools_problem()
            or rules.frame_problem(self.model, self.frame_count)
            or ""
        )
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))

    def blocker(self) -> str:
        """Why a video cannot start now (empty when it can); checked before submit so no paid run is wasted."""
        if not self.active:
            return ""
        if not rules.supported_video_model(self.model):
            return _unverified(self.model)
        if self._loading:
            return "Checking the selected start image; generation cannot begin yet."
        if self._choice_problem:
            return self._choice_problem
        if self._start is not None and self._start_source != self._backend_source():
            return "Connection changed; choose the start image again."
        return video_encode.tools_problem() or rules.frame_problem(self.model, self.frame_count) or ""

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

        def done(info, failure) -> None:
            if token != self._choice_revision or model != self.model or self._runner.closed:
                return
            if source != self._backend_source():
                self._clear()
                self._sync()
                return
            self._loading = False
            if failure is not None:
                self._choice_problem = (
                    str(failure) if isinstance(failure, ValueError) else "The start image could not be read."
                )
                self._show_start()
            else:
                self._start, self._sha, self._start_source = chosen, info.digest, source
                self._start_info = info
                self._show_start()
            self._sync()

        self._runner.submit(asyncio.to_thread(_read_start_image, chosen), done)

    def _clear(self) -> None:
        self._choice_revision += 1
        self._start, self._sha, self._start_source, self._start_info = None, "", None, None
        self._loading = False
        self._choice_problem = ""
        self._show_start()
        self.clear.setEnabled(False)
        self._sync()

    def _show_start(self) -> None:
        """The start-image row: a small preview, the file name with the image's own size, or the text-to-video default."""
        info = self._start_info
        if self._start is None or info is None:
            self.thumb.clear()
            self.thumb.hide()
            self.start_label.setText("Checking start image…" if self._loading else "None (text to video)")
            self.start_label.setToolTip("")
            self.start_note.clear()
            self.start_note.hide()
            return
        picture = QImage.fromData(info.thumbnail_png)
        self.thumb.setPixmap(
            QPixmap.fromImage(picture).scaled(
                self.thumb.size(),
                aspectMode=Qt.AspectRatioMode.KeepAspectRatio,
                mode=Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.thumb.show()
        self.start_label.setText(f"{_short_name(self._start.name)} · {info.width}×{info.height}")
        self.start_label.setToolTip(str(self._start))

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
            num_frames=self.frame_count,
            start_image=str(self._start) if self._start else None,
            start_image_sha256=self._sha or None,
            resolution_evidence=(
                *request.resolution_evidence,
                ("video.playback_fps", str(rules.playback_fps(self.model))),
            ),
        )

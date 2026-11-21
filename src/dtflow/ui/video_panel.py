"""Video mode of the Generate page: duration and one start image.

Changing a video checkpoint or base model never resets duration, output size, start image or sampling values.
The panel owns the controls; request validation and video execution stay in the existing core, backend and services layers.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
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


THUMB_BOX = (1600, 1600)  # One cached preview serves the small thumbnail and the large hover crop.


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
    """A long file name cut in the middle, preserving the extension and keeping the card readable."""
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
        self._hovering = False
        self._choice_problem = ""
        self._locked_tips: dict[str, str] = {}
        self._seen_video = False
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
        self.thumb.installEventFilter(self)
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

    def model_changed(self, text: str, *, catalogue_update: bool = False) -> None:
        """Change the video mode, never the user's draft, including during catalogue refreshes."""
        was = self.active
        self.model = text.strip()
        now = self.active
        if now and not self._seen_video:
            self._seen_video = True
            family = rules.family_of(self.model)
            self.seconds.setValue(round(family.default_frames / family.fps, 2))
        if not now:
            self._hide_hover()
        self.frame.setVisible(now)
        if now != was:
            self._lock(now)
        self._sync()

    def session_document(self) -> dict:
        """Credential-free video draft values in the existing per-provider last-session document."""
        return {
            "duration_seconds": self.seconds.value(),
            "start_image": str(self._start) if self._start is not None else None,
            "start_image_sha256": self._sha or None,
        }

    def restore(self, document: object) -> None:
        """Restore a saved video draft without resetting it for the current checkpoint."""
        if not isinstance(document, dict):
            return
        self._clear()
        duration = document.get("duration_seconds")
        if (
            type(duration) in (int, float)
            and math.isfinite(duration)
            and self.seconds.minimum() <= duration <= self.seconds.maximum()
        ):
            self.seconds.setValue(float(duration))
            self._seen_video = True
        path, digest = document.get("start_image"), document.get("start_image_sha256")
        if not (
            isinstance(path, str)
            and path
            and isinstance(digest, str)
            and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest)
        ):
            return
        chosen = Path(path)
        self._choice_revision += 1
        token, source = self._choice_revision, self._backend_source()
        self._start, self._sha, self._start_source = chosen, digest, source
        self._loading = True
        self._show_start()
        self._sync()

        def done(info, failure) -> None:
            if token != self._choice_revision or self._runner.closed:
                return
            self._loading = False
            if failure is not None:
                self._choice_problem = "Saved start image is missing or unreadable; choose it again."
            elif info.digest != digest:
                self._choice_problem = "Saved start image has changed; choose it again."
            else:
                self._start_info = info
            self._show_start()
            self._sync()

        self._runner.submit(asyncio.to_thread(_read_start_image, chosen), done)

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
        accepts = rules.accepts_start_image(self.model)
        self.choose.setEnabled(accepts)
        self.choose.setText("Replace…" if self._start is not None else "Choose…")
        self.choose.setToolTip(
            "Choose an image to start the clip from; leave empty for text to video."
            if accepts
            else "A start image is only verified with LTX-2.3 (LTX-2 froze on it)."
        )
        self.thumb.setCursor(Qt.CursorShape.PointingHandCursor if accepts else Qt.CursorShape.ArrowCursor)
        if self._start is not None and self._start_info is not None:
            width, height = self._start_info.width, self._start_info.height
            self.start_label.setText(f"{_short_name(self._start.name)} · {width}×{height}")
            self.start_label.setToolTip(
                crop_note((width, height), (self.page.widthSpinBox.value(), self.page.heightSpinBox.value()))
            )
        else:
            self.start_label.setText("Loading…" if self._loading else "None (text to video)")
            self.start_label.setToolTip("")
        if self._hovering:
            self._show_hover()
        self.clear.setEnabled(self._start is not None or self._loading)
        problem = (
            (_unverified(self.model) if not rules.supported_video_model(self.model) else "")
            or self._incompatible_settings()
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

    def _incompatible_settings(self) -> str:
        """Explain incompatible values rather than silently resetting the video draft."""
        low, high = rules.duration_limits(self.model)
        duration = self.seconds.value()
        if duration < low - 0.005 or duration > high + 0.005:
            return (
                f"This checkpoint supports {low:.2f}–{high:.2f} s; the {duration:.2f} s draft is preserved. "
                "Choose a compatible checkpoint or edit Duration."
            )
        if self._start is not None and not rules.accepts_start_image(self.model):
            return "This checkpoint cannot use a start image. Your image is preserved; choose a compatible checkpoint."
        return ""

    def blocker(self) -> str:
        """Why a video cannot start now (empty when it can); checked before submit so no paid run is wasted."""
        if not self.active:
            return ""
        if not rules.supported_video_model(self.model):
            return _unverified(self.model)
        incompatible = self._incompatible_settings()
        if incompatible:
            return incompatible
        if self._loading:
            return "Checking the selected start image; generation cannot begin yet."
        if self._choice_problem:
            return self._choice_problem
        if self._start is not None and self._start_source != self._backend_source():
            return "Connection changed; choose the start image again."
        return video_encode.tools_problem() or rules.frame_problem(self.model, self.frame_count) or ""

    def _choose(self) -> None:
        if not rules.accepts_start_image(self.model):
            return
        path = QFileDialog.getOpenFileName(self.page, "Choose start image", "", IMAGE_FILTER)[0]
        if not path:
            return
        self._clear()
        self._choice_revision += 1
        token, source = self._choice_revision, self._backend_source()
        chosen = Path(path)
        self._loading = True
        self._show_start()
        self._sync()

        def done(info, failure) -> None:
            if token != self._choice_revision or self._runner.closed:
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
        self._hide_hover()
        self._choice_revision += 1
        self._start, self._sha, self._start_source, self._start_info = None, "", None, None
        self._loading = False
        self._choice_problem = ""
        self._show_start()
        self.clear.setEnabled(False)
        self._sync()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Choose on click; hover previews the current centered crop without replacing output."""
        kind = event.type()
        if watched is self.thumb:
            if kind == QEvent.Type.Resize and self._start_info is not None:
                self._render_thumbnail()
            elif kind == QEvent.Type.Enter:
                self._hovering = True
                self._show_hover()
            elif kind == QEvent.Type.Leave:
                self._hide_hover()
            elif (
                kind == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and rules.accepts_start_image(self.model)
            ):
                self._choose()
                return True
        return super().eventFilter(watched, event)

    def _show_hover(self) -> None:
        """Show the cached source in the canvas; preserve its current result underneath."""
        if not self.active or self._start_info is None or self._start is None:
            self.page.resultPreviewLabel.clear_temporary_crop()
            return
        picture = QPixmap.fromImage(QImage.fromData(self._start_info.thumbnail_png))
        self.page.resultPreviewLabel.show_temporary_crop(
            picture,
            (self.page.widthSpinBox.value(), self.page.heightSpinBox.value()),
        )

    def _hide_hover(self) -> None:
        """Restore whatever output the central viewer currently holds."""
        self._hovering = False
        self.page.resultPreviewLabel.clear_temporary_crop()

    def _render_thumbnail(self) -> None:
        """Fit the entire selected image within the fixed thumbnail, without cropping."""
        info = self._start_info
        if info is None or self.thumb.width() <= 0 or self.thumb.height() <= 0:
            return
        picture = QPixmap.fromImage(QImage.fromData(info.thumbnail_png))
        self.thumb.setPixmap(
            picture.scaled(
                self.thumb.size(),
                aspectMode=Qt.AspectRatioMode.KeepAspectRatio,
                mode=Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _show_start(self) -> None:
        """Show the chosen image in the thumbnail, or None for text to video."""
        info = self._start_info
        if self._start is None or info is None:
            self.thumb.clear()
            self.thumb.setText("None")
            self.thumb.setToolTip("")
            if self._hovering:
                self.page.resultPreviewLabel.clear_temporary_crop()
            return
        self.thumb.setText("")
        self._render_thumbnail()
        self.thumb.setToolTip(f"{self._start.name} · {info.width}×{info.height}")
        if self._hovering:
            self._show_hover()

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

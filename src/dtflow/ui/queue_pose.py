"""Local editing of the independent Queue pose draft; no provider or persistence ownership."""

import asyncio
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QSignalBlocker, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtUiTools import QUiLoader

from dtflow.core.models import ValidationError
from dtflow.core.pose import CONTROL_MODES
from dtflow.core.tasks import settle
from dtflow.ui.controlnet_editor import IMAGE_FILTER, PoseJob
from dtflow.ui.file_dialogs import QFileDialog
from dtflow.ui.generation_presenter import FormError
from dtflow.ui.inline_feedback import feedback


class QueuePoseEditor(QObject):
    def __init__(self, queue, controls_source):
        super().__init__(queue.page)
        self.queue = queue
        self._controls_source = controls_source
        self._epoch = 0
        self._busy = False
        self._shown_control = None
        self._choices_key = None
        self._preview_key = None
        self._error = ""
        file = QFile(str(Path(__file__).with_name("forms") / "queue_pose_editor.ui"))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("Cannot open the Queue pose editor form.")
        try:
            self.form = QUiLoader().load(file, queue.page.queueComposer)
        finally:
            file.close()
        if self.form is None:
            raise RuntimeError("Cannot load the Queue pose editor form.")
        queue.page.queueComposerLayout.insertWidget(2, self.form)
        self.form.hide()
        self.form.installEventFilter(self)
        self.form.queuePoseModeCombo.addItems(CONTROL_MODES)
        for edit in self._edits():
            edit.textEdited.connect(self._changed)
        self.form.queuePoseCheckpointCombo.currentIndexChanged.connect(self._changed)
        self.form.queuePoseModeCombo.currentTextChanged.connect(self._changed)
        self.form.replaceQueuePoseButton.clicked.connect(self.replace_map)

    def _edits(self):
        return (self.form.queuePoseStrengthEdit, self.form.queuePoseFromEdit, self.form.queuePoseToEdit)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            self._epoch += 1
            self._busy = False
            self._preview_key = None
        return False

    def draft_changed(self):
        self._epoch += 1
        self._busy = False
        self._shown_control = self._choices_key = self._preview_key = None
        self._error = ""

    def _pose(self):
        q = self.queue
        if q._draft is None:
            return None, None
        if isinstance(q._draft_job, PoseJob):
            return q._draft_job.content, q._draft_job.control
        if q._draft[0].pose_control is not None:
            content = q._draft_template_pose[0] if q._draft_template_pose else None
            return content, q._draft[0].pose_control
        return None, None

    def sync(self):
        q, form = self.queue, self.form
        content, control = self._pose()
        form.setVisible(control is not None and q.composing)
        if control is None:
            return
        if control != self._shown_control:
            self._shown_control = control
            self._error = ""
            for edit, value in zip(
                self._edits(), (control.strength, control.guidance_start, control.guidance_end), strict=True
            ):
                edit.setText(str(value))
            blocker = QSignalBlocker(form.queuePoseModeCombo)
            form.queuePoseModeCombo.setCurrentText(control.control_mode)
            del blocker
        # Already cached data only; another connection's inventory never replaces this draft's choices.
        choices = tuple(self._controls_source()) if q._draft[1:3] == q._backend() else ()
        choices_key = (choices, control.checkpoint)
        if choices_key != self._choices_key:
            self._choices_key = choices_key
            combo = form.queuePoseCheckpointCombo
            blocker = QSignalBlocker(combo)
            combo.clear()
            for choice in choices:
                combo.addItem(choice.label, choice.checkpoint)
            if combo.findData(control.checkpoint) < 0:
                combo.addItem(f"Saved, not in current inventory: {control.checkpoint}", control.checkpoint)
            combo.setCurrentIndex(combo.findData(control.checkpoint))
            del blocker
        form.setEnabled(not self._busy and not q._runner.closed)
        form.replaceQueuePoseButton.setText("Preparing map…" if self._busy else "Replace pose map…")
        form.queuePoseError.setText(self._error)
        form.queuePoseError.setVisible(bool(self._error))
        if not form.isVisible() or q._runner.closed:
            return
        request = q._draft[0]
        key = (content.sha256 if content is not None else None, request.width_px, request.height_px)
        if key != self._preview_key:
            self._preview_key = key
            form.queuePosePreview.clear()
            if content is None:
                form.queuePosePreview.setText("Retained map unavailable; choose a replacement.")
            else:
                self._preview(content, request.width_px, request.height_px)

    def _read_control(self):
        _, control = self._pose()
        if control is None:
            return None
        try:
            strength, start, end = (float(edit.text().strip()) for edit in self._edits())
        except ValueError as exc:
            raise FormError("Enter numeric pose strength and Apply from/to values.", None) from exc
        try:
            return replace(
                control,
                checkpoint=self.form.queuePoseCheckpointCombo.currentData(),
                strength=strength,
                control_mode=self.form.queuePoseModeCombo.currentText(),
                guidance_start=start,
                guidance_end=end,
            )
        except ValidationError as exc:
            raise FormError(str(exc), None) from exc

    def validate(self):
        if self._pose()[1] is None:
            return
        if self._busy:
            raise FormError("Wait for the replacement pose map to finish preparing.", None)
        self._read_control()  # Invalid in-progress text must never queue/save the previous valid settings.

    def _changed(self, *_):
        q = self.queue
        try:
            control = self._read_control()
        except FormError as exc:
            self._error = str(exc)
        else:
            if control is None:
                return
            self._error = ""
            if isinstance(q._draft_job, PoseJob):
                q._draft_job = replace(q._draft_job, control=control)
            else:
                request, *tail = q._draft
                q._draft = (replace(request, pose_control=control), *tail)
                if q._draft_template_pose is not None:
                    q._draft_template_pose = (q._draft_template_pose[0], replace(control, evidence=()))
            self._shown_control = control  # Do not overwrite typing or reset the caret during ordinary polling.
        self.sync()
        q._plan_changed()

    def _preview(self, content, width, height):
        epoch = self._epoch

        def worker():
            from dtflow.services.body_pose import fit_pose_map

            prepared, _ = fit_pose_map(content, {}, width, height)
            image = QImage.fromData(prepared.image.encoded)
            if image.isNull():
                raise ValidationError("Map preview unavailable.")
            return image.scaled(
                160, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )

        async def work():
            return await settle(asyncio.to_thread(worker))

        def done(image, error):
            if self.queue._runner.closed or epoch != self._epoch or not self.form.isVisible():
                return
            if error is not None:
                self.form.queuePosePreview.setText("Map preview unavailable.")
            else:
                self.form.queuePosePreview.setPixmap(QPixmap.fromImage(image))

        self.queue._runner.submit(work(), done)

    def replace_map(self):
        q = self.queue
        if self._busy or q._runner.closed:
            return
        try:
            self.validate()
        except FormError as exc:
            feedback(q.page).notice("Map not replaced", str(exc), "error")
            return
        if self._pose()[1] is None:
            return
        draft, job, retained = q._draft, q._draft_job, q._draft_template_pose
        backend = q._backend()
        previous_epoch = self._epoch
        control = self._read_control()
        path = QFileDialog.getOpenFileName(q.page, "Replace Queue pose map (prepared body map only)", "", IMAGE_FILTER)[
            0
        ]
        if not path or q._runner.closed:
            return
        if (
            previous_epoch != self._epoch
            or q._draft is not draft
            or q._draft_job is not job
            or q._draft_template_pose is not retained
            or q._backend() != backend
            or not self.form.isVisible()
        ):
            feedback(q.page).notice(
                "Map not replaced",
                "The draft changed while choosing a map. Choose it again for the current draft.",
                "warning",
            )
            return
        request = draft[0]
        self._epoch += 1
        epoch = self._epoch
        self._busy = True
        self._preview_key = None
        self.sync()

        def worker():
            from dtflow.services.body_pose import fit_pose_map, load_prepared_map

            content, _ = load_prepared_map(Path(path))
            prepared, evidence = fit_pose_map(
                content,
                {"origin": "queue_replacement", "topology": "owner_supplied_openpose_body"},
                request.width_px,
                request.height_px,
            )
            return PoseJob(prepared.image, evidence, replace(control, evidence=()))

        async def work():
            return await settle(asyncio.to_thread(worker))

        def done(replacement, error):
            if q._runner.closed or epoch != self._epoch:
                return
            self._busy = False
            if (
                not self.form.isVisible()
                or q._draft is not draft
                or q._draft_job is not job
                or q._draft_template_pose is not retained
                or q._backend() != backend
            ):
                self.sync()
                return
            if error is not None:
                self.sync()
                q._plan_changed()
                feedback(q.page).notice(
                    "Map not replaced",
                    "The selected body map could not be prepared. The previous map and settings are unchanged.",
                    "error",
                )
                return
            # Fresh inputs only; never attach old execution references or the previous map's extraction evidence.
            request, *tail = q._draft
            q._draft = (replace(request, schema_version=2, pose_control=None, image_inputs=None), *tail)
            q._draft_job = replacement
            q._draft_template_pose = None
            self.draft_changed()
            self.sync()
            q._plan_changed()

        q._runner.submit(work(), done)

"""Connection middle-panel export hub; presentation only, no provider access."""

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QWidget

from dtflow.core.export import ExportOptions
from dtflow.core.gallery import GalleryQuery
from dtflow.core.models import ValidationError
from dtflow.persistence.export_preferences import load_export_preferences, save_export_preferences
from dtflow.services.image_export import export_outputs
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)


class ImageExportPanel(QObject):
    """One shared export operation; selected scopes are captured on the GUI thread."""

    progress = Signal(int, int)

    def __init__(self, form: QWidget, *, runner: AsyncRunner, state_dir: Path, selection: Callable):
        super().__init__(form)
        self.form, self.runner, self.state_dir = form, runner, Path(state_dir)
        self.selection = selection
        self._running = False
        self._preferences_dirty = False
        self._stop = threading.Event()
        self._exported_folder: Path | None = None
        self._inputs = (
            form.imageExportScopeCombo,
            form.imageExportFolderEdit,
            form.imageExportBrowseButton,
            form.imageExportPromptsCheck,
            form.imageExportSidecarsCheck,
            form.imageExportSeparateCheck,
        )
        for label, value in (
            ("All saved images", "all"),
            ("Current Images filter (all pages)", "filter"),
            ("Selected image in Images", "selected"),
            ("Selected Generate result", "generate"),
        ):
            form.imageExportScopeCombo.addItem(label, value)
        form.imageExportBrowseButton.clicked.connect(self.browse)
        form.imageExportStartButton.clicked.connect(self.start)
        form.imageExportCancelButton.clicked.connect(self.cancel)
        form.imageExportOpenButton.clicked.connect(self.open_folder)
        form.imageExportFolderEdit.textChanged.connect(self._mark_dirty)
        form.imageExportPromptsCheck.toggled.connect(self._mark_dirty)
        form.imageExportSidecarsCheck.toggled.connect(self._mark_dirty)
        form.imageExportSeparateCheck.toggled.connect(self._mark_dirty)
        form.imageExportProgress.hide()
        self.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        form.installEventFilter(self)
        self.runner.submit(asyncio.to_thread(load_export_preferences, self.state_dir), self._loaded)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Close:
            self.cancel()
        return False

    def _mark_dirty(self, *_) -> None:
        self._preferences_dirty = True

    def _loaded(self, document, failure) -> None:
        if self._preferences_dirty:
            return
        if failure:
            self.form.imageExportStatusLabel.setText("Saved export preferences could not be read; defaults are shown.")
            return
        if document:
            self.form.imageExportFolderEdit.setText(document["destination"])
            self.form.imageExportPromptsCheck.setChecked(document["include_prompts"])
            self.form.imageExportSidecarsCheck.setChecked(document["json_sidecars"])
            self.form.imageExportSeparateCheck.setChecked(document.get("separate_noise_blank", True))

    def browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self.form, "Choose image export folder", self.form.imageExportFolderEdit.text() or str(Path.home())
        )
        if folder:
            self.form.imageExportFolderEdit.setText(folder)

    def start(self) -> None:
        if self._running:
            return
        try:
            text = self.form.imageExportFolderEdit.text().strip()
            if not text:
                raise ValidationError("Choose an export folder first.")
            destination = Path(text).expanduser()
            if not destination.is_absolute():
                raise ValidationError("Use an absolute path for the export folder.")
            selection = self.selection(self.form.imageExportScopeCombo.currentData())
        except ValidationError as exc:
            self.form.imageExportStatusLabel.setText(str(exc))
            return
        options = ExportOptions(
            include_prompts=self.form.imageExportPromptsCheck.isChecked(),
            json_sidecars=self.form.imageExportSidecarsCheck.isChecked(),
            separate_noise_blank=self.form.imageExportSeparateCheck.isChecked(),
        )
        self._preferences_dirty = True
        self._stop = threading.Event()
        self._set_running(True)
        self.form.imageExportProgress.setRange(0, 0)
        self.form.imageExportStatusLabel.setText("Collecting saved image identities…")
        self.form.imageExportStatusLabel.setToolTip("")
        self.runner.submit(self._export(destination, options, selection, self._stop), self._done)

    async def _export(self, destination, options, selection, stop):
        preference_warning = False
        try:
            await asyncio.to_thread(
                save_export_preferences,
                self.state_dir,
                str(destination),
                options.include_prompts,
                options.json_sidecars,
                options.separate_noise_blank,
            )
        except (OSError, ValueError, ValidationError):
            preference_warning = True
        kwargs = {"query": selection} if isinstance(selection, GalleryQuery) else {"identities": selection}
        result = await export_outputs(
            self.state_dir,
            destination,
            options,
            cancel=stop,
            progress=self.progress.emit,
            **kwargs,
        )
        if preference_warning:
            result = replace(result, warnings=(*result.warnings, "Export preferences could not be saved."))
        return result

    def _set_running(self, value: bool) -> None:
        self._running = value
        for widget in self._inputs:
            widget.setEnabled(not value)
        self.form.imageExportStartButton.setEnabled(not value)
        self.form.imageExportCancelButton.setEnabled(value)
        self.form.imageExportProgress.setVisible(value)

    @Slot(int, int)
    def _progress(self, completed: int, total: int) -> None:
        if self._running:
            self.form.imageExportProgress.setRange(0, max(1, total))
            self.form.imageExportProgress.setValue(completed)
            if not self._stop.is_set():
                self.form.imageExportStatusLabel.setText(f"Processed {completed} of {total} images…")

    def cancel(self) -> None:
        if self._running:
            self._stop.set()
            self.form.imageExportCancelButton.setEnabled(False)
            self.form.imageExportStatusLabel.setText("Stopping after the current image; completed exports will remain…")

    def _done(self, result, failure) -> None:
        self._set_running(False)
        if failure:
            log.warning("image export stopped reason=%s", type(failure).__name__)
            message = (
                str(failure)
                if isinstance(failure, ValidationError)
                else "Export stopped; check destination permissions and saved outputs."
            )
            self.form.imageExportStatusLabel.setText(message)
            return
        prefix = "Export cancelled." if result.cancelled else "Export finished."
        if result.total == 0:
            text = "No saved images match this export scope."
        else:
            text = f"{prefix} {result.exported} exported, {result.failed} failed, {result.total - result.exported - result.failed} not processed."
        if result.noise or result.blank:
            text += f" Sorted into subfolders: {result.noise} noise, {result.blank} blank (advisory)."
        if result.warnings:
            text += " " + " ".join(result.warnings)
        self.form.imageExportStatusLabel.setText(text)
        self.form.imageExportStatusLabel.setToolTip("\n".join(result.failures))
        if result.exported:
            self._exported_folder = Path(result.destination)
            self.form.imageExportOpenButton.setEnabled(True)
        log.info(
            "image export completed exported=%d failed=%d cancelled=%s",
            result.exported,
            result.failed,
            result.cancelled,
        )

    def open_folder(self) -> None:
        if self._exported_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._exported_folder)))

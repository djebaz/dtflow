"""In-window, single-record LoRA metadata editor over Connections (#373).

Cancel or Escape closes it back to the expanded library with the same row selected. Save writes only the canonical local
metadata row; nothing is uploaded or renamed and no identifier or receipt changes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from dtflow.core.models import ValidationError
from dtflow.services.catalogue import FAMILY_LABELS
from dtflow.services.lora_convert.source_metadata import DetailModel
from dtflow.services.lora_metadata_edit import save_lora_metadata
from dtflow.services.lora_naming import architecture_shortcode
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.source_details_view import ElidedLabel, SourceDetailsPopup

_SHORT_NAME = re.compile(r"^[A-Za-z0-9_]{1,20}$")


def _load_form() -> QWidget:
    file = QFile(str(Path(__file__).with_name("forms") / "lora_metadata_editor.ui"))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError("LoRA metadata form unavailable.")
    try:
        form = QUiLoader().load(file)
    finally:
        file.close()
    if form is None:
        raise OSError("LoRA metadata form could not be loaded.")
    return form


class _Resize(QObject):
    def __init__(self, host: QWidget, overlay: QWidget):
        super().__init__(overlay)
        self.host, self.overlay = host, overlay
        host.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.overlay.setGeometry(self.host.rect())
        return False


class LoraMetadataOverlay(QObject):
    """Opens over ``host`` (the Connections center workspace); ``on_saved`` runs after a successful save."""

    def __init__(
        self,
        host: QWidget,
        runner: AsyncRunner,
        state_dir: Path,
        *,
        on_saved: Callable[[], None],
        on_closed: Callable[[], None],
    ):
        super().__init__(host)
        self.runner, self.state_dir, self._on_saved, self._on_closed = runner, Path(state_dir), on_saved, on_closed
        self.overlay = QFrame(host)
        self.overlay.setObjectName("loraMetadataOverlay")
        self.overlay.setStyleSheet("#loraMetadataOverlay { background-color: rgba(0, 0, 0, 150); }")
        self.overlay.setVisible(False)
        layout = QVBoxLayout(self.overlay)
        self.form = _load_form()
        layout.addStretch(1)
        layout.addWidget(self.form, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        _Resize(host, self.overlay)
        self._details_model = DetailModel()
        self._details_popup = SourceDetailsPopup(self.form)
        self._summary = ElidedLabel(self.form.loraMetadataSourceSummary)
        self.form.loraMetadataSourceToggle.clicked.connect(self._open_source)
        self.form.loraMetadataCancel.clicked.connect(self.close)
        self.form.loraMetadataSave.clicked.connect(self.save)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.overlay)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.close)
        self._identity: tuple[str, int] | None = None
        self._saving = False

    def _open_source(self) -> None:
        self._details_popup.show_above(self.form.loraMetadataSourceToggle, self._details_model, width=360)

    def is_open(self) -> bool:
        return self.overlay.isVisible()

    def open(
        self, record: dict, name: str, architectures: tuple[str, ...], source_details: DetailModel | None = None
    ) -> None:
        """Show one record. ``architectures`` are the backend-verified LoRA choices; the saved declaration stays listed.

        ``source_details`` is read-only content from the retained source metadata (#414); they are never editable.
        """
        form = self.form
        self._details_model = source_details or DetailModel()
        form.loraMetadataSourceToggle.setVisible(bool(self._details_model))
        form.loraMetadataSourceSummary.setVisible(bool(self._details_model))
        self._summary.set_text(self._details_model.line or "Declared by the source file: see Details")
        self._identity = (record["sha256"], record["size"])
        form.loraMetadataFile.setText(name)
        combo = form.loraMetadataArchitecture
        combo.clear()
        combo.addItem("Not declared (use file detection)", None)
        declared = record.get("declared_version")
        offered = tuple(
            dict.fromkeys(
                v for v in (*architectures, record.get("detected_version"), declared) if v and architecture_shortcode(v)
            )
        )
        for version in offered:
            combo.addItem(FAMILY_LABELS.get(version, version), version)
        combo.setCurrentIndex(max(0, combo.findData(declared)))
        detected = record.get("detected_version")
        evidence = (
            f"Detected from file: {FAMILY_LABELS.get(detected, detected)}."
            if detected
            else "No architecture could be detected from the file."
        )
        if declared and detected and declared != detected:
            evidence += " Your declaration wins for dispatch and the conflict stays recorded."
        form.loraMetadataArchitectureEvidence.setText(evidence)
        form.loraMetadataShortName.setText(record.get("short_name") or "")
        form.loraMetadataFormat.setCurrentIndex(
            max(0, form.loraMetadataFormat.findText(record.get("format_code") or ""))
        )
        form.loraMetadataTriggers.setText(record.get("trigger_words") or "")
        self._error("")
        form.loraMetadataSave.setEnabled(True)
        self.overlay.setGeometry(self.overlay.parentWidget().rect())
        self.overlay.raise_()
        self.overlay.setVisible(True)
        form.loraMetadataShortName.setFocus()

    def close(self) -> None:
        if self._saving:
            return
        self.overlay.setVisible(False)
        self._on_closed()

    def _error(self, text: str) -> None:
        self.form.loraMetadataError.setText(text)
        self.form.loraMetadataError.setVisible(bool(text))

    def save(self) -> None:
        if self._saving or self._identity is None or self.runner.closed:
            return
        form = self.form
        name = form.loraMetadataShortName.text().strip()
        if name and not _SHORT_NAME.fullmatch(name):
            self._error("Short name must be 1-20 ASCII letters, digits, or underscores.")
            return
        sha256, size = self._identity
        version = form.loraMetadataArchitecture.currentData()
        quantization = form.loraMetadataFormat.currentText()
        fields = {
            "declared_version": version,
            "short_name": name or None,
            "format_code": quantization if quantization in ("F16", "Q8P") else None,
            "trigger_words": form.loraMetadataTriggers.text(),
        }
        self._saving = True
        form.loraMetadataSave.setEnabled(False)

        def saved(_record, failure) -> None:
            self._saving = False
            if failure is not None:
                form.loraMetadataSave.setEnabled(True)
                self._error(str(failure) if isinstance(failure, ValidationError) else "The metadata was not saved.")
                return
            self.overlay.setVisible(False)
            self._on_saved()

        self.runner.submit(save_lora_metadata(self.state_dir, sha256, size, **fields), saved)

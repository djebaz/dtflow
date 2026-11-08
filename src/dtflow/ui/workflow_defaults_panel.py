"""Connection tab card: the workflow concurrency defaults (#286), kept in the shared app settings.

A workflow file or node that sets its own concurrency overrides these. Changes are saved as they are made and apply to
the next workflow run."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker
from PySide6.QtWidgets import QWidget

from dtflow.persistence.settings import (
    WORKFLOW_DEFAULTS,
    SettingsError,
    load_workflow_defaults,
    save_workflow_defaults,
)
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)


class WorkflowDefaultsPanel(QObject):
    def __init__(self, form: QWidget, runner: AsyncRunner, state_dir: Path):
        super().__init__(form)
        self.form, self.runner, self.state_dir = form, runner, Path(state_dir)
        self._spins = {
            "cloud": form.workflowCloudSpin,
            "local": form.workflowLocalSpin,
            "segmentation": form.workflowSegmentationSpin,
        }
        try:
            values = load_workflow_defaults(self.state_dir)
        except SettingsError:
            values = dict(WORKFLOW_DEFAULTS)
            for spin in self._spins.values():
                spin.setEnabled(False)  # an unreadable or newer settings file is left untouched
                spin.setToolTip("The settings file could not be read, so it is left unchanged.")
        for name, spin in self._spins.items():
            with QSignalBlocker(spin):
                spin.setValue(values[name])
            spin.valueChanged.connect(self._changed)

    def _changed(self, _value: int) -> None:
        values = {name: spin.value() for name, spin in self._spins.items()}

        async def save():
            await asyncio.to_thread(save_workflow_defaults, self.state_dir, values)

        def saved(_result, failure) -> None:
            if failure is not None:
                log.warning("The workflow concurrency defaults could not be saved: %s", type(failure).__name__)

        if not self.runner.closed:
            self.runner.submit(save(), saved)

"""Connection tab card: the Use LoRA trigger prefixes switches (#373), kept in the shared app settings.

One application default plus an independent saved choice for Generate, Queues, Workflows and Scripts. Each switch is its
own choice: changing one never changes another, and the default only seeds paths that have no choice of their own."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QWidget

from dtflow.persistence.settings import (
    SettingsError,
    load_trigger_choices,
    load_trigger_prefixes,
    save_trigger_prefix,
)
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)


class LoraTriggerPanel(QObject):
    def __init__(self, form: QWidget, runner: AsyncRunner, state_dir: Path, generate_check: QCheckBox):
        super().__init__(form)
        self.runner, self.state_dir, self._generate_page_check = runner, Path(state_dir), generate_check
        self._checks = {
            "default": form.loraTriggerDefaultCheck,
            "generate": form.loraTriggerGenerateCheck,
            "queues": form.loraTriggerQueuesCheck,
            "workflows": form.loraTriggerWorkflowsCheck,
            "scripts": form.loraTriggerScriptsCheck,
        }
        self._explicit: set[str] = set()
        try:
            values = load_trigger_prefixes(self.state_dir)
            self._explicit = set(load_trigger_choices(self.state_dir)[1])
        except SettingsError:
            values = dict.fromkeys(self._checks, False)
            for check in self._checks.values():
                check.setEnabled(False)  # an unreadable or newer settings file is left untouched
                check.setToolTip("The settings file could not be read, so it is left unchanged.")
            generate_check.setEnabled(False)
        for name, check in self._checks.items():
            with QSignalBlocker(check):
                check.setChecked(values[name])
            check.toggled.connect(lambda checked, name=name: self._changed(name, checked))
        with QSignalBlocker(generate_check):
            generate_check.setChecked(values["generate"])
        generate_check.toggled.connect(lambda checked: self._changed("generate", checked, from_generate=True))

    def _changed(self, name: str, checked: bool, *, from_generate: bool = False) -> None:
        if name == "default":  # paths without a choice of their own follow it; saved choices stay as they are
            for path, check in self._checks.items():
                if path != "default" and path not in self._explicit:
                    with QSignalBlocker(check):
                        check.setChecked(checked)
            if "generate" not in self._explicit:
                with QSignalBlocker(self._generate_page_check):
                    self._generate_page_check.setChecked(checked)
        else:
            self._explicit.add(name)
        if name == "generate":  # Generate's own control and this card show the one saved choice
            other = self._checks["generate"] if from_generate else self._generate_page_check
            with QSignalBlocker(other):
                other.setChecked(checked)

        async def save():
            await asyncio.to_thread(save_trigger_prefix, self.state_dir, name, checked)

        def saved(_result, failure) -> None:
            if failure is not None:
                log.warning("The LoRA trigger prefix setting could not be saved: %s", type(failure).__name__)

        if not self.runner.closed:
            self.runner.submit(save(), saved)

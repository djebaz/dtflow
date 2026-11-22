"""Connection tab card: local video encoding preferences (#550), kept in the shared app settings.

Max concurrent video encoders (0 = Unlimited) and FFmpeg threads per encoder (0 = Auto). A change is applied to the
next encoder that starts and saved as it is made; an encoder that is already running is never retuned.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker
from PySide6.QtWidgets import QWidget

from dtflow.persistence.settings import SettingsError, load_video_encoding, save_video_encoding
from dtflow.services import video_encode
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)


class VideoEncodingPanel(QObject):
    def __init__(self, form: QWidget, runner: AsyncRunner, state_dir: Path):
        super().__init__(form)
        self.runner, self.state_dir = runner, Path(state_dir)
        self._encoders, self._threads = form.videoEncodersSpin, form.videoThreadsSpin
        self._saving, self._latest = False, (0, 0)
        try:
            encoders, threads = load_video_encoding(self.state_dir)
        except SettingsError:
            encoders = threads = 0
            for spin in (self._encoders, self._threads):
                spin.setEnabled(False)  # an unreadable or newer settings file is left untouched
                spin.setToolTip("The settings file could not be read, so it is left unchanged.")
        for spin, value in ((self._encoders, encoders), (self._threads, threads)):
            with QSignalBlocker(spin):
                spin.setValue(value)
            spin.valueChanged.connect(self._changed)
        video_encode.set_policy(encoders, threads)

    def _changed(self, _value: int) -> None:
        encoders, threads = self._encoders.value(), self._threads.value()
        video_encode.set_policy(encoders, threads)
        self._latest = encoders, threads
        if not self._saving and not self.runner.closed:
            self._save_latest()

    def _save_latest(self) -> None:
        """One save at a time; values changed meanwhile are saved right after, so the newest always lands last."""
        encoders, threads = saved_values = self._latest

        async def save():
            await asyncio.to_thread(save_video_encoding, self.state_dir, encoders, threads)

        def saved(_result, failure) -> None:
            self._saving = False
            if failure is not None:
                log.warning("The video encoding settings could not be saved: %s", type(failure).__name__)
            if self._latest != saved_values and not self.runner.closed:
                self._save_latest()

        self._saving = True
        self.runner.submit(save(), saved)

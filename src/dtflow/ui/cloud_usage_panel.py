"""Connection card: local completed DT+ images per API key for one calendar day in a selectable timezone.

Presentation only. Records come from durable saved-attempt evidence through the read-only persistence reader; changing
the timezone recomputes from the already-read records without any database, provider or generation work.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from dtflow.persistence.cloud_usage import load_timezone, read_usage_records, save_timezone
from dtflow.persistence.store import PersistenceError
from dtflow.services.cloud_usage import (
    AMBER_FROM,
    DEFAULT_TIMEZONE,
    RED_FROM,
    DayCounts,
    KeyCount,
    UsageRecord,
    count_day,
    timezone_choices,
    zone_or_default,
)
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)

_STATE = {"ok": "ok", "warn": "warning", "over": "error"}
_BAND = {
    "ok": f"below {AMBER_FROM}",
    "warn": f"{AMBER_FROM} to {RED_FROM - 1}",
    "over": f"{RED_FROM} or more",
}
REFRESH_DEBOUNCE_MS = 400


class CloudUsagePanel(QObject):
    def __init__(
        self,
        form: QWidget,
        runner: AsyncRunner,
        state_dir: Path,
        *,
        active_credential: Callable[[], Awaitable[tuple[str, str | None] | None]] | None = None,
        key_directory: Callable[[], dict[str, str]] | None = None,
        on_active: Callable[[str | None, int, int, str, str], None] | None = None,
        on_unavailable: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        super().__init__(form)
        self.form, self.runner, self.state_dir = form, runner, Path(state_dir)
        self._active_credential, self._on_active, self._clock = active_credential, on_active, clock
        self._on_unavailable = on_unavailable
        self._key_directory = key_directory
        self._directory: dict[str, str] = {}
        self._records: tuple[UsageRecord, ...] = ()
        self._active: str | None = None
        self.active_label: str | None = None
        self._error = False
        self._error_reason: str | None = None
        self._loaded = False
        self._token: object | None = None
        self.counts: DayCounts | None = None
        combo = form.usageTimezoneCombo
        combo.custom_label = None  # a fixed list of IANA zones, not free text
        combo.custom_prompt = "Timezone"
        combo.setAccessibleName("Daily reset timezone")
        chosen = load_timezone(self.state_dir)
        for label, zone in timezone_choices(self._clock()):
            combo.addItem(label, zone)
        index = combo.findData(chosen)
        if index < 0:  # a valid zone that is not offered (for example EST or Etc/UTC): use the default, not item 0
            index = combo.findData(DEFAULT_TIMEZONE)
        combo.setCurrentIndex(max(index, 0))
        combo.currentIndexChanged.connect(self._zone_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(REFRESH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.refresh)
        self.render()

    @property
    def zone_name(self) -> str:
        return self.form.usageTimezoneCombo.currentData() or "UTC"

    def schedule_refresh(self) -> None:
        if not self.runner.closed:
            self._debounce.start()

    def refresh(self) -> None:
        """Re-read saved evidence and the active key identity; local only, never a provider call."""
        if self.runner.closed:
            return
        token = self._token = object()
        now = self._clock()

        async def read():
            records = await asyncio.to_thread(read_usage_records, self.state_dir, now)
            active = None
            if self._active_credential is not None:
                try:
                    active = await self._active_credential()
                except Exception:
                    active = None
            directory = {}
            if self._key_directory is not None:
                try:
                    directory = await asyncio.to_thread(self._key_directory)
                except Exception:
                    directory = {}
            return records, active, directory

        self.runner.submit(read(), lambda result, failure: self._read(token, result, failure))

    def _read(self, token, result, failure) -> None:
        if token is not self._token:
            return
        if failure is not None:
            reason = str(failure) if isinstance(failure, PersistenceError) else type(failure).__name__
            log.warning("Local DT+ usage could not be read: %s", reason)
            self._error = True
            self._error_reason = reason if isinstance(failure, PersistenceError) else None
        else:
            self._records, identity, self._directory = result
            self._active = identity[0] if identity else None
            self.active_label = (
                None
                if identity is None
                else self._directory.get(identity[0])
                or (f"Key {identity[1]}\u2022\u2022\u2022\u2022" if identity[1] else f"Key {identity[0][:4].upper()}")
            )
            self._error, self._loaded = False, True
            self._error_reason = None
        self.render()

    def _zone_changed(self, _index: int) -> None:
        zone = self.zone_name
        self.render()  # immediate, local recomputation

        async def remember():
            await asyncio.to_thread(save_timezone, self.state_dir, zone)

        def saved(_result, failure) -> None:
            if failure is not None:
                log.warning("The usage timezone could not be remembered: %s", type(failure).__name__)

        if not self.runner.closed:
            self.runner.submit(remember(), saved)

    def _refresh_clock_labels(self) -> None:
        """Keep each entry's current local time fresh without changing the selection or emitting a change."""
        combo = self.form.usageTimezoneCombo
        with QSignalBlocker(combo):
            for label, zone in timezone_choices(self._clock()):
                index = combo.findData(zone)
                if index >= 0 and combo.itemText(index) != label:
                    combo.setItemText(index, label)

    def render(self) -> None:
        form = self.form
        self._refresh_clock_labels()
        zone = zone_or_default(self.zone_name)
        counts = self.counts = count_day(
            self._records, self._clock(), zone, active_credential=self._active, directory=self._directory
        )
        start = counts.start.astimezone(zone)
        if self._error and self._on_unavailable is not None:
            self._on_unavailable()
        elif self._on_active is not None:
            mine = next((key for key in counts.keys if key.active), None)
            total = sum(key.count for key in counts.keys)
            self._on_active(self.active_label, mine.count if mine else 0, total, mine.level if mine else "ok", zone.key)
        form.usageDayLabel.setText(f"{start:%a %d %b %Y}, local midnight to midnight ({zone.key}, UTC{start:%z})")
        layout = form.usageRowsLayout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for key in counts.keys:
            layout.addWidget(self._row(key))
        if self._error:
            status = "Previous counts may be outdated." if self._loaded else "Saved image counts are unavailable."
            note = f"{status} {self._error_reason or 'See Logs for the error type.'}"
        elif not self._loaded:
            note = "Reading saved images…"
        else:
            note = "No DT+ images were saved on this day."
        form.usageEmptyLabel.setText(note)
        form.usageEmptyLabel.setVisible(self._error or not counts.keys)
        if counts.unattributed:
            form.usageUnattributedLabel.setText(
                f"{counts.unattributed} more were saved before key tracking began, so they belong to none of the keys above."
            )
        form.usageUnattributedLabel.setVisible(bool(counts.unattributed))

    def _row(self, key: KeyCount) -> QWidget:
        row = QFrame(self.form.usageBoxContents)
        row.setProperty("role", "usageRow")
        description = f"{key.label}: {key.count} completed images, {_BAND[key.level]} band"
        row.setAccessibleName(description)
        row.setToolTip("Local count of saved final images for this key, not a DT+ balance.")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        name = QLabel(key.label, row)
        name.setProperty("textRole", "value")
        layout.addWidget(name)
        if key.active:
            chip = QLabel("Active", row)
            chip.setProperty("role", "statusChip")
            chip.setProperty("state", "secondary")
            layout.addWidget(chip)
        layout.addStretch(1)
        count = QLabel(str(key.count), row)
        count.setObjectName("usageCount")
        count.setProperty("textRole", "value")
        count.setProperty("state", _STATE[key.level])
        count.setAccessibleDescription(description)
        layout.addWidget(count)
        return row

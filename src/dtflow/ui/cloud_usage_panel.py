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
        on_active: Callable[[str | None], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        super().__init__(form)
        self.form, self.runner, self.state_dir = form, runner, Path(state_dir)
        self._active_credential, self._on_active, self._clock = active_credential, on_active, clock
        self._records: tuple[UsageRecord, ...] = ()
        self._active: str | None = None
        self.active_label: str | None = None
        self._error = False
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
            return records, active

        self.runner.submit(read(), lambda result, failure: self._read(token, result, failure))

    def _read(self, token, result, failure) -> None:
        if token is not self._token:
            return
        if failure is not None:
            log.warning("Local DT+ usage could not be read: %s", type(failure).__name__)
            self._error = True
        else:
            self._records, identity = result
            self._active = identity[0] if identity else None
            self.active_label = (
                None
                if identity is None
                else f"Key {identity[1]}\u2022\u2022\u2022\u2022"
                if identity[1]
                else "Key (unidentified)"
            )
            if self._on_active is not None:
                self._on_active(self.active_label)
            self._error, self._loaded = False, True
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
        counts = self.counts = count_day(self._records, self._clock(), zone, active_credential=self._active)
        start = counts.start.astimezone(zone)
        form.usageDayLabel.setText(f"{start:%a %d %b %Y}, local midnight to midnight ({zone.key}, UTC{start:%z})")
        layout = form.usageRowsLayout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for key in counts.keys:
            layout.addWidget(self._row(key))
        if self._error:
            note = "Saved image records could not be read; counts are unavailable."
        elif not self._loaded:
            note = "Reading saved images…"
        else:
            note = "No DT+ images were saved on this day."
        form.usageEmptyLabel.setText(note)
        form.usageEmptyLabel.setVisible(not counts.keys)
        if counts.unattributed:
            form.usageUnattributedLabel.setText(
                f"{counts.unattributed} more saved before key tracking began; they cannot be attributed to a key "
                "and are not counted above."
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

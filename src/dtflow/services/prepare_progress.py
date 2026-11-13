"""Headless progress model for one Prepare run (a batch of LoRA files); Qt-free, no I/O.

The asset service reports ``PrepareEvent`` values; the panel feeds them here and renders ``snapshot()`` into the
shared activity bar. Everything is measured on this machine: DT+ reports no progress of its own.

Phases per file: optional ``converting`` for safetensors, then ``checking`` (measure, retain, negotiate) -> ``uploading`` (only when DT+ says ``upload_required``) -> ``confirming``.
The global fill is weighted by bytes through ``PHASE_COST`` (relative cost per byte, tuned from the logged phase
timings) and never moves backwards. Upload progress counts bytes handed to the HTTP client, so it is capped at 99 %
until the PUT returned successfully.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

PHASES = ("inspecting", "checking", "converting", "uploading", "confirming")
# Relative cost per byte of each phase. Initial guesses; "ui.asset_prepare_timing" logs the measured seconds per MiB.
# "checking" is mostly the kept copy: the content hash is cached from the inspecting read.
PHASE_COST = {"inspecting": 0.2, "checking": 0.1, "converting": 0.4, "uploading": 1.0, "confirming": 0.02}

MIB = 1024 * 1024
UPLOAD_CAP = 0.99
MIN_RATE_BYTES = 8 * MIB  # no estimate before this much of the run has been uploaded
MIN_RATE_SECONDS = 3.0  # ...or before the upload has been measured this long
STALL_SECONDS = 10.0  # no bytes for this long: "Waiting for DT+"
RATE_WINDOW = 10.0  # seconds; time constant of the moving average
SET_CHANGE_HIDE = 2.0  # seconds without an estimate after a file turned out to exist
MIN_ETA_SECONDS = 5.0  # a smaller estimate is shown as the stage instead

# Stages reported by the asset service.
CHECKING, UPLOADING, CONFIRMING, EXISTS, CONVERTING = "checking", "uploading", "confirming", "exists", "converting"


@dataclass(frozen=True, slots=True)
class PrepareEvent:
    """One stage change of the file being prepared. ``done``/``total`` are bytes (uploading) or work units."""

    stage: str
    done: int = 0
    total: int = 0
    label: str = ""  # bounded, UI-authored phase label; never source paths or converter text
    output_size: int = 0  # actual converted checkpoint bytes when known


@dataclass(slots=True)
class _File:
    name: str
    size: int
    stage: str = "pending"  # pending, a phase name, "exists" or "settled"
    frac: dict[str, float] = field(default_factory=dict)  # phase -> completed fraction 0..1
    skip: set[str] = field(default_factory=set)  # phases this file will not have
    started: dict[str, float] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    uploaded: int = 0
    upload_total: int = 0
    convert_label: str = ""
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class PrepareSnapshot:
    fraction: float | None  # None = unknown (striped bar)
    settled: int
    total: int
    stage: str
    wide: str
    elapsed: float
    eta: float | None  # seconds; None = hidden
    stalled: bool = False
    count: str = ""  # files finished / files in this step ("2/5")


def _fmt_rate(rate: float) -> str:
    return f"{rate / MIB:.1f} MiB/s"


class PrepareProgress:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._files: list[_File] = []
        self._current: int | None = None
        self._began = clock()
        self._shown = 0.0
        self._rate: float | None = None
        self._rate_at: float | None = None
        self._rate_bytes = 0  # bytes uploaded in this run (all files)
        self._pending_bytes = 0
        self._upload_began: float | None = None
        self._last_bytes_at: float | None = None
        self._hide_until = 0.0
        self._inspection: tuple[int, int, int, int] | None = None  # bytes read, bytes total, files done, files
        self._inspect_times: list[float] = []  # first and last inspection report, for the timing diagnostic
        self._inspect_mib = 0.0

    # -- feeding ---------------------------------------------------------------------------------------------------
    def inspect(self, done_bytes: int, total_bytes: int, finished: int, files: int) -> None:
        """Before the run: bytes read so far while the selected files are inspected (hashed). Part of the same fill."""
        self._inspection = (done_bytes, total_bytes, finished, files)
        now = self._clock()
        if not self._inspect_times:
            self._inspect_times = [now, now]
        self._inspect_times[1] = now
        self._inspect_mib = total_bytes / MIB

    def end_inspection(self) -> None:
        self._inspection = None

    def begin_run(self) -> None:
        """The user admitted files: elapsed time counts from now (not from when the editor opened); the fill keeps
        its place and never moves backwards."""
        self._began = self._clock()
        self._inspection = None

    def add_file(self, path: str | Path, size: int, *, convert: bool = False) -> int:
        """Register one more file of the run; returns its index. ``convert`` declares a convert phase.

        A file reaches the run only after it was inspected, so its inspecting phase is complete; files that are
        never admitted are never added and so leave the totals."""
        file = _File(Path(path).name, max(0, int(size)))
        file.frac["inspecting"] = 1.0
        if not convert:
            file.skip.add("converting")
        self._files.append(file)
        return len(self._files) - 1

    def start_file(self, index: int, *, convert: bool = False) -> None:
        self._current = index
        file = self._files[index]
        self._enter(file, CONVERTING if convert else CHECKING)

    def _enter(self, file: _File, phase: str) -> None:
        now = self._clock()
        previous = file.stage
        if previous in PHASES and previous != phase and previous in file.started:
            file.seconds[previous] = file.seconds.get(previous, 0.0) + now - file.started[previous]
        file.stage = phase
        file.started.setdefault(phase, now)

    def event(self, index: int, event: PrepareEvent) -> None:
        """Apply the newest event of file ``index``; stale events for a settled file are ignored."""
        if not 0 <= index < len(self._files):
            return
        file = self._files[index]
        if file.stage == "settled":
            return
        now = self._clock()
        stage = event.stage
        if event.output_size > 0:
            file.size = event.output_size
        if stage == UPLOADING:
            if file.stage in ("pending", CHECKING, CONVERTING):
                if file.stage == CONVERTING:
                    file.frac[CONVERTING] = 1.0
                file.frac[CHECKING] = 1.0
                self._enter(file, UPLOADING)
            elif file.stage != UPLOADING:
                return  # a late byte report after confirming began
            total = event.total or file.size
            file.upload_total = total
            fraction = min(event.done / total, UPLOAD_CAP) if total else 0.0
            file.frac[UPLOADING] = max(file.frac.get(UPLOADING, 0.0), fraction)
            self._byte_progress(file, event.done, now)
        elif stage == EXISTS:
            file.frac[CONVERTING] = 1.0
            file.frac[CHECKING] = 1.0
            file.skip |= {"uploading", "confirming"}
            self._enter(file, EXISTS)
            self._hide_until = now + SET_CHANGE_HIDE
        elif stage == CONFIRMING:
            file.frac[CONVERTING] = 1.0
            file.frac[CHECKING] = 1.0
            file.frac[UPLOADING] = 1.0
            self._enter(file, CONFIRMING)
        elif stage == CONVERTING:
            if file.stage not in ("pending", CHECKING, CONVERTING):
                return
            self._enter(file, CONVERTING)
            if event.label:
                file.convert_label = event.label
            if event.total:
                file.frac[CONVERTING] = max(
                    file.frac.get(CONVERTING, 0.0), min(max(event.done / event.total, 0.0), 1.0)
                )
        elif stage == CHECKING:
            if file.stage not in ("pending", CHECKING, CONVERTING):
                return
            if file.stage == CONVERTING:
                file.frac[CONVERTING] = 1.0
            self._enter(file, CHECKING)
            if event.total:
                # Two byte passes (hash, kept copy); the phase completes when DT+ has answered.
                file.frac[CHECKING] = max(file.frac.get(CHECKING, 0.0), min(event.done / event.total, UPLOAD_CAP))

    def _byte_progress(self, file: _File, done: int, now: float) -> None:
        delta = done - file.uploaded
        file.uploaded = done
        if delta <= 0:
            return
        if self._upload_began is None:
            self._upload_began = now
        self._last_bytes_at = now
        self._rate_bytes += delta
        self._pending_bytes += delta
        if self._rate_at is None:
            self._rate_at = now
            self._pending_bytes = 0
            return
        dt = now - self._rate_at
        if dt >= 0.25:
            instant = self._pending_bytes / dt
            weight = 1 - math.exp(-dt / RATE_WINDOW)
            self._rate = instant if self._rate is None else self._rate + weight * (instant - self._rate)
            self._rate_at, self._pending_bytes = now, 0

    def settle(self, index: int, outcome: str) -> dict:
        """The file ended (``confirmed``, ``failed`` or ``unresolved``). Returns its phase timings for diagnostics."""
        file = self._files[index]
        if file.stage != "settled":
            now = self._clock()
            if file.stage in PHASES and file.stage in file.started:
                file.seconds[file.stage] = file.seconds.get(file.stage, 0.0) + now - file.started[file.stage]
            if outcome == "confirmed":
                for phase in PHASES:
                    if phase not in file.skip:
                        file.frac[phase] = 1.0
            else:
                # A file that ended early leaves the totals: its unfinished phases no longer count.
                file.skip |= {phase for phase in PHASES if file.frac.get(phase, 0.0) < 1.0}
            file.stage, file.outcome = "settled", outcome
        mib = file.size / MIB
        data: dict = {"size_mib": round(mib, 1), "seconds": {p: round(s, 2) for p, s in file.seconds.items()}}
        if self._inspect_times and self._inspect_mib > 0:
            # The whole batch's inspection read (all selected files), to calibrate the "inspecting" weight.
            seconds = self._inspect_times[1] - self._inspect_times[0]
            data["inspection"] = {
                "seconds": round(seconds, 2),
                "mib": round(self._inspect_mib, 1),
                "seconds_per_mib": round(seconds / self._inspect_mib, 4),
            }
        if mib > 0:
            data["seconds_per_mib"] = {p: round(s / mib, 4) for p, s in file.seconds.items()}
        return data

    # -- reading ---------------------------------------------------------------------------------------------------
    @property
    def settled(self) -> int:
        return sum(1 for file in self._files if file.stage == "settled")

    def _weights(self, file: _File) -> tuple[float, float]:
        total = done = 0.0
        for phase in PHASES:
            if phase in file.skip:
                continue
            weight = file.size * PHASE_COST[phase]
            total += weight
            done += weight * file.frac.get(phase, 0.0)
        return total, done

    def _fraction(self) -> float | None:
        if not self._files and self._inspection is not None and self._inspection[1] > 0:
            # Only inspection has happened: every selected file is assumed to go through all later phases.
            read, size = self._inspection[:2]
            rest = sum(PHASE_COST[p] for p in ("checking", "uploading", "confirming"))
            part = read * PHASE_COST["inspecting"] / (size * (PHASE_COST["inspecting"] + rest))
            self._shown = max(self._shown, min(1.0, part))
            return self._shown
        total = done = 0.0
        for file in self._files:
            file_total, file_done = self._weights(file)
            total += file_total
            done += file_done
        if total <= 0:
            return None
        self._shown = max(self._shown, min(1.0, done / total))
        return self._shown

    def _stalled(self, now: float) -> bool:
        current = self._files[self._current] if self._current is not None else None
        if current is None or current.stage != UPLOADING:
            return False
        began = self._last_bytes_at if self._last_bytes_at is not None else current.started.get(UPLOADING, now)
        return now - max(began, current.started.get(UPLOADING, began)) >= STALL_SECONDS

    def _eta(self, now: float, stalled: bool) -> float | None:
        if stalled or not self._rate or now < self._hide_until:
            return None
        if (
            self._upload_began is None
            or now - self._upload_began < MIN_RATE_SECONDS
            or self._rate_bytes < MIN_RATE_BYTES
        ):
            return None
        remaining = 0.0
        for file in self._files:
            if file.stage == "settled" or "uploading" in file.skip:
                continue
            if file.stage == UPLOADING:
                remaining += max(0, (file.upload_total or file.size) - file.uploaded)
            elif file.stage in ("pending", CHECKING, CONVERTING):
                remaining += file.size  # assumed to upload (pessimistic; shrinks when a file already exists)
        current = self._files[self._current] if self._current is not None else None
        if current is not None and current.stage == UPLOADING and (current.upload_total or current.size):
            if current.uploaded < 0.05 * (current.upload_total or current.size) and current.uploaded < MIN_RATE_BYTES:
                return None
        seconds = remaining / self._rate
        return None if seconds < MIN_ETA_SECONDS else seconds

    def snapshot(self) -> PrepareSnapshot:
        now = self._clock()
        total = len(self._files)
        settled = self.settled
        fraction = self._fraction()
        current = self._files[self._current] if self._current is not None else None
        stalled = self._stalled(now)
        stage = wide = ""
        count = f"{settled}/{total}"
        if not self._files and self._inspection is not None:
            read, size, finished, files = self._inspection
            count = f"{finished}/{files}"
            percent = int(100 * read / size) if size else 0
            stage = f"Inspecting {finished}/{files} · {percent}%"
            wide = f"Inspecting {files} file{'s' if files != 1 else ''} · {read / MIB:.0f} / {size / MIB:.0f} MiB"
        if current is not None and current.stage != "settled":
            position = f"{(self._current or 0) + 1}/{total}"
            if current.stage == UPLOADING:
                percent = int(min(current.frac.get(UPLOADING, 0.0), UPLOAD_CAP) * 100)
                amount = f"{current.uploaded / MIB:.0f} / {(current.upload_total or current.size) / MIB:.0f} MiB"
                rate = f" · {_fmt_rate(self._rate)}" if self._rate else ""
                if stalled:
                    gap = int(now - (self._last_bytes_at or current.started.get(UPLOADING, now)))
                    stage = f"Waiting for DT+ · no data for {gap} s"
                    wide = f"Waiting for DT+ · {current.name} · {amount} · no data for {gap} s"
                else:
                    stage = f"Uploading {position} · {percent}%"
                    wide = f"Uploading {current.name} · {amount}{rate}"
            elif current.stage == CONFIRMING:
                stage = "Confirming on DT+"
                wide = f"Confirming {current.name} on DT+"
            elif current.stage == EXISTS:
                stage = "Already on DT+"
                wide = f"{current.name} is already on DT+"
            elif current.stage == CONVERTING:
                percent = int(current.frac.get(CONVERTING, 0.0) * 100)
                label = current.convert_label or "Converting"
                stage = f"{label} {position}" + (f" · {percent}%" if percent else "")
                wide = f"{label} {current.name}" + (f" · {percent}%" if percent else "")
            else:
                percent = int(current.frac.get(CHECKING, 0.0) * 100)
                stage = f"Checking {position}" + (f" · {percent}%" if percent else "")
                wide = f"Checking {current.name}" + (f" · {percent}%" if percent else "")
        return PrepareSnapshot(
            fraction,
            settled,
            total,
            stage,
            wide,
            now - self._began,
            self._eta(now, stalled),
            stalled,
            count,
        )

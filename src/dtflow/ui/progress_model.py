"""One Qt-free model of generation progress: per-item records fed by backend events, an immutable snapshot of them.

An item is keyed by ``(queue_id, item_id)``. Its attempt is the ``request_id`` of the events it receives (a retry runs
under a new identity); ``sequence`` orders the events of one attempt. Events only describe the live part of a run:
stage, sampler steps, received frames and a bounded whole-item estimate. Settled counts and outcomes always come from the
durable queue views handed to ``snapshot``/``reconcile``; a terminal event never counts as success.

The model calculates and carries no colours, pixel values or text layout; presentation decides how to paint it. It has no
timers, threads, persistence or event bus: it changes only when ``observe``/``reconcile`` are called.
"""

from __future__ import annotations

import math
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from dtflow.core.models import BackendEvent
from dtflow.ui.progress_stages import event_stage

# One stage vocabulary. ``phase`` is the observed stage of an item; every phase has at most one code, and the bar word
# keeps the wording the Activity Bar uses today for that phase.
STAGE_CODES = {
    "W": "waiting for a slot or local resources",
    "C": "DT+ Cloud processing",
    "S": "sampling",
    "R": "receiving",
    "E": "encoding",
    "V": "saving",
}
PHASE_CODE = {
    "waiting": "W",
    "cloud": "C",
    "sampling": "S",
    "second_pass": "S",
    "receiving": "R",
    "encoder_wait": "E",
    "encoding": "E",
    "verifying": "E",
    "saving": "V",
}  # "preparing" (before the request is sent) has no code
PHASE_WORDS = {
    "preparing": "preparing",
    "waiting": "waiting for local video resources",
    "cloud": "DT+ Cloud processing",
    "sampling": "sampling",
    "second_pass": "second pass",
    "receiving": "receiving",
    "encoder_wait": "waiting for a local video encoder",
    "encoding": "finalizing",
    "verifying": "finalizing",
    "saving": "saving",
}

# Window of the whole-item fraction owned by each phase, and the time constant (seconds) used while the phase reports no
# counter. Values are rough shares of a typical run, not measurements.
VIDEO_WINDOWS = {
    "preparing": (0.00, 0.03, 6.0),
    "waiting": (0.03, 0.50, 60.0),
    "cloud": (0.03, 0.50, 60.0),
    "sampling": (0.50, 0.80, 120.0),
    "second_pass": (0.70, 0.80, 60.0),
    "receiving": (0.80, 0.93, 40.0),
    "encoder_wait": (0.93, 0.94, 30.0),
    "encoding": (0.94, 0.98, 10.0),
    "verifying": (0.98, 0.99, 3.0),
    "saving": (0.99, 0.995, 3.0),
}
IMAGE_WINDOWS = {
    "preparing": (0.00, 0.05, 6.0),
    "waiting": (0.05, 0.10, 60.0),
    "cloud": (0.05, 0.10, 60.0),
    "sampling": (0.10, 0.90, 120.0),
    "second_pass": (0.10, 0.90, 60.0),
    "receiving": (0.90, 0.95, 10.0),
    "encoder_wait": (0.95, 0.96, 10.0),
    "encoding": (0.96, 0.98, 10.0),
    "verifying": (0.98, 0.99, 3.0),
    "saving": (0.99, 0.995, 3.0),
}
CEILING = 0.99  # an estimate stays below 1 until the durable queue view shows the item settled
BEAT_SECONDS = 10.0  # observed spacing of DT+'s messages while it works without reporting a step
_SUPERSEDED_KEPT = 8


def _counter(event: BackendEvent) -> tuple[int, int] | None:
    completed, total = event.completed, event.total
    if type(completed) is int and type(total) is int and total > 0 and 0 <= completed <= total:
        return completed, total
    return None


class ItemProgress:
    """Live evidence of one item's current attempt (mutable, owned by ``ProgressModel``)."""

    def __init__(self, queue_id: str, item_id: str, request_id: str, mode: str, now: float) -> None:
        self.queue_id = queue_id
        self.item_id = item_id
        self.request_id = request_id
        self.last_sequence = 0
        self.mode = mode  # "image" or "video"
        self.superseded: list[str] = []
        self._begin(now)

    def _begin(self, now: float) -> None:
        self.started = now
        self.sent_at: float | None = None
        self.phase = "preparing"
        self.text = "Preparing"
        self.done: int | None = None
        self.total: int | None = None
        self.bytes = 0
        self.steps: tuple[int, int] | None = None  # last observed sampler step counter
        self.frames: tuple[int, int] | None = None  # last observed received-frame counter
        self.counter: tuple[int, int] | None = None  # last valid counter of any kind, until the item settles
        self.label = ""  # last observed stage wording, until a terminal event of an image
        self.label_since: float | None = None
        self.phase_started = now
        self.beats = 0
        self.last_beat = now
        self.observed = False
        self.event_label = ""  # wording of the last event folded in
        self.presented = False  # whether that event is one a video run presents
        self._peak = 0.0

    @property
    def code(self) -> str:
        return PHASE_CODE.get(self.phase, "")

    @property
    def video(self) -> bool:
        return self.mode == "video"

    def elapsed(self, now: float) -> float:
        return max(0.0, now - (self.sent_at if self.sent_at is not None else self.started))

    def estimate(self, now: float | None = None) -> float:
        """Bounded whole-item fraction: 0 until something is observed, then 0..0.99, never decreasing in an attempt."""
        if not self.observed:
            return 0.0
        now = time.monotonic() if now is None else now
        windows = VIDEO_WINDOWS if self.video else IMAGE_WINDOWS
        low, high, tau = windows.get(self.phase, windows["preparing"])
        if self.done is not None and self.total:
            value = low + (high - low) * min(1.0, max(0.0, self.done / self.total))
        else:
            elapsed = max(0.0, now - self.phase_started)
            if self.phase == "cloud" and self.beats:
                # Advance with DT+'s own messages: between two of them for at most one beat interval, so a silent
                # connection stops moving the fill instead of creeping on.
                elapsed = min(elapsed, self.beats * BEAT_SECONDS + min(BEAT_SECONDS, max(0.0, now - self.last_beat)))
            value = low + (high - low) * (1.0 - math.exp(-elapsed / tau))
        self._peak = max(self._peak, min(CEILING, value))
        return self._peak

    @property
    def count_text(self) -> str:
        """Short observed counter of the current phase, empty when it reports none."""
        if self.done is None or not self.total:
            return ""
        unit = {"sampling": "steps", "second_pass": "steps", "receiving": "frames"}.get(self.phase)
        return f"{self.done}/{self.total} {unit}" if unit else ""

    def _observe(self, event: BackendEvent, now: float) -> bool:
        """Fold one event in; True when it is one a video run presents."""
        self.observed = True
        if event.kind == "progress" and event.stage == "waiting_heartbeat":
            if self.phase == "cloud":
                self.beats += 1
                self.last_beat = now
            return True  # text unchanged: callers keep showing the phase label, not the raw stage name
        if event.kind == "preparing":
            if event.stage in ("waiting_backend_response", "backend_response_received"):
                self.phase, self.done, self.total, self.text = "cloud", None, None, "DT+ Cloud processing"
            else:
                waiting = event.stage == "waiting_video_resources" or (
                    not self.video and event.stage in ("waiting_execution_lane", "waiting_provider_slot")
                )
                self.phase, self.text, self.done, self.total = (
                    "waiting" if waiting else "preparing",
                    event_stage(event) or "Preparing",
                    None,
                    None,
                )
        elif event.kind == "submission_possible":
            self.sent_at = now
            self.phase, self.text, self.done, self.total = "preparing", "Sending request", None, None  # until sent
        elif event.kind == "progress" and event.stage == "sampling":
            self.phase, self.done, self.total = "sampling", event.completed, event.total
            if (counter := _counter(event)) is not None:
                self.steps = counter
            self.text = f"Sampling · step {event.completed} of {event.total}"
        elif event.kind == "progress" and event.stage == "receiving_frames":
            self.phase, self.done, self.total = "receiving", event.completed, event.total
            if (counter := _counter(event)) is not None:
                self.frames = counter
            self.bytes = event.received_bytes or self.bytes
            self.text = f"Receiving frames · {event.completed}/{event.total} · {self.bytes / 1_048_576:.1f} MB received"
        elif event.kind == "progress" and event.stage == "waiting_local_encoder":
            self.phase, self.text, self.done, self.total = (
                "encoder_wait",
                "Received · waiting for a local video encoder",
                None,
                None,
            )
        elif event.kind == "progress" and event.stage == "encoding_video":
            self.phase, self.text, self.done, self.total = "encoding", "Encoding video and adding audio", None, None
        elif event.kind == "progress" and event.stage == "verifying_video":
            self.phase, self.text, self.done, self.total = "verifying", "Verifying the MP4", None, None
        elif event.kind == "progress" and event.stage and event.stage.startswith("second_pass"):
            self.phase, self.text = "second_pass", "Second-pass sampling"
            self.done, self.total = event.completed, event.total
        elif event.kind == "progress" and event.stage:
            self.text = "DT+: " + event.stage.replace("_", " ")
        elif event.kind == "terminal":
            self.phase, self.text, self.done, self.total = "saving", "Saving the video and its poster", None, None
        else:
            return False
        return True


@dataclass(frozen=True, slots=True)
class ItemSnapshot:
    queue_id: str
    item_id: str
    request_id: str
    mode: str
    stage: str  # stage code W C S R E V, or "" before the request is sent
    label: str
    steps: tuple[int, int] | None
    frames: tuple[int, int] | None
    elapsed: float
    estimate: float


@dataclass(frozen=True, slots=True)
class QueueTotals:
    """Durable counts of one queue, read from its queue view (never from events)."""

    queue_id: str
    settled: int
    total: int
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    unknown: int = 0
    running: int = 0
    paused: bool = False
    foreign: bool = False  # running in another process: durable counts only, no live evidence
    parent: bool = False  # a script or workflow: one parent item, its steps stay in their own views


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    totals: QueueTotals
    items: tuple[ItemSnapshot, ...]
    stage_counts: tuple[tuple[str, int], ...]
    steps: tuple[int, int, int]  # (completed, total, reporting items) over items in sampling that report both
    frames: tuple[int, int, int]  # the same for receiving
    in_flight: float  # sum of the running items' estimates (each below 1)

    @property
    def contribution(self) -> float:
        """Settled items plus in-flight estimates: this queue's share of the global fill, in items."""
        return min(float(self.totals.total), self.totals.settled + self.in_flight)


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    queues: tuple[QueueSnapshot, ...]
    settled: int
    total: int
    outcomes: tuple[tuple[str, int], ...]  # (state, count) for failed, cancelled, unknown; zero counts omitted
    stage_counts: tuple[tuple[str, int], ...]
    steps: tuple[int, int, int]
    frames: tuple[int, int, int]

    @property
    def fill(self) -> float:
        """Global fill 0..1 as the sum of every queue's contribution over the global total."""
        if self.total <= 0:
            return 0.0
        return min(1.0, sum(queue.contribution for queue in self.queues) / self.total)


def totals_from_view(view: dict, *, foreign: bool = False, parent: bool = False) -> QueueTotals:
    """Durable counts of one queue view (workflow images are counted as items, as everywhere else).

    A script or workflow (``parent``) counts as one item that settles when the run ends."""
    from dtflow.ui.queue_presenter import item_counts, summarize

    if parent:
        ended = view.get("status") not in ("running", "paused")
        return QueueTotals(
            view["queue_id"],
            int(ended),
            1,
            running=int(not ended),
            paused=view.get("status") == "paused",
            foreign=foreign,
            parent=True,
        )

    counts = item_counts(view)
    summary = summarize(view)
    return QueueTotals(
        view["queue_id"],
        summary.settled,
        summary.total,
        succeeded=counts.get("succeeded", 0),
        failed=counts.get("failed", 0) + counts.get("save_failed", 0),
        cancelled=counts.get("cancelled", 0),
        unknown=counts.get("unknown", 0),
        running=counts.get("running", 0),
        paused=view.get("status") == "paused",
        foreign=foreign,
    )


class ProgressModel:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ItemProgress] = {}

    # -- events ----------------------------------------------------------------------------------------------------
    def observe(
        self,
        queue_id: str,
        item_id: str,
        event: BackendEvent,
        *,
        mode: str | None = None,
        now: float | None = None,
    ) -> ItemProgress | None:
        """Fold one event into its item; None when it belongs to a superseded attempt or repeats a sequence."""
        now = time.monotonic() if now is None else now
        key = (queue_id, item_id)
        item = self._items.get(key)
        if item is None:
            item = self._items[key] = ItemProgress(queue_id, item_id, event.request_id, mode or "image", now)
        elif event.request_id != item.request_id:
            if event.request_id in item.superseded:
                return None  # a late event of an older attempt
            kept = (*item.superseded, item.request_id)[-_SUPERSEDED_KEPT:]
            item.superseded, item.request_id, item.last_sequence = list(kept), event.request_id, 0
            item._begin(now)  # a retry starts over: counters and estimate reset
            self._items[key] = self._items.pop(key)  # and it is the newest attempt of the queue again
        elif event.sequence <= item.last_sequence and (event.sequence or item.last_sequence):
            return None  # local preparation milestones all carry sequence 0 and repeat; numbered events stay ordered
        item.last_sequence = max(item.last_sequence, event.sequence)
        if mode == "video" or event.stage in ("waiting_video_resources", "video_preparing"):
            item.mode = "video"
        presented = item._observe(event, now)
        label = event_stage(event)
        if item.video and presented:
            label = item.text
            if item.done is None or item.total is None:
                item.counter = None
        if label and label != item.label:
            item.label = label
            item.label_since = (item.sent_at if item.sent_at is not None else item.started) if item.video else now
        if event.kind == "progress" and (counter := _counter(event)) is not None:
            item.counter = counter
        elif event.kind == "terminal":
            item.counter = None
            if not item.video:
                item.label, item.label_since = "", None
            # A video's terminal event precedes durable MP4/poster saving: its Saving phase stays until reconcile.
        item.event_label = label
        item.presented = presented
        return item

    def begin(
        self, queue_id: str, item_id: str, request_id: str, mode: str, *, now: float | None = None
    ) -> ItemProgress:
        """Start a fresh record before its first event (a run whose clock starts when it is submitted)."""
        now = time.monotonic() if now is None else now
        item = self._items[(queue_id, item_id)] = ItemProgress(queue_id, item_id, request_id, mode, now)
        return item

    def get(self, queue_id: str, item_id: str) -> ItemProgress | None:
        return self._items.get((queue_id, item_id))

    def for_request(self, request_id: str) -> ItemProgress | None:
        """The item whose current attempt has this request identity."""
        return next((item for item in self._items.values() if item.request_id == request_id), None)

    def items(self, queue_id: str | None = None) -> tuple[ItemProgress, ...]:
        return tuple(item for item in self._items.values() if queue_id is None or item.queue_id == queue_id)

    def has_video(self, queue_id: str) -> bool:
        return any(item.video for item in self.items(queue_id))

    def discard(self, queue_id: str, item_id: str) -> None:
        self._items.pop((queue_id, item_id), None)

    def clear(self) -> None:
        self._items.clear()

    # -- durable reconciliation --------------------------------------------------------------------------------------
    def reconcile(self, running: Mapping[str, Collection[str] | None]) -> None:
        """Keep only items that the durable views still show running.

        ``running`` maps each queue id with running work to the running item ids when they are known, or None when the
        queue's item list was not read (then every observed item of that queue stays). Absent queues lose their items.
        """
        keep = {}
        for key, item in self._items.items():
            if key[0] not in running:
                continue
            ids = running[key[0]]
            if ids is None or key[1] in ids:
                keep[key] = item
        self._items = keep

    # -- projection --------------------------------------------------------------------------------------------------
    def snapshot(self, totals: Collection[QueueTotals], *, now: float | None = None) -> ProgressSnapshot:
        """Immutable per-item, per-queue and global projection; counts and outcomes come from ``totals`` only."""
        now = time.monotonic() if now is None else now
        queues = tuple(self._queue(entry, now) for entry in totals)
        outcomes = {"failed": 0, "cancelled": 0, "unknown": 0}
        for queue in queues:
            outcomes["failed"] += queue.totals.failed
            outcomes["cancelled"] += queue.totals.cancelled
            outcomes["unknown"] += queue.totals.unknown
        return ProgressSnapshot(
            queues,
            sum(queue.totals.settled for queue in queues),
            sum(queue.totals.total for queue in queues),
            tuple((name, count) for name, count in outcomes.items() if count),
            _merge_stages(queue.stage_counts for queue in queues),
            _sum_counters(queue.steps for queue in queues),
            _sum_counters(queue.frames for queue in queues),
        )

    def _queue(self, totals: QueueTotals, now: float) -> QueueSnapshot:
        # Foreign-process queues have no live events here; their durable counts alone are shown.
        items = () if totals.foreign or totals.parent else self.items(totals.queue_id)
        # An item can never outnumber the durable running count (stale evidence of an earlier item is ignored).
        if totals.running and len(items) > totals.running:
            items = items[-totals.running :]  # the most recently started ones; earlier records are likelier settled
        elif not totals.running and not totals.paused:
            items = ()
        shots = tuple(
            ItemSnapshot(
                item.queue_id,
                item.item_id,
                item.request_id,
                item.mode,
                item.code,
                item.label,
                item.steps if item.phase in ("sampling", "second_pass") else None,
                item.frames if item.phase == "receiving" else None,
                item.elapsed(now),
                item.estimate(now),
            )
            for item in items
        )
        counts: dict[str, int] = {}
        for shot in shots:
            if shot.stage:
                counts[shot.stage] = counts.get(shot.stage, 0) + 1
        return QueueSnapshot(
            totals,
            shots,
            tuple(sorted(counts.items(), key=lambda pair: "WCSREV".index(pair[0]))),
            _sum_counters(((*s.steps, 1) if s.steps else (0, 0, 0)) for s in shots),
            _sum_counters(((*s.frames, 1) if s.frames else (0, 0, 0)) for s in shots),
            sum(shot.estimate for shot in shots),
        )


def _sum_counters(values) -> tuple[int, int, int]:
    done = total = reporters = 0
    for a, b, c in values:
        done, total, reporters = done + a, total + b, reporters + c
    return done, total, reporters


def _merge_stages(groups) -> tuple[tuple[str, int], ...]:
    merged: dict[str, int] = {}
    for group in groups:
        for code, count in group:
            merged[code] = merged.get(code, 0) + count
    return tuple(sorted(merged.items(), key=lambda pair: "WCSREV".index(pair[0])))

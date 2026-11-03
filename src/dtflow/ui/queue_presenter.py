"""Qt-free presentation of durable queue views: rows, status wording, control availability and uncertainty text.

Everything here maps the shared ``read_queues`` view (scheduler, reservation and no-replay semantics are Phase 3B) to
display data. The vocabularies below are the only ones the Queues page uses, so a state never gets two meanings.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dtflow.core.queue_eta import estimate_queue_seconds
from dtflow.services.connection import local_time
from dtflow.services.dynamic_prompts import decode_runs

MIN_ETA_SAMPLES = 1


def _duration_samples(view: dict) -> tuple[float, ...]:
    samples = []
    for value in view.get("successful_durations", ()):
        if type(value) not in (int, float):
            continue
        try:
            duration = float(value)
        except OverflowError:
            continue
        if math.isfinite(duration) and duration >= 0:
            samples.append(duration)
    return tuple(samples)


def _eta_eligible(view: dict) -> bool:
    """Only an active, owned queue with running work shows an ETA; durable snapshots never override this."""
    if view["status"] != "running" or view.get("owner_active") is False:
        return False
    counts = view["summary"]
    return counts.get("running", 0) > 0


def estimate_remaining(view: dict, minimum_samples: int = MIN_ETA_SAMPLES) -> float | None:
    """Estimate remaining wall time from succeeded item timings and observed parallel work, never the configured cap.

    Provider durations may include provider queueing. This does not predict per-item progress or elapsed running time.
    """
    samples = _duration_samples(view)
    if not _eta_eligible(view) or len(samples) < max(1, minimum_samples):
        return None
    return estimate_queue_seconds(view["summary"], samples, view.get("running_started_at_utc"))


def _snapshot_eta(view: dict) -> tuple[tuple, float] | None:
    """The latest durable timing snapshot's identity and ETA aged to now, when it is usable."""
    snapshot = view.get("timing_snapshot")
    if not snapshot or type(snapshot.get("eta_seconds")) is not int or snapshot["eta_seconds"] < 0:
        return None
    try:
        recorded = datetime.fromisoformat(snapshot["at_utc"])
        if recorded.tzinfo is None:
            return None
        age = max(0.0, (datetime.now().astimezone() - recorded).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None
    return (view["queue_id"], snapshot["at_utc"]), max(0.0, snapshot["eta_seconds"] - age)


@dataclass
class QueueCountdown:
    """A monotonic ETA deadline: unchanged polls keep ticking; settlements or changed live lanes rebase it.

    A durable timing snapshot seeds the deadline only when it is new. Once the live timing context (counts, samples,
    lanes or Resume) changes without a newer snapshot, that snapshot is stale and the live estimate is used instead.
    """

    signature: tuple | None = None
    deadline: float | None = None
    snapshot_key: tuple | None = None

    def reset(self) -> None:
        self.signature = self.deadline = self.snapshot_key = None

    def remaining(self, view: dict, now: float) -> float | None:
        if not _eta_eligible(view):
            # Keep the seen snapshot so Resume does not revive a pre-pause ETA.
            self.signature = self.deadline = None
            return None
        signature = (
            view["queue_id"],
            view.get("started_at_utc"),
            view.get("resume_event_id", 0),
            tuple(view["summary"].get(state, 0) for state in ("pending", "running", *SETTLED)),
            _duration_samples(view),
            tuple(view.get("running_started_at_utc") or ()),
        )
        snapshot = _snapshot_eta(view)
        key = snapshot[0] if snapshot else None
        if signature != self.signature or key != self.snapshot_key:
            if snapshot is not None and key != self.snapshot_key:
                estimate = snapshot[1]
            else:
                estimate = estimate_remaining(view)
            self.snapshot_key = key
            if estimate is None:
                self.signature = self.deadline = None
                return None
            self.signature, self.deadline = signature, now + estimate
        return max(0.0, self.deadline - now)


def _clock(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def eta_text(view: dict, *, remaining: float | None = None) -> str:
    """Explicitly estimated remaining time, with a caveat when model, size or step count varies."""
    seconds = estimate_remaining(view) if remaining is None else remaining
    if seconds is None:
        return ""
    duration = _clock(math.ceil(max(0, seconds)))
    varied = view.get("varied") or {}
    caveat = "; rough for mixed settings" if any(varied.get(key, 0) > 1 for key in ("model", "size", "steps")) else ""
    return f"~{duration} left (estimate{caveat})"


def elapsed_text(view: dict, now: datetime | None = None) -> str:
    """Observed wall time since the latest dispatch start, including pauses and provider waits."""
    try:
        started = datetime.fromisoformat(view.get("started_at_utc") or "")
        if started.tzinfo is None:
            return ""
        seconds = max(0, int(((now or datetime.now().astimezone()) - started).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return ""
    return f"Elapsed {_clock(seconds)}"


ITEM_LEVELS = {
    "pending": "secondary",
    "running": "warning",
    "succeeded": "ok",
    "failed": "error",
    "unknown": "warning",  # no final result was received: most likely failed, but not proven, and never re-sent
    "save_failed": "error",
    "cancelled": "warning",
}
SETTLED = ("succeeded", "failed", "unknown", "save_failed", "cancelled")
STATUS_LEVELS = {"paused": "warning", "queued": "secondary", "running": "ok", "finished": "ok", "cancelled": "warning"}
STATUS_TEXT = {
    "paused": "Paused. Nothing dispatches until Start.",
    "queued": "Up next. Starts by itself when the running queue ends.",
    "running": "Running.",
    "finished": "Finished.",
    "cancelled": "Cancelled. Pending items were cancelled; active ones stopped waiting locally.",
}
STATUS_CHIP = {
    "paused": "Paused",
    "queued": "Up next",
    "running": "Running",
    "finished": "Finished",
    "cancelled": "Cancelled",
}
CONCURRENCY_HINT = (
    "Maximum number of images generated at the same time (1 to 10). Cloud text-to-image reserves 32 MiB per request "
    "from a 320 MiB shared memory budget; image edits may reserve more. Local Draw Things always runs one at a time. "
    "This is a DTFlow setting, not a verified cloud entitlement."
)
NEW_CAP_HINT = (
    "Maximum number of images generated at the same time (1 to 10). With cloud chosen, this also saves the default "
    "for new cloud queues. Local Draw Things always runs one at a time; provider and memory limits still apply."
)
GROUPS = (("running", "Running"), ("queued", "Up next"), ("paused", "Paused"), ("history", "History"))
VARIED_LABELS = {
    "prompt": "prompts",
    "model": "models",
    "seed": "seeds",
    "steps": "step counts",
    "guidance": "guidance values",
    "sampler": "samplers",
    "size": "sizes",
    "strength": "strengths",
    "loras": "LoRA sets",
}
# Item-count chips under the header progress bar; each one filters the items table to its states.
FILTERS = (
    ("succeeded", "✓", ("succeeded",), "Succeeded"),
    ("failed", "✗", ("failed", "save_failed"), "Failed or not saved"),
    ("unknown", "?", ("unknown",), "No result"),
    ("cancelled", "–", ("cancelled",), "Cancelled"),
)
# Segment order and levels of the header and list progress bars: settled outcomes first, then live work.
SEGMENTS = (
    ("succeeded", "ok"),
    ("failed", "error"),
    ("save_failed", "error"),
    ("unknown", "unknown"),
    ("cancelled", "warning"),
    ("running", "accent"),
)
UNKNOWN_TEXT = (
    "No final result was received, so this generation most likely failed. It is not sent again automatically, and it "
    "does not block other items or queues. Retry item runs it again as a new attempt."
)
OWNERS = ("here", "none")


LORA_GAUGE_MIN_SCALE = 1.0  # a full bar is never less than the neutral weight 1.0


def gauge_scale(rows) -> float:
    """Full-bar weight for the items table: the largest recorded weight, rounded up to 0.5 and never below 1.0.

    One shared scale keeps gauges comparable across rows; it follows the weights actually recorded, not a fixed limit.
    """
    weights = [
        weight
        for row in rows
        for _, weight in row.loras
        if type(weight) in (int, float) and math.isfinite(weight) and weight > 0
    ]
    return max(LORA_GAUGE_MIN_SCALE, math.ceil(max(weights, default=0.0) * 2) / 2)


def lora_fraction(weight, scale: float = LORA_GAUGE_MIN_SCALE) -> float:
    """Gauge fill for a configured LoRA strength; an unknown or non-positive weight draws an empty track."""
    if type(weight) not in (int, float) or weight <= 0 or scale <= 0:
        return 0.0
    return min(1.0, weight / scale)


@dataclass(frozen=True, slots=True)
class ItemRow:
    item_id: str
    position: int
    state: str
    attempt_id: str | None
    seed: int
    outputs: tuple[str, ...]
    metadata: str | None
    failure_code: str | None
    operation: str = "txt2img"
    strength: float | None = None
    prompt: str = ""
    negative_prompt: str = ""
    model: str = ""
    loras: tuple[tuple[str, float | None], ...] = ()
    steps: int | float | None = None
    guidance: int | float | None = None
    sampler: str = ""
    duration: float | None = None  # observed backend seconds; None means unavailable, never zero
    width: int | float | None = None
    height: int | float | None = None
    gallery_identity: str | None = None  # "attempt:ordinal" of the first saved image, as the Images gallery names it
    prompt_runs: tuple[tuple[int, int], ...] = ()  # (start, length) of the prompt parts a prompt variation produced

    @property
    def dimensions_text(self) -> str:
        return "—" if self.width is None or self.height is None else f"{self.width:g} × {self.height:g}"

    @property
    def operation_text(self) -> str:
        return {"txt2img": "Text to image", "img2img": "Image to image", "inpaint": "Inpaint"}.get(
            self.operation, self.operation
        )

    @property
    def outcome_line(self) -> str:
        """Explicit only where the recorded evidence supports it; never implies a saved image."""
        if self.state == "succeeded":
            return "No error recorded" if self.outputs else "Succeeded; no saved image verified"
        if self.state in ("failed", "save_failed"):
            return self.failure_code or self.state_text.capitalize()
        return {
            "unknown": "No result received",
            "running": "In progress",
            "pending": "Not started",
            "cancelled": "Cancelled",
        }.get(self.state, "—")

    @property
    def duration_text(self) -> str:
        return "—" if self.duration is None else f"{self.duration:.1f} s"

    @property
    def steps_text(self) -> str:
        return "Steps —" if self.steps is None else f"Steps {self.steps:g}"

    @property
    def guidance_text(self) -> str:
        if self.guidance is None:
            return "CFG —"
        return f"CFG {self.guidance:.1f}" if round(self.guidance, 1) == self.guidance else f"CFG {self.guidance:g}"

    @property
    def sampler_text(self) -> str:
        return f"Sampler {self.sampler}" if self.sampler else "Sampler —"

    @property
    def edit_text(self) -> str:
        if self.operation == "txt2img":
            return "—"
        name = "Inpaint" if self.operation == "inpaint" else "Image to image"
        return f"{name} · {self.strength:g}" if self.strength is not None else name

    @property
    def level(self) -> str:
        return ITEM_LEVELS.get(self.state, "secondary")

    @property
    def state_text(self) -> str:
        return "no result" if self.state == "unknown" else self.state.replace("_", " ")


@dataclass(frozen=True, slots=True)
class QueueSummary:
    queue_id: str
    status: str
    level: str
    headline: str
    settled: int
    total: int
    succeeded: int
    backend: str


@dataclass(frozen=True, slots=True)
class Controls:
    start: bool
    pause: bool  # on an up-next queue: return it to paused
    resume: bool
    cancel: bool
    enqueue: bool = False  # Start next: wait up next behind this window's running queue
    reorder: bool = False


_STEP_ITEM_STATES = {
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "unknown": "unknown",
    "interrupted_before_submission": "failed",
}


def _step_rows(item: dict, steps: list) -> list[ItemRow]:
    rows = []
    for step in steps:
        paths = [path for path in step["artifacts"] if isinstance(path, str)]
        metadata = next((path for path in paths if Path(path).name == "metadata.json"), None)
        identity = next(
            (
                f"{step['attempt_id']}:{index}"
                for index, path in enumerate(step["artifacts"])
                if isinstance(path, str) and path != metadata
            ),
            None,
        )
        fields = step.get("fields") or {}
        state = _STEP_ITEM_STATES.get(step["state"], "save_failed" if step["state"] == "save_failed" else "running")
        rows.append(
            ItemRow(
                f"{step['ordinal']}:{item['item_id']}",
                step["ordinal"],
                state,
                step["attempt_id"],
                fields.get("seed") if type(fields.get("seed")) is int else (item.get("seed") or 0),
                tuple(path for path in paths if path != metadata),
                metadata,
                None,
                "txt2img",
                None,
                f"Step {step['ordinal']} · " + str(fields.get("prompt") or item.get("prompt") or ""),
                fields.get("negative_prompt") or "",
                fields.get("model") or item.get("model") or "",
                tuple((str(name), weight) for name, weight in fields.get("loras") or ()),
                fields.get("steps", item.get("steps")),
                fields.get("guidance", item.get("guidance")),
                fields.get("sampler") or item.get("sampler") or "",
                fields.get("duration"),
                fields.get("width", item.get("width")),
                fields.get("height", item.get("height")),
                identity,
                (),
            )
        )
    return rows


def item_rows(view: dict) -> tuple[ItemRow, ...]:
    rows = []
    for item in view["items"]:
        steps = (item.get("script") or {}).get("children") or []
        # Keep the parent script row (and its outcome/failure) ahead of child
        # attempts. Without it a failed script with successful children is
        # indistinguishable from a successful run in the Queues inspector.
        artifacts = [path for path in item["artifacts"] if isinstance(path, str)]
        metadata = next((path for path in artifacts if Path(path).name == "metadata.json"), None)
        failure = item.get("failure") or {}
        # The gallery numbers an attempt's outputs by their index in the recorded artifact list (metadata included).
        identity = next(
            (
                f"{item['attempt_id']}:{index}"
                for index, path in enumerate(item["artifacts"])
                if item["attempt_id"] and isinstance(path, str) and path != metadata
            ),
            None,
        )
        rows.append(
            ItemRow(
                item["item_id"],
                item["position"],
                item["state"],
                item["attempt_id"],
                item["seed"],
                tuple(path for path in artifacts if path != metadata),
                metadata,
                (failure.get("message") or failure.get("code")) if item.get("script") else failure.get("code"),
                item.get("operation") or "txt2img",
                item.get("strength"),
                item.get("prompt") or "",
                item.get("negative_prompt") or "",
                item.get("model") or "",
                tuple((str(name), weight) for name, weight in item.get("loras") or ()),
                item.get("steps"),
                item.get("guidance"),
                item.get("sampler") or "",
                item.get("duration"),
                item.get("width"),
                item.get("height"),
                identity,
                decode_runs(item.get("prompt") or "", item.get("prompt_runs")),
            )
        )
        if steps:
            rows.extend(_step_rows(item, steps))
    return tuple(rows)


def backend_label(view: dict) -> str:
    backend = view["backend"]
    endpoint = backend.get("endpoint") if backend["name"] == "local" else None  # cloud has no host:port
    return backend["name"] + (f" ({endpoint})" if endpoint else "")


def summarize(view: dict) -> QueueSummary:
    counts = view["summary"]
    total = sum(counts.values())
    settled = sum(counts[state] for state in SETTLED)
    status = view["status"]
    return QueueSummary(
        view["queue_id"],
        status,
        STATUS_LEVELS.get(status, "unknown"),
        STATUS_TEXT.get(status, status),
        settled,
        total,
        counts["succeeded"],
        backend_label(view),
    )


def script_of(view: dict) -> dict | None:
    """The script run of a script queue's single item, or None for an ordinary queue."""
    if view.get("kind") != "script":
        return None
    return next((item["script"] for item in view.get("items", ()) if item.get("script")), None)


def script_steps(script: dict) -> tuple[int, int, int | None]:
    """(started, committed, expected) steps of a script run; expected is None when it cannot be known statically."""
    children = script.get("children") or ()
    committed = sum(1 for child in children if child.get("state") == "succeeded")
    return len(children), committed, script.get("expected_steps") or None


def script_progress(view: dict) -> tuple[int, int] | None:
    """(committed, total) for the progress bar of a script queue with a known step total."""
    script = script_of(view)
    if script is None:
        return None
    _, committed, expected = script_steps(script)
    return (committed, expected) if expected else None


def progress_text(summary: QueueSummary, view: dict | None = None) -> str:
    script = script_of(view) if view else None
    if script is not None:
        started, committed, expected = script_steps(script)
        failed = script.get("steps_failed") or 0
        tail = f" · {failed} failed" if failed else ""
        if expected:
            return f"Progress: {committed}/{expected} steps · {committed} committed{tail}"
        return f"Progress: {started} step{'s' if started != 1 else ''} so far · {committed} committed{tail}"
    return f"Progress: {summary.settled}/{summary.total} · {summary.succeeded} succeeded"


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One rich queue-list row: what the queue asks for, where it runs, and how far it got."""

    queue_id: str
    group: str
    title: str
    subtitle: str
    chip: str
    level: str
    settled: int
    total: int
    segments: tuple[tuple[str, int], ...]
    title_runs: tuple[tuple[int, int], ...] = ()  # parts of ``title`` a prompt variation produced

    @property
    def text(self) -> str:
        """Plain text for accessibility, filtering and tests."""
        return f"{self.title} · {self.subtitle} · {self.chip.lower()} · {self.settled}/{self.total}"


@dataclass(frozen=True, slots=True)
class PrimaryAction:
    key: str  # start, enqueue, pause, dequeue or resume
    text: str
    tooltip: str = ""


def _first_line(text: str, limit: int) -> str:
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def queue_title(view: dict) -> str:
    """The prompt snippet that names a queue (edits say so); ids stay in tooltips."""
    first = view.get("first") or {}
    prompt = _first_line(first.get("prompt") or "", 80) or "(no prompt)"
    operation = first.get("operation") or "txt2img"
    if operation == "txt2img":
        return prompt
    return ("Inpaint" if operation == "inpaint" else "Image to image") + f" · {prompt}"


def queue_title_runs(view: dict) -> tuple[tuple[int, int], ...]:
    """``(start, length)`` of the parts of ``queue_title(view)`` that the first item's prompt variation produced."""
    first = view.get("first") or {}
    prompt = first.get("prompt") or ""
    runs = decode_runs(prompt, first.get("prompt_runs"))
    offset = 0
    for part in prompt.splitlines(keepends=True):
        if part.strip():
            break
        offset += len(part)
    else:
        return ()
    if not runs:
        return ()
    start = offset + len(part) - len(part.lstrip())
    line = part.strip()
    shown = len(line) if len(line) <= 80 else len(line[:79].rstrip())  # as ``_first_line`` truncates
    operation = first.get("operation") or "txt2img"
    prefix = 0 if operation == "txt2img" else len(("Inpaint" if operation == "inpaint" else "Image to image") + " · ")
    clipped = ((max(run_start, start), min(run_start + length, start + shown)) for run_start, length in runs)
    return tuple((low - start + prefix, high - low) for low, high in clipped if low < high)


def plan_text(view: dict) -> str:
    """Item count plus what the items differ in, e.g. ``4 items · 2 prompts, 2 step counts``."""
    script = script_of(view)
    if script is not None:
        _, _, expected = script_steps(script)
        return f"Script · {expected} step{'s' if expected != 1 else ''}" if expected else "Script · steps as it runs"
    total = sum(view["summary"].values())
    text = f"{total} item{'s' if total != 1 else ''}"
    varied = view.get("varied") or {}
    parts = [f"{count} {VARIED_LABELS.get(name, name)}" for name, count in varied.items() if name != "seed"]
    if parts:
        text += " · " + ", ".join(parts)
    return text


def created_text(at_utc: str, now: datetime | None = None) -> str:
    """Local creation time: the clock for today, the date and clock before that."""
    try:
        moment = datetime.fromisoformat(at_utc).astimezone()
    except (TypeError, ValueError):
        return str(at_utc)
    now = now or datetime.now().astimezone()
    return moment.strftime("%H:%M" if moment.date() == now.date() else "%b %d %H:%M")


def lora_text(identifier: str, weight) -> str:
    """``style.safetensors 0.8``: a LoRA and its weight, as the queue header and composer summary list it."""
    return f"{identifier} {weight:g}" if isinstance(weight, (int, float)) else identifier


DISPLAY_EXTENSIONS = (".ckpt", ".safetensors", ".pt", ".pth", ".bin")


def display_name(identifier: str) -> str:
    """List and table label without the weight-file extension; tooltips and the inspector keep the full identifier."""
    for extension in DISPLAY_EXTENSIONS:
        if identifier.lower().endswith(extension) and len(identifier) > len(extension):
            return identifier[: -len(extension)]
    return identifier


def queue_subtitle(view: dict, now: datetime | None = None) -> str:
    """One line for a list row: model and LoRA count, plan, backend and time (the header names them in ``queue_facts``)."""
    first = view.get("first") or {}
    parts = [display_name(first["model"])] if first.get("model") else []
    varied = view.get("varied")
    loras = first.get("loras") or ()
    # Until the variation scan has run, the first item's LoRAs may not be the queue's; varied sets go to plan_text.
    if loras and varied is not None and "loras" not in varied:
        parts.append(f"{len(loras)} LoRA{'s' if len(loras) != 1 else ''}")
    return " · ".join([*parts, plan_text(view), backend_label(view), created_text(view["created_at_utc"], now)])


def queue_facts(view: dict, now: datetime | None = None) -> dict[str, str]:
    """The header's labelled lines: Model, LoRAs (one per line), Plan, Backend and Created."""
    first = view.get("first") or {}
    varied = view.get("varied")
    loras = first.get("loras") or ()
    if varied is None:
        lora_value = "—"  # the variation scan has not run: the first item's LoRAs may not be the queue's
    elif "loras" in varied:
        lora_value = "Varied per item"  # plan_text counts the sets
    else:
        lora_value = "\n".join(lora_text(identifier, weight) for identifier, weight in loras) or "None"
    return {
        "Model": first.get("model") or "—",
        "Loras": lora_value,
        "Plan": plan_text(view),
        "Backend": backend_label(view),
        "Created": created_text(view["created_at_utc"], now),
    }


def group_of(view: dict) -> str:
    status = view["status"]
    return status if status in ("running", "queued", "paused") else "history"


def segments(summary: dict) -> tuple[tuple[str, int], ...]:
    """(level, count) runs for a segmented progress bar; pending items are the empty track."""
    return tuple((level, summary.get(state, 0)) for state, level in SEGMENTS if summary.get(state, 0))


def status_chip(view: dict, up_next: list[str]) -> tuple[str, str]:
    """(level, text) of the single status chip: the queue's status, its up-next place, or finished with problems."""
    status = view["status"]
    if status == "queued" and view["queue_id"] in up_next:
        return "secondary", f"Up next {up_next.index(view['queue_id']) + 1}"
    if status == "finished":
        counts = view["summary"]
        problems = counts["failed"] + counts["save_failed"] + counts["unknown"]
        return ("warning" if problems else "ok"), "Finished"
    return STATUS_LEVELS.get(status, "unknown"), STATUS_CHIP.get(status, status.capitalize())


def queue_entries(views, now: datetime | None = None) -> tuple[QueueEntry, ...]:
    """Rows grouped Running, Up next (in start order), Paused, then History; newest first within a group."""
    order = up_next_ids(views)
    newest = sorted(views, key=lambda view: (view["created_at_utc"], view["queue_id"]), reverse=True)
    rows = []
    for group, _ in GROUPS:
        members = [view for view in newest if group_of(view) == group]
        if group == "queued":
            members.sort(key=lambda view: order.index(view["queue_id"]) if view["queue_id"] in order else len(order))
        for view in members:
            summary = summarize(view)
            level, chip = status_chip(view, order)
            rows.append(
                QueueEntry(
                    view["queue_id"],
                    group,
                    queue_title(view),
                    queue_subtitle(view, now),
                    chip,
                    level,
                    summary.settled,
                    summary.total,
                    segments(view["summary"]),
                    queue_title_runs(view),
                )
            )
    return tuple(rows)


def matches(entry: QueueEntry, query: str) -> bool:
    """Every word of the filter appears in the row's text or id (case-insensitive)."""
    haystack = f"{entry.text} {entry.queue_id}".casefold()
    return all(word in haystack for word in query.casefold().split())


def filter_counts(summary: dict) -> dict[str, int]:
    return {key: sum(summary.get(state, 0) for state in states) for key, _, states, _ in FILTERS}


def filter_states(keys) -> frozenset[str]:
    """Item states the checked count chips show; no chip checked shows every item."""
    keys = frozenset(keys)
    return frozenset(state for key, _, states, _ in FILTERS if key in keys for state in states)


def primary_action(view: dict | None, available: "Controls", *, started: bool, owned: bool) -> PrimaryAction | None:
    """The one state-dependent header action: Start, Start next, Pause or Resume (None: nothing applies now)."""
    if view is None:
        return None
    if needs_recovery(view) and not owned:
        if available.start:
            return PrimaryAction("start", "Resume", "Recovers interrupted dispatch and continues pending items only.")
        return None
    if available.pause:
        if view["status"] == "queued":
            return PrimaryAction("dequeue", "Pause", "Leaves the up-next order; the queue stays paused until Start.")
        if view.get("kind") != "script":  # a script is one item: the runner has no pause point inside it
            return PrimaryAction("pause", "Pause", "Stops starting new items; running items finish.")
    if available.enqueue:
        return PrimaryAction("enqueue", "Start next", "Waits up next and starts by itself when the running queue ends.")
    if available.resume and owned:
        return PrimaryAction("resume", "Resume", "Starts new items again.")
    if available.start:
        if started:
            return PrimaryAction("start", "Resume", "Continues the pending items from this window.")
        return PrimaryAction("start", "Start", "Dispatches this queue from this window.")
    if available.resume:
        return PrimaryAction("resume", "Resume", "Posted for the process that dispatches this queue.")
    return None


def needs_recovery(view: dict) -> bool:
    """Durable running state with an observed absent owner needs an explicit ownership-acquiring resume."""
    return view["status"] == "running" and view.get("owner_active") is False


def has_started(detail: dict | None) -> bool:
    """Whether the queue's recorded events show it was ever started (a new queue has only ``created``)."""
    return any(event["kind"] == "started" for event in (detail or {}).get("events", ()))


def controls(
    view: dict | None,
    *,
    dispatching_here: bool,
    generating: bool = False,
    started: bool = False,
    owned: bool = False,
) -> Controls:
    """Which actions make sense now. Start needs this window to own dispatch (nothing else generating); the rest only
    post controls for whichever process owns dispatch, or for the next start. Resume is offered only for a queue that
    has run before: a new queue is started, not resumed. While this window dispatches, a paused queue can wait up next
    instead (auto-play), and an up-next queue can be moved or returned to paused. ``owned``: this window dispatches
    this very queue, so a paused one is resumed, not queued behind itself."""
    if view is None:
        return Controls(False, False, False, False)
    status = view["status"]
    return Controls(
        start=status in ("paused", "running") and not dispatching_here and not generating,
        pause=status in ("running", "queued"),
        resume=status == "paused" and started and (owned or not dispatching_here),
        cancel=status in ("paused", "running", "queued"),
        enqueue=status == "paused" and dispatching_here and not owned,
        reorder=status == "queued",
    )


def up_next_ids(views) -> list[str]:
    """Queue ids waiting up next, in the order the dispatch owner starts them."""
    queued = [view for view in views if view["status"] == "queued"]
    return [
        view["queue_id"] for view in sorted(queued, key=lambda view: (view.get("up_next") or 0, view["created_at_utc"]))
    ]


def outcome_note(row: ItemRow | None) -> tuple[str, str] | None:
    """(level, text) for the selected item when its outcome needs a sentence; None hides the Outcome card."""
    if row is None:
        return None
    if row.state == "unknown":
        return "warning", UNKNOWN_TEXT
    if row.state in ("failed", "save_failed") and row.failure_code:
        return "error", f"{row.state_text.capitalize()}: {row.failure_code}"
    return None


def concurrency_level(value: int) -> str:
    return "elevated" if value > 2 else "normal"


def event_lines(view: dict) -> str:
    """Compact human-readable queue activity; durable raw events remain unchanged in persistence."""
    positions = {item["item_id"]: item["position"] for item in view.get("items", ())}
    hidden = {"item_started", "backend_preparing", "backend_artifact_received", "backend_terminal", "composer_source"}
    lines = []

    def item_label(detail: dict) -> str:
        item_id = detail.get("item_id")
        position = positions.get(item_id)
        return f"#{position}" if position is not None else f"item {str(item_id)[:8]}"

    for event in view.get("events", ()):
        kind = event["kind"]
        detail = event["detail"]
        if kind in hidden:
            continue
        stamp = local_time(event["at_utc"])

        if kind == "created" and script_of(view) is not None:
            summary = "Script queued"
        elif kind == "created":
            count, cap = detail.get("items"), detail.get("cap")
            summary = ""
            if count is not None:
                summary += f"{count}"
            if cap > count:
                cap = count
            if cap is not None:
                summary += f" // {cap}"
        elif kind == "started":
            summary = "Queue started"
        elif kind == "backend_submission_possible":
            summary = f"{item_label(detail)} submitted"
        elif kind == "timing_snapshot":
            elapsed = detail.get("elapsed_seconds")
            eta = detail.get("eta_seconds")
            if type(elapsed) is not int or elapsed < 0:
                continue
            if type(eta) is int and eta >= 0:
                summary = f"{_clock(elapsed)} + {_clock(eta)} = {_clock(elapsed + eta)}"
            else:
                continue
        elif kind == "item_settled" and script_of(view) is not None:
            script = script_of(view)
            started, committed, _ = script_steps(script)
            state = str(detail.get("state", "settled"))
            summary = f"Run finished · {started} step{'s' if started != 1 else ''}, {committed} committed"
            if state != "succeeded":
                summary += f" · {state.replace('_', ' ')}"
            elapsed = script.get("elapsed")
            if type(elapsed) in (int, float):
                summary += f" · total {elapsed:.2f} s"
        elif kind == "item_settled":
            state = str(detail.get("state", "settled"))
            state = "no result" if state == "unknown" else state.replace("_", " ")
            if state == "succeeded":
                summary = f"{item_label(detail)} completed"
            else:
                summary = f"{item_label(detail)} {state}"
            duration = detail.get("backend_elapsed_seconds")
            if type(duration) in (int, float):
                summary += f" - {duration:.2f} s"
        else:
            summary = kind.replace("_", " ")
            compact = []
            for key, value in detail.items():
                if key == "item_id":
                    compact.append(item_label(detail))
                elif key in ("request_id", "attempt_id"):
                    compact.append(f"{key.replace('_', ' ')} {str(value)[:8]}")
                else:
                    compact.append(f"{key.replace('_', ' ')} {value}")
            if compact:
                summary += " - " + ", ".join(compact)
        lines.append(f"{stamp}  {summary}")
        if kind == "started" and script_of(view) is not None:
            lines.extend(_step_lines(script_of(view)))

    return "\n".join(lines)


def _step_lines(script: dict) -> list[str]:
    """One or two log lines per step (started, then committed with its duration); steps carry no event clock."""
    lines = []
    blank = " " * 8
    for child in script.get("children") or ():
        n = child["ordinal"]
        lines.append(f"{blank}  Step {n} started")
        state = child.get("state")
        if state == "succeeded":
            duration = (child.get("fields") or {}).get("duration")
            tail = f" - {duration:.2f} s" if type(duration) in (int, float) else ""
            lines.append(f"{blank}  Step {n} committed{tail}")
        elif state in ("failed", "save_failed", "unknown", "cancelled"):
            lines.append(f"{blank}  Step {n} {state.replace('_', ' ')}")
    return lines


def outcome_text(view: dict) -> tuple[str, str, str]:
    """(level, headline, detail) naming what actually ended a dispatch run, from the final queue view."""
    counts, status = view["summary"], view["status"]
    total = sum(counts.values())
    if status == "finished":
        problems = [
            f"{counts[state]} {label}"
            for state, label in (("failed", "failed"), ("save_failed", "not saved"), ("unknown", "no result"))
            if counts[state]
        ]
        level = "error" if problems else "ok"
        return (
            level,
            "Finished",
            f"{counts['succeeded']} of {total} succeeded" + (f"; {', '.join(problems)}." if problems else "."),
        )
    if status == "cancelled":
        return "warning", "Cancelled", f"{counts['cancelled']} item(s) not run; {counts['succeeded']} succeeded."
    return "warning", "Stopped", f"{counts['pending']} item(s) still pending; press Start to continue."

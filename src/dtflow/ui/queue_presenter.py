"""Qt-free presentation of durable queue views: rows, status wording, control availability and uncertainty text.

Everything here maps the shared ``read_queues`` view (scheduler, reservation and no-replay semantics are Phase 3B) to
display data. The vocabularies below are the only ones the Queues page uses, so a state never gets two meanings.
"""

from dataclasses import dataclass
from pathlib import Path

from dtflow.services.connection import local_time

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
STATUS_LEVELS = {"paused": "warning", "running": "ok", "finished": "ok", "cancelled": "warning"}
STATUS_TEXT = {
    "paused": "Paused. Nothing dispatches until Start.",
    "running": "Running.",
    "finished": "Finished.",
    "cancelled": "Cancelled. Pending items were cancelled; active ones stopped waiting locally.",
}
CONCURRENCY_HINT = (
    "Local Draw Things remains one effective lane. This is a client policy, not entitlement, and the memory budget "
    "currently admits about two real generations at once whatever the maximum."
)
UNKNOWN_TEXT = (
    "No final result was received, so this generation most likely failed. It is not sent again automatically, and it "
    "does not block other items or queues."
)
OWNERS = ("here", "none")


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
    pause: bool
    resume: bool
    cancel: bool


def item_rows(view: dict) -> tuple[ItemRow, ...]:
    rows = []
    for item in view["items"]:
        artifacts = [path for path in item["artifacts"] if isinstance(path, str)]
        metadata = next((path for path in artifacts if Path(path).name == "metadata.json"), None)
        failure = item.get("failure") or {}
        rows.append(
            ItemRow(
                item["item_id"],
                item["position"],
                item["state"],
                item["attempt_id"],
                item["seed"],
                tuple(path for path in artifacts if path != metadata),
                metadata,
                failure.get("code"),
                item.get("operation") or "txt2img",
                item.get("strength"),
            )
        )
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


def progress_text(summary: QueueSummary) -> str:
    return f"{summary.settled} of {summary.total} settled · {summary.succeeded} succeeded"


def has_started(detail: dict | None) -> bool:
    """Whether the queue's recorded events show it was ever started (a new queue has only ``created``)."""
    return any(event["kind"] == "started" for event in (detail or {}).get("events", ()))


def controls(view: dict | None, *, dispatching_here: bool, generating: bool = False, started: bool = False) -> Controls:
    """Which actions make sense now. Start needs this window to own dispatch (nothing else generating); the rest only
    post controls for whichever process owns dispatch, or for the next start. Resume is offered only for a queue that
    has run before: a new queue is started, not resumed."""
    if view is None:
        return Controls(False, False, False, False)
    status = view["status"]
    return Controls(
        start=status in ("paused", "running") and not dispatching_here and not generating,
        pause=status == "running",
        resume=status == "paused" and started,
        cancel=status in ("paused", "running"),
    )


def ownership_badge(view: dict | None, *, dispatching_here: bool) -> str:
    if dispatching_here:
        return "THIS WINDOW DISPATCHES"
    if view is not None and view["status"] == "running":
        return "RUNNING · NOT DISPATCHED HERE"
    return "NOT DISPATCHING"


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
    hidden = {"item_started", "backend_preparing", "backend_artifact_received", "backend_terminal"}
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

        if kind == "created":
            count, cap = detail.get("items"), detail.get("cap")
            summary = "Queue created"
            if count is not None:
                summary += f" - {count} item{'s' if count != 1 else ''}"
            if cap is not None:
                summary += f" - concurrency {cap}"
        elif kind == "started":
            summary = "Queue started"
        elif kind == "backend_submission_possible":
            summary = f"{item_label(detail)} submitted"
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

    return "\n".join(lines)


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

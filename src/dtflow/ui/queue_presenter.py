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
    "unknown": "unknown",  # distinct from failed: the request may have reached the server
    "save_failed": "error",
    "cancelled": "warning",
}
SETTLED = ("succeeded", "failed", "unknown", "save_failed", "cancelled")
STATUS_LEVELS = {"paused": "warning", "running": "ok", "blocked": "unknown", "finished": "ok", "cancelled": "warning"}
STATUS_TEXT = {
    "paused": "Paused. Nothing dispatches until Start.",
    "running": "Running.",
    "blocked": "Blocked by an unresolved uncertain reservation. Resume cannot clear it.",
    "finished": "Finished.",
    "cancelled": "Cancelled. Pending items were cancelled; active ones stopped waiting locally.",
}
CONCURRENCY_HINT = (
    "Local Draw Things remains one effective lane. This is a client policy, not entitlement, and the memory budget "
    "currently admits about two real generations at once whatever the maximum."
)
UNRESOLVED_UNKNOWN = (
    "Outcome unknown: the request may have reached the server and may still complete. It is never replayed, and "
    "Resume does not clear it. Override only acknowledges it for scheduling."
)
CANCEL_TEXT = (
    "Pending items will be cancelled and active attempts will stop waiting locally. DTFlow does not confirm "
    "cancellation on the server, so an active item may end as outcome unknown."
)
OVERRIDE_TEXT = (
    "This acknowledges every unresolved uncertain reservation on this backend so other work may continue. It is "
    "recorded, does not resolve or replay any attempt, and means remote concurrency is no longer guaranteed."
)
OWNERS = ("here", "none")


@dataclass(frozen=True, slots=True)
class ItemRow:
    item_id: str
    position: int
    state: str
    overridden: bool
    attempt_id: str | None
    seed: int
    outputs: tuple[str, ...]
    metadata: str | None
    failure_code: str | None

    @property
    def level(self) -> str:
        return ITEM_LEVELS.get(self.state, "secondary")

    @property
    def state_text(self) -> str:
        return self.state.replace("_", " ") + (" (overridden)" if self.overridden else "")


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
    override: bool


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
                item["overridden"],
                item["attempt_id"],
                item["seed"],
                tuple(path for path in artifacts if path != metadata),
                metadata,
                failure.get("code"),
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


def has_unresolved_unknown(view: dict) -> bool:
    return any(item["state"] == "unknown" and not item["overridden"] for item in view["items"]) or bool(
        view["blocked_by_uncertainty"]
    )


def has_started(detail: dict | None) -> bool:
    """Whether the queue's recorded events show it was ever started (a new queue has only ``created``)."""
    return any(event["kind"] == "started" for event in (detail or {}).get("events", ()))


def controls(view: dict | None, *, dispatching_here: bool, generating: bool = False, started: bool = False) -> Controls:
    """Which actions make sense now. Start needs this window to own dispatch (nothing else generating); the rest only
    post controls for whichever process owns dispatch, or for the next start. Resume is offered only for a queue that
    has run before: a new queue is started, not resumed."""
    if view is None:
        return Controls(False, False, False, False, False)
    status = view["status"]
    return Controls(
        start=(
            status in ("paused", "running") or (status == "blocked" and "override" in view.get("pending_controls", ()))
        )
        and not dispatching_here
        and not generating,
        pause=status == "running",
        resume=status == "paused" and started,
        cancel=status in ("paused", "running", "blocked"),
        override=has_unresolved_unknown(view),
    )


def ownership_badge(view: dict | None, *, dispatching_here: bool) -> str:
    if dispatching_here:
        return "THIS WINDOW DISPATCHES"
    if view is not None and view["status"] == "running":
        return "RUNNING · NOT DISPATCHED HERE"
    return "NOT DISPATCHING"


def uncertainty_text(row: ItemRow | None) -> tuple[str, str]:
    """(level, text) for the selected item's reservation."""
    if row is None:
        return "unknown", "No unresolved reservation selected."
    if row.state == "unknown":
        if row.overridden:
            return "warning", "Acknowledged by override (recorded). The attempt stays unknown and is never replayed."
        return "unknown", UNRESOLVED_UNKNOWN
    return "ok", "No unresolved reservation for this item."


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
            state = str(detail.get("state", "settled")).replace("_", " ")
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
            for state, label in (("failed", "failed"), ("save_failed", "not saved"), ("unknown", "outcome unknown"))
            if counts[state]
        ]
        level = "unknown" if counts["unknown"] else "error" if problems else "ok"
        return (
            level,
            "Finished",
            f"{counts['succeeded']} of {total} succeeded" + (f"; {', '.join(problems)}." if problems else "."),
        )
    if status == "cancelled":
        return "warning", "Cancelled", f"{counts['cancelled']} item(s) not run; {counts['succeeded']} succeeded."
    if status == "blocked":
        return "unknown", "Blocked", "An uncertain reservation holds this backend. Inspect the unknown item first."
    return "warning", "Stopped", f"{counts['pending']} item(s) still pending; press Start to continue."

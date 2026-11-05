"""A workflow run as one queue item (#223, D6): the stored record, its run state and the run-to-item state mapping.

The item's ``request_json`` is a workflow record (``kind`` ``workflow``), not a generation request::

    {"kind": "workflow", "run_id": str, "name": str, "workflow": <dump_workflow dict>, "backend": str,
     "planned": int, "run": <run state>, "seed": {...} (optional, Retry after the run ended)}

The run state (shape in ``services/workflow_queue``) lives inside the record; only the dispatch owner rewrites it, one
short transaction per update (``QueueMixin.update_workflow_run``). There is no separate run store.

Item state mapping (the same rule script items use for steps that failed inside a completed run):

- any step ``unknown``                  -> item ``unknown`` (never success or failure, never replayed)
- run ``cancelled`` (Stop)              -> item ``cancelled``
- run ``succeeded`` or ``partial``      -> item ``succeeded``; a partial run keeps its note in ``run.message`` and the
                                           failed/stopped steps in ``run.steps`` (like a script's ``steps_failed``)
- run ``failed`` (or anything else)     -> item ``failed`` with the run's plain message
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dtflow.core.models import ValidationError

KIND_WORKFLOW = "workflow"
RUN_STATES = ("pending", "running", "succeeded", "partial", "failed", "cancelled", "unknown")
STEP_STATES = ("pending", "running", "succeeded", "failed", "unknown", "skipped", "paused", "stopped", "cancelled")
BAD_STEP_STATES = ("failed", "unknown", "stopped", "cancelled")
MAX_PLANNED = 256  # mirrors core.workflows.MAX_PLANNED_GENERATIONS (checked again at creation)
INTERRUPTED = "DTFlow stopped while this workflow was running; it is never resumed or replayed."


@dataclass(frozen=True, slots=True)
class WorkflowRequest:
    run_id: str
    name: str
    workflow: dict[str, Any]
    backend: str
    planned: int
    run: dict[str, Any]
    seed: dict[str, Any] | None = None


def now() -> str:
    return datetime.now(UTC).isoformat()


def is_workflow_record(record: Any) -> bool:
    return isinstance(record, dict) and record.get("kind") == KIND_WORKFLOW


def workflow_record(
    *,
    run_id: str,
    name: str,
    workflow: dict[str, Any],
    backend: str,
    planned: int,
    run: dict[str, Any],
    seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "kind": KIND_WORKFLOW,
        "run_id": run_id,
        "name": name,
        "workflow": workflow,
        "backend": backend,
        "planned": planned,
        "run": run,
    }
    if seed is not None:
        record["seed"] = seed
    return record


def _branch(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(type(x) is int for x in value)


def _check_run(run: Any) -> bool:
    return (
        isinstance(run, dict)
        and run.get("state") in RUN_STATES
        and isinstance(run.get("steps"), list)
        and isinstance(run.get("pauses"), list)
        and isinstance(run.get("results"), list)
        and all(
            isinstance(step, dict)
            and type(step.get("node_id")) is int
            and _branch(step.get("branch"))
            and step.get("state") in STEP_STATES
            for step in run["steps"]
        )
    )


def _check_seed(seed: Any) -> bool:
    return seed is None or (
        isinstance(seed, dict)
        and type(seed.get("node_id")) is int
        and _branch(seed.get("branch"))
        and isinstance(seed.get("values"), list)
        and all(
            isinstance(value, dict)
            and type(value.get("node_id")) is int
            and all(value.get(key) is None or isinstance(value.get(key), str) for key in ("output", "mask"))
            for value in seed["values"]
        )
    )


def workflow_request_from_record(record: Any) -> WorkflowRequest:
    """Decode a stored workflow record (shape only; the graph is checked again by the runner). Raises
    ``ValidationError`` for anything malformed."""
    if not is_workflow_record(record):
        raise ValidationError("Stored item is not a workflow run.")
    run_id, name, workflow = record.get("run_id"), record.get("name"), record.get("workflow")
    backend, planned, run, seed = record.get("backend"), record.get("planned"), record.get("run"), record.get("seed")
    if not (
        isinstance(run_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", run_id) is not None
        and isinstance(name, str)
        and isinstance(workflow, dict)
        and isinstance(backend, str)
        and type(planned) is int
        and 0 <= planned <= MAX_PLANNED
        and _check_run(run)
        and _check_seed(seed)
    ):
        raise ValidationError("Stored workflow run is malformed.")
    return WorkflowRequest(run_id, name, workflow, backend, planned, run, seed)


def item_outcome(run: Any) -> tuple[str, str | None]:
    """The item state (and failure JSON, if any) a settled run decides; never invents success (module docstring)."""
    if not isinstance(run, dict):
        return "failed", json.dumps({"code": "workflow_unreadable", "message": "Workflow run state is unreadable."})
    message = run.get("message") if isinstance(run.get("message"), str) else None
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    if any(isinstance(step, dict) and step.get("state") == "unknown" for step in steps):
        text = message or "A workflow step has no confirmed outcome; check the backend before retrying."
        return "unknown", json.dumps({"code": "workflow_step_unknown", "message": text})
    state = run.get("state")
    if state == "cancelled":
        return "cancelled", None
    if state in ("succeeded", "partial"):
        return "succeeded", None
    return "failed", json.dumps({"code": "workflow_failed", "message": message or "Workflow run failed."})


def interrupted_run(run: dict[str, Any]) -> dict[str, Any]:
    """Restart reconciliation of a run left ``running`` by a crash: a step that was running may have been submitted,
    so it becomes ``unknown`` and the run with it; otherwise the run ``failed``. Nothing resumes or replays."""
    run = copy.deepcopy(run)
    stamp = now()
    submitted = False
    for step in run.get("steps", ()):
        if step.get("state") == "running":
            generative = step.get("type") in ("DTFGenerate", "DTFInpaint", "DTFImg2Img")
            step["state"] = "unknown" if generative else "failed"
            submitted = submitted or generative
            step["message"] = INTERRUPTED
            step["stage"] = None
            step["finished_at"] = stamp
        elif step.get("state") in ("pending", "paused"):
            step["state"] = "cancelled"
            step["stage"] = None
    run["pauses"] = []
    run["state"] = "unknown" if submitted else "failed"
    run["message"] = INTERRUPTED
    run["finished_at"] = stamp
    return run

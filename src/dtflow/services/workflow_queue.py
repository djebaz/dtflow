"""Workflow runs as queue items (#223, D6): creation, branch decisions, Retry and read views. Qt-free.

SKELETON (interface fixed by the #223 integration plan; bodies are implemented in the executor PR).

A workflow run is ONE queue item of kind ``workflow`` (store schema 8). The item's record holds the validated
workflow snapshot and the run state (per-step status, outputs, pauses); there is no separate run store. Creation
touches only local state and never generates. Start dispatches it like any queue (``dispatch_queue``).

Run state shape (``read_workflow_run`` / the queue view's ``workflow`` field)::

    {"run_id": str, "name": str, "state": "pending|running|succeeded|partial|failed|cancelled|unknown",
     "planned": int, "submitted": int, "message": str | None,
     "steps": [{"node_id": int, "type": str, "title": str, "branch": [gen_node_id, index], "branch_label": str,
                "state": "pending|running|succeeded|failed|unknown|skipped|paused|stopped|cancelled",
                "stage": str | None, "message": str | None, "output": str | None, "mask": str | None,
                "parent": str | None, "settings": dict | None, "retries": int,
                "started_at": str | None, "finished_at": str | None}],
     "pauses": [{"node_id": int, "branch": [int, int], "branch_label": str, "reason": str}],
     "results": [{"label": str, "path": str, "branch": [int, int], "node_id": int}]}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BRANCH_CONTROL = "workflow_branch"  # queue control action; payload {"node_id", "branch", "decision"}
RETRY_CONTROL = "workflow_retry"  # queue control action; payload {"node_id", "branch"}


async def create_workflow_queue(
    state: Path, backend: dict[str, Any], workflow: dict[str, Any], name: str, *, up_next: bool = False
) -> tuple[str, str]:
    """Validate (``load_workflow`` + ``plan_workflow``) and persist one paused workflow queue (one item, cap 1).

    ``backend`` is the same dict the queue composer records (``name``, ``endpoint``, fake simulation keys).
    Returns ``(queue id, run id)``. Contacts no backend, never generates and never downloads."""
    raise NotImplementedError


async def read_workflow_run(state: Path, queue_id: str) -> dict[str, Any] | None:
    """The run state of a workflow queue (shape in the module docstring), or None when it is not a workflow queue."""
    raise NotImplementedError


async def decide_branch(state: Path, queue_id: str, node_id: int, branch: tuple[int, int], decision: str) -> None:
    """Answer a branch's "No face detected" pause from any process: ``continue`` or ``stop`` (that branch only)."""
    raise NotImplementedError


async def retry_step(state: Path, queue_id: str, node_id: int, branch: tuple[int, int]) -> str:
    """Retry a failed/unknown step with its original settings, then continue its downstream branch.

    While the run is live the owner applies it in place and this returns ``queue_id``; after the run ended, a new
    paused workflow queue seeded with the step's saved inputs is created and its id returned (the caller starts it)."""
    raise NotImplementedError

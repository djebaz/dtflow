"""Dispatch of a workflow queue's one item: topological, per-branch execution of the workflow graph (#223).

SKELETON (interface fixed by the #223 integration plan; the body is implemented in the executor PR).

Separate from ``QueueDispatcher`` like ``ScriptItemRunner``. Generative nodes run through ``run_generation`` on the
queue's own ``GenerationService``; image nodes use an automatic exact-scope owner probe (D2) prepared by
``_dispatch_one``. Each node hands the next one the saved final artifact from ``output_paths``. A face-mask "no target"
pauses only its branch. A submission counter stops the run past the planned total plus the retry allowance.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.generation import GenerationService


class WorkflowItemRunner:
    def __init__(
        self,
        store: SQLiteAttempts,
        service: GenerationService,
        state: Path,
        *,
        poll: float = 0.5,
        on_settle: Callable[[str, str], None] | None = None,
    ):
        self.store, self.service, self.state, self.poll, self.on_settle = store, service, Path(state), poll, on_settle

    async def run(self, queue_id: str) -> dict[str, Any]:
        raise NotImplementedError

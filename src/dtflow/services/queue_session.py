"""Shared, Qt-independent queue orchestration used by ``dtflow queue`` and the desktop.

Creation touches only local state; dispatch makes this process the single owner of the state directory for its whole
duration (the store's ownership lock); controls and reads need no ownership. Nothing here replays an attempt or uploads.
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dtflow.composition import BackendSelection
from dtflow.core.models import CredentialProvider, ResolvedGenerationRequest, TimeoutPolicy, ValidationError
from dtflow.persistence.queues import post_control, read_queues
from dtflow.persistence.settings import load_cloud_concurrency, save_cloud_concurrency, validate_concurrency
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services import session
from dtflow.services.generation import GenerationService
from dtflow.services.planning import Plan, PlanError, expand_plan
from dtflow.services.queue import QueueDispatcher

log = logging.getLogger(__name__)


def expand_items(base: ResolvedGenerationRequest, count: int) -> list[ResolvedGenerationRequest]:
    """Independent queue items: item N uses seed + N - 1 and a fresh request identity, in queue order.

    This is the planner's ``seed`` mode, so count-based and planned queues share one expansion path.
    """
    try:
        return expand_plan(base, Plan("seed", count=count))
    except PlanError as exc:
        raise ValidationError(str(exc)) from exc


def default_cap(state: Path, backend_name: str) -> int:
    """The shared cloud maximum for cloud queues; local and fake queues use the adapter's own bound (4)."""
    return load_cloud_concurrency(state) if backend_name == "cloud" else 4


async def create_queue(
    state: Path,
    backend: dict[str, Any],
    requests: list[ResolvedGenerationRequest],
    cap: int | None = None,
) -> tuple[str, int]:
    """Persist a paused queue of resolved items; contacts no backend and never uploads."""
    if backend.get("name") == "local" and not backend.get("endpoint"):
        raise ValidationError("The local backend requires --endpoint HOST:PORT.")
    cap = validate_concurrency(cap) if cap is not None else default_cap(state, backend["name"])
    store = SQLiteAttempts(state)
    try:
        await store.open()
        return await store.create_queue(backend, cap, requests), cap
    finally:
        await store.aclose()


async def dispatch_queue(
    state: Path,
    queue_id: str,
    *,
    saved: CredentialProvider | None = None,
    timeouts: TimeoutPolicy | None = None,
    out: Path | None = None,
    on_settle: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Make this process the dispatch owner and run the queue until it finishes, is cancelled or is blocked.

    A held ownership lock raises ``StateBusyError`` before anything is dispatched. Interrupting (cancelling) this
    coroutine records honest local-stop evidence through the generation service.
    """
    policy = timeouts or TimeoutPolicy()
    store = SQLiteAttempts(state)
    backend = None
    try:
        await store.open()
        queue = await store.inspect_queue(queue_id)
        recorded = queue["backend"]
        selection = BackendSelection(
            recorded["name"],
            recorded.get("endpoint"),
            load_cloud_concurrency(state) if recorded["name"] == "cloud" else 4,
            recorded.get("scenario", "success"),
            recorded.get("delay", 0.0),
            recorded.get("previews", False),
        )
        backend = session.create_backend(selection, timeouts=policy, saved=saved)
        async with asyncio.timeout(policy.connect):
            await backend.open()
        service = GenerationService(
            backend, store, out or state / "outputs", timeouts=policy, assets=session.asset_validator(backend)
        )
        # Real adapters accept only inventory-listed models, so refresh once before dispatching.
        await service.get_inventory(refresh=True)
        log.info("command=queue start backend=%s", recorded["name"])
        return await QueueDispatcher(store, service, on_settle=on_settle).run(queue_id)
    finally:
        try:
            if backend is not None:
                async with asyncio.timeout(policy.cleanup):
                    await backend.aclose()
        finally:
            await store.aclose()


async def read_queue_views(state: Path, queue_id: str | None = None) -> tuple[dict[str, Any], ...]:
    """Read-only queue views (no ownership, no lock); a state directory with no database has no queues."""
    if not (Path(state) / "attempts.sqlite3").is_file():
        return ()
    return await asyncio.to_thread(read_queues, Path(state), queue_id)


async def post_queue_control(state: Path, queue_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    """Append one control for the dispatch owner to apply (at its next poll, or at the next start)."""
    await asyncio.to_thread(post_control, Path(state), queue_id, action, payload)


async def set_concurrency(state: Path, value: int, *, queue_id: str | None = None, save_shared: bool = False) -> int:
    """Validate a concurrency maximum (1..4) before anything is written or posted.

    ``save_shared`` persists the shared cloud maximum used for new cloud queues; ``queue_id`` posts a ``cap`` control
    for that queue (active work is never cancelled by it). The value is a client policy, not verified entitlement.
    """
    value = validate_concurrency(value)
    if save_shared:
        await asyncio.to_thread(save_cloud_concurrency, Path(state), value)
    if queue_id is not None:
        await post_queue_control(state, queue_id, "cap", {"cap": value})
    return value

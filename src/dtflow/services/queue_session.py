"""Shared, Qt-independent queue orchestration used by ``dtflow queue`` and the desktop.

Creation touches only local state; dispatch makes this process the single owner of the state directory for its whole
duration (the store's ownership lock); controls and reads need no ownership. Nothing here replays an attempt or uploads.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.composition import BackendSelection
from dtflow.core.image_inputs import PreparedImageInputs
from dtflow.core.models import (
    BackendEvent,
    CredentialProvider,
    ResolvedGenerationRequest,
    TimeoutPolicy,
    ValidationError,
)
from dtflow.core.tasks import settle
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.gallery_snapshot import schedule_publish
from dtflow.persistence.queues import (
    change_up_next,
    delete_idle_queues,
    delete_queue,
    insert_queue,
    post_control,
    read_queue_summaries,
    read_queues,
)
from dtflow.persistence.settings import load_cloud_concurrency, save_cloud_concurrency, validate_concurrency
from dtflow.persistence.store import SQLiteAttempts, StateBusyError, state_is_owned
from dtflow.services import session
from dtflow.services.generation import GenerationService
from dtflow.services.image_inputs import restore_image_inputs, snapshot_image_inputs
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
    *,
    up_next: bool = False,
) -> tuple[str, int]:
    """Persist a paused queue of resolved items; contacts no backend and never uploads.

    While another owner holds the state (this window's running dispatch or generation, or a CLI dispatcher), the queue
    is inserted over a short shared connection instead: it stays paused and nothing dispatches it until Start. With
    ``up_next`` it is queued last behind that live dispatch owner, which starts it after its current queue. Without a
    live owner there is nothing to wait behind, so the queue is saved paused.
    """
    if backend.get("name") == "local" and not backend.get("endpoint"):
        raise ValidationError("The local backend requires --endpoint HOST:PORT.")
    cap = validate_concurrency(cap) if cap is not None else default_cap(state, backend["name"])
    store = SQLiteAttempts(state)
    try:
        await store.open()
        return await store.create_queue(backend, cap, requests), cap
    except StateBusyError:
        return await asyncio.to_thread(insert_queue, state, backend, cap, requests, up_next=up_next), cap
    finally:
        await store.aclose()


async def create_edit_queue(
    state: Path,
    backend: dict[str, Any],
    selection: BackendSelection,
    base: ResolvedGenerationRequest,
    prepared: PreparedImageInputs,
    *,
    strength: float,
    preserve_outside: bool,
    plan: Plan | None = None,
    count: int = 1,
    cap: int | None = None,
    up_next: bool = False,
) -> tuple[str, int]:
    """Stage one prepared edit once and persist a paused queue of items that share it.

    Each item records the owner-probe consent for its exact backend, checkpoint and operation; nothing is uploaded or
    dispatched until Start, and the staged inputs stay durable evidence. Returns (queue id, cap).
    """
    operation = "inpaint" if prepared.mask is not None else "img2img"
    evidence_id = f"queue-{uuid4().hex}"
    permit = session.owner_probe_permit(selection, base.model_id, operation, evidence_id)
    permit.validate_scope(
        permit.backend_id, session.image_endpoint_label(selection), base.model_id, operation, allow_probe=True
    )
    # Pixel validation and the exclusive durable write run on a worker, never on the orchestration loop.
    staged = await settle(
        asyncio.to_thread(snapshot_image_inputs, state, uuid4().hex, prepared, preserve_outside=preserve_outside)
    )
    edit = replace(
        base,
        request_id=uuid4().hex,
        operation=operation,
        strength=strength,
        image_inputs=staged,
        resolution_evidence=(
            *base.resolution_evidence,
            ("image_permit_evidence", evidence_id),
            ("image_permit_status", permit.status),
        ),
    )
    items = expand_items(edit, count) if plan is None else expand_plan(edit, plan)
    return await create_queue(state, backend, items, cap, up_next=up_next)


async def dispatch_queue(
    state: Path,
    queue_id: str,
    *,
    saved: CredentialProvider | None = None,
    timeouts: TimeoutPolicy | None = None,
    out: Path | None = None,
    on_settle: Callable[[str, str], None] | None = None,
    on_event: Callable[[str, BackendEvent], None] | None = None,
    on_next: Callable[[dict[str, Any], str], None] | None = None,
    auto_play: bool = True,
) -> dict[str, Any]:
    """Make this process the dispatch owner and run the queue until it finishes or is cancelled, then auto-play.

    A held ownership lock raises ``StateBusyError`` before anything is dispatched. Interrupting (cancelling) this
    coroutine records honest local-stop evidence through the generation service. With ``auto_play`` the owner accepts
    queues up next while it runs; when a queue settles it atomically claims and starts the first one waiting
    (``on_next`` receives the settled view and the next id) and returns the last settled view. Whenever it stops, by
    failure or interruption included, every queue still waiting is restored paused: nothing starts without a live owner.
    Without ``auto_play`` (the CLI's ``queue start``) it runs exactly the queue it names.
    """
    policy = timeouts or TimeoutPolicy()
    store = SQLiteAttempts(state)
    owned, reason, claimed = False, "stopped", None
    try:
        await store.open()
        owned = True
        if auto_play:
            await store.open_up_next()
        while True:
            final = await _dispatch_one(store, state, queue_id, policy, saved, out, on_settle, on_event)
            claimed = None
            if not auto_play:
                return final
            schedule_publish(state, reason="queue_settled")
            following = await store.claim_queued()  # an empty order also closes it to new arrivals
            if following is None:
                reason = "drained"
                return final
            diagnostic_event(log, "queue.auto_play", level=logging.INFO, data={"settled": final["status"]})
            if on_next is not None:
                on_next(final, following)
            queue_id = claimed = following
    finally:
        try:
            if owned and auto_play:
                try:
                    await store.release_queued(reason, claimed)
                except Exception as exc:
                    diagnostic_event(
                        log,
                        "queue.up_next_not_released",
                        level=logging.WARNING,
                        data={"reason": type(exc).__name__},
                    )
        finally:
            await store.aclose()
            schedule_publish(state, reason="queue_session_end")


async def _dispatch_one(store, state, queue_id, policy, saved, out, on_settle, on_event) -> dict[str, Any]:
    """One queue on the open owner: its own recorded backend session, closed before the next queue starts."""
    backend = None
    try:
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
        # Staged edits carry their own recorded per-item consent; each distinct checkpoint/operation gets one exact
        # owner-probe permit for this dispatch. No consent, no permit: such items are refused before any upload.
        permits = tuple(
            session.owner_probe_permit(selection, model, operation, evidence)
            for model, operation, evidence in await store.image_scopes(queue_id)
        )
        image_options = (
            {
                "image_permits": permits,
                "image_inputs_loader": lambda snapshot: restore_image_inputs(state, snapshot),
                "allow_image_probe": True,
            }
            if permits
            else {}
        )
        backend = session.create_backend(selection, timeouts=policy, saved=saved, **image_options)
        async with asyncio.timeout(policy.connect):
            await backend.open()
        service = GenerationService(
            backend,
            store,
            out or state / "outputs",
            timeouts=policy,
            assets=session.asset_validator(backend),
            input_state=state if permits else None,
        )
        # Real adapters accept only inventory-listed models, so refresh once before dispatching.
        await service.get_inventory(refresh=True)
        diagnostic_event(
            log,
            "queue.command",
            level=logging.INFO,
            data={
                "backend": recorded["name"],
                "command": "queue",
            },
        )
        return await QueueDispatcher(store, service, on_settle=on_settle, on_event=on_event).run(queue_id)
    finally:
        if backend is not None:
            async with asyncio.timeout(policy.cleanup):
                await backend.aclose()


async def read_queue_views(state: Path, queue_id: str | None = None) -> tuple[dict[str, Any], ...]:
    """Read-only queue views (no ownership, no lock); a state directory with no database has no queues."""
    if not (Path(state) / "attempts.sqlite3").is_file():
        return ()
    return await asyncio.to_thread(read_queues, Path(state), queue_id)


async def read_queue_list(state: Path, described: tuple[str, ...] = ()) -> tuple[dict[str, Any], ...]:
    """Summary-only queue views (no items or events) for a list; ``described`` skips the item-variation scan."""
    if not (Path(state) / "attempts.sqlite3").is_file():
        return ()
    views = await asyncio.to_thread(read_queue_summaries, Path(state), described=described)
    owned = await asyncio.to_thread(state_is_owned, Path(state))
    return tuple(view | {"owner_active": owned} for view in views)


async def post_queue_control(state: Path, queue_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    """Append one control for the dispatch owner to apply (at its next poll, or at the next start)."""
    await asyncio.to_thread(post_control, Path(state), queue_id, action, payload)


async def change_queue_order(state: Path, queue_id: str, action: str) -> None:
    """Up next: ``enqueue`` a paused queue last, ``dequeue`` it back to paused, or move it ``earlier``/``later``."""
    await asyncio.to_thread(change_up_next, Path(state), queue_id, action)


async def clear_queues(state: Path, keep: tuple[str, ...] = ()) -> int:
    """Clear history: delete finished and cancelled queue records (never attempts or outputs); return how many went."""
    if not (Path(state) / "attempts.sqlite3").is_file():
        return 0
    return await asyncio.to_thread(delete_idle_queues, Path(state), keep)


async def remove_queue(state: Path, queue_id: str, keep: tuple[str, ...] = ()) -> None:
    """Delete one paused, finished or cancelled queue record (never attempts or outputs)."""
    await asyncio.to_thread(delete_queue, Path(state), queue_id, keep)


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

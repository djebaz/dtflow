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
from dtflow.persistence.gallery_snapshot import schedule_publish
from dtflow.persistence.queues import delete_idle_queues, post_control, read_queues
from dtflow.persistence.settings import load_cloud_concurrency, save_cloud_concurrency, validate_concurrency
from dtflow.persistence.store import SQLiteAttempts
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
    return await create_queue(state, backend, items, cap)


async def dispatch_queue(
    state: Path,
    queue_id: str,
    *,
    saved: CredentialProvider | None = None,
    timeouts: TimeoutPolicy | None = None,
    out: Path | None = None,
    on_settle: Callable[[str, str], None] | None = None,
    on_event: Callable[[str, BackendEvent], None] | None = None,
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
        log.info("command=queue start backend=%s", recorded["name"])
        return await QueueDispatcher(store, service, on_settle=on_settle, on_event=on_event).run(queue_id)
    finally:
        try:
            if backend is not None:
                async with asyncio.timeout(policy.cleanup):
                    await backend.aclose()
        finally:
            await store.aclose()
            schedule_publish(state, reason="queue_session_end")


async def read_queue_views(state: Path, queue_id: str | None = None) -> tuple[dict[str, Any], ...]:
    """Read-only queue views (no ownership, no lock); a state directory with no database has no queues."""
    if not (Path(state) / "attempts.sqlite3").is_file():
        return ()
    return await asyncio.to_thread(read_queues, Path(state), queue_id)


async def post_queue_control(state: Path, queue_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    """Append one control for the dispatch owner to apply (at its next poll, or at the next start)."""
    await asyncio.to_thread(post_control, Path(state), queue_id, action, payload)


async def clear_queues(state: Path, keep: tuple[str, ...] = ()) -> int:
    """Delete finished, cancelled and paused queue records (never attempts or outputs); return how many went."""
    if not (Path(state) / "attempts.sqlite3").is_file():
        return 0
    return await asyncio.to_thread(delete_idle_queues, Path(state), keep)


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

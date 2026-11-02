"""Shared, Qt-independent queue orchestration used by ``dtflow queue`` and the desktop.

Creation touches only local state; dispatch makes this process the single owner of the state directory for its whole
duration (the store's ownership lock); controls and reads need no ownership. Nothing here replays an attempt or uploads.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.composition import BackendSelection
from dtflow.core.image_inputs import PreparedImageInputs
from dtflow.core.models import (
    SEED_LIMIT,
    BackendEvent,
    CredentialProvider,
    PreparedAsset,
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
    delete_paused_queues,
    delete_queue,
    insert_queue,
    insert_script_queue,
    post_control,
    read_queue_requests,
    read_queue_composer_source,
    read_queue_summaries,
    read_queues,
    request_from_record,
)
from dtflow.persistence.script_items import is_script_record, script_record, store_source
from dtflow.persistence.settings import load_cloud_concurrency, save_cloud_concurrency, validate_concurrency
from dtflow.persistence.store import SQLiteAttempts, StateBusyError, state_is_owned
from dtflow.services import session
from dtflow.services.generation import GenerationService
from dtflow.services.image_inputs import restore_image_inputs, snapshot_image_inputs
from dtflow.services.planning import Plan, PlanError, expand_plan
from dtflow.services.queue import QueueDispatcher
from dtflow.services.script_item import ScriptItemRunner

log = logging.getLogger(__name__)


def expand_items(base: ResolvedGenerationRequest, count: int) -> list[ResolvedGenerationRequest]:
    """Independent queue items: item N uses seed + N - 1 and a fresh request identity, in queue order.

    This is the planner's ``seed`` mode, so count-based and planned queues share one expansion path.
    """
    try:
        return expand_plan(base, Plan("seed", count=count))
    except PlanError as exc:
        raise ValidationError(str(exc)) from exc


SCRIPT_REFUSED = "A script queue cannot be relaunched, retried or edited as a copy; press Start on the script again."


def refuse_script_records(records) -> None:
    if any(is_script_record(record) for record in records):
        raise ValidationError(SCRIPT_REFUSED)


async def create_script_queue(
    state: Path,
    backend: dict[str, Any],
    source: str,
    source_name: str,
    base: ResolvedGenerationRequest,
    *,
    max_generations: int = 4,
    answers: list | None = None,
    up_next: bool = False,
) -> tuple[str, str]:
    """Persist a paused script queue: exactly one item (a whole script run), cap 1. Returns ``(queue id, run id)``.

    The run id is generated now, so the item names its run durably before anything starts. Contacts no backend, never
    generates and never uploads; Start dispatches it like any queue.
    """
    if backend.get("name") == "local" and not backend.get("endpoint"):
        raise ValidationError("The local backend requires --endpoint HOST:PORT.")
    if not 1 <= max_generations <= 32:
        raise ValidationError("max_generations must be between 1 and 32.")
    if base.operation != "txt2img" or base.image_inputs is not None:
        raise ValidationError("Scripts start from text-to-image settings only.")
    sha = await asyncio.to_thread(store_source, state, source)
    run_id = uuid4().hex
    record = script_record(
        run_id=run_id,
        source_name=source_name,
        source_sha256=sha,
        base=asdict(base),
        backend=backend["name"],
        max_generations=max_generations,
        answers=answers,
    )
    store = SQLiteAttempts(state)
    try:
        await store.open()
        return await store.create_script_queue(backend, record), run_id
    except StateBusyError:
        return await asyncio.to_thread(insert_script_queue, state, backend, record, up_next=up_next), run_id
    finally:
        await store.aclose()


def default_cap(state: Path, backend_name: str) -> int:
    """The shared cloud maximum for cloud queues; local and fake queues use the adapter's own bound (4)."""
    return load_cloud_concurrency(state) if backend_name == "cloud" else 4


OVERLAP_POLL = 0.5  # seconds between looks for a free slot at a queue boundary


async def create_queue(
    state: Path,
    backend: dict[str, Any],
    requests: list[ResolvedGenerationRequest],
    cap: int | None = None,
    *,
    up_next: bool = False,
    composer_source: dict | None = None,
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
        return await store.create_queue(backend, cap, requests, composer_source=composer_source), cap
    except StateBusyError:
        return await asyncio.to_thread(
            insert_queue, state, backend, cap, requests, up_next=up_next, composer_source=composer_source
        ), cap
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
    receipts: tuple[PreparedAsset, ...] = (),
    composer_source: dict | None = None,
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
    items = expand_items(edit, count) if plan is None else expand_plan(edit, plan, receipts=receipts)
    return await create_queue(state, backend, items, cap, up_next=up_next, composer_source=composer_source)


# Items a relaunch with ``failed_only`` (and Retry item) takes again: settled without a saved image.
RELAUNCH_STATES = ("failed", "unknown", "save_failed", "cancelled")


def _relaunch_items(
    state: Path,
    source: str,
    items: tuple[tuple[int, str, Any], ...],
    *,
    new_seeds: bool,
    failed_only: bool,
    positions: tuple[int, ...] | None,
) -> list[ResolvedGenerationRequest]:
    """Fresh copies of recorded item requests: new identities, the same settings, optionally new seeds."""
    if positions is not None and failed_only:
        raise ValidationError("Choose items or only failed items, not both; nothing was created.")
    if positions is not None:
        known = {position for position, _, _ in items}
        missing = sorted(set(positions) - known)
        if missing:
            raise ValidationError(f"The queue has no item {missing[0]}; nothing was created.")
        chosen = [item for item in items if item[0] in positions]
    elif failed_only:
        chosen = [item for item in items if item[1] in RELAUNCH_STATES]
        if not chosen:
            raise ValidationError("No failed, cancelled or no-result items to relaunch; nothing was created.")
    else:
        chosen = list(items)
    # New seeds keep the plan's shape: items that shared a seed share one new seed.
    reseeded: dict[int, int] = {}
    checked: set[str] = set()  # staged inputs shared by several items are verified once
    requests = []
    for position, _, record in chosen:
        try:
            request = request_from_record(record)
        except (ValidationError, TypeError, ValueError):
            raise ValidationError(f"Item {position} has no usable recorded request; nothing was created.") from None
        if request.image_inputs is not None and request.image_inputs.attempt_id not in checked:
            checked.add(request.image_inputs.attempt_id)
            try:
                restore_image_inputs(state, request.image_inputs)
            except (ValidationError, OSError):
                raise ValidationError(
                    f"Item {position}'s staged edit inputs are no longer available; nothing was created."
                ) from None
        changes: dict[str, Any] = {"request_id": uuid4().hex}
        if new_seeds:
            changes["seed"] = reseeded.setdefault(request.seed, secrets.randbelow(SEED_LIMIT) + 1)
        # Positions and seed policy describe the source queue; the relaunch records where each item came from.
        stale = ("queue_position", "plan.index", *(("plan.seed", "seed.resolved") if new_seeds else ()))
        changes["resolution_evidence"] = (
            *(pair for pair in request.resolution_evidence if pair[0] not in stale),
            ("relaunch.source_queue", source),
            ("relaunch.source_position", str(position)),
            ("relaunch.seed", "new" if new_seeds else "same"),
        )
        requests.append(replace(request, **changes))
    return requests


async def relaunch_queue(
    state: Path,
    queue_id: str,
    *,
    new_seeds: bool = False,
    failed_only: bool = False,
    positions: tuple[int, ...] | None = None,
    cap: int | None = None,
    up_next: bool = False,
) -> tuple[str, int]:
    """Create a new paused (or up-next) queue from another queue's recorded item requests; returns (id, cap).

    The source queue, its items and attempts are only read: nothing is replayed, an unknown attempt stays unknown, and
    each new item gets its own identity and, when dispatched, its own attempt (a real and possibly billable
    generation, like Start). ``positions`` takes exactly those items (Retry item), ``failed_only`` the items that
    settled without a saved image. Staged edit items reuse their recorded inputs and owner-probe consent, which the
    dispatcher validates again at Start; inputs that are gone refuse the relaunch.
    """
    backend, source_cap, items = await asyncio.to_thread(read_queue_requests, Path(state), queue_id)
    refuse_script_records(record for _, _, record in items)
    requests = await asyncio.to_thread(
        _relaunch_items, Path(state), queue_id, items, new_seeds=new_seeds, failed_only=failed_only, positions=positions
    )
    return await create_queue(state, backend, requests, source_cap if cap is None else cap, up_next=up_next)


async def read_queue_records(state: Path, queue_id: str) -> tuple[dict[str, Any], ...]:
    """The stored request records of a queue's items in order (read-only), for editing it as a new queue."""
    _, _, items = await asyncio.to_thread(read_queue_requests, Path(state), queue_id)
    refuse_script_records(record for _, _, record in items)
    if any(not isinstance(record, dict) for _, _, record in items):
        raise ValidationError("Some items' recorded requests are unreadable, so this queue cannot be edited.")
    return tuple(record for _, _, record in items)


async def read_composer_source(state: Path, queue_id: str) -> dict | None:
    """Read saved authoring input, if present; no inferred reconstruction or provider access."""
    return await asyncio.to_thread(read_queue_composer_source, Path(state), queue_id)


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
    on_overlap: Callable[[str], None] | None = None,
    auto_play: bool = True,
) -> dict[str, Any]:
    """Make this process the dispatch owner and run the queue until it finishes or is cancelled, then auto-play.

    A held ownership lock raises ``StateBusyError`` before anything is dispatched. Interrupting (cancelling) this
    coroutine records honest local-stop evidence through the generation service. With ``auto_play`` the owner accepts
    queues up next while it runs; when a queue settles it atomically claims and starts the first one waiting
    (``on_next`` receives the settled view and the next id) and returns the last settled view. Whenever it stops, by
    failure or interruption included, every queue still waiting is restored paused: nothing starts without a live owner.
    A queue started early beside a running one is announced with ``on_overlap``; ``on_next`` still reports each queue
    only when it truly settles. Without ``auto_play`` (the CLI's ``queue start``) it runs exactly the queue it names.
    """
    policy = timeouts or TimeoutPolicy()
    store = SQLiteAttempts(state)
    owned, reason = False, "stopped"
    chain: list[tuple[str, asyncio.Task]] = []  # dispatch order; the head is the oldest queue still unsettled
    claimed: set[str] = set()  # queues this owner auto-started and has not seen settle
    limits: dict[str, int] = {}  # each open queue's backend maximum, known once its backend is open
    overlap_open = True

    def start(next_id: str) -> None:
        chain.append(
            (
                next_id,
                asyncio.create_task(
                    _dispatch_one(store, state, next_id, policy, saved, out, on_settle, on_event, limits)
                ),
            )
        )

    async def claim(**kwargs) -> str | None:
        """Claim up next, never abandoning a started queue to a cancellation that lands mid-claim."""
        task = asyncio.ensure_future(store.claim_queued(**kwargs))
        try:
            found = await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                found = await task
            except Exception:
                found = None
            if found is not None:
                claimed.add(found)
            raise
        if found is not None:
            claimed.add(found)
        return found

    try:
        await store.open()
        owned = True
        if auto_play:
            await store.open_up_next()
        start(queue_id)
        final: dict[str, Any] = {}
        while True:
            head_id, head = chain[0]
            for entry in chain[1:]:
                other_id, task = entry
                if task.done() and not task.cancelled() and task.exception() is not None:
                    # An overlapped queue failing on its own (backend, inventory, persistence) must not take the healthy
                    # queue ahead of it down: it goes back to paused and early starts stop for this session.
                    exc = task.exception()
                    diagnostic_event(
                        log, "queue.overlap_failed", level=logging.WARNING, data={"reason": type(exc).__name__}
                    )
                    chain.remove(entry)
                    claimed.discard(other_id)
                    overlap_open = False
                    await store.release_queued("overlap_failed", other_id)
                    break
            else:
                if head.done() and not head.cancelled() and head.exception() is not None:
                    raise head.exception()  # type: ignore[misc]
                if head.done():
                    chain.pop(0)
                    final = head.result()
                    claimed.discard(head_id)
                    limits.pop(head_id, None)
                    if not auto_play:
                        return final
                    schedule_publish(state, reason="queue_settled")
                    # A queue that already overlapped its predecessor is the next one: it keeps running undisturbed.
                    following = chain[0][0] if chain else await claim()  # an empty order also closes it
                    if following is None:
                        reason = "drained"
                        return final
                    diagnostic_event(log, "queue.auto_play", level=logging.INFO, data={"settled": final["status"]})
                    if on_next is not None:
                        on_next(final, following)
                    if not chain:
                        start(following)
                    continue
                if auto_play and overlap_open:
                    # Cross-queue overlap: once the newest unsettled queue has handed out its last item, a free slot on
                    # the shared backend is reused by the next up-next queue. The combined cap is still enforced item by
                    # item at claim.
                    live = [(qid, task) for qid, task in chain if not task.done()]
                    if live and live[-1][0] in limits and await store.overlap_ready(live[-1][0]):
                        tail_id = live[-1][0]
                        overlapping = await claim(overlap_after=tail_id, limit=limits[tail_id])
                        if overlapping is not None:
                            diagnostic_event(
                                log,
                                "queue.overlap_started",
                                level=logging.INFO,
                                data={"after": tail_id, "queue_id": overlapping, "after_active": len(chain)},
                            )
                            if on_overlap is not None:
                                on_overlap(overlapping)
                            start(overlapping)
                            continue
                pending = [task for _, task in chain if not task.done()]
                if pending:
                    await asyncio.wait(pending, timeout=OVERLAP_POLL, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for _, task in chain:
            task.cancel()
        if chain:
            await asyncio.shield(asyncio.gather(*(task for _, task in chain), return_exceptions=True))
        try:
            if owned and auto_play:
                try:
                    for current in claimed or {None}:
                        await store.release_queued(reason, current)
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


async def _dispatch_one(store, state, queue_id, policy, saved, out, on_settle, on_event, limits=None) -> dict[str, Any]:
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
        if limits is not None:
            limits[queue_id] = backend.capabilities.max_concurrency
        if queue.get("kind") == "script":  # a separate runner: a script bug must not enter the generation loop
            return await ScriptItemRunner(store, service, state, on_settle=on_settle).run(queue_id)
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


async def clear_paused_queues(state: Path, queue_ids: tuple[str, ...], keep: tuple[str, ...] = ()) -> tuple[int, int]:
    """Delete the confirmed paused queue records that are still paused; return ``(removed, skipped)``."""
    if not queue_ids or not (Path(state) / "attempts.sqlite3").is_file():
        return 0, len(queue_ids)
    return await asyncio.to_thread(delete_paused_queues, Path(state), queue_ids, keep)


async def remove_queue(state: Path, queue_id: str, keep: tuple[str, ...] = ()) -> None:
    """Delete one paused, finished or cancelled queue record (never attempts or outputs)."""
    await asyncio.to_thread(delete_queue, Path(state), queue_id, keep)


async def set_concurrency(state: Path, value: int, *, queue_id: str | None = None, save_shared: bool = False) -> int:
    """Validate a concurrency maximum (1..10) before anything is written or posted.

    ``save_shared`` persists the shared cloud maximum used for new cloud queues; ``queue_id`` posts a ``cap`` control
    for that queue (active work is never cancelled by it). The value is a client policy, not verified entitlement.
    """
    value = validate_concurrency(value)
    if save_shared:
        await asyncio.to_thread(save_cloud_concurrency, Path(state), value)
    if queue_id is not None:
        await post_queue_control(state, queue_id, "cap", {"cap": value})
    return value

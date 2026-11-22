"""Shared, Qt-independent queue orchestration used by ``dtflow queue`` and the desktop.

Creation touches only local state; dispatch makes this process the single owner of the state directory for its whole
duration (the store's ownership lock); controls and reads need no ownership. Nothing here replays an attempt or uploads.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.composition import BackendSelection
from dtflow.core.image_inputs import ImageContent, PreparedImageInputs
from dtflow.core.models import (
    MAX_QUEUE_CONCURRENCY,
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
    backend_key,
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
    retry_item,
)
from dtflow.persistence.script_items import is_script_record, script_record, store_source
from dtflow.persistence.settings import load_dtplus_concurrency
from dtflow.persistence.settings import trigger_prefix_enabled
from dtflow.persistence.store import SQLiteAttempts, StateBusyError, state_is_owned
from dtflow.persistence.workflow_items import KIND_WORKFLOW, is_workflow_record
from dtflow.services import session
from dtflow.services.assets import AssetService
from dtflow.services.generation import GenerationService
from dtflow.services.image_inputs import restore_image_inputs, snapshot_image_inputs
from dtflow.services.planning import Plan, PlanError, expand_plan
from dtflow.services.provider_admission import ProviderAdmission, QueueAdmission
from dtflow.services.queue import QueueDispatcher
from dtflow.services.script_item import ScriptItemRunner
from dtflow.services.workflow_item import WorkflowItemRunner

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


WORKFLOW_REFUSED = (
    "A workflow run cannot be relaunched or edited as a queue copy; run the workflow again or Retry its failed step."
)


def refuse_script_records(records) -> None:
    """Script and workflow items are whole runs, never generation requests to copy."""
    records = tuple(records)
    if any(is_script_record(record) for record in records):
        raise ValidationError(SCRIPT_REFUSED)
    if any(is_workflow_record(record) for record in records):
        raise ValidationError(WORKFLOW_REFUSED)


async def create_script_queue(
    state: Path,
    backend: dict[str, Any],
    source: str,
    source_name: str,
    base: ResolvedGenerationRequest,
    *,
    max_generations: int = 30,
    answers: list | None = None,
    up_next: bool = False,
    expected_steps: int | None = None,
    require_start: bool = False,
    prepared_run_id: str | None = None,
    setup_ordinal: int | None = None,
    setup_answers: list | None = None,
    interactive: bool = True,
) -> tuple[str, str]:
    """Persist a paused script queue: exactly one item (a whole script run), cap 1. Returns ``(queue id, run id)``.

    The run id is generated now, so the item names its run durably before anything starts. Contacts no backend, never
    generates and never uploads; Start dispatches it like any queue.
    """
    if backend.get("name") == "local" and not backend.get("endpoint"):
        raise ValidationError("The local backend requires --endpoint HOST:PORT.")
    if not 1 <= max_generations <= 32:
        raise ValidationError("max_generations must be between 1 and 32.")
    if type(interactive) is not bool:
        raise ValidationError("Script interactive input policy must be a boolean.")
    if type(require_start) is not bool or (require_start and (answers is not None or not interactive)):
        raise ValidationError("Script Setup requires an interactive run without supplied answer sets.")
    if base.operation != "txt2img" or base.image_inputs is not None or base.num_frames is not None:
        raise ValidationError("Scripts start from text-to-image settings only.")
    sha = await asyncio.to_thread(store_source, state, source)
    run_id = prepared_run_id or uuid4().hex
    record = script_record(
        run_id=run_id,
        source_name=source_name,
        source_sha256=sha,
        base=asdict(base),
        backend=backend["name"],
        max_generations=max_generations,
        answers=answers,
        expected_steps=expected_steps if type(expected_steps) is int and expected_steps > 0 else None,
        require_start=require_start,
        prepared=prepared_run_id is not None,
        setup_ordinal=setup_ordinal,
        setup_answers=setup_answers,
        interactive=interactive,
    )
    store = SQLiteAttempts(state)
    try:
        await store.open()
        return await store.create_script_queue(backend, record), run_id
    except StateBusyError:
        return await asyncio.to_thread(insert_script_queue, state, backend, record, up_next=up_next), run_id
    finally:
        await store.aclose()


# Generation queues keep no maximum of their own (#492): DT+ queues run as wide as the Connection page DT+ default,
# local and fake queues as wide as the adapter allows. The stored column is only the widest value.
STORED_CAP = MAX_QUEUE_CONCURRENCY


OVERLAP_POLL = 0.5  # seconds between looks for a free slot at a queue boundary


async def create_queue(
    state: Path,
    backend: dict[str, Any],
    requests: list[ResolvedGenerationRequest],
    *,
    up_next: bool = False,
    composer_source: dict | None = None,
) -> str:
    """Persist a paused queue of resolved items; contacts no backend and never uploads.

    While another owner holds the state (this window's running dispatch or generation, or a CLI dispatcher), the queue
    is inserted over a short shared connection instead: it stays paused and nothing dispatches it until Start. With
    ``up_next`` it is queued last behind that live dispatch owner, which starts it after its current queue. Without a
    live owner there is nothing to wait behind, so the queue is saved paused.
    """
    from dtflow.services.video_queue import capture_requests

    requests, composer_source = await settle(
        asyncio.to_thread(capture_requests, state, backend.get("name"), requests, composer_source)
    )
    if backend.get("name") == "local" and not backend.get("endpoint"):
        raise ValidationError("The local backend requires --endpoint HOST:PORT.")
    store = SQLiteAttempts(state)
    try:
        await store.open()
        return await store.create_queue(backend, STORED_CAP, requests, composer_source=composer_source)
    except StateBusyError:
        return await asyncio.to_thread(
            insert_queue, state, backend, STORED_CAP, requests, up_next=up_next, composer_source=composer_source
        )
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
    up_next: bool = False,
    receipts: tuple[PreparedAsset, ...] = (),
    composer_source: dict | None = None,
) -> str:
    """Stage one prepared edit once and persist a paused queue of items that share it.

    The scope is checked now so an unsupported backend refuses before anything is staged; nothing is uploaded or
    dispatched until Start, which is the consent (#244), and the staged inputs stay durable evidence. Returns the
    queue id.
    """
    operation = "inpaint" if prepared.mask is not None else "img2img"
    permit = session.owner_probe_permit(selection, base.model_id, operation, "queue-scope-check")
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
    )
    items = expand_items(edit, count) if plan is None else expand_plan(edit, plan, receipts=receipts)
    return await create_queue(state, backend, items, up_next=up_next, composer_source=composer_source)


async def _check_pose_queue(
    state: Path,
    selection: BackendSelection,
    items: list[ResolvedGenerationRequest],
    control,
    *,
    saved: CredentialProvider | None,
) -> None:
    """Validate all models and the current server's control availability without staging or uploading."""
    if selection.name != "cloud":
        raise ValidationError("A pose queue requires DT+ Cloud.")
    for model_id in dict.fromkeys(item.model_id for item in items):
        await settle(asyncio.to_thread(session._require_flux1, state, model_id))
    async with session.open_session(selection, state, durable=False, saved=saved) as service:
        inventory = await service.get_inventory(refresh=True)
    if not any(
        entry.identifier == control.checkpoint and entry.availability == "listed_in_server_inventory"
        for entry in inventory.entries
    ):
        raise ValidationError("DT+ does not list the selected pose control; no map was staged or uploaded.")


async def create_pose_queue(
    state: Path,
    backend: dict[str, Any],
    selection: BackendSelection,
    base: ResolvedGenerationRequest,
    content: ImageContent,
    evidence: tuple[tuple[str, str], ...],
    control,
    *,
    saved: CredentialProvider | None = None,
    plan: Plan | None = None,
    count: int = 1,
    up_next: bool = False,
    receipts: tuple[PreparedAsset, ...] = (),
    composer_source: dict | None = None,
) -> str:
    """Stage one verified pose map for a cloud queue; creation never uploads or generates.

    Check the exact backend, FLUX.1 model family and Union control inventory before writing staged inputs.
    Each item retains the same immutable map and control; Start is the generation/upload consent.
    """
    if selection.name != "cloud" or backend.get("name") != "cloud":
        raise ValidationError("A pose queue requires DT+ Cloud; no queue was created.")
    if base.pose_control is not None or base.operation != "txt2img" or base.image_inputs is not None:
        raise ValidationError("New pose queues require plain text-to-image settings.")
    if (base.batch_size, base.batch_count) != (1, 1):
        raise ValidationError("A pose queue item generates exactly one image.")

    items = expand_items(base, count) if plan is None else expand_plan(base, plan, receipts=receipts)
    if any((item.width_px, item.height_px) != (base.width_px, base.height_px) for item in items):
        raise ValidationError("A pose queue uses one fitted map size; remove size variations.")
    await _check_pose_queue(state, selection, items, control, saved=saved)

    # Only pose queue creation needs the image stack; ordinary queue imports remain light.
    from dtflow.services.body_pose import fit_pose_map

    prepared, map_evidence = await settle(
        asyncio.to_thread(fit_pose_map, content, dict(evidence), base.width_px, base.height_px)
    )
    pose = session.pose_request(base, control, prepared, map_evidence)
    staged = await settle(
        asyncio.to_thread(snapshot_image_inputs, state, pose.image_inputs.attempt_id, prepared, preserve_outside=False)
    )
    final = [replace(item, schema_version=3, pose_control=pose.pose_control, image_inputs=staged) for item in items]
    if composer_source is not None:
        # Edit as new must load the staged pose, not reconstruct a plain text-to-image base.
        source_base = replace(
            pose,
            image_inputs=staged,
            prepared_assets=(),
            resolution_evidence=tuple(pair for pair in pose.resolution_evidence if pair[0] != "prepared_assets"),
        )
        composer_source = {**composer_source, "base": asdict(source_base)}
    return await create_queue(state, backend, final, up_next=up_next, composer_source=composer_source)


async def create_saved_pose_queue(
    state: Path,
    backend: dict[str, Any],
    selection: BackendSelection,
    base: ResolvedGenerationRequest,
    *,
    saved: CredentialProvider | None = None,
    plan: Plan | None = None,
    count: int = 1,
    up_next: bool = False,
    receipts: tuple[PreparedAsset, ...] = (),
    composer_source: dict | None = None,
) -> str:
    """Create a queue from a recorded pose without changing the retained map or submitting anything."""
    if base.pose_control is None or base.image_inputs is None:
        raise ValidationError("This request has no retained pose to queue.")
    if backend.get("name") != "cloud":
        raise ValidationError("The recorded pose requires DT+ Cloud; no queue was created.")
    items = expand_items(base, count) if plan is None else expand_plan(base, plan, receipts=receipts)
    await _check_pose_queue(state, selection, items, base.pose_control, saved=saved)
    try:
        await settle(asyncio.to_thread(restore_image_inputs, state, base.image_inputs))
    except (ValidationError, OSError) as exc:
        raise ValidationError(f"The retained pose map is unavailable: {exc}. No queue was created.") from exc
    return await create_queue(state, backend, items, up_next=up_next, composer_source=composer_source)


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
                    f"Item {position}'s staged image or pose inputs are no longer available; nothing was created."
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
    up_next: bool = False,
) -> str:
    """Create a new paused (or up-next) queue from another queue's recorded item requests; returns its id.

    The source queue, its items and attempts are only read: nothing is replayed, an unknown attempt stays unknown, and
    each new item gets its own identity and, when dispatched, its own attempt (a real and possibly billable
    generation, like Start). ``positions`` takes exactly those items (Retry item), ``failed_only`` the items that
    settled without a saved image. Staged edit items reuse their recorded inputs; Start is the consent and the
    dispatcher builds their exact-scope permit then; inputs that are gone refuse the relaunch.
    """
    backend, _legacy_cap, items = await asyncio.to_thread(read_queue_requests, Path(state), queue_id)
    refuse_script_records(record for _, _, record in items)
    requests = await asyncio.to_thread(
        _relaunch_items, Path(state), queue_id, items, new_seeds=new_seeds, failed_only=failed_only, positions=positions
    )
    return await create_queue(state, backend, requests, up_next=up_next)


async def retry_queue_item(state: Path, queue_id: str, position: int) -> str:
    """Retry item in place (#240): the item reruns its recorded request, same settings and seed, as a new attempt.

    No new queue is created. Staged edit inputs must still exist (checked before anything changes). Returns
    ``"joins"`` when the queue's own run (or its Start/Resume) picks the item up, ``"start"`` when the queue was set
    paused and the caller starts it. Nothing is dispatched here; an unknown attempt stays recorded as unknown.
    """
    _, _, items = await asyncio.to_thread(read_queue_requests, Path(state), queue_id)
    record = next((record for at, _, record in items if at == position), None)
    if record is None:
        raise ValidationError(f"The queue has no item {position}; nothing was retried.")
    refuse_script_records((record,))
    try:
        request = request_from_record(record)
    except (ValidationError, TypeError, ValueError):
        raise ValidationError(f"Item {position} has no usable recorded request; nothing was retried.") from None
    if request.image_inputs is not None:
        try:
            await asyncio.to_thread(restore_image_inputs, Path(state), request.image_inputs)
        except (ValidationError, OSError):
            raise ValidationError(
                f"Item {position}'s staged image or pose inputs are no longer available; nothing was retried."
            ) from None
    if request.num_frames is not None:
        from dtflow.services.video_queue import check_source

        await asyncio.to_thread(check_source, request)
    return await asyncio.to_thread(retry_item, Path(state), queue_id, position)


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
    backends: dict[str, str] = {}  # each open queue's provider pool key, known once its backend is open
    admission = ProviderAdmission()  # one pool per backend, shared by every queue this owner dispatches
    overlap_open = True

    def start(next_id: str) -> None:
        chain.append(
            (
                next_id,
                asyncio.create_task(
                    _dispatch_one(
                        store,
                        state,
                        next_id,
                        policy,
                        saved,
                        out,
                        on_settle,
                        on_event,
                        backends,
                        admission,
                        admission.next_rank(),
                    )
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
                    backends.pop(head_id, None)
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
                    # Cross-queue overlap: once the newest unsettled queue has handed out its last item, capacity on the
                    # shared backend that no started queue wants is used by the next up-next queue. Its items then
                    # take slots from the same pool, in queue order.
                    live = [(qid, task) for qid, task in chain if not task.done()]
                    if (
                        live
                        and all(qid in backends for qid, _ in live)
                        and admission.spare(backends[live[-1][0]])
                        and await store.overlap_ready(live[-1][0])
                    ):
                        tail_id = live[-1][0]
                        overlapping = await claim(overlap_after=tail_id)
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


async def _dispatch_one(
    store, state, queue_id, policy, saved, out, on_settle, on_event, backends=None, admission=None, rank=None
) -> dict[str, Any]:
    """One queue on the open owner: its own recorded backend session, closed before the next queue starts."""
    backend = None
    try:
        queue = await store.inspect_queue(queue_id)
        recorded = queue["backend"]
        selection = BackendSelection(
            recorded["name"],
            recorded.get("endpoint"),
            load_dtplus_concurrency(state) if recorded["name"] == "cloud" else 4,
            recorded.get("scenario", "success"),
            recorded.get("delay", 0.0),
            recorded.get("previews", False),
        )
        workflow = queue.get("kind") == KIND_WORKFLOW
        if workflow:
            # D2: starting a workflow is the consent for its image nodes; one exact-scope owner probe per distinct
            # checkpoint/operation. The fake backend has no image operations, so its image steps fail plainly.
            permits = (
                tuple(
                    session.owner_probe_permit(selection, model, operation, evidence)
                    for model, operation, evidence in await store.workflow_scopes(queue_id)
                )
                if selection.name in ("local", "cloud")
                else ()
            )
        else:
            # #244: Start is the consent for staged edits too; each distinct checkpoint/operation gets one exact-scope
            # owner-probe permit for this dispatch. Items outside those scopes are refused before any upload.
            permits = tuple(
                session.owner_probe_permit(selection, model, operation, evidence)
                for model, operation, evidence in await store.image_scopes(queue_id)
            )
        video_requests = []
        if not workflow and queue.get("kind") != "script":
            _, _, records = await asyncio.to_thread(read_queue_requests, state, queue_id)
            for _, _, record in records:
                if isinstance(record, dict) and record.get("num_frames") is not None:
                    video_requests.append(request_from_record(record))
            permits += session.video_start_permits(selection, video_requests)
        # Pose is txt2img with an image-input snapshot. It needs the durable loader even though
        # image_scopes contains only img2img/inpaint operations.
        has_pose = not workflow and any(item.get("pose_control") is not None for item in queue["items"])
        if has_pose and selection.name != "cloud":
            raise ValidationError("Pose items require DT+ Cloud; the queue was not dispatched.")
        image_options = {
            **({"image_permits": permits, "allow_image_probe": True} if permits else {}),
            **(
                {"image_inputs_loader": lambda snapshot: restore_image_inputs(state, snapshot)}
                if permits or has_pose
                else {}
            ),
        }
        # A queue session covers ordinary queues, workflows and scripts; none consumes live preview images.
        backend = session.create_backend(selection, timeouts=policy, saved=saved, preview_images=False, **image_options)
        async with asyncio.timeout(policy.connect):
            await backend.open()
        key = backend_key(recorded)
        service = GenerationService(
            backend,
            store,
            out or state / "outputs",
            timeouts=policy,
            assets=session.asset_validator(backend, state),
            input_state=state if permits or has_pose else None,
            preview_images=False,
            admission=(
                QueueAdmission(admission.pool(key, backend.capabilities.max_concurrency), rank)
                if admission is not None
                else None
            ),
        )
        if on_event is not None and isinstance(service.assets, AssetService):

            def show_asset_resolution(report: dict) -> None:
                # UI-only names and error details; JSONL summaries have counts, not filenames.
                if report["restored_names"]:
                    on_event(
                        "_assets",
                        BackendEvent(
                            queue_id,
                            0,
                            "assets_restored",
                            datetime.now(UTC).isoformat(),
                            stage=", ".join(report["restored_names"]),
                        ),
                    )
                if report["error"]:
                    on_event(
                        "_assets",
                        BackendEvent(
                            queue_id,
                            0,
                            "assets_inconclusive" if report["outcome"] == "inconclusive" else "assets_failed",
                            datetime.now(UTC).isoformat(),
                            stage=report["error"],
                        ),
                    )

            # Applies to ordinary queues, Scripts, Workflows and their shared recovery checks.
            service.assets.on_resolution = show_asset_resolution
        # Real adapters accept only inventory-listed models, so refresh once before dispatching.
        await service.get_inventory(refresh=True)
        diagnostic_event(
            log,
            "queue.command",
            level=logging.INFO,
            data={
                "backend": recorded["name"],
                "command": "queue",
                "concurrency": backend.capabilities.max_concurrency,
            },
        )
        if backends is not None:
            backends[queue_id] = key
        if queue.get("kind") == "script":  # a separate runner: a script bug must not enter the generation loop
            return await ScriptItemRunner(store, service, state, on_settle=on_settle).run(queue_id)
        if workflow:  # likewise separate: one item is a whole graph run
            scoped = {(permit.model_id, permit.operation): permit for permit in permits}
            return await WorkflowItemRunner(
                store, service, state, on_settle=on_settle, permits=scoped, on_event=on_event
            ).run(queue_id)
        return await QueueDispatcher(
            store,
            service,
            on_settle=on_settle,
            on_event=on_event,
            trigger_prefix=lambda: trigger_prefix_enabled(state, "queues"),
        ).run(queue_id)
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

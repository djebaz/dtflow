"""Rolling queue dispatch over the shared generation service.

One dispatcher owns the state directory (through the store's ownership lock), claims items one at a time in queue order,
and starts the next eligible item the moment a provider slot is free: there is no group barrier. A slot is taken from
the owner's shared provider pool (``provider_admission``) when the item is claimed and freed when the provider has
finished sampling it, so decoding and saving never hold up the next generation. The claim is durable before an attempt
exists. Nothing here replays an attempt; an item that reached the remote boundary and
came back unknown is settled as ``unknown`` (most likely no result will arrive), releases its reservation and does not
block other work. The one automatic retry (``core.retry_policy``, #241) runs inside the item's reservation as a new
attempt under a new request identity, recorded on the item before it is sent. No database transaction is held while generating.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from dtflow.core.output_layout import OutputLayout, group_name, prompt_label
from dtflow.core.queue_eta import estimate_queue_seconds
from dtflow.core.retry_policy import (
    AUTO_RETRIES,
    AUTO_RETRY_DELAY,
    auto_retry_limit,
    auto_retry_request,
    LoraStreak,
    auto_retryable,
    refresh_failed,
)
from dtflow.core.models import (
    BackendError,
    BackendEvent,
    BackendFailure,
    ErrorCategory,
    RemoteOutcome,
    ResolvedGenerationRequest,
    ValidationError,
)
from dtflow.core.tasks import settle
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.queues import QueueError, post_control, read_queue_requests
from dtflow.services.preflight import apply_trigger_prefixes
from dtflow.services import lora_run_notes as notes
from dtflow.services.lora_naming import log_label
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.core.request_records import request_from_record
from dtflow.services.assets import possible_missing_asset
from dtflow.services.generation import GenerationService
from dtflow.services.provider_admission import Permit, ProviderAdmission, QueueAdmission
from dtflow.services.image_inputs import restore_image_inputs
from dtflow.services.session import _require_flux1

log = logging.getLogger(__name__)

RECORDED_BACKEND_EVENTS = {"preparing", "submission_possible", "artifact_received", "terminal"}


def _failure_data(failure: dict[str, Any]) -> dict[str, Any]:
    """The item's recorded failure for its settled record: the explanation Queues shows, not a generic summary."""
    return {
        key: failure[key]
        for key in ("code", "message", "category", "submission_stage", "remote_outcome")
        if failure.get(key) is not None
    }


class QueueDispatcher:
    def __init__(
        self,
        store: SQLiteAttempts,
        service: GenerationService,
        *,
        poll: float = 0.5,
        on_settle: Callable[[str, str], None] | None = None,
        on_event: Callable[[str, BackendEvent], None] | None = None,
        trigger_prefix: Callable[[], bool] | None = None,
    ):
        self.store, self.service, self.poll, self.on_settle, self.on_event = store, service, poll, on_settle, on_event
        if service.admission is None:
            # A caller with no shared owner (tests, direct use) gets a private pool sized to its own backend.
            owner = ProviderAdmission()
            caps = service.backend.capabilities
            service.admission = QueueAdmission(
                owner.pool(f"{caps.backend_id}:-", caps.max_concurrency), owner.next_rank()
            )
        self.admission: QueueAdmission = service.admission
        self._trigger_prefix = trigger_prefix  # the Queues switch, read when each item runs (#373)
        self._asset_recovery: asyncio.Task | None = None
        self._recovery_active = False
        self._recovery_attempted: set[tuple[str, str, int]] = set()
        self._unprepared_noted: set[str] = set()
        self._unprepared_unavailable = False  # the advisory check failed once; later items skip it
        self._recovery_assets: frozenset[tuple[str, str, int]] = frozenset()
        self._queue_assets: tuple = ()
        # Consecutive items that ended with DT+'s generic error per LoRA, to stop a queue full of a broken file (#361).
        self._lora_streak = LoraStreak()

    async def run(self, queue_id: str) -> dict[str, Any]:
        """Dispatch until the queue is finished or cancelled.

        A paused queue with nothing active keeps polling so a posted Resume can continue it; interrupting the process
        (Ctrl-C) ends dispatch and records honest evidence for whatever was active.
        """
        tasks: dict[asyncio.Task, str] = {}
        failure: BaseException | None = None
        await self.store.apply_controls(queue_id)
        await self.store.set_running(queue_id)
        try:
            # Resolve all distinct first-use receipts before opening up to ten generation lanes.
            # Legacy and cross-key receipts share one BYOM batch, not one check per item.
            assets = getattr(self.service, "assets", None)
            if (
                assets is not None
                and getattr(assets, "state", None) is not None
                and callable(getattr(assets, "ensure_many", None))
            ):
                try:
                    _, _, records = await asyncio.to_thread(read_queue_requests, self.store.directory, queue_id)
                    distinct = {}
                    for _, state, record in records:
                        if state != "pending" or not isinstance(record, dict) or "model_id" not in record:
                            continue
                        try:
                            request = request_from_record(record)
                        except (TypeError, ValueError):
                            # Leave malformed items to the established claim/settlement path.
                            continue
                        for prepared in request.prepared_assets:
                            distinct[(prepared.identifier, prepared.sha256, prepared.size)] = prepared
                    if distinct:
                        self._queue_assets = tuple(distinct.values())
                        preparation = asyncio.create_task(assets.ensure_many(self._queue_assets))
                        try:
                            while not preparation.done():
                                actions = await self.store.apply_controls(queue_id)
                                if "cancel" in actions or "pause" in actions:
                                    preparation.cancel()
                                    await asyncio.gather(preparation, return_exceptions=True)
                                    return await self.store.inspect_queue(queue_id)
                                await asyncio.sleep(self.poll)
                            await preparation
                        finally:
                            if not preparation.done():
                                preparation.cancel()
                                await asyncio.gather(preparation, return_exceptions=True)
                except BackendError as exc:
                    diagnostic_event(
                        log,
                        "queue.asset_prepare_blocked",
                        level=logging.WARNING,
                        data={"code": exc.failure.code, "queue_id": queue_id},
                    )
                    await asyncio.to_thread(post_control, self.store.directory, queue_id, "pause")
                    await self.store.apply_controls(queue_id)
                    return await self.store.inspect_queue(queue_id)
            while True:
                applied = await self.store.apply_controls(queue_id)
                if "cancel" in applied:
                    # Interruption of running attempts is local only; the service records any remote uncertainty.
                    if self._asset_recovery is not None and not self._asset_recovery.done():
                        self._asset_recovery.cancel()
                    for task in tasks:
                        task.cancel()
                changed = self.admission.pool.watch()  # armed before the claims, so a freed slot is never missed
                try:
                    while failure is None and not self._recovery_active:
                        # The pool hands a slot to the oldest queue that wants one; this queue says whether it does.
                        ready = await self.store.has_pending(queue_id)
                        self.admission.want(ready)
                        permit = self.admission.reserve() if ready else None
                        if permit is None:
                            break
                        claim = await self.store.claim_next(queue_id, None)
                        if claim is None:
                            permit.close()
                            break
                        item_id, request = claim
                        task = asyncio.create_task(self._execute(queue_id, item_id, request, permit))
                        tasks[task] = item_id
                    if tasks:
                        done, _ = await asyncio.wait(
                            (*tasks, changed), timeout=self.poll, return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in done - {changed}:
                            del tasks[task]
                            if not task.cancelled() and task.exception() is not None:
                                failure = failure or task.exception()
                        continue
                    if failure is not None:
                        raise failure
                    state = await self.store.inspect_queue(queue_id)
                    if state["state"] == "cancelled" or not (
                        state["summary"]["pending"] or state["summary"]["running"]
                    ):
                        return state
                    await asyncio.wait((changed,), timeout=self.poll)
                finally:
                    self.admission.pool.unwatch(changed)
        finally:
            self.admission.forget()
            if self._asset_recovery is not None and not self._asset_recovery.done():
                self._asset_recovery.cancel()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))

    async def _try_asset_recovery(
        self, queue_id: str, item_id: str, request: ResolvedGenerationRequest, failure: BackendFailure
    ) -> bool:
        """Hold new admissions while one bounded LoRA repair serves concurrent queue items."""
        assets = getattr(self.service, "assets", None)
        if (
            assets is None
            or not callable(getattr(assets, "recover_missing", None))
            or not request.prepared_assets
            or not possible_missing_asset(failure)
        ):
            return False
        wanted = {(item.identifier, item.sha256, item.size) for item in request.prepared_assets}
        running = self._asset_recovery
        if running is not None and not running.done():
            restored = await asyncio.shield(running)
            if restored and wanted & self._recovery_assets:
                return True

        pending = tuple(
            item
            for item in request.prepared_assets
            if (item.identifier, item.sha256, item.size) not in self._recovery_attempted
        )
        if not pending:
            return False
        identities = frozenset((item.identifier, item.sha256, item.size) for item in pending)
        self._recovery_attempted.update(identities)
        self._recovery_assets = identities
        self._recovery_active = True

        def progress(stage: str) -> None:
            if self.on_event is not None:
                self.on_event(
                    item_id,
                    BackendEvent(
                        request.request_id,
                        0,
                        "preparing",
                        datetime.now(timezone.utc).isoformat(),
                        stage=stage,
                    ),
                )

        async def recover():
            try:
                progress("restoring_assets")
                await self._record_event(queue_id, "asset_recovery_started", {"kind": "lora", "count": len(pending)})
                restored = await assets.recover_missing(pending, on_stage=progress, failure=failure)
                await self._record_event(queue_id, "asset_recovery_finished", {"restored": restored})
                return restored
            except Exception as exc:
                diagnostic_event(
                    log,
                    "queue.asset_recovery_failed",
                    level=logging.WARNING,
                    data={
                        "reason": type(exc).__name__,
                        "lora": [log_label(item.identifier, item.sha256) for item in pending],
                    },
                )
                await self._record_event(
                    queue_id, notes.RECOVERY_FAILED, notes.recovery_failed_detail([i.identifier for i in pending], exc)
                )
                await asyncio.to_thread(post_control, self.store.directory, queue_id, "pause")
                await self.store.apply_controls(queue_id)
                return False
            finally:
                self._recovery_active = False

        self._asset_recovery = asyncio.create_task(recover())
        return await asyncio.shield(self._asset_recovery)

    async def _warn_unprepared(self, queue_id: str, item_id: str, request: ResolvedGenerationRequest) -> None:
        """Say once per queue and LoRA that a selected cloud LoRA has no prepared receipt, so DT+ application stays
        unconfirmed. Reads the cached inventory only; it never blocks, retries or sends anything (#388)."""
        if (
            not request.loras
            or self._unprepared_unavailable
            or (request.prepared_assets and len(request.prepared_assets) == len(request.loras))
        ):
            return
        try:
            diagnostics = await self.service.preflight(request)
            fresh = [i for i in notes.unprepared_loras(request, diagnostics) if i not in self._unprepared_noted]
            if not fresh:
                return
            self._unprepared_noted.update(fresh)
            diagnostic_event(
                log,
                "queue.lora_unprepared",
                level=logging.WARNING,
                data={"lora": [log_label(identifier) for identifier in fresh]},
            )
            await self._record_event(queue_id, notes.UNPREPARED, {"item_id": item_id, "loras": fresh})
        except Exception as exc:  # advisory only; one failure is enough, later items do not repeat the wait
            self._unprepared_unavailable = True
            diagnostic_event(
                log, "queue.lora_unprepared_check_failed", level=logging.WARNING, data={"reason": type(exc).__name__}
            )

    async def _note_lora_outcome(
        self, queue_id: str, request: ResolvedGenerationRequest, failure: BackendFailure | None
    ) -> None:
        """Pause the queue when a changed LoRA could not be refreshed, or when ``LORA_FAILURE_PAUSE`` items in a row
        end with DT+'s generic error for the same LoRA; remaining items stay pending. A success clears the count."""
        refresh = failure is not None and refresh_failed(failure)
        if not self._lora_streak.note(request, failure) and not refresh:
            return
        kind = notes.REFRESH_PAUSE if refresh else notes.FAILURE_PAUSE
        diagnostic_event(
            log,
            "queue." + kind,
            level=logging.WARNING,
            data={"lora": [log_label(item.identifier, item.sha256) for item in request.prepared_assets]},
        )
        await self._record_event(queue_id, kind, {"loras": notes.request_identifiers(request)})
        await asyncio.to_thread(post_control, self.store.directory, queue_id, "pause")
        await self.store.apply_controls(queue_id)

    async def _record_event(
        self,
        queue_id: str,
        kind: str,
        detail: dict[str, Any],
        at_utc: str | None = None,
    ) -> None:
        try:
            await self.store.record_queue_event(queue_id, kind, detail, at_utc=at_utc)
        except Exception as exc:
            diagnostic_event(
                log,
                "queue.event_evidence_not_recorded",
                level=logging.WARNING,
                data={
                    "kind": kind,
                    "reason": type(exc).__name__,
                },
            )

    async def _event_writer(
        self,
        queue_id: str,
        evidence: asyncio.Queue[tuple[str, dict[str, Any], str | None] | None],
    ) -> None:
        while True:
            record = await evidence.get()
            if record is None:
                return
            kind, detail, at_utc = record
            await self._record_event(queue_id, kind, detail, at_utc)
            if kind == "item_settled":
                await self._record_timing_snapshot(queue_id)

    async def _record_timing_snapshot(self, queue_id: str) -> None:
        """Keep the observed timing and current ETA alongside each settled item."""
        try:
            view = await self.store.queue_timing(queue_id)
            if not view["started_at_utc"]:
                return
            started = datetime.fromisoformat(view["started_at_utc"])
            if started.tzinfo is None:
                return
            elapsed = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
            durations = view["successful_durations"]
            counts = dict(view["summary"])
            running_starts = list(view["running_started_at_utc"])
            # This runs just after a settlement, before the dispatch loop refills the freed lane; count the lanes the
            # next claim will fill as starting now so the ETA does not divide by one lane too few.
            lanes = min(view["cap"], self.service.backend.capabilities.max_concurrency)
            refill = max(0, min(lanes - counts["running"], counts["pending"]))
            if refill:
                counts["running"] += refill
                counts["pending"] -= refill
                running_starts += [datetime.now(timezone.utc).isoformat()] * refill
            estimate = estimate_queue_seconds(counts, durations, running_starts)
            eta = (
                math.ceil(estimate)
                if estimate is not None
                else (0 if not counts["pending"] and not counts["running"] else None)
            )
            await self._record_event(queue_id, "timing_snapshot", {"elapsed_seconds": elapsed, "eta_seconds": eta})
        except Exception as exc:
            diagnostic_event(
                log, "queue.timing_snapshot_not_recorded", level=logging.WARNING, data={"reason": type(exc).__name__}
            )

    @staticmethod
    def _backend_event_detail(item_id: str, request: ResolvedGenerationRequest, event: BackendEvent) -> dict[str, Any]:
        detail: dict[str, Any] = {"item_id": item_id, "request_id": request.request_id}
        if event.stage is not None:
            detail["stage"] = event.stage
        if event.completed is not None:
            detail["completed"] = event.completed
        if event.total is not None:
            detail["total"] = event.total
        if event.artifact is not None:
            detail["artifact_id"] = event.artifact.artifact_id
            detail["designation"] = str(event.artifact.designation)
        return detail

    async def _execute(
        self, queue_id: str, item_id: str, request: ResolvedGenerationRequest, permit: Permit | None = None
    ) -> None:
        """Run one claimed item; ``permit`` is the provider slot its claim took (the service closes it)."""
        evidence: asyncio.Queue[tuple[str, dict[str, Any], str | None] | None] = asyncio.Queue()
        writer = asyncio.create_task(self._event_writer(queue_id, evidence))
        failure: dict[str, Any] | None = None
        saved = None
        retries = 0

        def receive(event: BackendEvent) -> None:
            if self.on_event is not None:
                self.on_event(item_id, event)
            if event.kind in RECORDED_BACKEND_EVENTS:
                evidence.put_nowait(
                    (
                        f"backend_{event.kind}",
                        self._backend_event_detail(item_id, request, event),
                        event.timestamp_utc,
                    )
                )

        evidence.put_nowait(("item_started", {"item_id": item_id, "request_id": request.request_id}, None))
        try:
            try:
                options = {}
                if request.operation != "txt2img" or request.pose_control is not None:
                    try:
                        # Staged, hash-verified inputs are read only for the dispatched item.
                        options["prepared_inputs"] = await settle(
                            asyncio.to_thread(restore_image_inputs, self.service.input_state, request.image_inputs)
                        )
                        if request.pose_control is not None:
                            await settle(asyncio.to_thread(_require_flux1, self.service.input_state, request.model_id))
                            inventory = await self.service.get_inventory()
                            if not any(
                                entry.identifier == request.pose_control.checkpoint
                                and entry.availability == "listed_in_server_inventory"
                                for entry in inventory.entries
                            ):
                                raise ValidationError(
                                    "The selected pose control is not in DT+'s inventory. Nothing was submitted."
                                )
                    except (ValidationError, OSError) as exc:
                        raise BackendError(
                            BackendFailure(
                                ErrorCategory.VALIDATION,
                                "pose_inputs_unavailable"
                                if request.pose_control is not None
                                else "image_inputs_unavailable",
                                str(exc),
                                request.request_id,
                            )
                        ) from exc
                layout = OutputLayout(group_name("queue", queue_id), prompt_label(request.prompt))
                if self._trigger_prefix is not None and request.loras and self._trigger_prefix():
                    request = apply_trigger_prefixes(request, await self.service.get_inventory(refresh=True))
                await self._warn_unprepared(queue_id, item_id, request)
                while True:
                    try:
                        saved = await self.service.generate(
                            request, on_event=receive, layout=layout, permit=permit, **options
                        )
                        await self._note_lora_outcome(queue_id, request, None)
                        break
                    except BackendError as exc:
                        repaired = await self._try_asset_recovery(queue_id, item_id, request, exc.failure)
                        limit = auto_retry_limit(request, exc.failure)
                        # Retry a repaired request only when the provider definitely did not receive it.
                        safe_replay = (
                            repaired
                            and exc.failure.remote_outcome == RemoteOutcome.NOT_STARTED
                            and retries < min(AUTO_RETRIES, limit)
                        )
                        if not safe_replay and (retries >= limit or not auto_retryable(exc.failure)):
                            raise
                        retries += 1
                        retry, first = auto_retry_request(request), exc.failure
                        if self.on_event is not None:
                            self.on_event(
                                item_id,
                                BackendEvent(
                                    request.request_id,
                                    0,
                                    "preparing",
                                    datetime.now(timezone.utc).isoformat(),
                                    stage="retrying",
                                ),
                            )
                        # No slot is held while the retry waits; it takes its turn for one afterwards.
                        await asyncio.sleep(AUTO_RETRY_DELAY)
                        permit = await self.admission.acquire()
                        try:
                            # A queue paused meanwhile sends nothing more: the item settles with its first failure.
                            if (await self.store.inspect_queue(queue_id))["state"] != "running":
                                raise exc from None
                            # Durable before anything is sent: settlement and restart find the retry's attempt.
                            await self.store.auto_retry_item(item_id, retry)
                        except (QueueError, BackendError):
                            permit.close()
                            raise exc from None
                        except BaseException:
                            permit.close()
                            raise
                    request = retry
                    diagnostic_event(
                        log,
                        "queue.item_auto_retry",
                        level=logging.WARNING,
                        data={
                            "item_id": item_id,
                            "code": first.code,
                            "category": first.category,
                            **self.service._key_diagnostics(),
                        },
                    )
            except asyncio.CancelledError:
                state = await settle(self.store.settle_item(item_id))
                evidence.put_nowait(("item_settled", {"item_id": item_id, "state": state}, None))
                raise
            except BackendError as exc:
                await self._note_lora_outcome(queue_id, request, exc.failure)
                failure = {
                    "code": exc.failure.code,
                    # The original reason (same shape as the attempts table), so Queues shows what Generate shows.
                    "message": exc.failure.message,
                    "diagnostics": [list(pair) for pair in exc.failure.diagnostics],
                    "category": str(exc.failure.category),
                    "submission_stage": str(exc.failure.submission_stage),
                    "remote_outcome": str(exc.failure.remote_outcome),
                }
            except PersistenceError:
                # The state store itself failed: leave the durable reservation for the next owner's reconciliation.
                diagnostic_event(
                    log,
                    "queue.settlement_unavailable",
                    level=logging.ERROR,
                    data={
                        "item_id": item_id,
                    },
                )
                raise
            except Exception as exc:
                failure = {
                    "code": "unexpected",
                    "message": "An unexpected error stopped this item; the logs have the details.",
                    "category": type(exc).__name__,
                }

            state = await settle(self.store.settle_item(item_id, failure))
            detail: dict[str, Any] = {"item_id": item_id, "state": state}
            if saved is not None:
                detail["attempt_id"] = saved.attempt_id
                if saved.backend_result.backend_elapsed_seconds is not None:
                    detail["backend_elapsed_seconds"] = saved.backend_result.backend_elapsed_seconds
            evidence.put_nowait(("item_settled", detail, None))
            if self.on_settle is not None:
                self.on_settle(item_id, state)
            diagnostic_event(
                log,
                "queue.item_settled",
                level=logging.INFO,
                data={
                    "queue_id": queue_id,
                    "item_id": item_id,
                    "state": state,
                    "auto_retries": retries,
                    **({"failure": _failure_data(failure)} if failure else {}),
                    **self.service._key_diagnostics(),
                },
            )
        finally:
            if permit is not None:
                permit.close()  # normally already closed by the service; this covers a failure before it started
            evidence.put_nowait(None)
            await settle(writer)

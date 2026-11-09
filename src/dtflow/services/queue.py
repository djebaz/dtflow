"""Rolling queue dispatch over the shared generation service.

One dispatcher owns the state directory (through the store's ownership lock), claims items one at a time in queue order,
and starts the next eligible item the moment a reservation is released: there is no group barrier. Reservations are
taken durably before an attempt exists. Nothing here replays an attempt; an item that reached the remote boundary and
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
from dtflow.core.retry_policy import AUTO_RETRIES, AUTO_RETRY_DELAY, auto_retry_request, auto_retryable
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
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.core.request_records import request_from_record
from dtflow.services.generation import GenerationService
from dtflow.services.image_inputs import restore_image_inputs

log = logging.getLogger(__name__)

RECORDED_BACKEND_EVENTS = {"preparing", "submission_possible", "artifact_received", "terminal"}


class QueueDispatcher:
    def __init__(
        self,
        store: SQLiteAttempts,
        service: GenerationService,
        *,
        poll: float = 0.5,
        on_settle: Callable[[str, str], None] | None = None,
        on_event: Callable[[str, BackendEvent], None] | None = None,
    ):
        self.store, self.service, self.poll, self.on_settle, self.on_event = store, service, poll, on_settle, on_event
        self._asset_recovery: asyncio.Task | None = None
        self._recovery_active = False
        self._recovery_attempted = False

    async def run(self, queue_id: str) -> dict[str, Any]:
        """Dispatch until the queue is finished or cancelled.

        A paused queue with nothing active keeps polling so a posted Resume can continue it; interrupting the process
        (Ctrl-C) ends dispatch and records honest evidence for whatever was active.
        """
        limit = self.service.backend.capabilities.max_concurrency
        tasks: dict[asyncio.Task, str] = {}
        failure: BaseException | None = None
        await self.store.apply_controls(queue_id)
        await self.store.set_running(queue_id)
        try:
            # Resolve all distinct first-use receipts before opening up to ten generation lanes.
            # Legacy and cross-key receipts share one BYOM batch, not one check per item.
            assets = getattr(self.service, "assets", None)
            if assets is not None and callable(getattr(assets, "ensure_many", None)):
                try:
                    _, _, records = await asyncio.to_thread(read_queue_requests, self.store.directory, queue_id)
                    distinct = {}
                    for _, state, record in records:
                        if state != "pending" or not isinstance(record, dict) or "model_id" not in record:
                            continue
                        request = request_from_record(record)
                        for prepared in request.prepared_assets:
                            distinct[(prepared.identifier, prepared.sha256, prepared.size)] = prepared
                    if distinct:
                        await assets.ensure_many(tuple(distinct.values()))
                except BackendError as exc:
                    diagnostic_event(
                        log, "queue.asset_prepare_blocked", level=logging.WARNING,
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
                while failure is None and not self._recovery_active:
                    claim = await self.store.claim_next(queue_id, limit)
                    if claim is None:
                        break
                    item_id, request = claim
                    task = asyncio.create_task(self._execute(queue_id, item_id, request))
                    tasks[task] = item_id
                if tasks:
                    done, _ = await asyncio.wait(tasks, timeout=self.poll, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        del tasks[task]
                        if not task.cancelled() and task.exception() is not None:
                            failure = failure or task.exception()
                    continue
                if failure is not None:
                    raise failure
                state = await self.store.inspect_queue(queue_id)
                if state["state"] == "cancelled" or not (state["summary"]["pending"] or state["summary"]["running"]):
                    return state
                await asyncio.sleep(self.poll)
        finally:
            if self._asset_recovery is not None and not self._asset_recovery.done():
                self._asset_recovery.cancel()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))

    async def _try_asset_recovery(
        self, queue_id: str, request: ResolvedGenerationRequest, failure: BackendFailure
    ) -> bool:
        """One coordinated probe/reprepare episode per queue, with admission held until it settles."""
        assets = getattr(self.service, "assets", None)
        if (
            assets is None
            or not callable(getattr(assets, "recover_missing", None))
            or not request.prepared_assets
            or failure.category != ErrorCategory.REMOTE_REJECTION
        ):
            return False
        if self._recovery_attempted:
            if self._asset_recovery is None or self._asset_recovery.done():
                return False
            return await asyncio.shield(self._asset_recovery)
        self._recovery_attempted = True
        self._recovery_active = True

        async def recover():
            try:
                await self._record_event(queue_id, "asset_recovery_started", {"kind": "lora"})
                restored = await assets.recover_missing(request.prepared_assets)
                await self._record_event(
                    queue_id, "asset_recovery_finished", {"restored": restored}
                )
                return restored
            except Exception as exc:
                diagnostic_event(
                    log, "queue.asset_recovery_failed", level=logging.WARNING,
                    data={"reason": type(exc).__name__},
                )
                await self._record_event(queue_id, "asset_recovery_failed", {"reason": "restore_failed"})
                await asyncio.to_thread(post_control, self.store.directory, queue_id, "pause")
                await self.store.apply_controls(queue_id)
                return False
            finally:
                self._recovery_active = False

        self._asset_recovery = asyncio.create_task(recover())
        return await asyncio.shield(self._asset_recovery)

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

    async def _execute(self, queue_id: str, item_id: str, request: ResolvedGenerationRequest) -> None:
        evidence: asyncio.Queue[tuple[str, dict[str, Any], str | None] | None] = asyncio.Queue()
        writer = asyncio.create_task(self._event_writer(queue_id, evidence))
        failure: dict[str, Any] | None = None
        saved = None

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
                if request.operation != "txt2img":
                    try:
                        # Staged, hash-verified inputs are read on a worker only now, and released after dispatch.
                        options["prepared_inputs"] = await settle(
                            asyncio.to_thread(restore_image_inputs, self.service.input_state, request.image_inputs)
                        )
                    except ValidationError as exc:
                        raise BackendError(
                            BackendFailure(
                                ErrorCategory.VALIDATION,
                                "image_inputs_unavailable",
                                str(exc),
                                request.request_id,
                            )
                        ) from exc
                layout = OutputLayout(group_name("queue", queue_id), prompt_label(request.prompt))
                retries = 0
                while True:
                    try:
                        saved = await self.service.generate(request, on_event=receive, layout=layout, **options)
                        break
                    except BackendError as exc:
                        repaired = await self._try_asset_recovery(queue_id, request, exc.failure)
                        # Retry a repaired request only when the provider definitely did not receive it.
                        safe_replay = (
                            repaired and exc.failure.remote_outcome == RemoteOutcome.NOT_STARTED
                            and retries < AUTO_RETRIES
                        )
                        if not safe_replay and (retries >= AUTO_RETRIES or not auto_retryable(exc.failure)):
                            raise
                        retries += 1
                        retry, first = auto_retry_request(request), exc.failure
                        try:
                            # Durable before anything is sent: settlement and restart find the retry's attempt.
                            await self.store.auto_retry_item(item_id, retry)
                        except QueueError:
                            raise exc from None
                    request = retry
                    diagnostic_event(
                        log,
                        "queue.item_auto_retry",
                        level=logging.WARNING,
                        data={"item_id": item_id, "code": first.code, "category": first.category},
                    )
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
                    await asyncio.sleep(AUTO_RETRY_DELAY)
            except asyncio.CancelledError:
                state = await settle(self.store.settle_item(item_id))
                evidence.put_nowait(("item_settled", {"item_id": item_id, "state": state}, None))
                raise
            except BackendError as exc:
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
                    "item_id": item_id,
                    "state": state,
                },
            )
        finally:
            evidence.put_nowait(None)
            await settle(writer)

"""Rolling queue dispatch over the shared generation service.

One dispatcher owns the state directory (through the store's ownership lock), claims items one at a time in queue order,
and starts the next eligible item the moment a reservation is released: there is no group barrier. Reservations are
taken durably before an attempt exists. Nothing here replays an attempt; an item that reached the remote boundary and
came back unknown stays reserved and blocks its backend until an explicit, recorded override. No database transaction is
held while generating.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from dtflow.core.models import (
    BackendError,
    BackendEvent,
    BackendFailure,
    ErrorCategory,
    ResolvedGenerationRequest,
    ValidationError,
)
from dtflow.core.tasks import settle
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
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

    async def run(self, queue_id: str) -> dict[str, Any]:
        """Dispatch until the queue is finished, cancelled, or blocked by uncertainty with nothing active.

        A paused queue with nothing active keeps polling so a posted Resume can continue it; interrupting the process
        (Ctrl-C) ends dispatch and records honest evidence for whatever was active.
        """
        limit = self.service.backend.capabilities.max_concurrency
        tasks: dict[asyncio.Task, str] = {}
        failure: BaseException | None = None
        await self.store.apply_controls(queue_id)
        await self.store.set_running(queue_id)
        try:
            while True:
                applied = await self.store.apply_controls(queue_id)
                if "cancel" in applied:
                    # Interruption of running attempts is local only; the service records any remote uncertainty.
                    for task in tasks:
                        task.cancel()
                while failure is None:
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
                if state["blocked_by_uncertainty"]:
                    return state
                await asyncio.sleep(self.poll)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))

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
            log.warning("queue event evidence not recorded kind=%s reason=%s", kind, type(exc).__name__)

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
                saved = await self.service.generate(request, on_event=receive, **options)
            except asyncio.CancelledError:
                state = await settle(self.store.settle_item(item_id))
                evidence.put_nowait(("item_settled", {"item_id": item_id, "state": state}, None))
                raise
            except BackendError as exc:
                failure = {
                    "code": exc.failure.code,
                    "category": str(exc.failure.category),
                    "submission_stage": str(exc.failure.submission_stage),
                    "remote_outcome": str(exc.failure.remote_outcome),
                }
            except PersistenceError:
                # The state store itself failed: leave the durable reservation for the next owner's reconciliation.
                log.error("queue item %s could not be settled; reconciliation will classify it", item_id)
                raise
            except Exception as exc:
                failure = {"code": "unexpected", "category": type(exc).__name__}

            state = await settle(self.store.settle_item(item_id, failure))
            detail: dict[str, Any] = {"item_id": item_id, "state": state}
            if saved is not None:
                detail["attempt_id"] = saved.attempt_id
                if saved.backend_result.backend_elapsed_seconds is not None:
                    detail["backend_elapsed_seconds"] = saved.backend_result.backend_elapsed_seconds
            evidence.put_nowait(("item_settled", detail, None))
            if self.on_settle is not None:
                self.on_settle(item_id, state)
            log.info("queue item %s settled state=%s", item_id, state)
        finally:
            evidence.put_nowait(None)
            await settle(writer)

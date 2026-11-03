"""Dispatch of a script queue: its one item is a whole script run, its ``generateImage`` calls are child steps.

This is a separate function from the generation loop (``QueueDispatcher``) so a script bug cannot enter it. It applies
controls, claims the single item (a durable reservation), runs the script through the same host the CLI uses, drains
every submitted attempt, then settles the item from the run row plus its children. Stop is the existing ``cancel``
control: it sets the run's cancel event; in-flight attempts are never cancelled remotely and stay recorded.
"""

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dtflow.core.models import ValidationError
from dtflow.core.request_records import request_from_record
from dtflow.core.tasks import settle
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.script_items import ScriptRequest, load_source
from dtflow.persistence.script_runs import ScriptRunStore, read_open_prompt
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.scripting.host import ScriptHost, script_hooks, starting_context, validate_answers
from dtflow.scripting.runner import run_recorded
from dtflow.services.generation import GenerationService
from dtflow.services.session import script_asset_choices

log = logging.getLogger(__name__)


class ScriptItemRunner:
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
        """Run the queue's one item to its end; a paused queue with nothing active keeps polling for a posted Resume."""
        limit = self.service.backend.capabilities.max_concurrency
        await self.store.apply_controls(queue_id)
        await self.store.set_running(queue_id)
        while True:
            await self.store.apply_controls(queue_id)
            claim = await self.store.claim_next(queue_id, limit)
            if claim is not None:
                await self._run_item(queue_id, *claim)
            state = await self.store.inspect_queue(queue_id)
            if state["state"] == "cancelled" or not (state["summary"]["pending"] or state["summary"]["running"]):
                return state
            await asyncio.sleep(self.poll)

    async def _record(self, queue_id: str, kind: str, detail: dict[str, Any]) -> None:
        try:
            await self.store.record_queue_event(queue_id, kind, detail)
        except Exception as exc:
            diagnostic_event(
                log,
                "queue.event_evidence_not_recorded",
                level=logging.WARNING,
                data={"kind": kind, "reason": type(exc).__name__},
            )

    async def _run_item(self, queue_id: str, item_id: str, request: ScriptRequest) -> None:
        await self._record(queue_id, "item_started", {"item_id": item_id, "run_id": request.run_id})
        hosts: list[ScriptHost] = []
        cancel = threading.Event()
        failure: dict[str, Any] | None = None
        interrupted = False
        try:
            try:
                source = load_source(self.state, request.source_sha256)
                base = request_from_record(request.base)
                available_assets = await script_asset_choices(self.state, request.backend)
                loop = asyncio.get_running_loop()
                backend_limit = self.service.backend.capabilities.max_concurrency

                def reserve_child(attempt, concurrency):
                    return self.store.reserve_script_child(queue_id, attempt, concurrency, backend_limit)

                def release_child(attempt):
                    return self.store.release_script_child(queue_id, attempt)

                hooks = script_hooks(
                    lambda: self.service,
                    base=base,
                    loop=loop,
                    state=self.state,
                    max_generations=request.max_generations,
                    answers=request.answers,
                    hosts=hosts,
                    interactive=request.answers is None,  # no answer file: forms wait for the person
                    require_start=request.require_start,
                    reserve_child=reserve_child,
                    release_child=release_child,
                    available_assets=available_assets,
                )
                if request.prepared:
                    from dtflow.services.script_setup import adopt_setup

                    setup = adopt_setup(
                        self.state,
                        request,
                        self.service,
                        loop,
                        reserve_child=reserve_child,
                        release_child=release_child,
                        available_assets=available_assets,
                    )
                    hosts, cancel, task = setup.hosts, setup.cancel, setup.task
                    try:
                        if request.setup_ordinal is not None:
                            await asyncio.to_thread(
                                answer_script_prompt,
                                self.state,
                                request.run_id,
                                request.setup_answers,
                                request.setup_ordinal,
                            )
                    except BaseException:
                        cancel.set()
                        await settle(task)
                        raise
                else:
                    task = asyncio.ensure_future(
                        asyncio.to_thread(
                            run_recorded,
                            ScriptRunStore(self.state),
                            source,
                            hooks,
                            source_name=request.source_name,
                            cancel=cancel,
                            context_json=json.dumps(
                                starting_context(
                                    base,
                                    backend=request.backend,
                                    max_generations=request.max_generations,
                                    answers=request.answers,
                                )
                            ),
                            run_id=request.run_id,
                        )
                    )
            except (ValidationError, ValueError) as exc:
                failure = {"code": "script_invalid", "message": str(exc)}
                task = None
            if task is not None:
                try:
                    while not task.done():
                        await asyncio.wait({task}, timeout=self.poll)
                        if task.done():
                            break
                        if "cancel" in await self.store.apply_controls(queue_id):
                            cancel.set()  # Stop: the host stops waiting; in-flight attempts finish and stay durable
                    task.result()
                except asyncio.CancelledError:
                    interrupted = True
                    cancel.set()
                    await settle(task)  # run_recorded settles the ledger row as stopped
                except PersistenceError:
                    raise
                except Exception as exc:
                    failure = {"code": "script_run_failed", "category": type(exc).__name__}
        finally:
            pending = [future for host in hosts for future in host.pending]
            if pending:
                drain = asyncio.gather(*(asyncio.wrap_future(future) for future in pending), return_exceptions=True)
                while not drain.done():  # in-flight attempts finish (or hit their deadline) before the service closes
                    try:
                        await asyncio.shield(drain)
                    except asyncio.CancelledError:
                        interrupted = True
        state = await settle(self.store.settle_item(item_id, failure))
        await self._record(queue_id, "item_settled", {"item_id": item_id, "run_id": request.run_id, "state": state})
        if self.on_settle is not None:
            self.on_settle(item_id, state)
        diagnostic_event(log, "queue.item_settled", level=logging.INFO, data={"item_id": item_id, "state": state})
        if interrupted:
            raise asyncio.CancelledError


def answer_script_prompt(state: Path, run_id: str, answers: list, ordinal: int | None = None) -> int:
    """Answer the run's open form from any process sharing ``state``; returns the form's ordinal.

    The answers are validated against the form before they are stored. Raises ``ValidationError`` when the run has no
    open form (never started, already answered, or Stopped) or when the answers do not fit the form."""
    prompt = read_open_prompt(state, run_id)
    if prompt is None or (ordinal is not None and prompt.ordinal != ordinal):
        raise ValidationError("This script run is not waiting for input.")
    try:
        validate_answers(prompt.components, answers)
        from dtflow.services.script_form_inputs import resolve_form_inputs

        # Verify capabilities and the reply budget before accepting durable consent.
        resolve_form_inputs(state, run_id, prompt.ordinal, prompt.components, answers)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if not ScriptRunStore(state).answer_prompt(run_id, prompt.ordinal, answers):
        raise ValidationError("This form was already answered or closed.")
    return prompt.ordinal


def cancel_script_prompt(state: Path, run_id: str, ordinal: int) -> None:
    """Cancel this exact request, not a later request that won a polling race."""
    if not ScriptRunStore(state).cancel_prompt(run_id, ordinal):
        raise ValidationError("This form was already answered or closed.")

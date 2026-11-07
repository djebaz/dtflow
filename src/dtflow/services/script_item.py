"""Dispatch of a script queue: its one item is a whole script run, its ``generateImage`` calls are child steps.

This is a separate function from the generation loop (``QueueDispatcher``) so a script bug cannot enter it. It applies
controls, claims the single item (a durable reservation), runs the script through the same host the CLI uses, drains
every submitted attempt, then settles the item from its run plus its children. The run is kept in the item itself
(#243): every change is written there by this owner before it takes effect. Stop is the existing ``cancel`` control:
it sets the run's cancel event; in-flight attempts are never cancelled remotely and stay recorded. Form answers from
any process arrive as ``script_answer`` controls and are checked again here before the script sees them.
"""

import asyncio
import concurrent.futures
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
from dtflow.persistence.queues import QueueError, post_control, read_script_run
from dtflow.persistence.script_items import ScriptRequest, load_source
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.scripting.host import ScriptHost, script_hooks, starting_context, validate_answers
from dtflow.scripting.run_state import ScriptRun, open_prompt_of
from dtflow.scripting.runner import new_script_run, run_recorded
from dtflow.services.generation import GenerationService
from dtflow.services.session import script_asset_choices

log = logging.getLogger(__name__)
PERSIST_TIMEOUT_SECONDS = 60.0


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

    def _persist(self, item_id: str, loop: asyncio.AbstractEventLoop):
        """The run's durable writer: called on worker threads, it waits for the owner's write on ``loop``."""

        def persist(run, recall):
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is loop:  # waiting here would block the loop that does the write
                raise RuntimeError("A script run must not be written from its owner's loop thread.")
            future = asyncio.run_coroutine_threadsafe(self.store.update_script_run(item_id, run, recall), loop)
            try:
                future.result(PERSIST_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise PersistenceError("The script run could not be recorded in time.") from exc

        return persist

    async def _answer(self, run: ScriptRun | None, action: str, payload: dict[str, Any]) -> None:
        """Apply one posted form answer or cancel to the run, after checking it again; a stale one changes nothing."""
        if run is None:
            return
        ordinal = payload.get("ordinal")
        try:
            if action == "script_cancel_form":
                await asyncio.to_thread(run.cancel_prompt, ordinal)
            else:
                await asyncio.to_thread(_record_answer, self.state, run, ordinal, payload.get("answers"))
        except (ValidationError, ValueError, RuntimeError) as exc:
            diagnostic_event(
                log,
                "script.answer_refused",
                level=logging.WARNING,
                data={"run_id": run.run_id, "ordinal": ordinal, "reason": type(exc).__name__},
            )

    async def _run_item(self, queue_id: str, item_id: str, request: ScriptRequest) -> None:
        await self._record(queue_id, "item_started", {"item_id": item_id, "run_id": request.run_id})
        hosts: list[ScriptHost] = []
        cancel = threading.Event()
        failure: dict[str, Any] | None = None
        interrupted = False
        run: ScriptRun | None = None
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
                persist = self._persist(item_id, loop)
                if request.prepared:
                    from dtflow.services.script_setup import adopt_setup

                    setup = await asyncio.to_thread(
                        adopt_setup,
                        self.state,
                        request,
                        self.service,
                        loop,
                        persist=persist,
                        reserve_child=reserve_child,
                        release_child=release_child,
                        available_assets=available_assets,
                    )
                    hosts, cancel, task, run = setup.hosts, setup.cancel, setup.task, setup.run
                    try:
                        if request.setup_ordinal is not None:
                            await asyncio.to_thread(
                                _record_answer, self.state, run, request.setup_ordinal, request.setup_answers
                            )
                    except BaseException:
                        cancel.set()
                        await settle(task)
                        raise
                else:
                    run = new_script_run(
                        request.run_id,
                        source,
                        source_name=request.source_name,
                        context_json=json.dumps(
                            starting_context(
                                base,
                                backend=request.backend,
                                max_generations=request.max_generations,
                                answers=request.answers,
                            )
                        ),
                        persist=persist,
                    )
                    task = asyncio.ensure_future(asyncio.to_thread(run_recorded, run, source, hooks, cancel=cancel))
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
                        for action, payload in await self.store.take_script_controls(queue_id):
                            await self._answer(run, action, payload)
                    task.result()
                except asyncio.CancelledError:
                    interrupted = True
                    cancel.set()
                    await settle(task)  # run_recorded settles the run as stopped
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


def _record_answer(state: Path, run: ScriptRun, ordinal: Any, answers: Any) -> None:
    """Check answers against the run's open form and its owned inputs, then record them (worker thread only)."""
    from dtflow.services.script_form_inputs import resolve_form_inputs
    from dtflow.services.script_runs import prompt_input

    prompt = run.open_prompt()
    if prompt is None or prompt.ordinal != ordinal or not isinstance(answers, list):
        raise ValidationError("This form was already answered or closed.")
    # Verify capabilities and the reply budget before accepting durable consent.
    resolve_form_inputs(state, run.run_id, prompt.ordinal, prompt.components, answers)

    def origin(path, kind, token):
        prompt_input(state, run.run_id, prompt.ordinal, path, kind, token)

    if not run.answer_prompt(prompt.ordinal, answers, origin=origin):
        raise ValidationError("This form was already answered or closed.")


def answer_script_prompt(state: Path, run_id: str, answers: list, ordinal: int | None = None) -> int:
    """Answer the run's open form from any process sharing ``state``; returns the form's ordinal.

    The answers are checked against the form here, then posted to the run's queue as a control; the dispatcher checks
    them again and records them before the script continues. Raises ``ValidationError`` when the run has no open form
    (never started, already answered, or Stopped) or when the answers do not fit the form."""
    found = read_script_run(state, run_id)
    prompt = open_prompt_of(run_id, found[2]) if found is not None and found[1] == "running" else None
    if prompt is None or (ordinal is not None and prompt.ordinal != ordinal):
        raise ValidationError("This script run is not waiting for input.")
    try:
        validate_answers(prompt.components, answers)
        from dtflow.services.script_form_inputs import resolve_form_inputs

        resolve_form_inputs(state, run_id, prompt.ordinal, prompt.components, answers)
        post_control(state, found[0], "script_answer", {"ordinal": prompt.ordinal, "answers": answers})
    except (ValueError, QueueError) as exc:
        raise ValidationError(str(exc)) from exc
    return prompt.ordinal


def cancel_script_prompt(state: Path, run_id: str, ordinal: int) -> None:
    """Cancel this exact request, not a later request that won a polling race."""
    found = read_script_run(state, run_id)
    prompt = open_prompt_of(run_id, found[2]) if found is not None and found[1] == "running" else None
    if prompt is None or prompt.ordinal != ordinal:
        raise ValidationError("This form was already answered or closed.")
    try:
        post_control(state, found[0], "script_cancel_form", {"ordinal": ordinal})
    except QueueError as exc:
        raise ValidationError(str(exc)) from exc

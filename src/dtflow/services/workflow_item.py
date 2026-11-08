"""Dispatch of a workflow queue's one item: topological, per-branch execution of the workflow graph (#223).

Separate from ``QueueDispatcher`` like ``ScriptItemRunner``. Generative nodes run through ``run_generation`` on the
queue's own ``GenerationService``; image nodes use an automatic exact-scope owner probe (D2) prepared by
``_dispatch_one``. Each node hands the next one the saved final artifact from ``output_paths``. A face-mask "no target"
pauses only its branch. A submission counter stops the run past the planned total plus the retry allowance.

Execution model:

- A Generate node starts one task per planned output (#275): each is its own single-image request with seed = node
  seed + output index (wrapped at the seed limit); once its image is saved the same task walks that branch's nodes in
  topological order. Retry of a Generate output reruns that output only, with its recorded seed. A paused, failed or unknown branch never blocks its siblings; a failed or
  unknown step stops only the steps that depend on it (they become ``stopped``).
- Concurrent submissions share the script child lanes (``reserve_script_child``): the item's own reservation carries
  the first submission and each further concurrent one needs a free lane on the backend, bounded by the backend's
  ``max_concurrency``. Lanes are released when the generation settles, whatever its outcome.
- The loop guard counts submitted images (every Generate output and image step counts one). A submission that
  would pass the planned total plus the retry allowance is refused, the run stops and ends ``failed``. Each explicit
  Retry adds that step's own submission to the allowance; the shared automatic retry (``core.retry_policy``,
  #241) adds ``AUTO_RETRIES`` per planned submission.
- A remote outcome that is not confirmed (attempt ``unknown``) makes its step ``unknown``: never a failure or a
  success, never replayed. Only an explicit Retry submits that step again (a new request identity), except the one
  case ``auto_retryable`` accepts (DT+ ended the call itself with no image). A definite transient failure gets the
  same one automatic retry after a short wait, as a new attempt under a new request identity.
- Stop (queue ``cancel``) starts nothing new, closes every open pause and stops waiting at once (#247): in-flight
  waits are cancelled locally, like an app close. A step already sent ends ``unknown`` (never replayed; Retry sends it
  again), the other running and pending steps end ``cancelled``. A step that has not recorded its end after
  ``STOP_GRACE`` (a local thread that does not return) is abandoned: its lane is released and the run settles anyway,
  so the queue can be removed. Queue Pause holds new submissions until Resume.
- The run state is written after every step transition; stage text (from backend events) at most once per poll.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import SEED_LIMIT, BackendError, BackendEvent, RemoteOutcome, ValidationError
from dtflow.core.retry_policy import AUTO_RETRIES, AUTO_RETRY_DELAY, RETRY_STAGE, auto_retry_request, auto_retryable
from dtflow.core.tasks import settle
from dtflow.core.workflows import (
    DETECT_FACE,
    GENERATE,
    GENERATIVE,
    IMAGE_OPERATIONS,
    INPAINT,
    MASK,
    SAVE,
    load_workflow,
    node_request,
    plan_workflow,
)
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.queues import QueueError
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.persistence.workflow_items import BAD_STEP_STATES, WorkflowRequest
from dtflow.services import session
from dtflow.services.generation import GenerationService
from dtflow.services.workflow_queue import (
    BRANCH_CONTROL,
    RETRY_CONTROL,
    RETRYABLE,
    descendants,
)

log = logging.getLogger(__name__)

# Seconds a cancelled step gets to record its own end after Stop before the run settles without it (#247).
STOP_GRACE = 5.0
STOPPED_WAITING = "DTFlow stopped waiting; the image may still complete on the server. Retry sends it again."
SAVED_WHILE_STOPPING = "The image was received; DTFlow stopped while saving it. Check Images before retrying."
NOT_SENT = "Stopped before submission; nothing was sent."


def stopped_outcome(attempt: str | None) -> tuple[str, str]:
    """A step's end state and message when Stop cancelled its generation, from its attempt's recorded state."""
    if attempt in ("possibly_submitted", "unknown", "saving"):
        return "unknown", STOPPED_WAITING
    if attempt in ("succeeded", "save_failed"):
        return "unknown", SAVED_WHILE_STOPPING  # on disk and billed; Retry stays possible but is not suggested
    if attempt == "failed":
        return "failed", "Stopped; the backend reported a failure for this step."
    return "cancelled", NOT_SENT  # no attempt, prepared, cancelled or interrupted before submission


STAGE_TEXT = {
    "submission_possible": "Sending request",
    "terminal": "Saving output",
}
Branch = tuple[int, int]
Key = tuple[int, Branch]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def stage_text(event: BackendEvent) -> str | None:
    """UI-independent stage wording for a step, from observed backend events only."""
    if event.kind == "preparing":
        return (event.stage or "preparing").replace("_", " ").capitalize()
    if event.kind == "progress":
        if event.completed is not None and event.total:
            return f"Generating {event.completed}/{event.total}"
        return "Generating"
    return STAGE_TEXT.get(event.kind)


def failure_text(exc: BaseException) -> str:
    """Plain cause of a failed step; provider, credential and quota wording is kept as presented elsewhere."""
    from dtflow.services.connection import present_generation_failure

    if isinstance(exc, Exception):
        shown = present_generation_failure(exc)
        return f"{shown.headline}: {shown.detail}" if shown.detail else shown.headline
    return type(exc).__name__


def first_problem(steps: list[dict[str, Any]]) -> tuple[str, str] | None:
    """(``Title (image n): cause``, cause) of the first failed, then unconfirmed, step in run order."""
    for wanted in ("failed", "unknown"):
        for step in steps:
            if step.get("state") == wanted and step.get("message"):
                return f"{step.get('title')} ({step.get('branch_label')}): {step['message']}", step["message"]
    return None


def branch_seed(base: int, index: int) -> int:
    """Seed of Generate output ``index``: the node's seed plus the index, wrapped within 0..SEED_LIMIT."""
    return (base + index) % (SEED_LIMIT + 1)


def settings_of(request) -> dict[str, Any]:
    """The effective settings a step submitted (no identities, no inputs)."""
    return {
        "prompt": request.prompt,
        "negative_prompt": request.negative_prompt,
        "checkpoint": request.model_id,
        "loras": [[lora.identifier, lora.weight] for lora in request.loras],
        "seed": request.seed,
        "steps": request.steps,
        "guidance": request.guidance,
        "sampler": str(getattr(request.sampler_id, "value", request.sampler_id)),
        "strength": request.strength if request.operation != "txt2img" else None,
        "size": f"{request.width_px}x{request.height_px}",
        "operation": request.operation,
    }


def _prepare_source(source: str, mask: str | None, grow: int, feather: float):
    """Read the parent's saved final (and mask) and prepare them at the source size (worker thread)."""
    from dtflow.services.image_preparation import prepare_image_bytes, source_size, validate_prepared_inputs

    data = Path(source).read_bytes()
    width, height = source_size(data)
    if not (128 <= width <= 2048 and 128 <= height <= 2048 and width % 64 == 0 and height % 64 == 0):
        raise ValidationError(
            f"The source image is {width}x{height}; image steps need both sides a multiple of 64 within 128..2048."
        )
    mask_bytes = Path(mask).read_bytes() if mask is not None else None
    prepared = prepare_image_bytes(data, width, height, mask_bytes=mask_bytes, grow=grow, feather=feather)
    validate_prepared_inputs(prepared)
    return width, height, prepared


def _write_mask(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


class WorkflowItemRunner:
    def __init__(
        self,
        store: SQLiteAttempts,
        service: GenerationService,
        state: Path,
        *,
        poll: float = 0.5,
        on_settle: Callable[[str, str], None] | None = None,
        permits: dict[tuple[str, str], Any] | None = None,
        on_event: Callable[[str, BackendEvent], None] | None = None,
    ):
        self.store, self.service, self.state, self.poll, self.on_settle = store, service, Path(state), poll, on_settle
        self.permits = dict(permits or {})  # (checkpoint, operation) -> exact-scope owner-probe permit (D2)
        self.on_event = on_event

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

    async def _run_item(self, queue_id: str, item_id: str, request: WorkflowRequest) -> None:
        await self._record(queue_id, "item_started", {"item_id": item_id, "run_id": request.run_id})
        execution = _Execution(self, queue_id, item_id, request)
        interrupted = False
        try:
            await execution.execute()
        except asyncio.CancelledError:
            interrupted = True
        state = await settle(self.store.settle_item(item_id))
        await self._record(queue_id, "item_settled", {"item_id": item_id, "run_id": request.run_id, "state": state})
        if self.on_settle is not None:
            self.on_settle(item_id, state)
        diagnostic_event(log, "queue.item_settled", level=logging.INFO, data={"item_id": item_id, "state": state})
        if interrupted:
            raise asyncio.CancelledError


class _Execution:
    """One run of one workflow item: its in-memory run state (written through after each transition) and tasks."""

    def __init__(self, runner: WorkflowItemRunner, queue_id: str, item_id: str, request: WorkflowRequest):
        self.runner, self.store, self.queue_id, self.item_id, self.request = (
            runner,
            runner.store,
            queue_id,
            item_id,
            request,
        )
        self.run = copy.deepcopy(request.run)
        self.steps: dict[Key, dict[str, Any]] = {
            (step["node_id"], (step["branch"][0], step["branch"][1])): step for step in self.run["steps"]
        }
        self.values: dict[Key, dict[str, Any]] = {}
        self.tasks: dict[tuple[str, int | Branch], asyncio.Task] = {}
        self.decisions: dict[Key, asyncio.Future] = {}
        self.stopped_branches: set[Branch] = set()
        self.stopping = False
        self.halted: str | None = None
        self.go = asyncio.Event()
        self.go.set()
        self.dirty = False
        # Each planned submission may get the shared automatic retry (#241): at most planned x 2 before any Retry.
        self.allowance = request.planned * (1 + AUTO_RETRIES)
        limit = runner.service.backend.capabilities.max_concurrency
        self.lanes = max(1, min(limit, 10))
        self.graph = None
        self.plan = None
        self.in_flight: dict[str, list[Key]] = {}  # attempt id -> steps whose generation may have been sent
        self.closed = False  # the run settled; a step abandoned at Stop may not write over it

    # -- run state -------------------------------------------------------------------------------------------------

    async def flush(self) -> None:
        if self.closed:
            return
        self.dirty = False
        await self.store.update_workflow_run(self.item_id, self.run)

    async def begin(self, keys: list[Key]) -> None:
        stamp = _now()
        for key in keys:
            step = self.steps[key]
            step.update(
                state="running",
                stage=None,
                message=None,
                started_at=stamp,
                finished_at=None,
                warnings=[],
                lora_preflight=[],
            )
        await self.flush()

    async def end(self, keys: list[Key], state: str, message: str | None = None, **fields) -> None:
        stamp = _now()
        for key in keys:
            step = self.steps[key]
            step.update(state=state, stage=None, message=message, finished_at=stamp, **fields)
            diagnostic_event(
                log,
                "workflow.step_settled",
                level=logging.INFO if state in ("succeeded", "skipped") else logging.WARNING,
                data={"node_type": step["type"], "state": state},
            )
        await self.flush()

    def set_stage(self, keys: list[Key], text: str | None) -> None:
        if text is None:
            return
        for key in keys:
            if self.steps[key]["state"] == "running" and self.steps[key]["stage"] != text:
                self.steps[key]["stage"] = text
                self.dirty = True

    def node_ids(self, branch: Branch) -> list[int]:
        """The non-Generate nodes of one branch in this run, in topological order."""
        return [
            node_id
            for node_id in self.plan.order
            if (node_id, branch) in self.steps and self.graph.node(node_id).type != GENERATE
        ]

    # -- main loop -------------------------------------------------------------------------------------------------

    async def execute(self) -> None:
        self.run.update(state="running", started_at=self.run.get("started_at") or _now(), message=None)
        await self.flush()
        diagnostic_event(log, "workflow.run_started", level=logging.INFO, data={"planned": self.request.planned})
        try:
            self.graph = load_workflow(self.request.workflow)
            self.plan = plan_workflow(self.graph)
            if any(key[0] not in self.plan.nodes for key in self.steps):
                raise ValidationError("The stored run does not match its workflow.")
            if self.plan.needs_face_models:
                # Required local detectors are checked before any (possibly billable) submission; never downloaded here.
                from dtflow.services.model_provisioning import WORKFLOW_FACE_ROLES, missing_models

                missing = await asyncio.to_thread(missing_models, self.runner.state, WORKFLOW_FACE_ROLES)
                if missing:
                    names = ", ".join(entry.name for entry in missing)
                    raise ValidationError(f"Detection models are missing ({names}). Download them in Workflows first.")
                # Integrity before any submission: a changed detector must not fail only after Generate spent credits.
                from dtflow.persistence.person_models import load_person_models
                from dtflow.services.person_selection import verify_model_hashes

                models = await asyncio.to_thread(load_person_models, self.runner.state)
                await asyncio.to_thread(verify_model_hashes, models, WORKFLOW_FACE_ROLES)
        except ValidationError as exc:
            self.halt(f"The workflow cannot run: {exc}")
            await self.finish()
            return
        self.start_roots()
        try:
            while True:
                actions = await self.store.apply_controls(self.queue_id)
                if "cancel" in actions:
                    await self.stop()
                    await self.take_controls()  # a Retry or branch answer posted meanwhile is recorded as ignored
                    break
                if "pause" in actions:
                    self.go.clear()
                if "resume" in actions:
                    self.go.set()
                await self.take_controls()
                for task in list(self.tasks.values()):
                    if task.done() and not task.cancelled() and task.exception() is not None:
                        raise task.exception()  # only state-store failures escape a step
                if self.dirty:
                    await self.flush()
                live = [task for task in self.tasks.values() if not task.done()]
                if not live:
                    await self.take_controls()  # a Retry that raced the run's end still applies
                    if not any(not task.done() for task in self.tasks.values()):
                        break
                    continue
                await asyncio.wait(live, timeout=self.runner.poll)
        except asyncio.CancelledError:
            await self.interrupt()
            raise
        except BaseException:
            await self.cancel_tasks()
            raise
        await self.finish()

    async def cancel_tasks(self, grace: float | None = None) -> None:
        """Cancel every live step task and wait for them, or at most ``grace`` seconds."""
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if not tasks:
            return
        if grace is None:
            await asyncio.gather(*tasks, return_exceptions=True)
            return
        _, left = await asyncio.wait(tasks, timeout=grace)
        if left:
            diagnostic_event(log, "workflow.stop_abandoned", level=logging.WARNING, data={"tasks": len(left)})

    async def abandon(self) -> None:
        """Stop waiting now: cancel in-flight waits and free the lanes of generations that did not settle in time."""
        self.stopping = True
        self.go.set()
        self.close_pauses()
        await self.cancel_tasks(STOP_GRACE)
        for attempt_id in list(self.in_flight):
            # The step's own ``finally`` releases it again when it ever returns; releasing is idempotent.
            await settle(self.store.release_script_child(self.queue_id, attempt_id))

    async def interrupt(self) -> None:
        """This process stops dispatching: in-flight waits are cancelled and recorded honestly, nothing is replayed."""
        await self.abandon()
        await self.finish(interrupted=True)

    def halt(self, message: str) -> None:
        """The run ends with ``message``: nothing new starts and open pauses close (their answer no longer matters)."""
        self.halted = message
        self.go.set()
        self.close_pauses()

    def close_pauses(self) -> None:
        for future in self.decisions.values():
            if not future.done():
                future.set_result("cancel")

    async def stop(self) -> None:
        """Queue Stop: nothing new starts, open pauses close and in-flight waits end now (#247); never replayed."""
        await self.abandon()

    async def finish(self, *, interrupted: bool = False) -> None:
        stamp = _now()
        abandoned: dict[Key, tuple[str, str]] = {}
        for attempt_id, keys in list(self.in_flight.items()):
            # Abandoned at Stop while its generation was out: its attempt says whether anything was sent; never replayed.
            outcome = stopped_outcome(await settle(self.attempt_state(attempt_id)))
            abandoned.update({key: outcome for key in keys})
        for step in self.run["steps"]:
            if step["state"] == "running":
                key = (step["node_id"], (step["branch"][0], step["branch"][1]))
                if key in abandoned:
                    state, message = abandoned[key]
                    step.update(state=state, message=message, finished_at=stamp)
                else:
                    # A submitted step records its own end (``submit``); still running means nothing was sent.
                    step.update(
                        state="cancelled", message="DTFlow stopped before this step was sent.", finished_at=stamp
                    )
            if step["state"] in ("pending", "paused"):
                step["state"] = "cancelled" if (self.stopping or interrupted) else "stopped"
                if self.halted and not self.stopping:
                    step["message"] = "Not run: the run stopped."
            step["stage"] = None
        self.run["pauses"] = []
        steps = self.run["steps"]
        results = self.run["results"]
        incomplete = [step for step in steps if step["state"] in BAD_STEP_STATES]
        problem = first_problem(steps)
        if self.halted:
            state, message = "failed", self.halted
        elif self.stopping or interrupted:
            state, message = "cancelled", "Stopped before every step ran."
            if any(step["state"] == "unknown" for step in steps):
                message += (
                    " A step already sent has no confirmed outcome: its image may still complete; Retry sends it again."
                )
        elif results and not incomplete:
            state, message = "succeeded", None
        elif results:
            state = "partial"
            message = f"{len(results)} result(s) saved; {len(incomplete)} step(s) failed, stopped or unconfirmed."
        elif any(step["state"] == "unknown" for step in steps):
            state, message = "unknown", "No result was saved and a step has no confirmed outcome."
        else:
            state, message = "failed", "No result was saved."
        if problem and state in ("partial", "unknown", "failed") and problem[1] not in (message or ""):
            # The provider's own cause (auth, quota, refusal) reaches Queues through the run message.
            message = f"{message} First problem: {problem[0]}"
        self.run.update(state=state, message=message, finished_at=stamp)
        await settle(self.flush())
        self.closed = True
        diagnostic_event(
            log,
            "workflow.run_settled",
            level=logging.INFO,
            data={"state": state, "submitted": self.run["submitted"], "planned": self.request.planned},
        )

    # -- controls --------------------------------------------------------------------------------------------------

    async def take_controls(self) -> None:
        for action, payload in await self.store.take_workflow_controls(self.queue_id):
            try:
                key = (int(payload["node_id"]), (int(payload["branch"][0]), int(payload["branch"][1])))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if action == BRANCH_CONTROL:
                future = self.decisions.get(key)
                if future is not None and not future.done():
                    future.set_result(payload.get("decision"))
                else:
                    await self.ignored(action)
            elif action == RETRY_CONTROL and not await self.retry(key):
                await self.ignored(action)

    async def ignored(self, action: str) -> None:
        await self.runner._record(self.queue_id, "control_ignored", {"action": action, "state": "workflow"})

    async def retry(self, key: Key) -> bool:
        """Rerun a failed/unknown step in place with its original settings, then its branch downstream."""
        step = self.steps.get(key)
        if step is None or step["state"] not in RETRYABLE or self.stopping or self.halted:
            return False
        node_id, branch = key
        node = self.graph.node(node_id)
        task = self.tasks.get(("branch", branch))
        if task is not None and not task.done():
            return False
        # Only this output's branch reruns (a Generate output included); sibling outputs keep their results.
        wanted = descendants(self.graph, node_id) | {node_id}
        reset = [k for k in self.steps if k[1] == branch and k[0] in wanted]
        weight = 1 if node.type in GENERATIVE else 0  # Detect/Save submit nothing; downstream is in the plan
        for other in reset:
            if self.steps[other]["state"] in BAD_STEP_STATES or other == key:
                self.steps[other].update(
                    state="pending", stage=None, message=None, output=None, mask=None, started_at=None, finished_at=None
                )
                self.values.pop(other, None)
        step["retries"] = int(step.get("retries") or 0) + 1
        self.allowance += weight
        self.run.update(state="running", message=None)
        await self.flush()
        diagnostic_event(log, "workflow.step_retry", level=logging.INFO, data={"node_type": node.type})
        if node.type == GENERATE:
            self.spawn(("branch", branch), self.produce(key))
        else:
            self.spawn(("branch", branch), self.walk(branch))
        return True

    # -- scheduling ------------------------------------------------------------------------------------------------

    def spawn(self, key: tuple[str, int | Branch], coroutine) -> None:
        self.tasks[key] = asyncio.create_task(coroutine)

    def start_roots(self) -> None:
        seed = self.request.seed
        if seed is not None and self.graph.node(int(seed["node_id"])).type != GENERATE:
            branch = (int(seed["branch"][0]), int(seed["branch"][1]))
            for value in seed["values"]:
                self.values[(int(value["node_id"]), branch)] = {
                    "output": value.get("output"),
                    "mask": value.get("mask"),
                    "no_target": bool(value.get("no_target")),
                }
            self.spawn(("branch", branch), self.walk(branch))
            return
        for node_id in self.plan.order:
            if self.graph.node(node_id).type == GENERATE and any(key[0] == node_id for key in self.steps):
                self.spawn(("generate", node_id), self.generate(node_id))

    def inputs(self, node_id: int, branch: Branch) -> dict[str, Any] | None:
        """The node's input values on this branch, or None when a parent did not produce one."""
        node = self.graph.node(node_id)
        found: dict[str, Any] = {}
        for slot, port in enumerate(node.spec.inputs):
            link = self.graph.input_link(node_id, slot)
            value = self.values.get((link.origin_id, branch)) if link is not None else None
            if value is None:
                return None
            if port.type == MASK:
                found["no_target"] = bool(value.get("no_target"))
                found[port.name] = value.get("mask")
                if found[port.name] is None and not found["no_target"]:
                    return None
            else:
                if value.get("output") is None:
                    return None
                found[port.name] = value["output"]
        return found

    async def walk(self, branch: Branch) -> None:
        for node_id in self.node_ids(branch):
            key = (node_id, branch)
            if self.steps[key]["state"] != "pending":
                continue
            if self.stopping or self.halted:
                return  # finish() records what never ran
            if branch in self.stopped_branches:
                await self.end([key], "stopped", "Stopped: this image branch was stopped.")
                continue
            inputs = self.inputs(node_id, branch)
            if inputs is None:
                await self.end([key], "stopped", "Stopped: an earlier step on this branch did not finish.")
                continue
            await self.step(node_id, branch, inputs)

    async def step(self, node_id: int, branch: Branch, inputs: dict[str, Any]) -> None:
        key = (node_id, branch)
        node_type = self.graph.node(node_id).type
        try:
            if node_type == DETECT_FACE:
                await self.detect(node_id, branch, inputs)
            elif node_type in IMAGE_OPERATIONS:
                await self.image_step(node_id, branch, inputs)
            elif node_type == SAVE:
                await self.save(node_id, branch, inputs)
        except (asyncio.CancelledError, PersistenceError):
            raise
        except Exception as exc:  # a local defect fails this step only; siblings continue
            if self.steps[key]["state"] in ("running", "pending", "paused"):
                await self.end([key], "failed", f"Step failed unexpectedly ({type(exc).__name__}).")

    # -- submission ------------------------------------------------------------------------------------------------

    async def submit(self, keys: list[Key], request, weight: int, prepared=None):
        """One generation for ``keys`` under the loop guard and a backend lane; None when it did not succeed (the
        steps then hold their end state)."""
        if self.halted:
            await self.end(keys, "stopped", "Not run: the run stopped.")
            return None
        if self.run["submitted"] + weight > self.allowance:
            self.halt(
                f"Stopped: the next submission would exceed the planned {self.request.planned} generation(s) plus "
                f"{self.allowance - self.request.planned} allowed retry submission(s). Nothing more was sent."
            )
            diagnostic_event(log, "workflow.loop_guard", level=logging.WARNING, data={"planned": self.request.planned})
            await self.end(keys, "failed", self.halted)
            return None
        # Counted at the check so concurrent branches cannot pass it together; given back when nothing is sent.
        self.run["submitted"] += weight
        try:
            while not self.go.is_set() and not self.stopping and not self.halted:
                for key in keys:
                    self.steps[key]["stage"] = "Waiting: queue paused"
                self.dirty = True
                await self.go.wait()
        except asyncio.CancelledError:
            self.run["submitted"] -= weight  # interrupted before any attempt: nothing was sent
            raise
        if self.stopping or self.halted:
            self.run["submitted"] -= weight
            await self.end(keys, "cancelled", "Stopped before submission; nothing was sent.")
            return None
        lane = attempt_id = uuid4().hex  # the lane keeps the first attempt's id across an automatic retry
        reserved = False
        try:
            while True:
                try:
                    reserved = await self.store.reserve_script_child(self.queue_id, lane, self.lanes, self.lanes)
                except QueueError:
                    reserved = False  # paused or settling queue: wait, Stop ends the wait
                if reserved:
                    break
                if self.stopping or self.halted:
                    self.run["submitted"] -= weight
                    await self.end(keys, "cancelled", "Stopped before submission; nothing was sent.")
                    return None
                self.set_stage(keys, "Waiting for a free slot")
                try:
                    await asyncio.sleep(self.runner.poll)
                except asyncio.CancelledError:
                    self.run["submitted"] -= weight  # interrupted before any attempt: nothing was sent
                    raise
            diagnostic_event(
                log, "workflow.step_submitted", level=logging.INFO, data={"attempt_id": attempt_id, "weight": weight}
            )

            def receive(event: BackendEvent) -> None:
                self.set_stage(keys, stage_text(event))
                if self.runner.on_event is not None:
                    self.runner.on_event(self.item_id, event)

            options = {"prepared_inputs": prepared} if prepared is not None else {}
            try:
                if self.request.backend == "cloud" and request.loras:
                    # Workflow files carry names/weights only. Bind existing local receipts at dispatch,
                    # independently of Generate; the generation service still validates their account/scope.
                    choices = await session.script_asset_choices(self.runner.state, self.request.backend)
                    selected = {lora.identifier for lora in request.loras}
                    request = await session.bind_prepared(
                        self.runner.state,
                        self.request.backend,
                        request,
                        tuple(asset.receipt_id for asset in choices if asset.identifier in selected),
                    )
                    while not self.go.is_set() and not self.stopping and not self.halted:
                        self.set_stage(keys, "Waiting: queue paused")
                        await self.go.wait()
                    if self.stopping or self.halted:
                        self.run["submitted"] -= weight
                        await self.end(keys, "cancelled", "Stopped before submission; nothing was sent.")
                        return None
                auto_left = AUTO_RETRIES
                while True:
                    self.in_flight[attempt_id] = keys
                    try:
                        outcome = await session.run_generation(
                            self.runner.service, request, on_event=receive, attempt_id=attempt_id, **options
                        )
                        break
                    except BackendError as exc:
                        if auto_left <= 0 or not auto_retryable(exc.failure):
                            raise
                        first = exc
                    auto_left -= 1
                    self.set_stage(keys, RETRY_STAGE)
                    diagnostic_event(
                        log,
                        "workflow.step_auto_retry",
                        level=logging.WARNING,
                        data={"attempt_id": attempt_id, "code": first.failure.code},
                    )
                    await asyncio.sleep(AUTO_RETRY_DELAY)
                    if self.stopping or self.halted or self.run["submitted"] + weight > self.allowance:
                        raise first  # settles the step from the first try's evidence
                    self.run["submitted"] += weight
                    self.in_flight.pop(attempt_id, None)
                    attempt_id, request = uuid4().hex, auto_retry_request(request)
            except asyncio.CancelledError:
                attempt = await settle(self.attempt_state(attempt_id))
                if attempt is None:
                    self.run["submitted"] -= weight
                await settle(self.end(keys, *stopped_outcome(attempt)))
                raise
            except PersistenceError:
                raise
            except Exception as exc:
                attempt = await self.attempt_state(attempt_id)
                if attempt is None:
                    self.run["submitted"] -= weight  # refused before any attempt existed: nothing was submitted
                remote_unknown = (
                    isinstance(exc, BackendError) and exc.failure.remote_outcome == RemoteOutcome.UNKNOWN
                ) or attempt == "unknown"
                diagnostic_event(
                    log,
                    "workflow.step_failed",
                    level=logging.WARNING,
                    data={"attempt_id": attempt_id, "attempt_state": attempt, "reason": type(exc).__name__},
                )
                await self.end(keys, "unknown" if remote_unknown else "failed", failure_text(exc))
                return None
            if self.request.backend == "cloud":
                bound = {asset.identifier for asset in outcome.request.prepared_assets}
                preflight = [
                    {
                        "identifier": item.identifier,
                        "availability": item.availability,
                        "code": item.code,
                        "prepared": item.identifier in bound,
                    }
                    for item in outcome.diagnostics
                ]
                warnings = [
                    f"{item.identifier}: DT+ availability unconfirmed (no prepared receipt). If this is a custom LoRA, use Prepare."
                    for item in outcome.diagnostics
                    if item.code == "availability_unknown" and item.identifier not in bound
                ]
                for key in keys:
                    self.steps[key].update(warnings=list(warnings), lora_preflight=copy.deepcopy(preflight))
                if warnings:
                    await self.runner._record(
                        self.queue_id,
                        "workflow_warning",
                        {
                            "item_id": self.item_id,
                            "node_id": keys[0][0],
                            "branch": self.steps[keys[0]]["branch_label"],
                            "message": " ".join(warnings),
                        },
                    )
                    diagnostic_event(
                        log,
                        "workflow.lora_warning",
                        level=logging.WARNING,
                        data={
                            "code": "prepared_receipt_missing",
                            "count": len(warnings),
                            "node_type": self.steps[keys[0]]["type"],
                        },
                    )
            return outcome
        finally:
            self.in_flight.pop(attempt_id, None)
            # Unconditional: a reserve cancelled mid-call may have committed before ``reserved`` was set.
            await settle(self.store.release_script_child(self.queue_id, lane))

    async def attempt_state(self, attempt_id: str) -> str | None:
        try:
            rows = await self.store.inspect(attempt_id)
        except PersistenceError:
            return None
        return rows[0]["state"] if rows else None

    def evidence(self, branch: Branch | None = None) -> tuple[tuple[str, str], ...]:
        pairs = [("workflow_run", self.request.run_id)]
        if branch is not None:
            pairs.append(("workflow_branch", f"{branch[0]}:{branch[1]}"))
        return tuple(pairs)

    # -- node kinds ------------------------------------------------------------------------------------------------

    async def generate(self, node_id: int) -> None:
        """Start one single-image request per planned output (#275); each continues down its own branch."""
        keys = sorted((key for key in self.steps if key[0] == node_id), key=lambda key: key[1])
        for key in keys:
            self.spawn(("branch", key[1]), self.produce(key))

    async def produce(self, key: Key) -> None:
        """One Generate output as its own request: batch size 1 x count 1 and its own seed (base seed + output index,
        wrapped at the seed limit). A failure or unknown outcome stays on this output; its siblings are unaffected."""
        node_id, branch = key
        node = self.graph.node(node_id)
        recorded = self.steps[key].get("settings") or {}
        seed = recorded.get("seed")  # Retry reuses the seed this output was first submitted with
        if seed is None:
            seed = branch_seed(int(node.params["seed"]), branch[1])
        await self.begin([key])
        try:
            request = node_request(
                node, request_id=uuid4().hex, evidence=self.evidence(branch), seed=int(seed), single=True
            )
        except ValidationError as exc:
            await self.end([key], "failed", str(exc))
            outcome = None
        else:
            self.steps[key]["settings"] = settings_of(request)
            outcome = await self.submit([key], request, 1)
        if outcome is not None:
            settings = settings_of(outcome.request)
            if outcome.output_paths:
                path = outcome.output_paths[0]
                self.values[key] = {"output": path}
                await self.end([key], "succeeded", output=path, settings=settings)
            else:
                await self.end([key], "failed", "The backend returned no image.", settings=settings)
        await self.walk(branch)  # a branch without an image marks its steps stopped and submits nothing

    async def detect(self, node_id: int, branch: Branch, inputs: dict[str, Any]) -> None:
        from dtflow.persistence.person_models import load_person_models
        from dtflow.services.face_mask import detect_face_mask

        node = self.graph.node(node_id)
        key = (node_id, branch)
        source = inputs["image"]
        self.steps[key]["parent"] = source
        await self.begin([key])
        loop = asyncio.get_running_loop()

        def on_stage(text: str) -> None:
            loop.call_soon_threadsafe(self.set_stage, [key], text)

        try:
            models = await asyncio.to_thread(load_person_models, self.runner.state)
            data = await asyncio.to_thread(Path(source).read_bytes)
            result = await settle(
                asyncio.to_thread(
                    detect_face_mask,
                    data,
                    models,
                    mask_region=str(node.params["mask_region"]),
                    person=str(node.params["person"]),
                    on_stage=on_stage,
                )
            )
        except ValidationError as exc:
            await self.end([key], "failed", str(exc))
            return
        except OSError:
            await self.end([key], "failed", "The source image could not be read.")
            return
        except NotImplementedError:
            await self.end([key], "failed", "Face detection is not available in this build.")
            return
        if result.reason is None and result.mask_png:
            name = f"mask-{node_id}-{branch[0]}-{branch[1]}-{uuid4().hex[:8]}.png"
            path = self.runner.state / "workflows" / "runs" / self.request.run_id / name
            await asyncio.to_thread(_write_mask, path, result.mask_png)
            self.values[key] = {"mask": str(path)}
            await self.end([key], "succeeded", mask=str(path))
            return
        reason = result.reason or "Face region is empty"
        if self.stopping or self.halted:
            # Stop or a halt already closed the open pauses; a pause made now would never be answered.
            await self.end([key], "cancelled", reason)
            return
        future = loop.create_future()
        self.decisions[key] = future
        step = self.steps[key]
        step.update(state="paused", stage=None, message=reason)
        self.run["pauses"].append(
            {
                "node_id": node_id,
                "branch": [branch[0], branch[1]],
                "branch_label": step["branch_label"],
                "reason": reason,
            }
        )
        await self.flush()
        diagnostic_event(log, "workflow.branch_paused", level=logging.INFO, data={"faces": result.faces})
        try:
            decision = await future
        finally:
            self.decisions.pop(key, None)
            self.run["pauses"] = [
                pause
                for pause in self.run["pauses"]
                if not (pause["node_id"] == node_id and tuple(pause["branch"]) == branch)
            ]
        if decision == "continue":
            self.values[key] = {"mask": None, "no_target": True}
            await self.end([key], "skipped", f"{reason}; continued without a mask.")
        elif decision == "stop":
            self.stopped_branches.add(branch)
            await self.end([key], "stopped", f"{reason}; this image branch was stopped.")
        else:
            await self.end([key], "cancelled", reason)

    async def image_step(self, node_id: int, branch: Branch, inputs: dict[str, Any]) -> None:
        node = self.graph.node(node_id)
        key = (node_id, branch)
        source = inputs["image"]
        self.steps[key]["parent"] = source
        if node.type == INPAINT and inputs.get("no_target"):
            # D-branch "continue": the inpaint does not run and its image output is the unchanged source.
            self.values[key] = {"output": source}
            await self.end([key], "skipped", "No face to inpaint; the source image passes through.", output=source)
            return
        await self.begin([key])
        operation = IMAGE_OPERATIONS[node.type]
        permit = self.runner.permits.get((str(node.params["checkpoint"]), operation))
        if permit is None:
            message = (
                "Image steps need the local or cloud backend; the fake backend runs text-to-image only."
                if self.request.backend == "fake"
                else "No image permit was prepared for this checkpoint and operation; nothing was sent."
            )
            await self.end([key], "failed", message)
            return
        self.set_stage([key], "Preparing image inputs")
        try:
            width, height, prepared = await settle(
                asyncio.to_thread(
                    _prepare_source,
                    source,
                    inputs.get("mask"),
                    int(node.params.get("grow", 0)),
                    float(node.params.get("feather", 0.0)),
                )
            )
            request = node_request(node, width=width, height=height, evidence=self.evidence(branch))
            request = session.image_operation_request(
                request,
                prepared,
                permit,
                strength=float(node.params["strength"]),
                preserve_outside=bool(node.params.get("preserve_outside", True)),
            )
        except ValidationError as exc:
            await self.end([key], "failed", str(exc))
            return
        except OSError:
            await self.end([key], "failed", "The source image or mask could not be read.")
            return
        self.steps[key]["settings"] = settings_of(request)
        outcome = await self.submit([key], request, 1, prepared)
        if outcome is None:
            return
        if not outcome.output_paths:
            await self.end([key], "failed", "The backend returned no image.", settings=settings_of(outcome.request))
            return
        output = outcome.output_paths[0]  # the saved final (composite for a preserved inpaint), never raw or display
        self.values[key] = {"output": output}
        await self.end([key], "succeeded", output=output, settings=settings_of(outcome.request))

    async def save(self, node_id: int, branch: Branch, inputs: dict[str, Any]) -> None:
        node = self.graph.node(node_id)
        key = (node_id, branch)
        path = inputs["image"]
        self.run["results"].append(
            {"label": str(node.params["label"]), "path": path, "branch": [branch[0], branch[1]], "node_id": node_id}
        )
        self.values[key] = {"output": path}
        await self.end([key], "succeeded", parent=path, output=path)

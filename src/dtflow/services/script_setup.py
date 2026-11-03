"""Window-owned script preparation, without a queue, backend session or generation.

The bounded JS child parks on its first durable form. Run Script later queues the
same run and the ordinary dispatcher adopts it before releasing its first answer.
Lost preparations fail closed; they are never reconstructed or replayed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.persistence.script_items import read_run_snapshot
from dtflow.persistence.script_runs import ScriptRunStore, read_open_prompt
from dtflow.scripting.host import ScriptHost, starting_context
from dtflow.scripting.runner import run_recorded
from dtflow.services.session import script_asset_choices


class _InventoryOnly:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def get_inventory(self):
        if self.snapshot is None:
            raise ValidationError(
                "Refresh inventory on Generate, then reload Setup; preparation never contacts a backend."
            )
        return self.snapshot


@dataclass
class ScriptSetup:
    state: Path
    run_id: str
    source: str
    source_name: str
    base: object
    backend: dict
    cap: int
    expected_steps: int | None
    loop: object
    owner: object
    cancel: threading.Event = field(default_factory=threading.Event)
    admitted: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    problem: str | None = None
    hosts: list = field(default_factory=list)
    task: object = None
    queued: bool = False


_lock = threading.Lock()
_setups: dict[tuple[str, str], ScriptSetup] = {}
_closed_owners: set[object] = set()


def _key(state: Path, run_id: str):
    return os.path.normcase(os.path.abspath(state)), run_id


def find_setup(state: Path, run_id: str) -> ScriptSetup:
    with _lock:
        setup = _setups.get(_key(state, run_id))
    if setup is None or setup.cancel.is_set():
        raise ValidationError("This Setup is no longer available. Return to the form and prepare a fresh one.")
    return setup


async def prepare_script(
    state: Path,
    source: str,
    source_name: str,
    base,
    backend: dict,
    cap: int,
    *,
    inventory=None,
    expected_steps=None,
    owner=None,
) -> str:
    """Start bounded preparation on the existing runner loop; never create a queue or open a provider."""
    owner = owner if owner is not None else object()
    available_assets = await script_asset_choices(Path(state), backend["name"])
    setup = ScriptSetup(
        Path(state),
        uuid.uuid4().hex,
        source,
        source_name,
        base,
        dict(backend),
        cap,
        expected_steps,
        asyncio.get_running_loop(),
        owner,
    )
    with _lock:
        if owner in _closed_owners:
            raise ValidationError("The Setup window has closed.")
        _setups[_key(state, setup.run_id)] = setup

    def hooks(context):
        host = ScriptHost(
            service=_InventoryOnly(inventory),
            base=base,
            loop=setup.loop,
            link_child=context.link_child,
            console=context.console,
            cancel=setup.cancel,
            sandbox=Path(state) / "script_files" / context.run_id,
            max_generations=cap,
            record_answers=context.record_answers,
            ask=context.ask,
            run_id=context.run_id,
            require_start=True,
            admission_gate=setup.admitted,
            available_assets=available_assets,
            source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        )
        setup.hosts.append(host)
        return host.hooks()

    def run():
        return run_recorded(
            ScriptRunStore(state),
            source,
            hooks,
            source_name=source_name,
            cancel=setup.cancel,
            run_id=setup.run_id,
            context_json=json.dumps(starting_context(base, backend=backend["name"], max_generations=cap, answers=None)),
        )

    async def supervise():
        try:
            return await asyncio.to_thread(run)
        except asyncio.CancelledError:
            setup.cancel.set()
            raise
        except Exception as exc:
            setup.problem = str(exc)[:4000] or type(exc).__name__
            raise
        finally:
            setup.finished.set()

    setup.task = asyncio.create_task(supervise(), name="dtflow-script-setup")
    setup.task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
    return setup.run_id


def read_setup(state: Path, run_id: str) -> dict:
    setup = find_setup(state, run_id)
    run = read_run_snapshot(state, run_id)
    if run is None and setup.finished.is_set():
        raise ValidationError(setup.problem or "Setup ended before it could publish its form.")
    if run is not None and run.state not in ("running", "completed"):
        raise ValidationError(run.error or "Setup was stopped.")
    prompt = read_open_prompt(state, run_id)
    if prompt is not None:
        return {
            "ready": True,
            "preparation_only": any(prompt.ordinal in host.preparation_ordinals for host in setup.hosts),
            "prompt": {
                "ordinal": prompt.ordinal,
                "title": prompt.title,
                "confirm": prompt.confirm,
                "components": prompt.components,
            },
        }
    if run is not None and run.state == "completed":
        return {"ready": True, "prompt": None, "preview_completed": any(host.preparation_ordinals for host in setup.hosts)}
    return {"ready": False, "prompt": None}


def answer_preparation(state: Path, run_id: str, ordinal: int, answers: list) -> None:
    """Advance an explicitly marked Setup form without queueing or opening a provider."""
    from dtflow.services.script_form_inputs import resolve_form_inputs

    setup = find_setup(state, run_id)
    with _lock:
        if setup.queued or setup.admitted.is_set() or setup.cancel.is_set():
            raise ValidationError("This Setup was already admitted or closed.")
        if not any(ordinal in host.preparation_ordinals for host in setup.hosts):
            raise ValidationError("This form requires Run Script; it cannot advance during preparation.")
    prompt = read_open_prompt(state, run_id)
    if prompt is None or prompt.ordinal != ordinal:
        raise ValidationError("This preview form has changed or closed.")
    resolve_form_inputs(state, run_id, ordinal, prompt.components, answers)
    if not ScriptRunStore(state).answer_prompt(run_id, ordinal, answers):
        raise ValidationError("This preview form was already answered or closed.")


def cancel_setup(state: Path, run_id: str) -> None:
    with _lock:
        setup = _setups.get(_key(state, run_id))
        if setup is not None and not setup.queued:
            _setups.pop(_key(state, run_id), None)
        else:
            setup = None
    if setup is not None:
        setup.cancel.set()


def reserve_setup(state: Path, run_id: str) -> ScriptSetup:
    prompt = read_open_prompt(state, run_id)
    with _lock:
        setup = _setups.get(_key(state, run_id))
        if setup is None or setup.cancel.is_set() or setup.queued:
            raise ValidationError("This Setup was already submitted or closed.")
        if any(host.preparation_ordinals for host in setup.hosts) and (
            prompt is None or any(prompt.ordinal in host.preparation_ordinals for host in setup.hosts)
        ):
            raise ValidationError("Finish preview and reach the separate Run Script form before queue admission.")
        if setup.finished.is_set() and any(host.preparation_ordinals for host in setup.hosts):
            raise ValidationError("This preview finished without a queue. Reload Setup to prepare another plan.")
        setup.queued = True
    return setup


def release_setup(setup: ScriptSetup) -> None:
    with _lock:
        setup.queued = False


def abandon_setup(state: Path, run_id: str) -> None:
    """Terminal/cancelled queue whose preparation was never adopted owns no child work."""
    with _lock:
        setup = _setups.pop(_key(state, run_id), None)
    if setup is not None:
        setup.cancel.set()


def close_setups(state: Path, owner: object) -> None:
    """Nonblocking shutdown signal, including queued but not yet adopted preparations."""
    root = os.path.normcase(os.path.abspath(state))
    with _lock:
        _closed_owners.add(owner)
        setups = [v for (directory, _), v in _setups.items() if directory == root and v.owner is owner]
        for setup in setups:
            _setups.pop(_key(state, setup.run_id), None)
    for setup in setups:
        setup.cancel.set()


def adopt_setup(
    state: Path, request, service, loop, *, reserve_child=None, release_child=None, available_assets=()
) -> ScriptSetup:
    """Called only after the ordinary dispatcher claims the queue item."""
    setup = find_setup(state, request.run_id)
    if (
        not setup.queued
        or setup.loop is not loop
        or hashlib.sha256(setup.source.encode("utf-8")).hexdigest() != request.source_sha256
        or json.dumps(asdict(setup.base), sort_keys=True) != json.dumps(request.base, sort_keys=True)
        or setup.backend["name"] != request.backend
        or setup.cap != request.max_generations
    ):
        raise ValidationError("The queued script does not match its owned preparation; it was not replayed.")
    for host in setup.hosts:
        # Attach capacity accounting before releasing Run Script's admission gate.
        host.reserve_child, host.release_child = reserve_child, release_child
        host.available_assets = available_assets
        host.service = service  # No generation hook can have returned from its initial prompt before this adoption.
    setup.admitted.set()
    with _lock:
        _setups.pop(_key(state, request.run_id), None)
    return setup

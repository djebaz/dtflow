"""Where a script run's state is read from, and the owned picker inputs of its forms (#243).

A run is read from Scripts Setup (in this process, before Run Script) or from its queue item (any process, read-only).
Only the run's writer changes it: Setup's in-memory run, or the dispatch owner for a queued run. Other processes answer
a form by posting a queue control (``answer_script_prompt``).

A picker input is an owned snapshot made on a worker. Its capability token is a small file in the run's own file area
(``script_inputs/<run>/<token>.json``, outside the script's own sandbox ``script_files/<run>``) bound to the form and
field, so any process can register one without
writing the run, and the run's writer checks every token against it before the script sees an answer.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from dtflow.core.models import ValidationError
from dtflow.persistence.queues import read_recalled_answers, read_script_run
from dtflow.scripting.run_state import ScriptPrompt, open_prompt_of

MAX_INPUTS = 1024


def _setup_run(state: Path, run_id: str):
    from dtflow.services.script_setup import live_setup_run

    return live_setup_run(state, run_id)


def read_run(state: Path, run_id: str) -> dict[str, Any] | None:
    """The run's current state: Setup's run while it is being prepared here, otherwise the one kept in its item."""
    run = _setup_run(state, run_id)
    if run is not None:
        return run.snapshot()
    found = read_script_run(state, run_id)
    return found[2] if found is not None else None


def read_open_prompt(state: Path, run_id: str) -> ScriptPrompt | None:
    """The run's open form, or ``None`` when it has none (or there is no such run)."""
    run = _setup_run(state, run_id)
    if run is not None:
        return run.open_prompt()
    found = read_script_run(state, run_id)
    return open_prompt_of(run_id, found[2]) if found is not None else None


def recalled_answers(state: Path, run_id: str, components: list) -> list | None:
    run = read_run(state, run_id)
    source = run.get("source_sha256") if isinstance(run, dict) else None
    return read_recalled_answers(state, source, components) if isinstance(source, str) else None


def _inputs(state: Path, run_id: str) -> Path:
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValidationError("Invalid script run identity.")
    return Path(os.path.abspath(state)) / "script_inputs" / run_id  # never inside the script's sandbox


def register_prompt_input(state: Path, run_id: str, ordinal: int, field: tuple, kind: str, record: dict) -> str:
    """Register a worker-created owned snapshot, only while its exact form is still open."""
    prompt = read_open_prompt(state, run_id)
    if prompt is None or prompt.ordinal != ordinal:
        raise ValidationError("The input form has closed; selected files were not submitted.")
    directory = _inputs(state, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    if sum(1 for _ in directory.glob("*.json")) >= MAX_INPUTS:
        raise ValidationError(f"This script run exceeded its {MAX_INPUTS} owned-input selection limit.")
    name = uuid.uuid4().hex
    entry = {"ordinal": ordinal, "field": list(field), "kind": kind, "record": record}
    with (directory / f"{name}.json").open("x", encoding="utf-8") as stream:
        json.dump(entry, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return "dtflow-input:" + name


def prompt_input(state: Path, run_id: str, ordinal: int, field: tuple, kind: str, token: str) -> dict:
    """The owned snapshot a token names, only for this run, form and field."""
    name = token.removeprefix("dtflow-input:") if isinstance(token, str) else ""
    if len(name) != 32 or any(c not in "0123456789abcdef" for c in name):
        raise ValidationError("Selected input does not belong to this run, form and field.")
    try:
        entry = json.loads((_inputs(state, run_id) / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError("Selected input does not belong to this run, form and field.") from exc
    if (
        not isinstance(entry, dict)
        or entry.get("ordinal") != ordinal
        or entry.get("field") != list(field)
        or entry.get("kind") != kind
        or not isinstance(entry.get("record"), dict)
    ):
        raise ValidationError("Selected input does not belong to this run, form and field.")
    return entry["record"]

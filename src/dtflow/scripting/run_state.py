"""A script run's state, kept in its queue item (#243): no second run store.

The run is one JSON object, stored as ``request_json["run"]`` of the script's queue item by the dispatch owner (like a
workflow run, ``persistence/workflow_items.py``). ``ScriptRun`` holds it in memory and hands every change to a
``persist`` callback *before* the change takes effect, so a child attempt is durably linked before anything can be
submitted, and a form or its answers are durable before the script sees them. A ``persist`` failure raises and leaves
the run as it was.

Without ``persist`` (Scripts Setup before Run Script, and ``dtflow script --run`` without ``--queue``) the run lives in
memory only. Setup's run is attached to its queue item when the dispatcher adopts it (``attach``), before generation
can be admitted.

A run left ``running`` by a crash is turned ``interrupted`` by the queue reconcile, in the same transaction that settles
its item; it is never resumed or replayed.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

STATES = ("running", "completed", "failed", "stopped", "interrupted")
TERMINAL = ("completed", "failed", "stopped", "interrupted")
MAX_PROMPTS = 128
MAX_PROMPT_JSON_CHARS = 1_000_000
MAX_ANSWERS_JSON_CHARS = 6_000_000
PROMPT_POLL_SECONDS = 0.25

#: ``persist(run, recall)``: write ``run`` durably; ``recall`` is ``(source sha, form sha, answers json)`` or ``None``.
Persist = Callable[[dict[str, Any], tuple[str, str, str] | None], None]


class ScriptRunError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScriptPrompt:
    run_id: str
    ordinal: int
    title: str
    confirm: str
    components: list
    state: str


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_run(
    *,
    source_name: str,
    source_sha256: str,
    api_version: str | None,
    vendored_js_sha256: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "state": "running",
        "source_name": source_name,
        "source_sha256": source_sha256,
        "api_version": api_version,
        "vendored_js_sha256": vendored_js_sha256,
        "context": context or {},
        "created_at": now(),
        "finished_at": None,
        "error": None,
        "children": [],
        "answers": [],
        "prompts": [],
    }


def open_prompt_of(run_id: str, run: Any) -> ScriptPrompt | None:
    """The run's open form, read from its stored state; ``None`` when it has none (or the state is not a run)."""
    if not isinstance(run, dict) or run.get("state") != "running":
        return None
    for prompt in reversed(run.get("prompts") or ()):
        if isinstance(prompt, dict) and prompt.get("state") == "open":
            return ScriptPrompt(
                run_id,
                int(prompt["ordinal"]),
                str(prompt.get("title") or ""),
                str(prompt.get("confirm") or ""),
                list(prompt.get("components") or ()),
                "open",
            )
    return None


def interrupted(run: dict[str, Any], at: str) -> dict[str, Any]:
    """The state a run left ``running`` by a crash takes: interrupted, with its open form closed."""
    run = copy.deepcopy(run)
    if run.get("state") == "running":
        run["state"], run["finished_at"] = "interrupted", at
        _close_prompts(run, at)
    return run


def _close_prompts(run: dict[str, Any], at: str, ordinal: int | None = None) -> bool:
    closed = False
    for prompt in run.get("prompts") or ():
        if prompt.get("state") == "open" and (ordinal is None or prompt.get("ordinal") == ordinal):
            prompt.update(state="cancelled", answered_at=at)
            prompt.pop("components", None)  # only an open form needs its fields
            closed = True
    return closed


class ScriptRun:
    """One run's state with its durable writer. Thread-safe; never call a mutator on the ``persist`` loop's thread."""

    def __init__(self, run_id: str, run: dict[str, Any], persist: Persist | None = None) -> None:
        self.run_id = run_id
        self._run = run
        self._persist = persist
        self._delivered: dict[int, list] = {}  # answers handed to a waiting form, by ordinal (never stored twice)
        self._condition = threading.Condition()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return copy.deepcopy(self._run)

    @property
    def state(self) -> str:
        with self._condition:
            return str(self._run["state"])

    @property
    def children(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(self._run["children"])

    @property
    def source_sha256(self) -> str:
        return str(self._run.get("source_sha256") or "")

    def open_prompt(self) -> ScriptPrompt | None:
        with self._condition:
            return open_prompt_of(self.run_id, self._run)

    def _commit(self, change: Callable[[dict[str, Any]], Any], recall=None) -> Any:
        """Apply ``change`` to a copy, persist the copy, then make it current. Nothing changes if persisting fails."""
        with self._condition:
            draft = copy.deepcopy(self._run)
            result = change(draft)
            if self._persist is not None:
                self._persist(draft, recall(draft) if callable(recall) else None)
            self._run = draft
            self._condition.notify_all()
            return result

    def attach(self, persist: Persist) -> None:
        """Make the run durable from now on (Setup adopted by its queue item); the current state is written first."""
        with self._condition:
            persist(copy.deepcopy(self._run), None)
            self._persist = persist

    def begin(self) -> None:
        """Write the starting state; called before the script child starts, so a crash finds the run."""
        self._commit(lambda run: None)

    def _require_running(self, run: dict[str, Any], what: str) -> None:
        if run["state"] != "running":
            raise ScriptRunError(f"{what} only for a running script run.")

    def link_child(self, attempt_id: str) -> int:
        """Durably append a child attempt before any provider submission; returns its 1-based ordinal."""

        def change(run):
            self._require_running(run, "Child attempts can be linked")
            run["children"].append(attempt_id)
            return len(run["children"])

        return self._commit(change)

    def append_answers(self, entry: dict) -> int:
        """Durably append one form-answer record (before the script sees the answers). Returns the entry count."""
        from dtflow.scripting.forms import bounded_json

        def change(run):
            self._require_running(run, "Answers can be recorded")
            run["answers"].append(entry)
            try:
                bounded_json(run["answers"], MAX_ANSWERS_JSON_CHARS)
            except ValueError as exc:
                raise ScriptRunError("Recorded answers exceed the size limit.") from exc
            return len(run["answers"])

        return self._commit(change)

    def publish_prompt(self, title: str, confirm: str, components: list) -> int:
        """Durably open a form and return its ordinal. Any earlier open form of the run is superseded."""
        from dtflow.scripting.forms import bounded_json, validate_components

        validate_components(components)
        if any(not isinstance(x, str) or len(x) > 64_000 for x in (title, confirm)):
            raise ScriptRunError("Form title or confirmation exceeds its limit.")
        try:
            bounded_json(components, MAX_PROMPT_JSON_CHARS)
        except ValueError as exc:
            raise ScriptRunError("The form is too large.") from exc

        def change(run):
            self._require_running(run, "A form can be opened")
            at = now()
            _close_prompts(run, at)
            ordinal = len(run["prompts"]) + 1
            if ordinal > MAX_PROMPTS:
                raise ScriptRunError(f"This script run exceeded its {MAX_PROMPTS}-form limit.")
            run["prompts"].append(
                {
                    "ordinal": ordinal,
                    "title": title,
                    "confirm": confirm,
                    "components": copy.deepcopy(components),
                    "state": "open",
                    "created_at": at,
                    "answered_at": None,
                }
            )
            return ordinal

        return self._commit(change)

    def answer_prompt(self, ordinal: int, answers: list, origin: Callable | None = None) -> bool:
        """Record the answers of the open form ``ordinal``; False (nothing changed) if it is no longer open.

        ``origin`` checks every owned picker input against this run, form and field. The recallable part of the
        answers is kept for the next run of the same script and form."""
        from dtflow.scripting.forms import MAX_ANSWER_BYTES, bounded_json, form_key, recallable_answers
        from dtflow.scripting.forms import validate_answers

        with self._condition:
            prompt = open_prompt_of(self.run_id, self._run)
            if prompt is None or prompt.ordinal != ordinal:
                return False
            validate_answers(prompt.components, answers, origin=origin)
            bounded_json(answers, MAX_ANSWER_BYTES)
            recall = (
                self.source_sha256,
                form_key(prompt.components),
                bounded_json(recallable_answers(prompt.components, answers), MAX_ANSWER_BYTES),
            )

            def change(run):
                for entry in run["prompts"]:
                    if entry.get("ordinal") == ordinal and entry.get("state") == "open":
                        entry.update(state="answered", answered_at=now())
                        entry.pop("components", None)

            self._commit(change, recall=lambda run: recall if self.source_sha256 else None)
            self._delivered[ordinal] = answers
            return True

    def cancel_prompt(self, ordinal: int) -> bool:
        with self._condition:
            if (prompt := open_prompt_of(self.run_id, self._run)) is None or prompt.ordinal != ordinal:
                return False
            self._commit(lambda run: _close_prompts(run, now(), ordinal))
            return True

    def cancel_prompts(self) -> None:
        """Resolve every open form of the run as cancelled (Stop, or the script ended)."""
        with self._condition:
            if open_prompt_of(self.run_id, self._run) is not None:
                self._commit(lambda run: _close_prompts(run, now()))

    def wait_for_answer(self, ordinal: int, cancel: threading.Event, poll: float = PROMPT_POLL_SECONDS) -> list | None:
        """Block until the form is answered; ``None`` when it was cancelled or Stop was requested."""
        with self._condition:
            while True:
                if cancel.is_set():
                    break
                if ordinal in self._delivered:
                    return self._delivered.pop(ordinal)
                prompt = open_prompt_of(self.run_id, self._run)
                if prompt is None or prompt.ordinal != ordinal:
                    return None
                self._condition.wait(poll)
        self.cancel_prompts()
        return None

    def ask(self, title: str, confirm: str, components: list, cancel: threading.Event | None) -> list | None:
        if cancel is not None and cancel.is_set():
            return None
        ordinal = self.publish_prompt(title, confirm, components)
        return self.wait_for_answer(ordinal, cancel if cancel is not None else threading.Event())

    def finish(self, state: str, error: str | None = None) -> bool:
        """Settle a running run; returns False (changing nothing) if it was no longer running."""
        if state not in TERMINAL:
            raise ScriptRunError("Unknown terminal script-run state.")

        def change(run):
            if run["state"] != "running":
                return False
            at = now()
            _close_prompts(run, at)
            run.update(state=state, error=error, finished_at=at)
            return True

        with self._condition:
            if self._run["state"] != "running":
                return False
            return self._commit(change)

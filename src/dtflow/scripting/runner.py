"""Parent-side script runner: disposable child process, host-call dispatch, deadline and Stop.

The JS engine lives only in the child. Every hook call arrives here as a message; implementations run in the calling
(worker) thread and must poll ``cancel`` if they can block. Stop refuses further hook calls (they return the upstream
``cancelled`` error) and kills a child that does not finish within ``STOP_GRACE_SECONDS``. The deadline kills the child
unconditionally. Killing never cancels provider work that a hook already submitted; that stays owned by its attempt.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from dtflow.diagnostics import event as diagnostic_event
from dtflow.scripting.engine import MAX_CALL_BYTES, ScriptLimits, ScriptOutcome
from dtflow.scripting.hooks import HOOKS, UNSUPPORTED
from dtflow.scripting.worker import main as worker_main

log = logging.getLogger("dtflow.script")

MAX_CONSOLE_LINES = 2000
MAX_CONSOLE_LINE_CHARS = 4000
MAX_SLEEP_SECONDS = 3600.0
STOP_GRACE_SECONDS = 1.0
POLL_SECONDS = 0.05


class ScriptCancelled(Exception):
    """Raised by a hook implementation when Stop was requested; surfaces to JS as the upstream ``cancelled`` error."""


@dataclass(slots=True)
class RunResult:
    status: str  # completed | failed | stopped
    error: str | None = None
    console: list[tuple[int, str]] = field(default_factory=list)
    host_calls: int = 0


class HookDispatcher:
    """Runs hook implementations for the parent side; enforces Stop, argument bounds and error forwarding."""

    def __init__(self, implementations: Mapping[str, Callable[..., Any]] | None, cancel: threading.Event) -> None:
        self.cancel = cancel
        self.result = RunResult("failed")
        self.implementations: dict[str, Callable[..., Any]] = {"log": self.log, "sleep": self.sleep}
        for name, function in (implementations or {}).items():
            if name not in HOOKS:
                raise ValueError(f"Unknown hook {name!r}.")
            self.implementations[name] = function

    def log(self, message: Any = "", kind: int = 0) -> None:
        text = message if isinstance(message, str) else json.dumps(message)
        if len(text) > MAX_CONSOLE_LINE_CHARS:
            text = text[:MAX_CONSOLE_LINE_CHARS] + "…"
        kind = kind if kind in (0, 1, 2) else 0
        if len(self.result.console) < MAX_CONSOLE_LINES:
            self.result.console.append((kind, text))
        diagnostic_event(
            log,
            "script.console",
            level=(logging.INFO, logging.WARNING, logging.ERROR)[kind],
            data={"kind": ("log", "warn", "error")[kind], "length": len(text)},
        )

    def sleep(self, seconds: Any = 0) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds != seconds or seconds < 0:
            raise ValueError("__dtSleep expects a non-negative number of seconds.")
        if self.cancel.wait(min(float(seconds), MAX_SLEEP_SECONDS)):
            raise ScriptCancelled()

    def call(self, name: str, payload: str) -> str:
        self.result.host_calls += 1
        try:
            if self.cancel.is_set():
                raise ScriptCancelled()
            if len(payload) > MAX_CALL_BYTES:
                raise ValueError(f"Arguments to {name} are too large.")
            spec = HOOKS.get(name)
            function = self.implementations.get(name)
            if spec is None or function is None:
                reason = (
                    "is not supported by DTFlow"
                    if spec is not None and spec.state == UNSUPPORTED
                    else "is not implemented in this DTFlow build yet"
                )
                raise NotImplementedError(f"{name} {reason}.")
            return json.dumps({"value": function(*json.loads(payload))})
        except ScriptCancelled:
            return json.dumps({"error": "cancelled"})
        except Exception as exc:  # forwarded to JS like upstream forwardExceptionsToJS
            return json.dumps({"error": str(exc) or type(exc).__name__})


def run_script(
    source: str,
    implementations: Mapping[str, Callable[..., Any]] | None = None,
    *,
    limits: ScriptLimits | None = None,
    cancel: threading.Event | None = None,
) -> RunResult:
    """Evaluate ``source`` in a child process. Blocks the calling thread; never call it from the Qt GUI thread."""
    limits = limits or ScriptLimits()
    cancel = cancel or threading.Event()
    dispatcher = HookDispatcher(implementations, cancel)
    result = dispatcher.result
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=worker_main, args=(child,), name="dtflow-script", daemon=True)
    process.start()
    child.close()
    started = time.monotonic()
    stop_deadline: float | None = None
    try:
        parent.send({"source": source, "limits": asdict(limits)})
        while True:
            now = time.monotonic()
            if now - started > limits.deadline_seconds:
                result.status, result.error = "failed", "Script exceeded its time limit and was terminated."
                break
            if cancel.is_set():
                stop_deadline = stop_deadline if stop_deadline is not None else now + STOP_GRACE_SECONDS
                if now > stop_deadline:
                    result.status, result.error = "stopped", None
                    break
            if not parent.poll(POLL_SECONDS):
                if not process.is_alive() and not parent.poll(0):
                    result.status, result.error = "failed", "Script process ended unexpectedly."
                    break
                continue
            try:
                message = parent.recv()
            except (EOFError, OSError):
                result.status, result.error = "failed", "Script process ended unexpectedly."
                break
            if "call" in message:
                parent.send({"reply": dispatcher.call(message["call"], message["payload"])})
            elif "done" in message:
                outcome = ScriptOutcome(message["done"], message.get("error"))
                result.status, result.error = outcome.status, outcome.error
                if cancel.is_set() and result.status == "completed":
                    result.status = "stopped"
                break
    except (BrokenPipeError, OSError):
        if result.status == "failed" and result.error is None:
            result.error = "Script process ended unexpectedly."
    finally:
        parent.close()
        process.join(timeout=0.5)
        if process.is_alive():
            process.kill()
            process.join(timeout=2.0)
    return result

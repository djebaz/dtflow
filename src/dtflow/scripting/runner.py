"""Parent-side script runner: disposable child process, host-call dispatch, deadline and Stop.

The JS engine lives only in the child. Each hook call arrives here as a message and runs on a short-lived hook thread so
the supervising loop stays free: Stop and the JS-segment deadline are enforced even while a hook blocks. A hook should
still poll ``cancel`` so it can end promptly; after Stop the child is killed once ``STOP_GRACE_SECONDS`` pass, and the
run is reported stopped without waiting for a hook that ignores it. Killing never cancels provider work that a hook
already submitted; that stays owned by its durable attempt.

Clocks: the startup wait and the segment deadline start only when the child has announced ``ready`` or received a hook
reply. The segment deadline bounds JavaScript that runs without making a host call; time spent inside hooks is excluded.
The child is also contained so it cannot outlive the app (Linux parent-death signal, Windows kill-on-close Job Object).
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
from dtflow.scripting.containment import contain_child
from dtflow.scripting.engine import MAX_CALL_BYTES, ScriptLimits
from dtflow.scripting.hooks import HOOKS, UNSUPPORTED
from dtflow.scripting.worker import main as worker_main
from dtflow.scripting.worker import syntax_main

log = logging.getLogger("dtflow.script")

MAX_CONSOLE_LINES = 2000
MAX_CONSOLE_LINE_CHARS = 4000
MAX_SLEEP_SECONDS = 3600.0
STOP_GRACE_SECONDS = 1.0
STARTUP_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.05


class ScriptCancelled(Exception):
    """Raised by a hook implementation when Stop was requested; surfaces to JS as the upstream ``cancelled`` error."""


@dataclass(slots=True)
class RunResult:
    status: str  # completed | failed | stopped
    error: str | None = None
    console: list[tuple[int, str]] = field(default_factory=list)
    host_calls: int = 0
    startup_seconds: float | None = None  # spawn until the child announced ready
    contained: bool = False  # child is bound to the app's lifetime by the OS


class HookDispatcher:
    """Runs hook implementations for the parent side; enforces Stop, size bounds and error forwarding."""

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
            reply = json.dumps({"value": function(*json.loads(payload))})
            if len(reply) > MAX_CALL_BYTES:
                raise ValueError(f"The result of {name} is too large.")
            return reply
        except ScriptCancelled:
            return json.dumps({"error": "cancelled", "cancelled": True})
        except Exception as exc:  # forwarded to JS like upstream forwardExceptionsToJS
            return json.dumps({"error": str(exc) or type(exc).__name__})


class _HookThread(threading.Thread):
    def __init__(self, dispatcher: HookDispatcher, name: str, payload: str) -> None:
        super().__init__(name=f"dtflow-hook-{name}", daemon=True)
        self._dispatcher, self._hook, self._payload = dispatcher, name, payload
        self.reply: str | None = None

    def run(self) -> None:
        self.reply = self._dispatcher.call(self._hook, self._payload)


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
    spawned = time.monotonic()
    process = context.Process(target=worker_main, args=(child,), name="dtflow-script", daemon=True)
    process.start()
    child.close()
    containment = contain_child(process)
    result.contained = containment.active or _linux_contained()
    phase = "starting"  # starting -> js <-> hook
    busy_since = spawned
    stop_deadline: float | None = None
    hook: _HookThread | None = None
    try:
        while True:
            now = time.monotonic()
            if cancel.is_set():
                stop_deadline = stop_deadline if stop_deadline is not None else now + STOP_GRACE_SECONDS
                if now > stop_deadline:
                    result.status, result.error = "stopped", None
                    break
            if phase == "starting" and now - spawned > STARTUP_TIMEOUT_SECONDS:
                result.status, result.error = "failed", "The script process did not start."
                break
            if phase == "js" and now - busy_since > limits.js_segment_seconds:
                result.status = "failed"
                result.error = "Script ran JavaScript for too long without a host call and was terminated."
                break
            if phase == "hook":
                assert hook is not None
                hook.join(POLL_SECONDS)
                if hook.is_alive():
                    continue
                parent.send({"reply": hook.reply})
                hook, phase, busy_since = None, "js", time.monotonic()
                continue
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
            if "ready" in message:
                result.startup_seconds = round(time.monotonic() - spawned, 3)
                parent.send({"source": source, "limits": asdict(limits)})
                phase, busy_since = "js", time.monotonic()
            elif "call" in message:
                hook = _HookThread(dispatcher, message["call"], message["payload"])
                hook.start()
                phase = "hook"
            elif "done" in message:
                result.status, result.error = message["done"], message.get("error")
                break
    except (BrokenPipeError, OSError):
        result.status, result.error = "failed", "Script process ended unexpectedly."
    finally:
        parent.close()
        process.join(timeout=0.5)
        if process.is_alive():
            process.kill()
            process.join(timeout=2.0)
        containment.close()
    return result


def _linux_contained() -> bool:
    import sys

    return sys.platform.startswith("linux")


def check_syntax(source: str, *, timeout_seconds: float = 5.0) -> str | None:
    """Syntax error text, or ``None`` when the script parses. Parse-only: the script is data for the vendored Acorn and is
    never evaluated. Runs in a disposable hook-less child (see ``worker.syntax_main``) that only bounds memory and time."""
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=syntax_main, args=(child,), name="dtflow-syntax", daemon=True)
    process.start()
    child.close()
    containment = contain_child(process)
    try:
        parent.send(source)
        if not parent.poll(timeout_seconds):
            return "The script did not finish parsing in time and was terminated."
        return parent.recv()["error"]
    except (EOFError, OSError):
        return "The syntax check process ended unexpectedly."
    finally:
        parent.close()
        process.join(timeout=0.5)
        if process.is_alive():
            process.kill()
            process.join(timeout=2.0)
        containment.close()

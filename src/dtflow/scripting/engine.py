"""QuickJS evaluation of the vendored upstream script layer.

``ScriptEngine`` loads ``resources/shared_script.js`` unchanged, installs a single ``__dtCall`` bridge and builds the
upstream ``__dtHooks`` object from the hook table. Arguments and results cross the bridge as JSON text so no engine
object types leak into DTFlow. The engine is deliberately dumb: it forwards every hook call to a ``call`` function and
has no deadline of its own, because QuickJS refuses host callbacks while a time limit is set. Deadlines, Stop and
blocked-callback protection are provided by running the engine in a disposable child process (``runner.py``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

import quickjs

from dtflow.scripting.hooks import HOOKS

API_HEADER = re.compile(r"^\s*//\s*@api-(\d+(?:\.\d+)*)\s*$")
MAX_SOURCE_BYTES = 1_048_576
MAX_CALL_BYTES = 8 * 1024 * 1024
SHARED_SCRIPT_RESOURCE = "shared_script.js"
ACORN_RESOURCE = "acorn.js"
ACORN_VERSION = "8.14.0"

BRIDGE = """
globalThis.console = {};  // JavaScriptCore provides `console`; the vendored script only assigns its methods.
globalThis.__dtHooks = {};
for (const name of %s) {
  globalThis.__dtHooks[name] = (...args) => {
    const reply = JSON.parse(__dtCall(name, JSON.stringify(args)));
    if (reply.error !== undefined) { throw new Error(reply.error); }
    return reply.value;
  };
}
"""
# DTFlow overlay (kept out of the vendored file): upstream __dtSleep busy-spins; this waits cancellably in the host.
# JavaScriptCore also provides console.info/debug; the vendored script only assigns log/warn/error.
OVERLAY = (
    "globalThis.__dtflowScriptHost = Object.freeze({name: 'DTFlow', apiVersion: 1});\n"
    "globalThis.__dtSleep = function (seconds) { __dtHooks.sleep(seconds); };\n"
    "console.info = console.debug = console.log;\n"
)


class ScriptEngineError(Exception):
    """Source-level problem (size) detected before evaluation."""


@dataclass(frozen=True, slots=True)
class ScriptLimits:
    memory_bytes: int = 256 * 1024 * 1024
    stack_bytes: int = 1024 * 1024
    js_segment_seconds: float = 300.0  # longest stretch of JS allowed without a host call; hook time is excluded
    source_bytes: int = MAX_SOURCE_BYTES


@dataclass(slots=True)
class ScriptOutcome:
    status: str  # completed | failed | stopped
    error: str | None = None


def wrap_user_script(source: str) -> str:
    """Upstream evaluates every script as ``(function(){ <script> })();`` so top-level ``return`` and declarations that
    shadow facade names work. The opening is placed on the script's first line so reported line numbers are unchanged."""
    return "(function(){" + source + "\n})();"


def shared_script() -> str:
    return resources.files("dtflow.scripting").joinpath("resources", SHARED_SCRIPT_RESOURCE).read_text("utf-8")


def acorn_source() -> str:
    """Vendored Acorn 8.14.0 (see resources/acorn.NOTICE.txt); used only by the parse-only analyzer."""
    return resources.files("dtflow.scripting").joinpath("resources", ACORN_RESOURCE).read_text("utf-8")


def engine_versions() -> dict[str, str]:
    """Parser and engine versions for reports, so a parse/run syntax mismatch is traceable."""
    from importlib import metadata

    try:
        engine = metadata.version("quickjs-ng")
    except metadata.PackageNotFoundError:
        engine = "unknown"
    return {"acorn": ACORN_VERSION, "quickjs-ng": engine}


def shared_script_sha256() -> str:
    import hashlib

    return hashlib.sha256(shared_script().encode("utf-8")).hexdigest()


def api_version(source: str) -> str | None:
    """Parse ``//@api-X`` from the leading comment block; absent headers return ``None`` (callers warn)."""
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("//"):
            return None
        match = API_HEADER.match(stripped)
        if match:
            return match.group(1)
    return None


class ScriptEngine:
    """One script evaluation. ``call(name, json_arguments) -> json_reply`` receives every host call."""

    def __init__(self, call: Callable[[str, str], str], limits: ScriptLimits | None = None) -> None:
        self.limits = limits or ScriptLimits()
        self._host_call = call
        self.cancel_acknowledged = False

    def _call(self, name: str, payload: str) -> str:
        reply = self._host_call(name, payload)
        if '"cancelled": true' in reply:
            self.cancel_acknowledged = True  # only a host-sent cancellation counts as Stop, never a script's own text
        return reply

    def run(self, source: str) -> ScriptOutcome:
        if len(source.encode("utf-8")) > self.limits.source_bytes:
            raise ScriptEngineError("Script source is too large.")
        context = quickjs.Context()
        context.set_memory_limit(self.limits.memory_bytes)
        context.set_max_stack_size(self.limits.stack_bytes)
        context.add_callable("__dtCall", self._call)
        try:
            context.eval(BRIDGE % json.dumps(sorted(HOOKS)))
            context.eval(shared_script())
            context.eval(OVERLAY)
            context.eval(wrap_user_script(source))
            while context.execute_pending_job():  # JavaScriptCore drains promise jobs after evaluation
                pass
        except quickjs.JSException as exc:
            lines = str(exc).splitlines()
            message = lines[0] if lines else "JavaScript error"
            if message.strip() in ("", "null", "undefined"):
                # QuickJS cannot build an Error object once its heap limit is hit, so the exception arrives empty.
                return ScriptOutcome("failed", "Script ran out of memory (or threw an empty value).")
            if self.cancel_acknowledged:
                return ScriptOutcome("stopped")  # after a host-sent cancellation, whatever the script then threw
            return ScriptOutcome("failed", "\n".join(lines[:4]) if lines else message)
        except quickjs.StackOverflow:
            return ScriptOutcome("failed", "Script exceeded the stack limit.")
        if self.cancel_acknowledged:
            return ScriptOutcome("stopped")  # a script that caught the cancelled error is still stopped
        return ScriptOutcome("completed")

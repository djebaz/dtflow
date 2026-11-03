"""Child-process entry: evaluates one script and forwards every host call to the parent over a pipe.

Importable without side effects; ``main`` is the ``multiprocessing`` spawn target (also correct in frozen builds when
the entry point calls ``multiprocessing.freeze_support()``). It announces ``ready`` before reading the script so the
parent can measure spawn latency and start its clocks only once the child can actually run JavaScript.
"""

from __future__ import annotations

import json

import os

from dtflow.scripting.containment import child_in_job, die_with_parent
from dtflow.scripting.engine import MAX_CALL_BYTES, ScriptEngine, ScriptEngineError, ScriptLimits


def main(connection) -> None:
    die_with_parent()
    try:
        connection.send({"ready": True})
        request = connection.recv()
        limits = ScriptLimits(**request["limits"])
        # The parent contains this process before it sends the script request, so this is evidence about containment order.
        connection.send({"started": {"pid": os.getpid(), "in_job": child_in_job()}})

        def call(name: str, payload: str) -> str:
            if len(payload) > MAX_CALL_BYTES:  # refuse before the parent has to receive and unpickle it
                return json.dumps({"error": f"Arguments to {name} are too large."})
            connection.send({"call": name, "payload": payload})
            return connection.recv()["reply"]

        try:
            outcome = ScriptEngine(call, limits).run(request["source"])
            connection.send({"done": outcome.status, "error": outcome.error})
        except ScriptEngineError as exc:
            connection.send({"done": "failed", "error": str(exc)})
    except (EOFError, BrokenPipeError, OSError):
        return  # the parent went away or killed this process
    finally:
        connection.close()


def syntax_main(connection) -> None:
    """Child entry for the analyzer: parse (never evaluate) a script with the vendored Acorn inside a memory-capped
    QuickJS context that has no host bridge and no hooks.

    The script reaches the parser only as a string value, so no part of it is compiled or run by the engine. The options
    mirror upstream's wrapper: scripts are function bodies, so top-level ``return`` is accepted and a leading ``#!``
    line is not (the runtime wrapper rejects it too). Parse-only resource policy
    lives in ``scripting.syntax``; the parent still bounds total process time."""
    die_with_parent()
    try:
        from dtflow.scripting.syntax import parse_in_reserved_thread

        connection.send({"error": parse_in_reserved_thread(connection.recv())})
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        connection.close()

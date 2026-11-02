"""Child-process entry: evaluates one script and forwards every host call to the parent over a pipe.

Importable without side effects; ``main`` is the ``multiprocessing`` spawn target (also correct in frozen builds when
the entry point calls ``multiprocessing.freeze_support()``). It announces ``ready`` before reading the script so the
parent can measure spawn latency and start its clocks only once the child can actually run JavaScript.
"""

from __future__ import annotations

import json

from dtflow.scripting.containment import die_with_parent
from dtflow.scripting.engine import MAX_CALL_BYTES, ScriptEngine, ScriptEngineError, ScriptLimits


def main(connection) -> None:
    die_with_parent()
    try:
        connection.send({"ready": True})
        request = connection.recv()
        limits = ScriptLimits(**request["limits"])

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
    mirror upstream's wrapper: scripts are function bodies, so top-level ``return`` is accepted. The child exists only to
    bound memory and time on pathological input."""
    die_with_parent()
    try:
        import quickjs

        from dtflow.scripting.engine import acorn_source

        source = connection.recv()
        context = quickjs.Context()
        context.set_memory_limit(64 * 1024 * 1024)
        context.set_max_stack_size(512 * 1024)
        try:
            context.eval(acorn_source())
            context.set("__source", source)
            context.eval(
                'acorn.parse(__source, {ecmaVersion: "latest", sourceType: "script", '
                "allowReturnOutsideFunction: true, allowHashBang: true}); true"
            )
            connection.send({"error": None})
        except quickjs.JSException as exc:
            lines = str(exc).splitlines()
            connection.send({"error": lines[0] if lines else "Syntax error"})
        except quickjs.StackOverflow:
            connection.send({"error": "Script is nested too deeply to analyze."})
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        connection.close()

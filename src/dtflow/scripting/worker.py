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

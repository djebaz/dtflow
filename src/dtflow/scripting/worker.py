"""Child-process entry: evaluates one script and forwards every host call to the parent over a pipe.

Importable without side effects; ``main`` is the ``multiprocessing`` spawn target (also correct in frozen builds when
the entry point calls ``multiprocessing.freeze_support()``).
"""

from __future__ import annotations

from dtflow.scripting.engine import ScriptEngine, ScriptEngineError, ScriptLimits


def main(connection) -> None:
    try:
        request = connection.recv()
        limits = ScriptLimits(**request["limits"])

        def call(name: str, payload: str) -> str:
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

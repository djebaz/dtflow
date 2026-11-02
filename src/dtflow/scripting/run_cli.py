"""``dtflow script FILE --run``: run a script through the normal generation path with an explicit backend.

Running a script can generate (and, on local/cloud backends, spend credits), so it needs ``--run`` and an explicit
``--backend``; ``fake`` is the offline fixture. The starting settings come from the same options as ``dtflow generate``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from pathlib import Path

from dtflow.core.models import TimeoutPolicy, ValidationError
from dtflow.persistence.script_runs import ScriptRunStore
from dtflow.scripting.engine import MAX_SOURCE_BYTES
from dtflow.scripting.host import ScriptHost
from dtflow.scripting.runner import RunContext, check_syntax, run_recorded
from dtflow.services.session import open_session, selection_for


def read_answers(path: Path | None) -> list[list] | None:
    if path is None:
        return None
    data = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, list) for item in data):
        raise ValidationError("--answers must be a JSON list of answer lists, one per requestFromUser call.")
    return data


async def run_script_file(args: argparse.Namespace, *, exit_ok: int, exit_failed: int, exit_invalid: int) -> int:
    from dtflow.cli import bind_assets, resolve_generation  # lazy: cli imports this package

    if args.file is None or args.backend is None:
        print("Invalid request: --run needs a script file and an explicit --backend.", file=sys.stderr)
        return exit_invalid
    data = Path(args.file).read_bytes()
    if len(data) > MAX_SOURCE_BYTES:
        print("Invalid request: script source is too large.", file=sys.stderr)
        return exit_invalid
    if not 1 <= args.max_generations <= 32:
        print("Invalid request: --max-generations must be between 1 and 32.", file=sys.stderr)
        return exit_invalid
    source = data.decode("utf-8")
    problem = check_syntax(source)
    if problem:
        print(f"Invalid request: {problem}", file=sys.stderr)
        return exit_invalid
    answers = read_answers(args.answers)
    base = await bind_assets(args, resolve_generation(args))
    store = ScriptRunStore(args.state)
    cancel = threading.Event()
    loop = asyncio.get_running_loop()
    selection = selection_for(args.backend, args.endpoint, args.state)
    hosts: list[ScriptHost] = []

    def hooks(context: RunContext):
        host = ScriptHost(
            service=service,
            base=base,
            loop=loop,
            link_child=context.link_child,
            console=context.console,
            cancel=context.cancel,
            sandbox=Path(args.state) / "script_files" / context.run_id,
            max_generations=args.max_generations,
            answers=answers,
        )
        hosts.append(host)
        return host.hooks()

    async with open_session(
        selection, args.state, output=args.outputs, timeouts=TimeoutPolicy(total=args.timeout)
    ) as service:
        task = asyncio.ensure_future(
            asyncio.to_thread(run_recorded, store, source, hooks, source_name=Path(args.file).name, cancel=cancel)
        )
        interrupted = False
        try:
            run_id, result = await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
            cancel.set()  # stop waiting; in-flight attempts are left to finish and stay durable
            run_id, result = await task
        finally:
            pending = [future for host in hosts for future in host.pending]
            drain = asyncio.gather(*(asyncio.wrap_future(future) for future in pending), return_exceptions=True)
            while not drain.done():  # in-flight attempts finish (or hit their deadline) before the service closes
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    continue
    host = hosts[0] if hosts else None
    report = {
        "run_id": run_id,
        "status": result.status,
        "error": result.error,
        "attempts": list(host.attempts) if host else [],
        "outputs": list(host.last.output_paths) if host and host.last else [],
        "console": [{"kind": ("log", "warn", "error")[kind], "text": text} for kind, text in result.console],
    }
    print(json.dumps(report, indent=2))
    if interrupted:
        raise asyncio.CancelledError
    return {"completed": exit_ok, "stopped": 130}.get(result.status, exit_failed)

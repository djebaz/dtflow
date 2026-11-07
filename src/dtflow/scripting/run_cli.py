"""``dtflow script FILE --run``: run a script through the normal generation path with an explicit backend.

Running a script can generate (and, on local/cloud backends, spend credits), so it needs ``--run`` and an explicit
``--backend``; ``fake`` is the offline fixture. The starting settings come from the same options as ``dtflow generate``.

Every run is a script queue item (#243), like a run started from Scripts: ``--queue`` saves it paused for a later
``dtflow queue start``; without it this command starts it as the dispatch owner, waits for it to end and prints the
report read from the item and its child attempts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dtflow.core.models import TimeoutPolicy, ValidationError
from dtflow.persistence.store import StateBusyError
from dtflow.scripting.engine import MAX_SOURCE_BYTES
from dtflow.scripting.runner import check_syntax


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
    try:
        data = Path(args.file).read_bytes()
    except OSError as exc:
        print(f"Invalid script file: {type(exc).__name__}: {exc}", file=sys.stderr)
        return exit_invalid
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
    from dtflow.services.queue_session import create_script_queue, dispatch_queue  # lazy: services import this package

    backend = {"name": args.backend, "endpoint": args.endpoint, "scenario": "success"}
    queue_id, run_id = await create_script_queue(
        Path(args.state),
        backend,
        source,
        Path(args.file).name,
        base,
        max_generations=args.max_generations,
        answers=answers,
    )
    if getattr(args, "queue", False):
        print(json.dumps({"queue_id": queue_id, "run_id": run_id, "state": "paused", "items": 1, "cap": 1}))
        return exit_ok
    # Like every other path (#243): the run is a script queue item, started here and reported when it ends.
    interrupted = False
    try:
        await dispatch_queue(
            Path(args.state),
            queue_id,
            timeouts=TimeoutPolicy(total=args.timeout),
            out=args.outputs,
            auto_play=False,
        )
    except StateBusyError:
        print(
            f"Invalid request: another DTFlow process owns this state; the script was saved paused as queue {queue_id}.",
            file=sys.stderr,
        )
        return exit_invalid
    except asyncio.CancelledError:
        interrupted = True  # Stop: waiting ended; in-flight attempts finished or stay durable, never replayed
    report = await asyncio.to_thread(script_report, Path(args.state), queue_id, run_id)
    print(json.dumps(report, indent=2))
    if interrupted:
        raise asyncio.CancelledError
    return {"completed": exit_ok, "stopped": 130}.get(report["status"], exit_failed)


def script_report(state: Path, queue_id: str, run_id: str) -> dict:
    """The JSON report of a script run, read from its queue item and its child attempts."""
    from dtflow.persistence.queues import read_queues, read_script_run

    found = read_script_run(state, run_id)
    run = (found[2] if found is not None else None) or {}
    queue = next(iter(read_queues(state, queue_id)), {})
    item = next(iter(queue.get("items") or ()), {})
    children = (item.get("script") or {}).get("children") or []

    def images(child):
        return [str(path) for path in child.get("artifacts") or () if not str(path).endswith("metadata.json")]

    committed = [child for child in children if child.get("state") == "succeeded"]
    return {
        "queue_id": queue_id,
        "run_id": run_id,
        "status": run.get("state") or "failed",
        "item_state": item.get("state"),
        "error": run.get("error"),
        "attempts": [child["attempt_id"] for child in children],
        "outputs": images(committed[-1]) if committed else [],
        "attempt_outputs": [{"attempt_id": child["attempt_id"], "outputs": images(child)} for child in committed],
        "console": list(run.get("console") or ()),
    }

"""Parent process for the offline renderer: runs independent window groups as separate worker processes.

Qt widgets need the main thread of one QApplication, so parallelism means several renderer processes, each with its
own windows, temporary state and fake cloud. The parent imports no Qt; it starts the workers, labels their output,
and merges their manifests into one report in product-journey order.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

LEVEL_FLAGS = {"f": "-f", "m": "-m", "s": "-s"}
# A worker's progress line: "[connection/cache] 19/60  connection-19-cache-measuring  0.31s".
PROGRESS = re.compile(r"^\[(?P<group>[^\]]+)\] \d+/\d+  (?P<id>\S+)  (?P<seconds>[\d.]+)s$")


def clock(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _worker_command(args, root, worker):
    command = [
        sys.executable,
        "-u",
        str(root / "tools/ui/preview_states.py"),
        "--out",
        str(args.out.resolve()),
        "--category",
        args.category,
        LEVEL_FLAGS[args.level],
        "--jobs",
        "1",
        "--worker",
        worker,
    ]
    for flag, values in (("--group", args.group), ("--state", args.state), ("--prefix", args.prefix)):
        for value in values:
            command += [flag, value]
    if args.verbose:
        command.append("--verbose")
    return command


def run_workers(args, root):
    """Run every planned worker, stream labelled output, then write the merged manifest and summary."""
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    lock = threading.Lock()
    log = (out / f"preview-details-{args.report_key}.log").open("w", encoding="utf-8", newline="\n")
    width = max(len(name) for name in args.workers)
    level = {"f": "fast", "m": "medium", "s": "slow (all)"}[args.level]

    def emit(text, *, show=True):
        with lock:
            log.write(text + "\n")
            log.flush()
            if show or args.verbose:
                print(text, flush=True)

    total = len(args.selection.states)
    by_id = args.selection.by_id
    category_total = {}
    for state in args.selection.states:
        category_total[state.category] = category_total.get(state.category, 0) + 1
    category_done = dict.fromkeys(category_total, 0)
    done = 0
    first = 0.0
    id_width = max((len(state.id) for state in args.selection.states), default=0)

    def progress(worker, match):
        """One console line for a saved state: overall done/total, elapsed, ETA and the category's own count."""
        nonlocal done, first
        state = by_id.get(match["id"])
        with lock:
            done += 1
            elapsed = time.monotonic() - started
            first = first or time.monotonic()
            # Worker startup (imports, Qt) is not render speed: the rate starts at the first saved state.
            rate = (time.monotonic() - first) / (done - 1) if done > 1 else None
            eta = rate * (total - done) if rate is not None else None
            if state is not None:
                category_done[state.category] += 1
                where = f"{match['group']:<22} {category_done[state.category]:>3}/{category_total[state.category]:<3}"
            else:
                where = match["group"]
            text = (
                f"[{done:>{len(str(total))}}/{total} {done * 100 // total:>3}%  {clock(elapsed)}  ETA {clock(eta) if eta is not None else '--:--'}] "
                f"{where} {match['id']:<{id_width}} {match['seconds']:>5}s  {tags[worker]}"
            )
            log.write(text + "\n")
            log.flush()
            print(text, flush=True)

    scope = "Explicit selection" if args.selection.explicit else f"Level {level}"
    emit(f"{scope}: selected {len(args.selection.states)} states in {len(args.selection.fixtures)} fixture groups.")
    emit(f"Workers ({min(args.jobs, len(args.workers))} at a time): {', '.join(args.workers)}")
    tags = {worker: f"p{index}" for index, worker in enumerate(args.workers, 1)}
    for worker, tag in tags.items():
        emit(f"  {tag} = {worker}")

    wait_events = []
    running = []

    def run_one(worker):
        begun = time.monotonic()
        process = subprocess.Popen(
            _worker_command(args, root, worker),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
        )
        running.append(process)
        failing = False
        waits = []
        progressed = [time.monotonic()]
        stalled = []
        done_reading = threading.Event()

        def watchdog():
            # Stack dumps and Qt notices are output too, so only a saved state counts as progress.
            while not done_reading.wait(min(5, max(args.stall_timeout / 4, 0.5))):
                if time.monotonic() - progressed[0] > args.stall_timeout:
                    stalled.append(True)
                    emit(f"WARNING: worker {worker} saved no state for {args.stall_timeout}s; stopping it.")
                    process.kill()
                    return

        if args.stall_timeout > 0:
            threading.Thread(target=watchdog, daemon=True).start()
        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue
            match = PROGRESS.match(line)
            if match:
                progressed[0] = time.monotonic()
                progress(worker, match)
                if waits:
                    # A fixture wait timed out while preparing this state; it was rendered as it stood.
                    for event in waits:
                        event["capture"] = match["id"]
                    noun = "wait" if len(waits) == 1 else "waits"
                    emit(f"  ^ {match['id']}: {len(waits)} fixture {noun} timed out; capture may be incomplete")
                    waits = []
                continue
            if line.startswith("WARNING: offline UI fixture wait timed out"):
                event = {"worker": worker, "capture": None, "message": line}
                waits.append(event)
                wait_events.append(event)
            failing = failing or line.startswith("Traceback")
            # Window headers, summaries and expected Qt notices stay in the log; problems reach the console.
            loud = failing or line.startswith("WARNING")
            emit(f"{worker:<{width}} | {line}", show=loud)
        done_reading.set()
        code = process.wait()
        if waits:
            emit(f"WARNING: worker {worker}: {len(waits)} timed-out waits with no subsequent saved capture.")
        if stalled:
            code = code or -9
        seconds = time.monotonic() - begun
        emit(f"Worker {worker} finished in {clock(seconds)} (exit {code}{', stalled' if stalled else ''}).")
        return worker, code, seconds

    pool = ThreadPoolExecutor(max_workers=min(args.jobs, len(args.workers)))
    try:
        results = list(pool.map(run_one, args.workers))
    except BaseException:
        # Ctrl+C or an internal error must not leave orphaned Qt renderer processes behind.
        for process in running:
            if process.poll() is None:
                process.kill()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    manifests = {}
    for worker, _code, _seconds in results:
        path = out / f"preview-manifest-{args.report_key}-{worker}.json"
        try:
            manifests[worker] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifests[worker] = None
    # Completed worker manifests retain structured timeout durations and exact saved attribution.
    # A crashed/stalled worker may have no final manifest; retain its observed stdout events instead.
    observed_events = wait_events
    wait_events = []
    for worker, data in manifests.items():
        structured = data.get("wait_timeout_events") if data is not None and data.get("status") != "running" else None
        if structured is not None:
            wait_events.extend({**event, "worker": worker} for event in structured)
        else:
            wait_events.extend(event for event in observed_events if event["worker"] == worker)
    timed_out = sorted({event["capture"] for event in wait_events if event["capture"] is not None})
    unattributed = sum(event["capture"] is None for event in wait_events)
    records = {}
    duplicates = []
    notices, unregistered = {}, []
    for worker, data in manifests.items():
        if data is None:
            continue
        for row in data["states"]:
            if row["status"] == "saved":
                if row["id"] in records:
                    duplicates.append(row["id"])
                records[row["id"]] = {
                    **row,
                    "worker": worker,
                    "wait_timed_out": row["id"] in timed_out,
                    "wait_timeout_count": sum(event["capture"] == row["id"] for event in wait_events),
                }
        for name, count in data.get("expected_notices", {}).items():
            notices[name] = notices.get(name, 0) + count
        unregistered += data.get("unregistered_states", [])

    wall = time.monotonic() - started
    states = []
    for state in args.selection.states:
        row = records.get(state.id)
        if row is None:
            states.append({**vars(state), "status": "not_saved"})
        else:
            states.append({**vars(state), **{k: row[k] for k in row if k not in vars(state)}})
    missing = [s["id"] for s in states if s["status"] != "saved"]
    blank = [s["id"] for s in states if s.get("blank")]
    failed = [f"{worker} (exit {code})" for worker, code, _ in results if code != 0]
    status = "failed" if failed or missing or blank or duplicates else "completed"
    data = {
        "schema_version": 1,
        "status": status,
        "elapsed_seconds": round(wall, 3),
        "timing_policy": "Each state's seconds and duration_seconds are measured inside its worker process; "
        "capture intervals include prerequisite work since that worker's previous selected capture or start.",
        "workers": {
            worker: {"exit_code": code, "elapsed_seconds": round(seconds, 3)} for worker, code, seconds in results
        },
        "wait_timeouts": sorted(timed_out),
        "wait_timeout_capture_count": len(timed_out),
        "wait_timeout_count": len(wait_events),
        "wait_timeout_events": wait_events,
        "unattributed_wait_timeout_count": unattributed,
        "wait_timeout_attribution": "Next saved capture in the same worker, not necessarily the originating fixture; "
        "null capture means no subsequent saved capture. Completed worker manifests are authoritative; "
        "stdout events are the fallback for missing, incomplete or legacy manifests.",
        "states": states,
        "unregistered_states": unregistered,
        "expected_notices": notices,
        "error": "; ".join(f"worker {item} failed" for item in failed) or None,
    }
    target = out / f"preview-manifest-{args.report_key}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(target)

    emit("")
    for category in args.selection.categories:
        group = [s for s in states if s["category"] == category]
        emit(f"{category}: {sum(s['status'] == 'saved' for s in group)}/{len(group)} saved")
    timings = ", ".join(f"{worker} {seconds:.1f}s" for worker, _code, seconds in results)
    emit(f"Rendered {len(states) - len(missing)} states in {wall:.1f}s wall time (workers: {timings}).")
    if notices:
        emit(f"Expected offline notices: {sum(notices.values())}; details in the per-worker logs next to {target.name}")
    if wait_events:
        captures = "capture" if len(timed_out) == 1 else "captures"
        waits = "wait" if len(wait_events) == 1 else "waits"
        emit(
            f"WARNING: {len(timed_out)} {captures} affected by {len(wait_events)} timed-out {waits} "
            f"({unattributed} waits without a subsequent capture); captures may be incomplete: {', '.join(timed_out)}"
        )
    if unregistered:
        emit(f"WARNING: rendered states missing from preview_catalog.json: {', '.join(unregistered)}")
    log.close()
    problems = []
    if failed:
        problems.append(f"Failed workers: {', '.join(failed)}")
    if duplicates:
        problems.append(f"States rendered by more than one worker: {', '.join(duplicates)}")
    if blank:
        problems.append(f"Blank renders: {', '.join(blank)}")
    if missing:
        problems.append(f"Selected states not saved ({len(missing)}): {', '.join(missing)}; see {target}")
    if problems:
        raise SystemExit("\n".join(problems))
    return 0

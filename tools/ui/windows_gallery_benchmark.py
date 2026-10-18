"""Drive the actual DTFlow Images UI on a native Windows desktop and compare diagnostics.

Requires explicit acknowledgement of the existing saved-output catalogue and a
pinned, optional pywinauto overlay. No generation, export or deletion is driven.
Run --help on any platform; real automation is Windows-only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from tools.ui.gallery_perf_launch import validate_profile

# Each trial differs from production defaults in just one family.
DEFAULT_PROFILES = {
    "baseline": {},
    "workers-3": {"thumbnail_workers": 3},
    "lookahead-1": {"lookahead_screens": 1},
    "preparation-2ms": {"preparation_budget_ms": 2},
    "mount-2ms": {"mount_budget_ms": 2},
    "thumbnails-480px": {"thumbnail_side_px": 480},
    "pending-writes-64": {"pending_cache_writes": 64},
    "cache-prune-earlier": {"cache_entries": 10000, "cache_prune_to": 8000, "cache_prune_every": 250},
    "refresh-10s": {"refresh_ms": 10000, "full_refresh_s": 60},
    "debounce-faster": {"filter_debounce_ms": 150, "save_debounce_ms": 125, "page_jump_debounce_ms": 20},
}
QUERY_DONE = re.compile(r"gallery_perf phase=query_done\b[^\n]*\breturned=(\d+)\b[^\n]*\berror=none\b")


def load_profiles(path: Path | None, names: list[str] | None) -> dict[str, dict]:
    """Load one-factor experiments or explicit benchmark profiles."""

    raw = DEFAULT_PROFILES if path is None else json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Profiles must be a non-empty mapping of names to overrides.")
    selected = list(raw) if names is None else names
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("Select at least one distinct profile.")
    profiles = {}
    for name in selected:
        if name not in raw:
            raise ValueError(f"Unknown profile: {name}")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,48}", name):
            raise ValueError(f"Unsafe profile name: {name}")
        profiles[name] = validate_profile(raw[name])
    return profiles


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def summarize_csv(path: Path) -> dict:
    """Read the application's existing sanitized listener-side gallery CSV.

    These are aggregated diagnostic samples. Their P95 cannot be reconstructed
    as a global percentile, so only directly observed maxima and medians of
    recorded per-event means are reported.
    """

    metrics: dict[tuple[str, str], list[float]] = {}
    events: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"phase", "metric", "samples", "mean", "max"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("DTFlow gallery CSV schema is missing required fields.")
        for row in reader:
            phase = row["phase"]
            metric = row["metric"]
            count = int(row["samples"] or 1)
            events[phase] = events.get(phase, 0) + count
            number = _number(row["max"])
            if number is not None and metric:
                metrics.setdefault((phase, metric), []).append(number)

    def maximum(*keys: tuple[str, str]) -> float | None:
        found = [number for key in keys for number in metrics.get(key, [])]
        return max(found) if found else None

    def median(phase: str, metric: str) -> float | None:
        values = metrics.get((phase, metric), [])
        return round(statistics.median(values), 2) if values else None

    if not any(name in events for name in ("query_done", "adjacent_read")):
        raise ValueError("No successful gallery query diagnostics were recorded.")
    return {
        "query_events": events.get("query_done", 0),
        "adjacent_events": events.get("adjacent_read", 0),
        "gui_gap_over_50_max_ms": maximum(("gui_gaps", "wall_max_ms"), ("gui_gap", "elapsed_ms")),
        "query_done_median_recorded_ms": median("query_done", "elapsed_ms"),
        "prepare_median_recorded_ms": median("prepare_complete", "elapsed_ms"),
        "grid_mount_median_recorded_ms": median("grid_mount_complete", "elapsed_ms"),
        "thumbnail_median_recorded_ms": median("thumbnail_end_to_end_summary", "elapsed_ms"),
        "phase_counts": events,
    }


def _read_query_count(directory: Path) -> int:
    """Read live query completion records from the rotating text log."""

    maximum = 0
    for path in directory.glob("dtflow-*.log*"):
        if "-gallery" in path.name:
            continue
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        maximum = max(maximum, *(int(m.group(1)) for m in QUERY_DONE.finditer(data)))
    return maximum


def _await_query(directory: Path, proc: subprocess.Popen, seconds: float) -> int:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError("DTFlow exited before the Images query completed.")
        returned = _read_query_count(directory)
        if returned:
            return returned
        time.sleep(0.2)
    raise TimeoutError("No successful Images query appeared in the DTFlow development log.")


def _button(window, name: str):
    """Resolve one named Qt accessible button; never guess or click coordinates."""

    button = window.child_window(title=name, control_type="Button")
    button.wait("exists visible enabled", timeout=12)
    return button


def _click(window, name: str, actions: list[dict]) -> None:
    started = time.perf_counter()
    _button(window, name).click_input()
    actions.append({"action": name, "dispatch_ms": round((time.perf_counter() - started) * 1000, 2)})


def _scroll(window, direction: int, actions: list[dict]) -> None:
    """Wheel-scroll inside the gallery content, never on its action toolbar."""

    from pywinauto import mouse

    rect = window.rectangle()
    coords = (int(rect.left + rect.width() * 0.52), int(rect.top + rect.height() * 0.66))
    started = time.perf_counter()
    mouse.scroll(coords=coords, wheel_dist=direction * 5)
    actions.append({"action": "scroll-up" if direction > 0 else "scroll-down",
                    "dispatch_ms": round((time.perf_counter() - started) * 1000, 2)})


def _exercise_prompt_filter(window, actions: list[dict]) -> None:
    """Change the real search field and restore an empty query after debounce."""

    field = window.child_window(title="Search positive prompts", control_type="Edit")
    field.wait("exists visible enabled", timeout=12)
    for query in ("dtflow_benchmark_no_matches_expected", ""):
        started = time.perf_counter()
        field.click_input()
        field.type_keys("^a{BACKSPACE}", set_foreground=True)
        if query:
            field.type_keys(query, set_foreground=True)
        actions.append({
            "action": "filter-no-match" if query else "filter-reset",
            "dispatch_ms": round((time.perf_counter() - started) * 1000, 2),
        })
        time.sleep(1.5)


def _drive(proc: subprocess.Popen, directory: Path, actions: list[dict], *, cycles: int, timeout: float) -> int:
    """Attach only to this newly launched process; never use an existing window."""

    from pywinauto import Desktop

    window = Desktop(backend="uia").window(title="DTFlow", process=proc.pid)
    window.wait("exists visible enabled", timeout=timeout)
    window.set_focus()
    _click(window, "Show saved Images", actions)
    initial_count = _await_query(directory, proc, timeout)
    _button(window, "Jump to image batch 2")
    for _ in range(cycles):
        _click(window, "Grid", actions)
        _scroll(window, -1, actions)
        time.sleep(0.8)
        _scroll(window, 1, actions)
        time.sleep(0.8)
        _click(window, "Jump to image batch 2", actions)
        time.sleep(0.8)
        _click(window, "Jump to image batch 1", actions)
        time.sleep(0.8)
        _click(window, "List", actions)
        _scroll(window, -1, actions)
        time.sleep(0.8)
        _scroll(window, 1, actions)
        time.sleep(0.8)
        _click(window, "Detail", actions)
        time.sleep(0.8)
        _click(window, "Grid", actions)
        time.sleep(0.8)
        _exercise_prompt_filter(window, actions)
    window.close()
    return initial_count


def run_trial(name: str, profile: dict, *, trial: int, directory: Path, cycles: int,
              timeout: float, no_cache: bool) -> dict:
    """Launch one source GUI process and retain evidence even if automation fails."""

    directory.mkdir(parents=True, exist_ok=False)
    logs = directory / "logs"
    logs.mkdir()
    profile_path = directory / "profile.json"
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    args = [
        sys.executable, "-m", "tools.ui.gallery_perf_launch",
        "--profile", str(profile_path), "--logs", str(logs),
    ]
    if no_cache:
        args.append("--no-cache")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    record: dict = {
        "profile_name": name, "profile": profile, "trial": trial, "no_cache": no_cache,
        "status": "failed", "actions": [], "error_type": None,
        "initial_query_records": None,
    }
    process = None
    stderr = directory / "app-stderr.txt"
    with stderr.open("w", encoding="utf-8") as output:
        try:
            process = subprocess.Popen(args, cwd=Path(__file__).resolve().parents[2],
                                       env=env, stdout=subprocess.DEVNULL, stderr=output)
            record["initial_query_records"] = _drive(
                process, logs, record["actions"], cycles=cycles, timeout=timeout
            )
            process.wait(timeout=timeout)
            csvs = sorted(logs.glob("dtflow-*-gallery.csv"))
            if len(csvs) != 1:
                raise ValueError("Expected exactly one session gallery CSV.")
            record["metrics"] = summarize_csv(csvs[0])
            record["status"] = "ok"
        except (Exception, KeyboardInterrupt) as exc:
            record["error_type"] = type(exc).__name__
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            (directory / "trial.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def write_report(records: list[dict], out: Path) -> None:
    """Emit inspectable raw trials and a comparison that never ranks missing data."""

    columns = (
        "case", "trial", "status", "initial_query_records", "gui_gap_over_50_max_ms",
        "query_done_median_recorded_ms", "prepare_median_recorded_ms",
        "grid_mount_median_recorded_ms", "adjacent_events", "error_type",
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            metrics = record.get("metrics", {})
            writer.writerow({
                "case": record["profile_name"],
                "trial": record["trial"],
                "status": record["status"],
                "initial_query_records": record.get("initial_query_records"),
                "gui_gap_over_50_max_ms": metrics.get("gui_gap_over_50_max_ms"),
                "query_done_median_recorded_ms": metrics.get("query_done_median_recorded_ms"),
                "prepare_median_recorded_ms": metrics.get("prepare_median_recorded_ms"),
                "grid_mount_median_recorded_ms": metrics.get("grid_mount_median_recorded_ms"),
                "adjacent_events": metrics.get("adjacent_events"),
                "error_type": record.get("error_type"),
            })


    # Cross-trial comparison: keep missing >50 ms heartbeat gaps as unknown
    # observations rather than inventing a zero-millisecond latency.
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(record["profile_name"], []).append(record)
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "profile", "trials", "successful", "observed_gap_max_ms",
            "median_recorded_query_ms", "notes",
        ))
        writer.writeheader()
        for name, trials in grouped.items():
            good = [trial for trial in trials if trial["status"] == "ok"]
            gaps = [
                trial["metrics"]["gui_gap_over_50_max_ms"]
                for trial in good
                if trial["metrics"].get("gui_gap_over_50_max_ms") is not None
            ]
            queries = [
                trial["metrics"]["query_done_median_recorded_ms"]
                for trial in good
                if trial["metrics"].get("query_done_median_recorded_ms") is not None
            ]
            writer.writerow({
                "profile": name,
                "trials": len(trials),
                "successful": len(good),
                "observed_gap_max_ms": max(gaps) if gaps else "",
                "median_recorded_query_ms": round(statistics.median(queries), 2) if queries else "",
                "notes": (
                    "incomplete trials" if len(good) != len(trials)
                    else "no >=50ms gap samples" if not gaps else "observed threshold-crossing gaps"
                ),
            })


def main(argv: list[str] | None = None) -> int:
    """Drive named performance profiles on an interactive Windows desktop."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Fresh output directory under ignored tmp/.")
    parser.add_argument("--profiles-file", type=Path,
                        help="JSON mapping profile names to overrides; otherwise use built-in one-factor profiles.")
    parser.add_argument("--profiles", nargs="+", metavar="NAME",
                        help="Only selected profiles from the file or built-in set (default: all).")
    parser.add_argument("--runs", type=int, default=2, help="Trials per profile (1–10).")
    parser.add_argument("--cycles", type=int, default=2, help="Repeated UI action sequences (1–10).")
    parser.add_argument("--timeout", type=float, default=30, help="Seconds for GUI/process readiness.")
    parser.add_argument("--no-cache", action="store_true", help="Bypass disposable thumbnail cache.")
    parser.add_argument("--ack-user-state", action="store_true",
                        help="Allow reading saved outputs and updating preferences/disposable cache.")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Native Windows is required; Linux/offscreen cannot establish UI acceptance.")
    if not args.ack_user_state:
        parser.error("--ack-user-state is required before accessing the saved-output catalogue.")
    if not 1 <= args.runs <= 10 or not 1 <= args.cycles <= 10:
        parser.error("--runs and --cycles must be between 1 and 10.")
    if not 5 <= args.timeout <= 120:
        parser.error("--timeout must be between 5 and 120 seconds.")
    try:
        profiles = load_profiles(args.profiles_file, args.profiles)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if len(profiles) * args.runs > 40:
        parser.error("Limit the matrix to 40 runs per invocation.")
    if args.out.exists():
        parser.error("--out must be new; never mix benchmarking sessions.")
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        parser.error("pywinauto missing; use uv run --frozen --with pywinauto==0.6.9 python -m ...")
    records = []
    args.out.mkdir(parents=True)
    try:
        for name, profile in profiles.items():
            for trial in range(1, args.runs + 1):
                directory = args.out / f"{name}-r{trial:02}"
                record = run_trial(name, profile, trial=trial, directory=directory,
                                   cycles=args.cycles, timeout=args.timeout, no_cache=args.no_cache)
                records.append(record)
                write_report(records, args.out)
                print(f"{name} trial={trial} status={record['status']}", flush=True)
    except KeyboardInterrupt:
        write_report(records, args.out)
        return 130
    return 0 if records and all(record["status"] == "ok" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())

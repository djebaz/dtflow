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
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CASES = ("20:100", "40:120", "100:200")
QUERY_DONE = re.compile(r"gallery_perf phase=query_done\b[^\n]*\breturned=(\d+)\b[^\n]*\berror=none\b")


@dataclass(frozen=True)
class Case:
    """Source-only gallery configuration under test."""

    batch: int
    cards: int
    format: str = "jpg"

    @property
    def name(self) -> str:
        return f"load{self.batch}-cards{self.cards}-{self.format}"


def parse_case(value: str, fmt: str = "jpg") -> Case:
    """Resolve explicit batch/card inputs exactly as the current gallery does."""

    from dtflow.gallery_limits import resolve

    match = re.fullmatch(r"(\d+):(\d+)", value)
    if match is None:
        raise ValueError("Cases must use BATCH:CARDS (for example 20:100).")
    batch, requested = map(int, match.groups())
    actual_batch, actual_cards = resolve(batch, requested)
    if actual_cards != requested:
        raise ValueError(f"Cards must be a multiple of batch: {value} resolves to {actual_cards} cards.")
    if fmt not in ("jpg", "webp"):
        raise ValueError("Thumbnail format must be jpg or webp.")
    if requested > 1000 or batch > 1000:
        raise ValueError("Keep ordinary benchmark limits at or below 1000; stress-test separately.")
    return Case(actual_batch, actual_cards, fmt)


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
    window.close()
    return initial_count


def run_trial(case: Case, *, trial: int, directory: Path, cycles: int, timeout: float, no_cache: bool) -> dict:
    """Launch one source GUI process and retain evidence even if automation fails."""

    directory.mkdir(parents=True, exist_ok=False)
    logs = directory / "logs"
    logs.mkdir()
    args = [
        sys.executable, "-c", "from dtflow.cli import main; raise SystemExit(main())",
        "-x", "-q", str(logs), "gui", "-ml", str(case.batch),
        "-mc", str(case.cards), "-format", case.format,
    ]
    if no_cache:
        args.append("-nc")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    record: dict = {
        "case": asdict(case), "trial": trial, "no_cache": no_cache,
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
                "case": Case(**record["case"]).name,
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


def main(argv: list[str] | None = None) -> int:
    """Parse arguments without importing or launching Windows automation for help."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New output directory under ignored tmp/.")
    parser.add_argument("--cases", nargs="+", default=list(DEFAULT_CASES), metavar="BATCH:CARDS")
    parser.add_argument("--formats", nargs="+", choices=("jpg", "webp"), default=["jpg"])
    parser.add_argument("--runs", type=int, default=3, help="Trials per configuration (1–10).")
    parser.add_argument("--cycles", type=int, default=2, help="Repeated UI action sequences (1–10).")
    parser.add_argument("--timeout", type=float, default=30, help="Seconds for UI and process readiness.")
    parser.add_argument("--no-cache", action="store_true", help="Bypass DTFlow thumbnail disk cache.")
    parser.add_argument("--ack-user-state", action="store_true",
                        help="Explicitly allow the source app to read saved outputs and update viewer preferences/cache.")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Native Windows is required. Linux/offscreen runs cannot verify pywinauto behaviour.")
    if not args.ack_user_state:
        parser.error("--ack-user-state is required before automating the saved-output catalogue.")
    if not 1 <= args.runs <= 10 or not 1 <= args.cycles <= 10:
        parser.error("--runs and --cycles must be between 1 and 10.")
    if not 5 <= args.timeout <= 120:
        parser.error("--timeout must be between 5 and 120 seconds.")
    try:
        cases = [parse_case(case, fmt) for case in args.cases for fmt in args.formats]
    except ValueError as exc:
        parser.error(str(exc))
    if len(cases) * args.runs > 40:
        parser.error("Limit the matrix to 40 runs per invocation.")
    if args.out.exists():
        parser.error("--out must be a fresh directory (never mix different benchmark sessions).")
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        parser.error("pywinauto is required. Use: uv run --frozen --with pywinauto==0.6.9 python ...")
    records = []
    args.out.mkdir(parents=True)
    try:
        for case in cases:
            for index in range(1, args.runs + 1):
                directory = args.out / f"{case.name}-r{index:02}"
                record = run_trial(case, trial=index, directory=directory, cycles=args.cycles,
                                   timeout=args.timeout, no_cache=args.no_cache)
                records.append(record)
                write_report(records, args.out)
                print(f"{case.name} trial={index} status={record['status']}", flush=True)
    except KeyboardInterrupt:
        write_report(records, args.out)
        return 130
    return 0 if records and all(record["status"] == "ok" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())

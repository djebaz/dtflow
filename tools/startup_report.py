"""Summarize recorded DTFlow -x startup phases without starting DTFlow or contacting providers.

Usage: python3 tools/startup_report.py path/to/run1.jsonl path/to/run2.jsonl
Only comparable logs of the same source/build, machine, mode, state and warm/cold
classification should be compared. The 'first paint' mark is not a usable-window metric.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def read_startups(paths: list[Path]) -> list[dict]:
    """Read existing diagnostic events, ignoring other JSONL records."""
    runs = []
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSONL") from exc
                if entry.get("event") != "app.startup_timings":
                    continue
                data = entry.get("data", {})
                phases = data.get("phases", [])
                if not isinstance(phases, list) or not phases:
                    raise ValueError(f"{path}:{line_number}: missing startup phases")
                runs.append(
                    {
                        "source": str(path),
                        "elapsed_ms": float(data["elapsed_ms"]),
                        "phases": {phase["phase"]: float(phase["elapsed_ms"]) for phase in phases},
                    }
                )
    return runs


def summarize(runs: list[dict]) -> dict:
    """Report observations without claiming comparability or GUI usability."""
    if not runs:
        raise ValueError("No app.startup_timings events found")
    totals = [run["elapsed_ms"] for run in runs]
    names = sorted({name for run in runs for name in run["phases"]})
    return {
        "run_count": len(runs),
        "elapsed_ms": {
            "min": min(totals),
            "median": statistics.median(totals),
            "max": max(totals),
        },
        "phase_median_ms": {
            name: statistics.median([r["phases"][name] for r in runs if name in r["phases"]])
            for name in names
        },
        "runs": [{"source": r["source"], "elapsed_ms": r["elapsed_ms"]} for r in runs],
        "caveat": (
            "Startup marks include a queued first-paint callback, not first usable window. "
            "Do not compare unmatched machines, builds, cold/warm modes, or state."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path, help="Existing -x diagnostic JSONL files")
    args = parser.parse_args()
    print(json.dumps(summarize(read_startups(args.logs)), indent=2))


if __name__ == "__main__":
    main()

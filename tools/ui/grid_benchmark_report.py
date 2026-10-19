"""Fair, diagnostic-only ranking of completed DTFlow Grid scroll trials.

Uses actual reached image positions, per-journey wall times, and individual
development-mode GUI heartbeat outliers recorded while wheel input was active.
Startup heartbeat gaps and partially completed journeys cannot win.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dtflow._diagnostic_reader import EvidenceError, discover, scan

_THRESHOLD_MS = 100
ATTRIBUTION_POLICY = "jsonl-v1-source-millisecond-exact-window"


def analyze_grid_trial(logs: Path, actions: list[dict]) -> dict:
    """Source-ms GUI outliers in actually dispatched Grid wheel intervals only.

    No per-aggregate approximation or old whole-second timestamp widening.
    """
    intervals = []
    for item in actions:
        if item.get("action") not in ("grid-seek-down", "grid-seek-up"):
            continue
        try:
            start = datetime.fromisoformat(item["started_at"])
            end = datetime.fromisoformat(item["finished_at"])
        except (ValueError, KeyError, TypeError):
            continue
        if start.tzinfo is not None and end.tzinfo is not None and end >= start:
            intervals.append((start, end))
    total_ms = sum(
        float(action["duration_ms"]) for action in actions
        if action.get("action") in ("grid-seek-down", "grid-seek-up")
        and type(action.get("duration_ms")) in (int, float)
    )
    sessions = discover(logs)
    gaps = []
    states = []
    for sid in sessions:
        def reset():
            gaps.clear()
        def collect(record, _ref):
            if record["event"] != "gallery.gui_gap" or record.get("kind") != "measurement":
                return
            data = record["data"]
            if (str(data["mode"]).lower() != "grid" or data["mounted"] <= 0
                    or data["elapsed_ms"] < _THRESHOLD_MS):
                return
            timestamp = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
            if any(start <= timestamp <= end for start, end in intervals):
                gaps.append(float(data["elapsed_ms"]))
        status = scan(logs, sid, on_record=collect, on_reset=reset)
        states.append(status)
    complete = len(states) == 1 and states[0].complete
    return {
        "diagnostics_available": complete and bool(intervals),
        "evidence_status": states[0].state if len(states) == 1 else "incomplete",
        "evidence_reasons": list(states[0].reasons) if len(states) == 1 else ["expected_one_session"],
        "timestamp_attribution_policy": ATTRIBUTION_POLICY,
        "grid_stalls_100ms": len(gaps) if complete else None,
        "grid_stall_max_ms": round(max(gaps, default=0), 2) if complete else None,
        "round_trip_ms": round(total_ms, 2),
        "observed_browsing_legs": len(intervals),
        "threshold_ms": _THRESHOLD_MS,
    }


def rank_grid_matrix(records: list[dict]) -> dict:
    """Rank only clean, instrumented, identically targeted round trips.

    One trial per candidate is an indicative leader, not statistical proof.
    If any candidate is incomplete, mention it rather than hiding it.
    """

    grid = [r for r in records if r.get("benchmark_mode") == "grid-matrix"]
    if not grid:
        return {"status": "not-grid-matrix", "ranking": [], "winner": None}
    targets = {r.get("grid_target_card") for r in grid}
    grouped = defaultdict(list)
    for record in grid:
        grouped[(record["requested_ml"], record["requested_mc"], record["effective_mc"])].append(record)
    ranking = []
    for (ml, mc, effective_mc), trials in grouped.items():
        clean = []
        for trial in trials:
            actions = [a for a in trial.get("actions", []) if a.get("action") in ("grid-seek-down", "grid-seek-up")]
            valid = (
                trial.get("status") == "ok"
                and not trial.get("warnings")
                and trial.get("grid_sweep", {}).get("position_verified") is True
                and len(actions) == 2 * trial.get("grid_sweep", {}).get("cycles", 1)
                and all(action.get("target_reached") for action in actions)
                and trial.get("grid_responsiveness", {}).get("diagnostics_available") is True
            )
            if valid:
                clean.append(trial)
        if len(clean) == len(trials) and clean:
            responsiveness = [r["grid_responsiveness"] for r in clean]
            round_trips = [r["round_trip_ms"] for r in responsiveness]
            stalls = [r["grid_stalls_100ms"] for r in responsiveness]
            max_gap = max(r["grid_stall_max_ms"] for r in responsiveness)
            candidate = {
                "ml": ml,
                "mc": mc,
                "effective_mc": effective_mc,
                "trials": len(trials),
                "clean_trials": len(clean),
                "median_round_trip_ms": round(statistics.median(round_trips), 2),
                "total_grid_stalls_100ms": sum(stalls),
                "worst_grid_stall_ms": max_gap,
                "eligible": True,
            }
        else:
            candidate = {
                "ml": ml,
                "mc": mc,
                "effective_mc": effective_mc,
                "trials": len(trials),
                "clean_trials": len(clean),
                "median_round_trip_ms": None,
                "total_grid_stalls_100ms": None,
                "worst_grid_stall_ms": None,
                "eligible": False,
            }
        ranking.append(candidate)
    complete = [r for r in ranking if r["eligible"]]
    complete.sort(
        key=lambda r: (
            r["total_grid_stalls_100ms"] / r["trials"],
            r["worst_grid_stall_ms"],
            r["median_round_trip_ms"],
            r["ml"],
            r["mc"],
        )
    )
    incomplete = sorted((r for r in ranking if not r["eligible"]), key=lambda r: (r["ml"], r["mc"]))
    ranking = complete + incomplete
    comparable = len(targets) == 1 and None not in targets
    winner = complete[0] if complete and comparable else None
    stable = bool(winner and not incomplete and all(r["clean_trials"] >= 3 for r in complete))
    return {
        "status": "best" if stable else "provisional" if winner else "insufficient-evidence",
        "target_card": next(iter(targets)) if comparable else None,
        "ranking": ranking,
        "winner": winner,
        "exclusions": len(incomplete),
        "warning": (
            "At least three clean trials per combination are required for a repeatable winner." if not stable else None
        ),
        "ranking_rule": (
            "Fewest observed >=100ms Grid-thread gaps per trial, then lowest worst gap, "
            "then shortest median completed round trip. Startup gaps are excluded."
        ),
    }


def grid_console_report(report: dict) -> str:
    if report["status"] == "not-grid-matrix":
        return ""
    lines = [
        "",
        f"GRID SCROLL BENCHMARK  |  image #1 -> #{report.get('target_card') or '?'} -> #1",
        "Requested       Effective   Clean    Grid stalls >=100ms   Worst gap   Median trip",
    ]
    for case in report["ranking"]:
        if case["eligible"]:
            gaps = f"{case['total_grid_stalls_100ms']:>6}"
            worst = f"{case['worst_grid_stall_ms']:>7.1f} ms"
            trip = f"{case['median_round_trip_ms'] / 1000:>7.2f} s"
        else:
            gaps, worst, trip = "     —", "       —", "       —"
        lines.append(
            f"-ml {case['ml']:<3} -mc {case['mc']:<4}  "
            f"{case['effective_mc']:>6}   {case['clean_trials']}/{case['trials']:<4}"
            f"  {gaps}       {worst}   {trip}"
        )
    winner = report["winner"]
    if winner:
        heading = "BEST COMBINATION" if report["status"] == "best" else "PROVISIONAL BEST"
        lines.append(f"{heading}: -ml {winner['ml']} -mc {winner['mc']} (effective -mc {winner['effective_mc']})")
    else:
        lines.append("NO RELIABLE BEST COMBINATION: insufficient comparable complete journeys.")
    lines.append("Priority: no long Grid GUI gaps; journey speed breaks ties. Startup excluded.")
    if report.get("exclusions"):
        lines.append(f"{report['exclusions']} configuration(s) excluded due to incomplete or uninstrumented trials.")
    if report.get("warning"):
        lines.append(report["warning"])
    return "\n".join(lines) + "\n"


def write_grid_report(records: list[dict], out: Path) -> str:
    report = rank_grid_matrix(records)
    if report["status"] == "not-grid-matrix":
        return ""
    (out / "best_combination.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    text = grid_console_report(report)
    (out / "benchmark_report.txt").write_text(text, encoding="utf-8")
    return text

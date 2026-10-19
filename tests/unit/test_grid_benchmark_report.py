"""Offline contracts for evidence-based Grid performance leaderboard."""

import json

from tools.ui.grid_benchmark_report import (
    analyze_grid_trial,
    grid_console_report,
    rank_grid_matrix,
    write_grid_report,
)


def _trial(ml, mc, *, seconds, stalls=0, worst=0, ok=True, target=300):
    return {
        "benchmark_mode": "grid-matrix",
        "profile_name": f"grid-ml{ml}-mc{mc}",
        "requested_ml": ml,
        "requested_mc": mc,
        "effective_mc": (mc // ml) * ml,
        "grid_target_card": target,
        "status": "ok" if ok else "warning",
        "warnings": [] if ok else [{"step": "grid-seek-down"}],
        "grid_sweep": {"position_verified": ok, "cycles": 1},
        "actions": [
            {"action": "grid-seek-down", "target_reached": ok},
            {"action": "grid-seek-up", "target_reached": True},
        ],
        "grid_responsiveness": {
            "diagnostics_available": True,
            "grid_stalls_100ms": stalls,
            "grid_stall_max_ms": worst,
            "round_trip_ms": seconds * 1000,
        },
    }


def test_browsing_gui_gap_counter_excludes_startup_and_after_scroll(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    def record(seq, ts, event, data, *, kind="event"):
        return {
            "v": 1, "seq": seq, "ts": ts, "level": "DEBUG",
            "component": "gallery", "event": event, "kind": kind, "data": data,
            **({"mono_ns": seq * 1_000_000_000} if kind == "measurement" else {}),
        }

    started = {
        "session_id": "example", "version": "test", "source_commit": "unknown",
        "build_state": "unknown", "runtime": "python", "platform": "test",
        "dev": True, "instrumentation_version": "gallery-jsonl-v1",
        "schema_version": 1, "queue_max_records": 1024,
        "queue_max_bytes": 8 * 1024 * 1024,
        "max_record_bytes": 1_048_576, "aggregation_policy": "test",
    }
    rows = [record(1, "2026-10-19T00:00:00.000Z", "session.started", started)]
    for seq, ts, elapsed, mode, mounted in (
        (2, "2026-10-19T00:00:01.000Z", 1700, "Grid", 0),
        (3, "2026-10-19T00:00:12.000Z", 112, "Grid", 100),
        (4, "2026-10-19T00:00:15.000Z", 900, "List", 100),
        (5, "2026-10-19T00:00:35.000Z", 1200, "Grid", 100),
    ):
        rows.append(record(seq, ts, "gallery.gui_gap", {
            "elapsed_ms": elapsed, "mode": mode, "mounted": mounted,
            "query_busy": seq == 3, "preparation_busy": False,
        }, kind="measurement"))
    closed = {
        "observed": 5, "admitted": 5, "rejected": 0, "dropped": 0,
        "written_records": 6, "aggregate_source_samples": 0,
        "writer_failures": 0, "serialization_failures": 0,
    }
    rows.append(record(6, "2026-10-19T00:00:36.000Z", "session.closed", closed))
    (logs / "dtflow-example.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )
    actions = [
        {
            "action": "grid-seek-down",
            "duration_ms": 1234,
            "started_at": "2026-10-19T02:00:10+02:00",
            "finished_at": "2026-10-19T02:00:20+02:00",
        },
        {
            "action": "grid-seek-up",
            "duration_ms": 2345,
            "started_at": "2026-10-19T02:00:21+02:00",
            "finished_at": "2026-10-19T02:00:31+02:00",
        },
    ]
    observed = analyze_grid_trial(logs, actions)
    assert observed["diagnostics_available"]
    assert observed["grid_stalls_100ms"] == 1
    assert observed["grid_stall_max_ms"] == 112
    assert observed["round_trip_ms"] == 3579
    assert observed["observed_browsing_legs"] == 2


def test_grid_ranking_prefers_smoothness_before_speed_and_excludes_failed(tmp_path):
    rows = [
        _trial(20, 50, seconds=23),
        _trial(50, 150, seconds=18),
        _trial(100, 300, seconds=10, stalls=3, worst=402),
        _trial(300, 1000, seconds=6, ok=False),
    ]
    report = rank_grid_matrix(rows)
    assert report["status"] == "provisional"
    assert report["winner"]["ml"] == 50
    assert report["winner"]["mc"] == 150
    assert report["ranking"][-1]["eligible"] is False
    message = grid_console_report(report)
    assert "PROVISIONAL BEST: -ml 50 -mc 150" in message
    assert "excluded" in message
    write_grid_report(rows, tmp_path)
    assert "PROVISIONAL BEST" in (tmp_path / "benchmark_report.txt").read_text()
    assert json.loads((tmp_path / "best_combination.json").read_text())["winner"]["ml"] == 50


def test_grid_rank_is_verified_only_after_three_repeats_per_case():
    rows = [_trial(20, 50, seconds=20) for _ in range(3)]
    rows.extend(_trial(50, 150, seconds=18) for _ in range(3))
    ranked = rank_grid_matrix(rows)
    assert ranked["status"] == "best"
    assert ranked["winner"]["ml"] == 50
    assert "BEST COMBINATION" in grid_console_report(ranked)


def test_grid_rank_will_not_compare_different_card_destinations():
    rows = [_trial(20, 50, seconds=20, target=200), _trial(50, 150, seconds=18, target=300)]
    result = rank_grid_matrix(rows)
    assert result["status"] == "insufficient-evidence"
    assert result["winner"] is None


def test_grid_report_requires_actual_diagnostics_and_completed_journey():
    row = _trial(50, 150, seconds=8)
    row["grid_responsiveness"]["diagnostics_available"] = False
    assert rank_grid_matrix([row])["winner"] is None
    row["grid_responsiveness"]["diagnostics_available"] = True
    row["actions"][0]["target_reached"] = False
    assert rank_grid_matrix([row])["winner"] is None

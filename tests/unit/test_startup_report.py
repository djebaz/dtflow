"""Pure, offline tests for the startup JSONL comparison helper (#169)."""

import json
from pathlib import Path

import pytest

from tools.startup_report import read_startups, summarize


def test_startup_report_keeps_measured_phases_and_caveat(tmp_path: Path):
    path = tmp_path / "startup.jsonl"
    entries = [
        {"event": "other.event", "data": {}},
        {
            "event": "app.startup_timings",
            "data": {
                "elapsed_ms": 1200.0,
                "phases": [
                    {"phase": "python imports", "elapsed_ms": 300.0},
                    {"phase": "first paint", "elapsed_ms": 900.0},
                ],
            },
        },
    ]
    path.write_text("\n".join(map(json.dumps, entries)) + "\n", encoding="utf-8")
    result = summarize(read_startups([path]))
    assert result["run_count"] == 1
    assert result["elapsed_ms"]["median"] == 1200.0
    assert result["phase_median_ms"]["first paint"] == 900.0
    assert "not first usable window" in result["caveat"]


def test_startup_report_does_not_invent_missing_results(tmp_path: Path):
    path = tmp_path / "empty.jsonl"
    path.write_text('{"event":"other.event"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="No app.startup_timings"):
        summarize(read_startups([path]))

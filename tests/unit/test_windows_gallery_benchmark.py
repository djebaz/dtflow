"""Offline contracts for native-Windows gallery performance profiles and reports."""

import csv
import json

import pytest

from tools.ui.gallery_perf_launch import DEFAULTS, validate_profile
from tools.ui.windows_gallery_benchmark import load_profiles, summarize_csv, write_report


def test_validated_profile_changes_one_parameter_without_changing_defaults():
    profile = validate_profile({"thumbnail_workers": 3, "mount_budget_ms": 2.0})
    assert profile["thumbnail_workers"] == 3
    assert profile["mount_budget_ms"] == 2.0
    assert profile["lookahead_screens"] == DEFAULTS["lookahead_screens"]
    assert DEFAULTS["thumbnail_workers"] == 6


@pytest.mark.parametrize(
    "overrides",
    [
        {"unknown": 3},
        {"thumbnail_workers": 0},
        {"thumbnail_workers": 1.5},
        {"thumbnail_workers": True},
        {"thumbnail_side_px": 5000},
        {"cache_entries": 5000, "cache_prune_to": 8000},
        {"refresh_ms": 20000, "full_refresh_s": 5},
    ],
)
def test_unsafe_or_inconsistent_tuning_is_rejected(overrides):
    with pytest.raises(ValueError):
        validate_profile(overrides)


def test_named_profiles_load_with_exact_explicit_overrides(tmp_path):
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"baseline": {}, "workers-2": {"thumbnail_workers": 2}}))
    assert list(load_profiles(profiles, ["workers-2"])) == ["workers-2"]
    assert load_profiles(profiles, ["workers-2"])["workers-2"]["thumbnail_workers"] == 2
    with pytest.raises(ValueError, match="Unknown profile"):
        load_profiles(profiles, ["missing"])


def test_report_uses_original_diagnostic_max_and_never_invents_missing_events(tmp_path):
    metrics = tmp_path / "metrics.csv"
    with metrics.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "utc", "phase", "metric", "samples", "mean", "p95", "max", "total", "details"
        ))
        writer.writeheader()
        writer.writerows([
            {
                "utc": "", "phase": "query_done", "metric": "elapsed_ms", "samples": "1",
                "mean": "28", "p95": "28", "max": "28", "total": "28", "details": "",
            },
            {
                "utc": "", "phase": "gui_gaps", "metric": "wall_max_ms", "samples": "3",
                "mean": "0", "p95": "0", "max": "87", "total": "0", "details": "",
            },
        ])
    result = summarize_csv(metrics)
    assert result["gui_gap_over_50_max_ms"] == 87
    assert result["query_done_median_recorded_ms"] == 28
    assert result["prepare_median_recorded_ms"] is None
    assert result["phase_counts"]["gui_gaps"] == 3

    write_report([{
        "profile_name": "baseline", "profile": validate_profile({}),
        "trial": 1, "status": "ok", "initial_query_records": 20, "metrics": result
    }], tmp_path / "out")
    rows = list(csv.DictReader((tmp_path / "out" / "results.csv").open(newline="", encoding="utf-8")))
    assert rows[0]["case"] == "baseline"
    assert rows[0]["gui_gap_over_50_max_ms"] == "87.0"


def test_missing_gallery_query_cannot_be_ranked(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("phase,metric,samples,mean,max\ngui_gaps,wall_max_ms,1,80,80\n")
    with pytest.raises(ValueError, match="query"):
        summarize_csv(path)


class _FakeInfo:
    def __init__(self, name, automation_id=""):
        self.name = name
        self.automation_id = automation_id


class _FakeButton:
    def __init__(self, name, *, automation_id="", enabled=True, visible=True):
        self.element_info = _FakeInfo(name, automation_id)
        self._enabled = enabled
        self._visible = visible

    def is_enabled(self):
        return self._enabled

    def is_visible(self):
        return self._visible


class _FakeWindow:
    def __init__(self, *buttons):
        self.buttons = list(buttons)

    def descendants(self, *, control_type):
        assert control_type == "Button"
        return self.buttons


def test_images_navigation_accepts_visible_text_or_qt_accessibility_name():
    from tools.ui.windows_gallery_benchmark import _button

    images = _FakeButton("Images")
    assert _button(_FakeWindow(images), "Show saved Images", alternatives=("Images",), timeout=1) is images

    accessible = _FakeButton("Show saved Images")
    assert _button(_FakeWindow(accessible), "Show saved Images", alternatives=("Images",), timeout=1) is accessible

    object_id = _FakeButton("", automation_id="imagesNavigationButton")
    assert _button(
        _FakeWindow(object_id), "Show saved Images", alternatives=("Images",),
        automation_id="imagesNavigationButton", timeout=1,
    ) is object_id


def test_button_selector_fails_closed_on_ambiguous_matches():
    from tools.ui.windows_gallery_benchmark import _button

    window = _FakeWindow(_FakeButton("Images"), _FakeButton("Show saved Images"))
    with pytest.raises(RuntimeError, match="Ambiguous"):
        _button(window, "Show saved Images", alternatives=("Images",), timeout=1)

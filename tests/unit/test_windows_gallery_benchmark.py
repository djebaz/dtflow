"""Offline contracts for native-Windows gallery performance profiles and reports."""

import csv
import json
import sys
from types import SimpleNamespace

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
    def __init__(self, name, automation_id="", control_type="Button"):
        self.name = name
        self.automation_id = automation_id
        self.control_type = control_type


class _FakeButton:
    def __init__(self, name, *, automation_id="", control_type="Button", enabled=True, visible=True):
        self.element_info = _FakeInfo(name, automation_id, control_type)
        self._enabled = enabled
        self._visible = visible

    def is_enabled(self):
        return self._enabled

    def is_visible(self):
        return self._visible


class _FakeWindow:
    def __init__(self, *buttons):
        self.buttons = list(buttons)

    def descendants(self):
        return self.buttons


@pytest.mark.parametrize("control_type", ["Button", "CheckBox", "RadioButton"])
def test_images_navigation_accepts_visible_text_or_qt_accessibility_name(control_type):
    from tools.ui.windows_gallery_benchmark import _button

    images = _FakeButton("Images", control_type=control_type)
    assert _button(_FakeWindow(images), "Show saved Images", alternatives=("Images",), timeout=1) is images

    accessible = _FakeButton("Show saved Images", control_type=control_type)
    assert _button(_FakeWindow(accessible), "Show saved Images", alternatives=("Images",), timeout=1) is accessible

    object_id = _FakeButton("", automation_id="imagesNavigationButton", control_type=control_type)
    assert _button(
        _FakeWindow(object_id), "Show saved Images", alternatives=("Images",),
        automation_id="imagesNavigationButton", timeout=1,
    ) is object_id


def test_button_selector_fails_closed_on_ambiguous_matches():
    from tools.ui.windows_gallery_benchmark import _button

    window = _FakeWindow(_FakeButton("Images"), _FakeButton("Show saved Images", control_type="RadioButton"))
    with pytest.raises(RuntimeError, match="Ambiguous"):
        _button(window, "Show saved Images", alternatives=("Images",), timeout=1)


@pytest.mark.parametrize("name", ["Grid", "List", "Detail"])
def test_checkable_gallery_controls_ignore_text_hidden_and_disabled_matches(name):
    from tools.ui.windows_gallery_benchmark import _button

    target = _FakeButton(name, control_type="CheckBox")
    window = _FakeWindow(
        _FakeButton(name, control_type="Text"),
        _FakeButton(name, control_type="CheckBox", visible=False),
        _FakeButton(name, control_type="RadioButton", enabled=False),
        target,
    )
    assert _button(window, name, timeout=1) is target


@pytest.mark.parametrize(
    ("restored", "expected"),
    [("Newest first", ["Oldest first", "Newest first"]),
     ("Oldest first", ["Newest first"]), ("Model", ["Oldest first", "Newest first"])],
)
@pytest.mark.parametrize("focused", [True, False])
def test_sort_setup_changes_restored_selection_and_verifies_page_one(tmp_path, monkeypatch, restored, expected, focused):
    from tools.ui import windows_gallery_benchmark as benchmark

    selected = []
    completions = []
    page_checks = []

    class Combo:
        value = restored

        def wait(self, state, *, timeout):
            assert state == "exists visible enabled"

        def selected_text(self):
            return self.value

        def set_focus(self):
            self.focused = True

        def has_keyboard_focus(self):
            return self.focused and focused

        def type_keys(self, keys, *, pause, set_foreground):
            assert pause == 0.05
            assert self.focused and set_foreground is False
            value = {
                "{F4}{HOME}{DOWN}{ENTER}": "Oldest first",
                "{F4}{HOME}{ENTER}": "Newest first",
            }[keys]
            assert value != self.value
            selected.append(value)
            self.value = value

    combo = Combo()

    class Window:
        def child_window(self, **criteria):
            assert criteria == {"title": "Sort saved outputs", "control_type": "ComboBox"}
            return combo

    def await_query(directory, proc, seconds, *, after):
        assert after == {"prior-completion"}
        completions.append(combo.value)
        return 20

    def button(window, name, **kwargs):
        page_checks.append(name)
        assert selected == expected and completions == expected
        assert kwargs["automation_id"] == "galleryPagerPage1"
        return SimpleNamespace(get_toggle_state=lambda: 1)

    monkeypatch.setattr(benchmark, "_completed_queries", lambda directory: {"prior-completion": 80})
    monkeypatch.setattr(benchmark, "_await_query", await_query)
    monkeypatch.setattr(benchmark, "_button", button)
    record = {}
    if not focused:
        with pytest.raises(RuntimeError, match="no keys sent"):
            benchmark._reset_gallery_sort(Window(), tmp_path, object(), record, timeout=1)
        assert selected == completions == page_checks == []
        assert record["setup_actions"] == []
        return
    benchmark._reset_gallery_sort(Window(), tmp_path, object(), record, timeout=1)
    assert selected == expected
    assert combo.value == "Newest first"
    assert page_checks == ["Jump to image batch 1"]
    assert [action["value"] for action in record["setup_actions"]] == expected
    assert record["step"] == "verify-page-one-after-sort"


def test_query_readiness_ignores_old_completions_across_rotation(tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark

    log = tmp_path / "dtflow-session.log"
    log.write_text("session starting\n", encoding="utf-8")
    assert benchmark._read_query_count(tmp_path) == 0
    log.write_text("gallery_perf phase=query_done returned=80 error=none\n", encoding="utf-8")
    previous = set(benchmark._completed_queries(tmp_path))
    log.rename(tmp_path / "dtflow-session.log.1")
    assert set(benchmark._completed_queries(tmp_path)) == previous
    log.write_text("gallery_perf phase=query_done returned=20 error=none\n", encoding="utf-8")
    assert benchmark._await_query(tmp_path, SimpleNamespace(poll=lambda: None), 1, after=previous) == 20


@pytest.mark.parametrize("app_pid", [9001, 9002])
def test_driver_attaches_to_reported_interpreter_not_venv_launcher(tmp_path, monkeypatch, app_pid):
    from tools.ui.windows_gallery_benchmark import _drive

    (tmp_path / "app-process.json").write_text(json.dumps({"pid": app_pid}), encoding="utf-8")
    process = SimpleNamespace(pid=9001, poll=lambda: None)
    selected = []

    class UIATimeoutError(RuntimeError):
        pass

    class Window:
        def wait(self, state, *, timeout):
            assert state == "exists visible enabled"
            raise UIATimeoutError("timed out")

    class Desktop:
        def __init__(self, *, backend):
            assert backend == "uia"

        def window(self, **criteria):
            selected.append(criteria)
            return Window()

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=Desktop))
    monkeypatch.setitem(sys.modules, "pywinauto.timings", SimpleNamespace(TimeoutError=UIATimeoutError))
    record = {"actions": []}
    with pytest.raises(TimeoutError, match=f"launcher_pid=9001, app_pid={app_pid}"):
        _drive(process, tmp_path / "logs", record, cycles=1, timeout=1)
    assert selected == [{"title": "DTFlow", "process": app_pid}]
    assert record["launcher_pid"] == 9001
    assert record["app_pid"] == app_pid
    assert record["step"] == "find-dtflow-window"
    assert record["actions"] == []


@pytest.mark.parametrize("report", [{"pid": True}, {"pid": 0}, {"pid": -1}, {"pid": "9002"}, [], {}])
def test_invalid_child_pid_never_falls_back_to_other_windows(tmp_path, report):
    from tools.ui.windows_gallery_benchmark import _await_app_pid

    path = tmp_path / "app-process.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid DTFlow child PID"):
        _await_app_pid(path, SimpleNamespace(poll=lambda: None), 1)


def test_child_pid_wait_detects_early_exit_and_missing_report(tmp_path, monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark

    path = tmp_path / "app-process.json"
    with pytest.raises(RuntimeError, match="exit=7"):
        benchmark._await_app_pid(path, SimpleNamespace(poll=lambda: 7, returncode=7), 1)
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(benchmark.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(benchmark.time, "sleep", lambda seconds: None)
    with pytest.raises(TimeoutError, match="did not publish its PID"):
        benchmark._await_app_pid(path, SimpleNamespace(poll=lambda: None), 1)


def test_launcher_publishes_own_pid_before_gui_setup(tmp_path, monkeypatch):
    import dtflow.cli
    from tools.ui import gallery_perf_launch as launcher

    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    pid_file = tmp_path / "app-process.json"
    monkeypatch.setattr(launcher.os, "getpid", lambda: 9002)

    def apply_profile(profile):
        assert json.loads(pid_file.read_text(encoding="utf-8")) == {"pid": 9002}
        assert not pid_file.with_suffix(".tmp").exists()

    monkeypatch.setattr(launcher, "apply_profile", apply_profile)
    monkeypatch.setattr(dtflow.cli, "main", lambda argv: 0)
    assert launcher.main([
        "--profile", str(profile), "--logs", str(tmp_path / "logs"), "--pid-file", str(pid_file),
    ]) == 0


def test_windows_cleanup_targets_only_live_trial_tree(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark

    monkeypatch.setattr(benchmark.sys, "platform", "win32")
    monkeypatch.setattr(benchmark.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    commands = []
    waits = []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == 10
        assert kwargs["creationflags"] == 0x08000000
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(benchmark.subprocess, "run", run)
    benchmark._stop_trial_process(SimpleNamespace(pid=9001, poll=lambda: None, wait=lambda **kw: waits.append(kw)))
    assert commands == [["taskkill.exe", "/PID", "9001", "/T", "/F"]]
    assert waits == [{"timeout": 5}]
    benchmark._stop_trial_process(SimpleNamespace(pid=9001, poll=lambda: 0))
    assert len(commands) == 1


def test_windows_cleanup_failure_is_not_hidden(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark

    monkeypatch.setattr(benchmark.sys, "platform", "win32")
    monkeypatch.setattr(benchmark.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=5))
    with pytest.raises(RuntimeError, match="process-tree cleanup failed"):
        benchmark._stop_trial_process(SimpleNamespace(pid=9001, poll=lambda: None))

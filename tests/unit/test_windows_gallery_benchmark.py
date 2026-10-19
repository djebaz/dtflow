"""Offline contracts for native-Windows gallery performance profiles and reports."""

import csv
import json
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from tools.ui.gallery_perf_launch import DEFAULTS, validate_profile
from tools.ui.windows_gallery_benchmark import load_profiles, summarize_jsonl, write_report


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


def _jsonl_record(seq, event, data, *, kind="event", agg=None):
    record = {
        "v": 1,
        "seq": seq,
        "ts": f"2026-10-19T00:00:{seq:02d}.000Z",
        "level": "DEBUG",
        "component": "gallery",
        "event": event,
        "kind": kind,
        "data": data,
    }
    if kind == "measurement":
        record["mono_ns"] = seq * 1_000_000_000
    if agg is not None:
        record["agg"] = agg
    return record


def _write_jsonl_session(directory, observations):
    directory.mkdir(exist_ok=True)
    session = "benchmarktest"
    start = {
        "session_id": session,
        "version": "test",
        "source_commit": "unknown",
        "build_state": "unknown",
        "runtime": "python",
        "platform": "test",
        "dev": True,
        "instrumentation_version": "gallery-jsonl-v1",
        "schema_version": 1,
        "queue_max_records": 1024,
        "queue_max_bytes": 8 * 1024 * 1024,
        "max_record_bytes": 1_048_576,
        "aggregation_policy": "test",
    }
    records = [_jsonl_record(1, "session.started", start)]
    records.extend(
        _jsonl_record(index, event, data, kind=kind, agg=agg)
        for index, (event, data, kind, agg) in enumerate(observations, 2)
    )
    closing_seq = len(records) + 1
    close = {
        "observed": len(records),
        "admitted": len(records),
        "rejected": 0,
        "dropped": 0,
        "written_records": closing_seq,
        "aggregate_source_samples": 0,
        "writer_failures": 0,
        "serialization_failures": 0,
    }
    records.append(_jsonl_record(closing_seq, "session.closed", close))
    (directory / f"dtflow-{session}.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records),
        encoding="utf-8",
    )


def test_report_uses_original_diagnostic_max_and_never_invents_missing_events(tmp_path):
    logs = tmp_path / "logs"
    _write_jsonl_session(
        logs,
        [
            (
                "gallery.query_done",
                {"elapsed_ms": 28, "returned": 20, "source": "live", "error": None},
                "measurement",
                None,
            ),
            (
                "gallery.performance_aggregate",
                {"phase": "gui_gaps"},
                "aggregate",
                {
                    "samples": 3,
                    "window_start": "2026-10-19T00:00:01.000Z",
                    "window_end": "2026-10-19T00:00:02.000Z",
                    "metrics": {"wall_max_ms": {"count": 3, "mean": 0, "p95": 0, "max": 87, "total": 0}},
                },
            ),
        ],
    )
    result = summarize_jsonl(logs)
    assert result["gui_gap_over_50_max_ms"] == 87
    assert result["query_done_median_recorded_ms"] == 28
    assert result["prepare_median_recorded_ms"] is None
    assert result["phase_counts"]["gui_gaps"] == 3

    write_report(
        [
            {
                "profile_name": "baseline",
                "profile": validate_profile({}),
                "trial": 1,
                "status": "ok",
                "initial_query_records": 20,
                "metrics": result,
            }
        ],
        tmp_path / "out",
    )
    rows = list(csv.DictReader((tmp_path / "out" / "results.csv").open(newline="", encoding="utf-8")))
    assert rows[0]["case"] == "baseline"
    assert rows[0]["gui_gap_over_50_max_ms"] == "87.0"


def test_missing_gallery_query_cannot_be_ranked(tmp_path):
    logs = tmp_path / "logs"
    _write_jsonl_session(
        logs,
        [
            (
                "gallery.performance_aggregate",
                {"phase": "gui_gaps"},
                "aggregate",
                {
                    "samples": 1,
                    "window_start": "2026-10-19T00:00:01.000Z",
                    "window_end": "2026-10-19T00:00:01.000Z",
                    "metrics": {"wall_max_ms": {"count": 1, "mean": 80, "p95": 80, "max": 80, "total": 80}},
                },
            ),
        ],
    )
    with pytest.raises(ValueError, match="query"):
        summarize_jsonl(logs)


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
    assert (
        _button(
            _FakeWindow(object_id),
            "Show saved Images",
            alternatives=("Images",),
            automation_id="imagesNavigationButton",
            timeout=1,
        )
        is object_id
    )


def test_button_selector_fails_closed_on_ambiguous_matches():
    from tools.ui.windows_gallery_benchmark import _button

    window = _FakeWindow(_FakeButton("Images"), _FakeButton("Show saved Images", control_type="RadioButton"))
    with pytest.raises(RuntimeError, match="Ambiguous"):
        _button(window, "Show saved Images", alternatives=("Images",), timeout=1)


@pytest.mark.parametrize(
    ("name", "accessible"), [("Grid", "Grid"), ("List", "List"), ("Detail", "Full screen image view")]
)
def test_checkable_gallery_controls_ignore_text_hidden_and_disabled_matches(name, accessible):
    from tools.ui.windows_gallery_benchmark import _button

    target = _FakeButton(accessible, control_type="CheckBox")
    window = _FakeWindow(
        _FakeButton(name, control_type="Text"),
        _FakeButton(name, control_type="CheckBox", visible=False),
        _FakeButton(name, control_type="RadioButton", enabled=False),
        target,
    )
    assert _button(window, name, alternatives=(accessible,), timeout=1) is target


@pytest.mark.parametrize(
    ("restored", "expected"),
    [
        ("Newest first", ["Oldest first", "Newest first"]),
        ("Oldest first", ["Newest first"]),
        ("Model", ["Oldest first", "Newest first"]),
    ],
)
@pytest.mark.parametrize("popup_ready", [True, False])
def test_sort_setup_changes_restored_selection_and_verifies_page_one(
    tmp_path, monkeypatch, restored, expected, popup_ready
):
    from tools.ui import windows_gallery_benchmark as benchmark

    selected = []
    completions = []
    page_checks = []

    class Combo:
        value = restored
        opened = False

        def wait(self, state, *, timeout):
            assert state == "exists visible enabled"

        def selected_text(self):
            return self.value

        def click_input(self):
            assert not self.opened
            self.opened = True

        def child_window(self, *, title, control_type):
            assert self.opened and control_type == "ListItem"
            assert title in ("Oldest first", "Newest first")
            owner = self

            class Option:
                def wait(self, state, *, timeout):
                    assert state == "exists visible enabled"
                    if not popup_ready:
                        raise TimeoutError("Sort popup item unavailable")

                def click_input(self):
                    assert popup_ready and title != owner.value
                    selected.append(title)
                    owner.value = title
                    owner.opened = False

            return Option()

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
    if not popup_ready:
        with pytest.raises(TimeoutError, match="Sort popup item unavailable"):
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


def test_run_directory_uses_requested_timestamp_without_merging_runs(tmp_path):
    from tools.ui.windows_gallery_benchmark import _create_run_directory

    root = tmp_path / "tmp"
    root.mkdir()
    now = datetime(2026, 10, 12, 0, 40)
    output = _create_run_directory(root, now=now)
    assert output == root / "gallery_bench_smoke" / "10120040"
    sentinel = output / "trial.json"
    sentinel.write_text("retained evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _create_run_directory(root, now=now)
    assert sentinel.read_text(encoding="utf-8") == "retained evidence"


def test_query_readiness_ignores_old_completions_across_rotation(tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark

    sid = "benchmarktest"
    current = tmp_path / f"dtflow-{sid}.jsonl"
    started = {
        "session_id": sid,
        "version": "test",
        "source_commit": "unknown",
        "build_state": "unknown",
        "runtime": "python",
        "platform": "test",
        "dev": True,
        "instrumentation_version": "gallery-jsonl-v1",
        "schema_version": 1,
        "queue_max_records": 1024,
        "queue_max_bytes": 8 * 1024 * 1024,
        "max_record_bytes": 1_048_576,
        "aggregation_policy": "test",
    }

    def append(*records):
        with current.open("a", encoding="utf-8") as stream:
            for row in records:
                stream.write(json.dumps(row) + "\n")

    append(_jsonl_record(1, "session.started", started))
    assert benchmark._read_query_count(tmp_path) == 0
    append(
        _jsonl_record(
            2,
            "gallery.query_done",
            {
                "elapsed_ms": 28,
                "returned": 80,
                "source": "live",
                "error": None,
            },
            kind="measurement",
        )
    )
    previous = set(benchmark._completed_queries(tmp_path))
    assert previous == {(sid, 2)}
    current.rename(tmp_path / f"dtflow-{sid}.jsonl.1")
    # The writer creates the new active segment after rotation; the old
    # segment alone is an incomplete snapshot, not clean live evidence.
    append(
        _jsonl_record(
            3,
            "gallery.query_done",
            {
                "elapsed_ms": 30,
                "returned": 20,
                "source": "live",
                "error": None,
            },
            kind="measurement",
        )
    )
    assert previous.issubset(benchmark._completed_queries(tmp_path))
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
    assert (
        launcher.main(
            [
                "--profile",
                str(profile),
                "--logs",
                str(tmp_path / "logs"),
                "--pid-file",
                str(pid_file),
            ]
        )
        == 0
    )


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


def test_missed_click_is_recorded_as_warning_and_following_actions_continue():
    from tools.ui.windows_gallery_benchmark import _attempt

    record = {"actions": [], "warnings": []}
    attempted = []

    def miss():
        attempted.append("missing-control")
        raise TimeoutError("UIA action timed out")

    assert _attempt(record, "cycle-1-page-two", miss) is False
    assert _attempt(record, "cycle-1-page-one", lambda: attempted.append("page-one")) is True
    assert attempted == ["missing-control", "page-one"]
    assert record["warnings"] == [
        {
            "step": "cycle-1-page-two",
            "error_type": "TimeoutError",
            "message": "UIA action timed out",
        }
    ]


def test_page_change_requires_actual_toggle_confirmation(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark

    class Button:
        selected = 0
        clicks = 0

        def get_toggle_state(self):
            return self.selected

        def click_input(self):
            self.clicks += 1
            self.selected = 1

    button = Button()

    def select(window, name, **kwargs):
        assert name == "Jump to image batch 2"
        assert kwargs["automation_id"] == "galleryPagerPage2"
        return button

    monkeypatch.setattr(benchmark, "_button", select)
    actions = []
    benchmark._click_page(object(), 2, actions)
    assert button.clicks == 1
    assert actions[0]["verified"] is True

    benchmark._click_page(object(), 2, actions)
    assert button.clicks == 1  # Already selected: no fake extra click.


def test_fullscreen_uses_recorded_qt_control_and_escape(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark

    events = []

    class Window:
        def type_keys(self, keys):
            events.append(("key", keys))

    class Toggle:
        def click_input(self):
            events.append(("click", "fullscreen"))

    def find(window, name, **kwargs):
        assert name == "Full screen image view"
        assert kwargs["automation_id"] == "galleryFocusButton"
        return Toggle()

    monkeypatch.setattr(benchmark, "_button", find)
    monkeypatch.setattr(benchmark.time, "sleep", lambda value: None)
    actions = []
    benchmark._toggle_fullscreen(Window(), actions)
    assert events == [("click", "fullscreen"), ("key", "{ESC}")]
    assert [a["action"] for a in actions] == ["fullscreen-enter", "fullscreen-exit-esc"]


@pytest.mark.parametrize(
    ("starting_value", "expected_targets", "mouse_events"),
    [(100.0, [55.0, 210.0], 9), (55.0, [210.0, 55.0], 6)],
)
def test_thumbnail_slider_uses_uia_slider_and_restores_original_value(
    monkeypatch,
    starting_value,
    expected_targets,
    mouse_events,
):
    from tools.ui import windows_gallery_benchmark as benchmark

    class Range:
        CurrentValue = 100.0

    state = Range()
    state.CurrentValue = starting_value
    dragged = []

    class Rect:
        left = 0
        right = 240
        top = 0
        bottom = 20

        def width(self):
            return 240

        def height(self):
            return 20

    class Slider:
        element_info = SimpleNamespace(
            control_type="Slider",
            name="ThumbnailSizeSlider",
            automation_id="galleryThumbnailSizeSlider",
        )
        iface_range_value = state

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def rectangle(self):
            return Rect()

    class Window:
        def descendants(self):
            return [Slider()]

    class Mouse:
        @staticmethod
        def press(*, coords):
            dragged.append(("press", coords))

        @staticmethod
        def move(*, coords):
            dragged.append(("move", coords))

        @staticmethod
        def release(*, coords):
            dragged.append(("release", coords))
            state.CurrentValue = 25 + (coords[0] - 12) * 225 / 216

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    monkeypatch.setattr(benchmark.time, "sleep", lambda value: None)

    actions = []
    benchmark._move_thumbnail_slider(Window(), actions)
    assert len(dragged) == mouse_events
    assert abs(state.CurrentValue - starting_value) <= 5
    assert [action["target"] for action in actions] == expected_targets
    assert all(action["action"] == "thumbnail-slider-drag" for action in actions)
    assert all(action["verified"] is True for action in actions)
    assert abs(actions[1]["after"] - actions[0]["after"]) > 100


def test_failed_slider_does_not_abort_next_step(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=object()))
    record = {"actions": [], "warnings": []}
    assert not benchmark._attempt(
        record,
        "cycle-1-slider",
        lambda: benchmark._move_thumbnail_slider(
            SimpleNamespace(descendants=lambda: []),
            record["actions"],
        ),
    )
    assert benchmark._attempt(record, "cycle-1-list", lambda: record["actions"].append({"action": "List"}))
    assert len(record["warnings"]) == 1
    assert record["actions"] == [{"action": "List"}]


def test_scroll_intent_parser_requires_a_real_qt_wheel_event(tmp_path):
    from tools.ui.windows_gallery_benchmark import _wheel_intents

    _write_jsonl_session(
        tmp_path,
        [
            (
                "gallery.scroll_intent",
                {
                    "direction": direction,
                    "source": source,
                    "offset": 0,
                    "mounted": 80,
                },
                "measurement",
                None,
            )
            for direction, source in (
                ("forward", "wheel"),
                ("backward", "wheel"),
                ("forward", "drag"),
            )
        ],
    )
    events = _wheel_intents(tmp_path)
    assert len(events) == 2
    assert {event[2] for event in events} == {"forward", "backward"}


class _HeaderNode:
    """Minimal UIA tree supporting named Qt groups and checkable controls."""

    def __init__(self, name, role="Group", *, automation_id="", children=(), visible=True, enabled=True):
        self.element_info = SimpleNamespace(name=name, control_type=role, automation_id=automation_id)
        self.children = list(children)
        self.visible = visible
        self.enabled = enabled
        self.clicks = 0

    def descendants(self):
        return list(self.children)

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def click_input(self):
        self.clicks += 1


def test_recorder_header_scopes_detail_checkbox_without_ambiguous_names():
    from tools.ui.windows_gallery_benchmark import _click_gallery_mode

    detail = _HeaderNode("Detail", "CheckBox")
    unrelated = _HeaderNode("Detail", "CheckBox")
    header = _HeaderNode("HeaderScrollContents", children=(detail,))
    window = SimpleNamespace(descendants=lambda: [unrelated, header, detail])
    actions = []
    _click_gallery_mode(window, "Detail", actions)
    assert detail.clicks == 1
    assert unrelated.clicks == 0
    assert actions[0]["uia_target"] == "HeaderScrollContents/Detail/CheckBox"


def test_gallery_mode_uses_unique_qt_automation_id_when_header_not_exposed():
    from tools.ui.windows_gallery_benchmark import _gallery_mode_button

    target = _HeaderNode("Detail", "CheckBox", automation_id="galleryDetailButton")
    unrelated = _HeaderNode("Detail", "CheckBox")
    window = SimpleNamespace(descendants=lambda: [unrelated, target])
    assert _gallery_mode_button(window, "Detail", timeout=1) is target


def test_ambiguous_checkbox_within_header_fails_closed():
    from tools.ui.windows_gallery_benchmark import _gallery_mode_button

    header = _HeaderNode(
        "HeaderScrollContents",
        children=(_HeaderNode("Detail", "CheckBox"), _HeaderNode("Detail", "CheckBox")),
    )
    window = SimpleNamespace(descendants=lambda: [header])
    with pytest.raises(RuntimeError, match="Ambiguous Detail CheckBox"):
        _gallery_mode_button(window, "Detail", timeout=1)


def test_scroll_bursts_target_gallery_instead_of_window_center(monkeypatch, tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark

    events = []
    rect = SimpleNamespace(left=500, top=150, width=lambda: 400, height=lambda: 500)

    class Gallery(_HeaderNode):
        def rectangle(self):
            return rect

    grid = Gallery("gallery", automation_id="galleryGridScroll")
    window = SimpleNamespace(
        descendants=lambda: [grid],
        rectangle=lambda: SimpleNamespace(left=0, top=0, width=lambda: 1000, height=lambda: 800),
    )

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            events.append((coords, wheel_dist))

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    monkeypatch.setattr(benchmark, "_wheel_intents", lambda directory: set())
    monkeypatch.setattr(benchmark.time, "sleep", lambda seconds: None)
    actions = []
    benchmark._scroll(window, -1, actions, tmp_path, mode="grid", notches=96)
    assert len(events) == 8
    assert all(coords == (700, 400) and wheel_dist == -12 for coords, wheel_dist in events)
    assert actions[0]["notches"] == 96
    assert actions[0]["wheel_target"] == "galleryGridScroll"
    assert actions[0]["scroll_distance_verified"] is False
    assert actions[0]["wheel_intent_observed"] is False


def test_scroll_rejects_invalid_direction_or_huge_wheel_count(tmp_path):
    from tools.ui.windows_gallery_benchmark import _scroll

    with pytest.raises(ValueError, match="direction"):
        _scroll(object(), 0, [], tmp_path, mode="grid")
    with pytest.raises(ValueError, match="notches"):
        _scroll(object(), -1, [], tmp_path, mode="grid", notches=500)


def test_scroll_target_falls_back_to_gallery_panel_not_toolbar():
    from tools.ui.windows_gallery_benchmark import _gallery_wheel_point

    panel_rect = SimpleNamespace(
        left=300,
        top=100,
        width=lambda: 800,
        height=lambda: 600,
    )

    class Panel(_HeaderNode):
        def rectangle(self):
            return panel_rect

    panel = Panel("GalleryDetailPage")
    window = SimpleNamespace(
        descendants=lambda: [panel],
        rectangle=lambda: SimpleNamespace(left=0, top=0, width=lambda: 1200, height=lambda: 900),
    )
    coords, source = _gallery_wheel_point(window, "grid")
    assert source == "GalleryDetailPage-fallback"
    assert coords == (716, 532)


def test_default_wheel_sweep_doubles_previous_96_notches(monkeypatch, tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark

    events = []

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            events.append(wheel_dist)

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    monkeypatch.setattr(benchmark, "_gallery_wheel_point", lambda window, mode: ((600, 400), mode))
    monkeypatch.setattr(benchmark, "_wheel_intents", lambda directory: set())
    monkeypatch.setattr(benchmark.time, "sleep", lambda seconds: None)

    actions = []
    benchmark._scroll(object(), -1, actions, tmp_path, mode="grid")
    assert events == [-12] * 16
    assert actions[0]["notches"] == 192
    assert actions[0]["bursts"] == 16


def test_fullscreen_is_exercised_on_grid_before_list_or_detail(tmp_path, monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark

    active_mode = {"name": ""}
    sequence = []

    class Window:
        def wait(self, state, *, timeout):
            assert state == "exists visible enabled"

        def set_focus(self):
            pass

        def close(self):
            sequence.append("closed")

    window = Window()

    class Desktop:
        def __init__(self, *, backend):
            assert backend == "uia"

        def window(self, **criteria):
            assert criteria["process"] == 9002
            return window

    class Button:
        def click_input(self):
            sequence.append("images")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=Desktop))
    monkeypatch.setitem(sys.modules, "pywinauto.timings", SimpleNamespace(TimeoutError=TimeoutError))
    monkeypatch.setattr(benchmark, "_await_app_pid", lambda *a: 9002)
    monkeypatch.setattr(benchmark, "_button", lambda *a, **kw: Button())
    monkeypatch.setattr(benchmark, "_await_query", lambda *a, **kw: 20)
    monkeypatch.setattr(benchmark, "_reset_gallery_sort", lambda *a, **kw: None)
    monkeypatch.setattr(benchmark, "_click_page", lambda window, page, actions: sequence.append(f"page-{page}"))
    monkeypatch.setattr(benchmark, "_move_thumbnail_slider", lambda window, actions: sequence.append("slider"))
    monkeypatch.setattr(
        benchmark,
        "_scroll",
        lambda window, direction, actions, directory, **kwargs: sequence.append(
            f"{kwargs['mode']}-scroll-{'down' if direction < 0 else 'up'}"
        ),
    )

    def switch_mode(window, mode, actions):
        active_mode["name"] = mode
        sequence.append(mode)

    def fullscreen(window, actions):
        assert active_mode["name"] == "Grid"
        sequence.append("fullscreen-on-grid")

    monkeypatch.setattr(benchmark, "_click_gallery_mode", switch_mode)
    monkeypatch.setattr(benchmark, "_toggle_fullscreen", fullscreen)
    monkeypatch.setattr(benchmark.time, "sleep", lambda seconds: None)

    record = {"actions": [], "warnings": []}
    proc = SimpleNamespace(pid=9001)
    assert benchmark._drive(proc, tmp_path, record, cycles=1, timeout=2) == 20
    assert record["warnings"] == []
    assert sequence.count("fullscreen-on-grid") == 1
    assert sequence.index("slider") < sequence.index("fullscreen-on-grid") < sequence.index("List")
    assert sequence.index("fullscreen-on-grid") < sequence.index("Detail")
    assert sequence[-1] == "closed"


@pytest.mark.parametrize(
    ("ml", "mc", "effective"),
    [(20, 50, 40), (20, 100, 100), (50, 150, 150), (100, 300, 300), (200, 600, 600), (300, 1000, 900)],
)
def test_grid_matrix_resolves_actual_batch_rounding(ml, mc, effective):
    from tools.ui.windows_gallery_benchmark import parse_grid_matrix

    assert parse_grid_matrix([f"{ml}:{mc}"]) == [(ml, mc, effective)]


def test_grid_matrix_default_cases_are_bounded_and_distinct():
    from tools.ui.windows_gallery_benchmark import DEFAULT_GRID_MATRIX, parse_grid_matrix

    default = parse_grid_matrix([])
    assert [(ml, mc) for ml, mc, _effective in default] == list(DEFAULT_GRID_MATRIX)
    assert len(default) == len(set(default))
    assert default[0] == (20, 50, 40)
    assert default[-1] == (300, 1000, 900)


@pytest.mark.parametrize(
    "specs",
    [["20"], ["20/50"], ["0:50"], ["20:0"], ["301:1000"], ["300:1001"], ["40:20"], ["20:50", "20:50"], ["20:50"] * 17],
)
def test_grid_matrix_rejects_invalid_or_unsafe_cases(specs):
    from tools.ui.windows_gallery_benchmark import parse_grid_matrix

    with pytest.raises(ValueError):
        parse_grid_matrix(specs)


def test_page_one_is_physically_clicked_even_if_checked(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark

    calls = []

    class Page:
        def get_toggle_state(self):
            return 1

        def click_input(self):
            calls.append("clicked")

    monkeypatch.setattr(benchmark, "_button", lambda *a, **k: Page())
    actions = []
    benchmark._click_grid_page_one(object(), actions, at="start")
    benchmark._click_grid_page_one(object(), actions, at="end")
    assert calls == ["clicked", "clicked"]
    assert [item["action"] for item in actions] == ["click-page-one-start", "click-page-one-end"]
    assert all(item["verified"] for item in actions)


def test_grid_matrix_does_not_wait_for_query_or_open_other_views(monkeypatch, tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark

    trace = []

    class Rect:
        left = 0
        top = 0

        def width(self):
            return 1280

        def height(self):
            return 800

    class FakeWindow:
        def wait(self, state, *, timeout):
            assert state == "exists visible enabled"

        def set_focus(self):
            pass

        def rectangle(self):
            return Rect()

        def close(self):
            trace.append("close")

    class FakeDesktop:
        def __init__(self, *, backend):
            assert backend == "uia"

        def window(self, **kw):
            assert kw["process"] == 9876
            return FakeWindow()

    class FakeButton:
        def click_input(self):
            trace.append("images-or-page")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=FakeDesktop))
    monkeypatch.setitem(sys.modules, "pywinauto.timings", SimpleNamespace(TimeoutError=TimeoutError))
    # The matrix physically hovers a numbered chip, then the Grid viewport.
    mouse_moves = []
    sys.modules["pywinauto"].mouse = SimpleNamespace(move=lambda *, coords: mouse_moves.append(coords))
    monkeypatch.setattr(
        benchmark, "_grid_batch_button",
        lambda *_a: SimpleNamespace(
            rectangle=lambda: SimpleNamespace(left=10, right=50, top=10, bottom=34),
            element_info=SimpleNamespace(name="Jump to image batch 1"),
        ),
    )
    monkeypatch.setattr(benchmark, "_gallery_wheel_point", lambda *_a: ((300, 400), "grid"))
    monkeypatch.setattr(benchmark, "_await_app_pid", lambda *a: 9876)
    monkeypatch.setattr(benchmark, "_button", lambda *a, **kw: FakeButton())
    monkeypatch.setattr(
        benchmark, "_await_query", lambda *a, **kw: pytest.fail("Grid matrix must not await query_done")
    )
    monkeypatch.setattr(benchmark, "_read_query_count", lambda *a: 1000)
    monkeypatch.setattr(benchmark, "_reset_gallery_sort", lambda *a, **kw: pytest.fail("Sort must not open"))
    monkeypatch.setattr(benchmark, "_click_page", lambda *a, **kw: pytest.fail("Only page 1 is allowed"))
    monkeypatch.setattr(benchmark, "_toggle_fullscreen", lambda *a: pytest.fail("No fullscreen"))
    monkeypatch.setattr(benchmark, "_move_thumbnail_slider", lambda *a: pytest.fail("No slider"))
    monkeypatch.setattr(benchmark, "_click_gallery_mode", lambda window, mode, actions: trace.append("view-" + mode))
    monkeypatch.setattr(
        benchmark, "_click_grid_page_one", lambda window, actions, *, at: trace.append("page-one-" + at)
    )
    monkeypatch.setattr(
        benchmark,
        "_wait_for_grid_page_one",
        lambda window, probe, actions, *, directory, timeout: trace.append("page-one-applied"),
    )
    monkeypatch.setattr(
        benchmark,
        "_seek_grid_card",
        lambda window, probe, actions, *, target, direction, **kwargs: trace.append(
            (
                "seek",
                direction,
                target,
                kwargs["wheel_point"],
                kwargs.get("action_name"),
            )
        ),
    )

    record = {"actions": [], "warnings": []}
    assert (
        benchmark._drive(
            SimpleNamespace(pid=7890, poll=lambda: None),
            tmp_path,
            record,
            cycles=1,
            timeout=2,
            scroll_notches=192,
            grid_limits=(300, 900),
        )
        == 1000
    )
    assert record["warnings"] == []
    assert record["grid_start_verified"] is True
    assert record["grid_sweep"]["requested_card"] == 300
    assert record["grid_sweep"]["pulse_notches"] == 12
    assert record["grid_sweep"]["position_verified"] is True
    assert [step[1:3] for step in trace if isinstance(step, tuple)] == [
        (-1, 300),
        (1, 1),
    ]
    assert "page-one-applied" in trace
    assert trace.index("page-one-applied") < next(
        i for i, step in enumerate(trace) if isinstance(step, tuple) and step[1] == -1
    )
    seek_events = [step for step in trace if isinstance(step, tuple)]
    assert seek_events[0][1:3] == (-1, 300)
    assert record["grid_start_verified"] is True
    assert trace[-2:] == ["page-one-end", "close"]
    assert set(value for value in trace if isinstance(value, str) and value.startswith("view-")) == {"view-Grid"}


def test_grid_matrix_samples_first_query_after_scrolls(monkeypatch, tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark

    events = []

    class Rect:
        left = 0
        top = 0

        def width(self):
            return 1280

        def height(self):
            return 800

    class Window:
        def wait(self, state, *, timeout):
            pass

        def set_focus(self):
            pass

        def rectangle(self):
            return Rect()

        def close(self):
            events.append("close")

    class Desktop:
        def __init__(self, *, backend):
            pass

        def window(self, **kw):
            return Window()

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=Desktop))
    monkeypatch.setitem(sys.modules, "pywinauto.timings", SimpleNamespace(TimeoutError=TimeoutError))
    # The matrix physically hovers a numbered chip, then the Grid viewport.
    mouse_moves = []
    sys.modules["pywinauto"].mouse = SimpleNamespace(move=lambda *, coords: mouse_moves.append(coords))
    monkeypatch.setattr(
        benchmark, "_grid_batch_button",
        lambda *_a: SimpleNamespace(
            rectangle=lambda: SimpleNamespace(left=10, right=50, top=10, bottom=34),
            element_info=SimpleNamespace(name="Jump to image batch 1"),
        ),
    )
    monkeypatch.setattr(benchmark, "_gallery_wheel_point", lambda *_a: ((300, 400), "grid"))
    monkeypatch.setattr(benchmark, "_await_app_pid", lambda *a: 9876)
    monkeypatch.setattr(benchmark, "_button", lambda *a, **kw: SimpleNamespace(click_input=lambda: None))
    monkeypatch.setattr(
        benchmark, "_await_query", lambda *a, **kw: pytest.fail("Initial query cannot block wheel input")
    )
    monkeypatch.setattr(benchmark, "_read_query_count", lambda *a: events.append("sample-query") or 100)
    monkeypatch.setattr(benchmark, "_click_gallery_mode", lambda *a: None)
    monkeypatch.setattr(benchmark, "_click_grid_page_one", lambda *a, **kw: None)
    monkeypatch.setattr(benchmark, "_wait_for_grid_page_one", lambda *a, **kw: None)
    monkeypatch.setattr(
        benchmark,
        "_seek_grid_card",
        lambda window, probe, actions, *, direction, **kw: events.append(
            "home" if kw.get("action_name") == "grid-home-start" else "down" if direction < 0 else "up"
        ),
    )
    record = {"actions": [], "warnings": []}
    assert (
        benchmark._drive(
            SimpleNamespace(pid=7890, poll=lambda: None),
            tmp_path,
            record,
            cycles=1,
            timeout=2,
            grid_limits=(300, 900),
        )
        == 100
    )
    assert [w["step"] for w in record["warnings"]] == ["insufficient-images-for-full-batch"]
    assert events == ["down", "up", "sample-query", "close"]


def test_launcher_passes_requested_batch_and_resolved_card_limit(tmp_path, monkeypatch):
    import dtflow.cli
    from tools.ui import gallery_perf_launch as launcher

    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    invocations = []
    monkeypatch.setattr(launcher, "apply_profile", lambda profile: None)
    monkeypatch.setattr(dtflow.cli, "main", lambda argv: invocations.append(argv) or 0)

    assert launcher.main(["--profile", str(profile), "--logs", str(tmp_path), "--ml", "300", "--mc", "1000"]) == 0
    command = invocations[0]
    assert command[command.index("-ml") + 1] == "300"
    assert command[command.index("-mc") + 1] == "900"
    assert command[command.index("-format") + 1] == "jpg"


@pytest.mark.parametrize(
    ("value", "result"),
    [
        ("1–20 of 1,000", (1, 20, 1000)),
        ("295-310 of 1000", (295, 310, 1000)),
        ("Visible image range", None),
        ("0–0 of 0", None),
        ("1–5 of 4", None),
    ],
)
def test_grid_progress_parses_actual_visible_range(value, result):
    from tools.ui.grid_scroll_probe import parse_visible_range

    observed = parse_visible_range(value)
    assert ((observed.first, observed.last, observed.total) if observed else None) == result


@pytest.mark.parametrize(
    "label",
    [
        "200 buffered",
        "Loading 200 more…",
        "200",
        "1–20 of 200  ·  200 buffered",
        "200 buffered 1–20 of 200",
        "Showing 1–20 of 200; 200 buffered.",
    ],
)
def test_grid_position_rejects_total_or_buffered_status(label):
    from tools.ui.grid_scroll_probe import parse_visible_range

    assert parse_visible_range(label) is None


def test_grid_target_not_reached_when_only_total_and_buffer_equal_target():
    from tools.ui.grid_scroll_probe import parse_visible_range

    position = parse_visible_range("1–20 of 200")
    assert position is not None
    assert position.total == 200
    assert not (position.first <= 200 <= position.last)


def test_grid_progress_reads_status_bar_by_stable_id(tmp_path):
    from tools.ui.grid_scroll_probe import GridProgressProbe

    state = {"first": 1}

    class Control:
        @property
        def element_info(self):
            return SimpleNamespace(
                name=f"{state['first']}/1000"
            )

        def exists(self, *, timeout):
            assert timeout == 0
            return True

    class Window:
        def __init__(self):
            self.lookups = []

        def child_window(self, **kwargs):
            self.lookups.append(kwargs)
            return Control()

    window = Window()
    probe = GridProgressProbe(window, tmp_path)
    assert probe.read().first == 1
    assert probe.read().source == "grid-footer-uia"
    state["first"] = 281
    assert probe.read().first == 281
    state["first"] = 1
    assert probe.read().first == 1
    assert window.lookups == [
        {"auto_id": "dtflowStatusGridFirstVisibleIndex", "control_type": "Text"}
    ]


def test_grid_progress_does_not_accept_old_text_log_fallback(tmp_path):
    from tools.ui.grid_scroll_probe import GridProgressProbe

    (tmp_path / "dtflow-123.log").write_text(
        "gallery_perf phase=benchmark_view first=1 last=20 total=1000 loaded=40\n",
        encoding="utf-8",
    )

    class Window:
        def child_window(self, **_kwargs):
            return SimpleNamespace(exists=lambda **_kw: False)

    assert GridProgressProbe(Window(), tmp_path).read() is None


def test_grid_seek_reaches_same_card_independently_of_batch(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    state = {"first": 1}
    sent = []

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            sent.append(wheel_dist)
            state["first"] = max(1, min(295, state["first"] - wheel_dist * 8))

    class Probe:
        def read(self):
            first = state["first"]
            return GridPosition(first, min(1000, first + 15), 1000, "uia")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    for _ml, _mc in ((20, 40), (300, 900)):
        state["first"] = 1
        actions = []
        down = benchmark._seek_grid_card(
            object(),
            Probe(),
            actions,
            target=300,
            direction=-1,
            timeout=3,
            wheel_point=((600, 400), "grid"),
        )
        assert down["target_reached"]
        assert down["first_after"] <= 300 <= down["last_after"]
        assert down["wheel_pulses"] > 0
        assert "wheel_input_ms" in down and "position_probe_ms" in down
        back = benchmark._seek_grid_card(
            object(),
            Probe(),
            actions,
            target=1,
            direction=1,
            timeout=3,
            wheel_point=((600, 400), "grid"),
        )
        assert back["first_after"] == 1
        assert back["target_reached"]
        assert len(actions) == 2
    assert sent


def test_grid_seek_does_not_dispatch_without_position_evidence(monkeypatch):
    import time

    from tools.ui import windows_gallery_benchmark as benchmark

    sent = []

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            sent.append(wheel_dist)

    class SlowProbe:
        def read(self):
            time.sleep(0.12)
            return None

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    actions = []
    with pytest.raises(TimeoutError, match="never exposed"):
        benchmark._seek_grid_card(
            object(),
            SlowProbe(),
            actions,
            target=300,
            direction=-1,
            timeout=0.14,
            wheel_point=((600, 400), "grid"),
        )
    assert sent == []
    assert actions[0]["wheel_pulses"] == 0
    assert actions[0]["first_wheel_ms"] is None


def test_grid_seek_reports_real_incomplete_journey(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            pass

    class Probe:
        def read(self):
            return GridPosition(1, 20, 1000, "pager-log")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    actions = []
    with pytest.raises(TimeoutError, match="did not reach"):
        benchmark._seek_grid_card(
            object(),
            Probe(),
            actions,
            target=300,
            direction=-1,
            timeout=0.08,
            wheel_point=((600, 400), "grid"),
        )
    assert actions[0]["target_reached"] is False
    assert actions[0]["first_after"] == 1
    assert actions[0]["wheel_pulses"] > 0


def test_grid_seek_rejects_undersized_catalogue(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    class Probe:
        def read(self):
            return GridPosition(1, 30, 250, "uia")

    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(mouse=SimpleNamespace(scroll=lambda **kw: None)),
    )
    with pytest.raises(ValueError, match="Only 250"):
        benchmark._seek_grid_card(
            object(),
            Probe(),
            [],
            target=300,
            direction=-1,
            timeout=1,
            wheel_point=((600, 400), "grid"),
        )


def test_grid_seek_overshoot_stops_without_correcting_direction(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    state = {"first": 1}
    movements = []

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            movements.append(wheel_dist)
            if wheel_dist < 0:
                state["first"] = 330

    class Probe:
        def read(self):
            first = state["first"]
            return GridPosition(first, first + 15, 833, "uia")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    result = benchmark._seek_grid_card(
        object(),
        Probe(),
        [],
        direction=-1,
        target=300,
        timeout=2,
        wheel_point=((600, 400), "grid"),
    )
    assert result["target_reached"]
    assert result["first_after"] >= 300
    assert result["target_tolerance"] == 0
    assert movements and all(movement < 0 for movement in movements)


def test_grid_home_corrects_stale_first_card_by_scrolling_up(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    state = {"first": 281}
    movements = []

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            movements.append(wheel_dist)
            state["first"] = max(1, state["first"] - max(0, wheel_dist) * 12)

    class Probe:
        def read(self):
            first = state["first"]
            return GridPosition(first, first + 14, 833, "uia")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    actions = []
    result = benchmark._seek_grid_card(
        object(),
        Probe(),
        actions,
        target=1,
        direction=1,
        timeout=3,
        pulse_notches=12,
        wheel_point=((600, 400), "grid"),
        action_name="grid-home-start",
    )
    assert result["target_reached"]
    assert result["first_after"] == 1
    assert result["action"] == "grid-home-start"
    assert movements and all(delta > 0 for delta in movements)
    assert all(item["action"] != "grid-seek-up" for item in actions)


def test_grid_home_does_not_accept_stale_position_281(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    inputs = []

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            inputs.append(wheel_dist)

    class Probe:
        def read(self):
            return GridPosition(281, 295, 833, "pager-log")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    actions = []
    with pytest.raises(TimeoutError, match="did not reach card 1"):
        benchmark._seek_grid_card(
            object(),
            Probe(),
            actions,
            target=1,
            direction=1,
            timeout=0.1,
            wheel_point=((600, 400), "grid"),
            action_name="grid-home-start",
        )
    assert actions[-1]["target_reached"] is False
    assert actions[-1]["first_after"] == 281
    assert inputs and all(delta > 0 for delta in inputs)


def test_large_batch_does_not_fake_target_from_batch_end(monkeypatch):
    """Regression: -ml 200 returned 1–200 of 833 and stopped after one pulse."""
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    state = {"first": 1}
    wheel = []

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            wheel.append(wheel_dist)
            # The initial visible end is 200, but position only advances after
            # real wheel messages. Simulate slow-ish continuous progress.
            if wheel_dist < 0:
                state["first"] = min(190, state["first"] + 35)
            else:
                state["first"] = max(1, state["first"] - 8)

    class Probe:
        def read(self):
            first = state["first"]
            last = 200 if first == 1 else min(833, first + 14)
            return GridPosition(first, last, 833, "pager-log")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    # The sender thread may dispatch the first wheel before the probe's
    # initial read. Verify the synthetic starting state explicitly.
    assert Probe().read().first == 1
    actions = []
    result = benchmark._seek_grid_card(
        object(),
        Probe(),
        actions,
        target=200,
        direction=-1,
        timeout=3,
        wheel_point=((600, 400), "grid"),
    )
    assert result["target_reached"]
    assert 180 <= result["first_after"] <= 220
    assert wheel[0] < 0
    assert result["first_before"] is not None
    assert result["first_before"] < 200
    assert len(wheel) > 1
    assert result["wheel_pulses"] > 1


def test_large_batch_still_fails_if_visible_first_never_advances(monkeypatch):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    sent = []

    class Mouse:
        @staticmethod
        def scroll(*, coords, wheel_dist):
            sent.append(wheel_dist)

    class Probe:
        def read(self):
            return GridPosition(1, 200, 833, "pager-log")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=Mouse))
    actions = []
    with pytest.raises(TimeoutError, match="did not reach card 200"):
        benchmark._seek_grid_card(
            object(),
            Probe(),
            actions,
            target=200,
            direction=-1,
            timeout=0.12,
            wheel_point=((600, 400), "grid"),
        )
    assert len(sent) >= 1
    assert actions[-1]["target_reached"] is False
    assert actions[-1]["first_after"] == 1
    assert actions[-1]["last_after"] == 200


def test_grid_start_clicks_page_one_and_verifies_live_range(monkeypatch, tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    events = []
    first = [281]

    class Chip:
        pass

    def find(_window, number):
        assert number == 1
        events.append("resolve-page-one")
        return Chip()

    def activate(_chip):
        events.append("activate-page-one")
        first[0] = 1
        return "uia-invoke"

    class Probe:
        def read(self):
            return GridPosition(first[0], first[0] + 19, 833, "uia")

    monkeypatch.setattr(benchmark, "_grid_batch_button", find)
    monkeypatch.setattr(benchmark, "_activate_grid_batch_chip", activate)
    actions = []
    benchmark._wait_for_grid_page_one(object(), Probe(), actions, directory=tmp_path, timeout=1)
    assert events == ["resolve-page-one", "activate-page-one"]
    assert actions[-1]["verified"] is True
    assert actions[-1]["click_attempts"] == 1
    assert actions[-1]["visible_first"] == 1


def test_grid_start_uses_page_one_without_sort_dropdown(monkeypatch, tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    class Window:
        def child_window(self, **_kwargs):
            pytest.fail("Grid matrix must not open the Sort dropdown")

    class Probe:
        def read(self):
            return GridPosition(1, 20, 833, "uia")

    monkeypatch.setattr(benchmark, "_grid_batch_button", lambda _window, page: object())
    monkeypatch.setattr(benchmark, "_activate_grid_batch_chip", lambda _chip: "mouse-click")
    actions = []
    benchmark._wait_for_grid_page_one(Window(), Probe(), actions, directory=tmp_path, timeout=1)
    assert actions[-1]["verified"] is True


def test_grid_start_rejects_unverified_page_one_click(monkeypatch, tmp_path):
    from tools.ui import windows_gallery_benchmark as benchmark
    from tools.ui.grid_scroll_probe import GridPosition

    attempts = []

    class Probe:
        def read(self):
            return GridPosition(281, 295, 833, "uia")

    monkeypatch.setattr(benchmark, "_grid_batch_button", lambda _window, page: object())
    monkeypatch.setattr(
        benchmark, "_activate_grid_batch_chip",
        lambda _chip: attempts.append("click") or "mouse-click",
    )
    actions = []
    with pytest.raises(TimeoutError, match="could not verify first visible image 1"):
        benchmark._wait_for_grid_page_one(object(), Probe(), actions, directory=tmp_path, timeout=0.12)
    assert attempts
    assert actions[-1]["verified"] is False
    assert actions[-1]["visible_first"] == 281

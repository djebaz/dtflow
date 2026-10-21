"""Queue view presentation (Qt-free): the vocabularies, control matrix and outcome wording."""

from datetime import UTC, datetime

import pytest

from dtflow.ui.queue_presenter import (
    QueueCountdown,
    concurrency_level,
    controls,
    elapsed_text,
    estimate_remaining,
    eta_text,
    has_started,
    item_rows,
    outcome_note,
    outcome_text,
    primary_action,
    status_chip,
    summarize,
)

ZERO = {s: 0 for s in ("pending", "running", "succeeded", "failed", "unknown", "save_failed", "cancelled")}


@pytest.mark.parametrize(
    "samples,running,pending,expected",
    [
        ([30], 1, 2, 90),
        ([10, 20, 900], 2, 4, 60),
        ([], 1, 2, None),
        ([True, None, -3, float("nan"), float("inf")], 1, 2, None),
        ([10], 0, 2, None),
        ([0], 1, 2, None),
    ],
)
def test_eta_uses_valid_successful_samples_and_observed_parallelism(samples, running, pending, expected):
    current = view("running", succeeded=1, running=running, pending=pending)
    current.update(successful_durations=samples, cap=4)
    assert estimate_remaining(current) == expected
    for status in ("paused", "cancelled", "finished"):
        assert eta_text(current | {"status": status}) == ""
    assert eta_text(current | {"owner_active": False}) == ""


def test_elapsed_and_eta_wording_disclose_estimation_and_mixed_settings():
    current = view("running", succeeded=1, running=1, pending=1)
    current.update(successful_durations=[30], started_at_utc="2026-10-20T12:00:00+00:00")
    assert eta_text(current) == "~1:00 left (estimate)"
    assert "rough for mixed settings" in eta_text(current | {"varied": {"steps": 2}})
    now = datetime(2026, 10, 20, 12, 2, 15, tzinfo=UTC)
    assert elapsed_text(current, now) == "Elapsed 2:15"
    assert elapsed_text(current | {"started_at_utc": None}, now) == ""
    assert elapsed_text(current | {"started_at_utc": "2026-10-20T12:00:00"}, now) == ""


def test_eta_counts_down_between_polls_and_rebases_on_settlement_or_resume():
    current = view("running", succeeded=1, running=1, pending=2)
    current["successful_durations"] = [30]
    clock = QueueCountdown()
    assert clock.remaining(current, 10) == 90
    assert clock.remaining(dict(current), 11) == 89  # an unchanged poll does not restart the estimate
    assert eta_text(current, remaining=clock.remaining(current, 12)) == "~1:28 left (estimate)"
    # A failed completion still reduces remaining work and rebases, without adding a duration sample.
    ended = current | {"summary": dict(current["summary"], failed=1, pending=1)}
    assert clock.remaining(ended, 20) == 60
    assert clock.remaining(ended, 21) == 59
    ended = ended | {"summary": dict(ended["summary"], succeeded=2, pending=0), "successful_durations": [30, 10]}
    assert clock.remaining(ended, 30) == 20
    assert clock.remaining(ended, 100) == 0  # expiry is not completion; real progress stays authoritative
    resumed = ended | {"resume_event_id": 42}
    assert clock.remaining(resumed, 101) == 20  # Pause and Resume happened between polls; counts did not change
    assert clock.remaining(dict(resumed), 102) == 19
    assert clock.remaining(ended | {"status": "paused"}, 101) is None
    assert clock.remaining(ended, 110) == 20


def view(status="paused", items=(), **counts):
    return {
        "queue_id": "q" * 32,
        "status": status,
        "backend": {"name": "fake", "endpoint": None},
        "summary": {**ZERO, **counts},
        "items": list(items),
    }


def item(state="pending", artifacts=(), failure=None):
    return {
        "item_id": "i" * 32,
        "position": 1,
        "state": state,
        "attempt_id": None,
        "seed": 3,
        "artifacts": list(artifacts),
        "failure": failure,
    }


@pytest.mark.parametrize(
    "status,start,pause,resume,cancel",
    [
        ("paused", True, False, False, True),  # a never-started queue is started, not resumed
        ("running", True, True, False, True),
        ("finished", False, False, False, False),
        ("cancelled", False, False, False, False),
    ],
)
def test_control_matrix_by_status(status, start, pause, resume, cancel):
    available = controls(view(status), dispatching_here=False)
    assert (available.start, available.pause, available.resume, available.cancel) == (start, pause, resume, cancel)
    assert not controls(view(status), dispatching_here=True).start  # this window already dispatches
    assert not controls(view(status), dispatching_here=False, generating=True).start  # the lock is in use
    assert controls(None, dispatching_here=False) == controls(None, dispatching_here=True)
    assert controls(view(status), dispatching_here=False, started=True).resume == (status == "paused")
    if status == "running":
        for owner_active in (True, False):
            current = view(status) | {"owner_active": owner_active}
            action = primary_action(current, available, started=True, owned=False)
            assert action.key == ("pause" if owner_active else "start")
            assert action.text == ("Pause" if owner_active else "Resume")
            local = controls(current, dispatching_here=True, started=True, owned=True)
            assert primary_action(current, local, started=True, owned=True).key == "pause"
        busy = controls(current, dispatching_here=False, generating=True)
        assert primary_action(current, busy, started=True, owned=False) is None


def test_unknown_is_a_warning_with_an_aborted_note_and_failed_stays_an_error():
    unknown, failed = item_rows(view(items=[item("unknown")]))[0], item_rows(view(items=[item("failed")]))[0]
    assert (unknown.level, failed.level) == ("warning", "error")
    assert unknown.state_text == "no result" and failed.state_text == "failed"
    level, text = outcome_note(unknown)
    assert level == "warning" and "most likely failed" in text and "does not block" in text
    assert outcome_note(failed) is None and outcome_note(None) is None


def test_rows_separate_outputs_from_metadata_and_summary_counts_settled():
    (row,) = item_rows(view(items=[item("succeeded", artifacts=["/o/a/image.png", "/o/a/metadata.json"])]))
    assert row.outputs == ("/o/a/image.png",) and row.metadata == "/o/a/metadata.json"
    summary = summarize(view("running", succeeded=2, failed=1, pending=1, running=1))
    assert (summary.settled, summary.total, summary.succeeded, summary.level) == (3, 5, 2, "ok")


def test_status_chip_and_concurrency_threshold():
    assert status_chip(view("running"), []) == ("ok", "Running")
    assert status_chip(view("queued"), ["q" * 32]) == ("secondary", "Up next 1")
    assert status_chip(view("finished", succeeded=1, unknown=1), []) == ("warning", "Finished")
    assert [concurrency_level(v) for v in (1, 2, 3, 4)] == ["normal", "normal", "elevated", "elevated"]


def test_a_dispatch_run_ends_with_the_outcome_that_happened():
    assert outcome_text(view("finished", succeeded=3)) == ("ok", "Finished", "3 of 3 succeeded.")
    level, _, detail = outcome_text(view("finished", succeeded=1, failed=1, unknown=1))
    assert level == "error" and detail == "1 of 3 succeeded; 1 failed, 1 no result."
    assert outcome_text(view("cancelled", succeeded=1, cancelled=2))[1:] == (
        "Cancelled",
        "2 item(s) not run; 1 succeeded.",
    )
    assert not has_started({"events": [{"kind": "created"}]}) and has_started({"events": [{"kind": "started"}]})

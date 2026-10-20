"""Queue view presentation (Qt-free): the vocabularies, control matrix and outcome wording."""

import pytest

from dtflow.ui.queue_presenter import (
    concurrency_level,
    controls,
    has_started,
    item_rows,
    outcome_note,
    outcome_text,
    ownership_badge,
    summarize,
)

ZERO = {s: 0 for s in ("pending", "running", "succeeded", "failed", "unknown", "save_failed", "cancelled")}


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


def test_badge_and_concurrency_threshold():
    assert ownership_badge(view("running"), dispatching_here=True) == "THIS WINDOW DISPATCHES"
    assert ownership_badge(view("running"), dispatching_here=False).startswith("RUNNING")
    assert ownership_badge(view("paused"), dispatching_here=False) == "NOT DISPATCHING"
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

"""Queue view presentation (Qt-free): the vocabularies, control matrix and uncertainty wording."""

import pytest

from dtflow.ui.queue_presenter import (
    concurrency_level,
    controls,
    item_rows,
    ownership_badge,
    summarize,
    uncertainty_text,
)

ZERO = {s: 0 for s in ("pending", "running", "succeeded", "failed", "unknown", "save_failed", "cancelled")}


def view(status="paused", items=(), blocked=False, **counts):
    return {
        "queue_id": "q" * 32,
        "status": status,
        "backend": {"name": "fake", "endpoint": None},
        "summary": {**ZERO, **counts},
        "blocked_by_uncertainty": blocked,
        "items": list(items),
    }


def item(state="pending", overridden=False, artifacts=(), failure=None):
    return {
        "item_id": "i" * 32,
        "position": 1,
        "state": state,
        "overridden": overridden,
        "attempt_id": None,
        "seed": 3,
        "artifacts": list(artifacts),
        "failure": failure,
    }


@pytest.mark.parametrize(
    "status,start,pause,resume,cancel",
    [
        ("paused", True, False, True, True),
        ("running", True, True, False, True),
        ("blocked", False, False, False, True),
        ("finished", False, False, False, False),
        ("cancelled", False, False, False, False),
    ],
)
def test_control_matrix_by_status(status, start, pause, resume, cancel):
    available = controls(view(status), dispatching_here=False)
    assert (available.start, available.pause, available.resume, available.cancel) == (start, pause, resume, cancel)
    assert not available.override
    assert not controls(view(status), dispatching_here=True).start  # this window already dispatches
    assert not controls(view(status), dispatching_here=False, generating=True).start  # the lock is in use
    assert controls(None, dispatching_here=False) == controls(None, dispatching_here=True)


def test_override_needs_an_unresolved_unknown_and_unknown_stays_distinct_from_failed():
    assert controls(view("blocked", [item("unknown")], blocked=True), dispatching_here=False).override
    assert not controls(view("running", [item("unknown", overridden=True)]), dispatching_here=False).override
    unknown, failed = item_rows(view(items=[item("unknown")]))[0], item_rows(view(items=[item("failed")]))[0]
    assert (unknown.level, failed.level) == ("unknown", "error")
    assert (
        uncertainty_text(unknown)[1].startswith("Outcome unknown")
        and "Resume does not clear" in uncertainty_text(unknown)[1]
    )
    acknowledged = item_rows(view(items=[item("unknown", overridden=True)]))[0]
    assert "never replayed" in uncertainty_text(acknowledged)[1] and acknowledged.state_text == "unknown (overridden)"


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

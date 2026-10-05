"""Queues page composition against the fake backend and real SQLite state (5B-O1 to O5 presentation).

Scheduling, reservation and no-replay semantics are owned by the Phase 3B queue tests; these check what the page shows
and which shared operation each action calls. Dispatch uses the same dispatcher the CLI runs.
"""

import asyncio
import json
import os
import sqlite3

import pytest

pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QAbstractItemModelTester  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.backends.fake import FakeBackend  # noqa: E402
from dtflow.core.models import ResolvedGenerationRequest  # noqa: E402
from dtflow.persistence.settings import load_cloud_concurrency  # noqa: E402
from dtflow.services import session  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.services.queue_session import create_queue, expand_items  # noqa: E402
from dtflow.ui.main_window import MainWindow  # noqa: E402
from dtflow.ui.queue_model import QueueItemModel  # noqa: E402
from dtflow.ui.queue_presenter import ItemRow  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path):
    shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=tmp_path / "state")
    qtbot.addWidget(shell.form)
    shell.form.backendCombo.setCurrentText("fake")
    shell.generation_page.promptEdit.setPlainText("a cat")
    shell.queue.confirm = lambda parent, title, text: True
    yield shell
    shell.runner.shutdown()


@pytest.fixture
def gated(monkeypatch):
    """Dispatch against a fake whose generations are held until the returned release() is called."""
    holder = {}

    def make(selection, **_):
        holder.setdefault("gate", asyncio.Event())
        return FakeBackend(gate=holder["gate"], scenario=selection.scenario)

    monkeypatch.setattr(session, "create_backend", make)
    return lambda window: window.runner._loop.call_soon_threadsafe(holder["gate"].set)


def state_text(window):
    return window.queue_page.queueStateLabel.text()


def create(window, qtbot, count=3, cap=4):
    page = window.queue_page
    page.queueCountSpinBox.setValue(count)
    page.concurrencySpinBox.setValue(cap)
    page.createQueueButton.click()
    qtbot.waitUntil(lambda: page.queueList.count() == 1 and "paused" in page.queueList.item(0).text(), timeout=5000)
    qtbot.waitUntil(lambda: page.queueItemsView.model().rowCount() == count, timeout=5000)


def states(window):
    model = window.queue_page.queueItemsView.model()
    return [model.data(model.index(row, 3)) for row in range(model.rowCount())]


def row(item_id, position, state, outputs=()):
    return ItemRow(item_id, position, state, False, None, position, tuple(outputs), None, None)


def test_model_keeps_order_and_updates_only_the_changed_row_out_of_order(qtbot):
    """5B-O1 / 5B-O5: rows are keyed by item, never re-sorted, and outputs stay attached to their own item."""
    model = QueueItemModel()
    QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    ids = ["a" * 32, "b" * 32, "c" * 32]
    model.apply(tuple(row(item, n, "pending") for n, item in enumerate(ids, 1)))
    changed = []
    model.dataChanged.connect(lambda top, bottom: changed.append((top.row(), bottom.row())))
    model.apply((row(ids[0], 1, "pending"), row(ids[1], 2, "pending"), row(ids[2], 3, "succeeded", ["/o/c.png"])))
    model.apply(
        (
            row(ids[0], 1, "succeeded", ["/o/a.png"]),
            row(ids[1], 2, "pending"),
            row(ids[2], 3, "succeeded", ["/o/c.png"]),
        )
    )
    assert changed == [(2, 2), (0, 0)]  # item 3 finished before item 1; nothing else was touched
    assert [model.row_at(i).item_id for i in range(3)] == ids and model.row_at(0).outputs == ("/o/a.png",)
    assert model.row_at(1).outputs == () and model.row_of(ids[2]) == 2


def test_create_start_and_complete_a_queue_with_stable_rows_and_outputs(window, qtbot, gated):
    page = window.queue_page
    assert page.emptyStateCard.isVisible() or not page.emptyStateCard.isHidden()
    create(window, qtbot, count=3)
    assert states(window) == ["pending"] * 3 and state_text(window).startswith("Paused")
    page.startQueueButton.click()
    assert window.queue.dispatching_here and page.ownershipBadgeLabel.text() == "THIS WINDOW DISPATCHES"
    assert not window.generation_page.generateButton.isEnabled()  # dispatch holds the state lock
    qtbot.waitUntil(lambda: "running" in states(window), timeout=5000)
    qtbot.wait(50)  # the GUI thread keeps processing while dispatch runs
    gated(window)
    qtbot.waitUntil(lambda: states(window) == ["succeeded"] * 3 and not window.queue.dispatching_here, timeout=10000)
    assert page.queueProgressBar.format() == "3 of 3 settled · 3 succeeded"
    assert state_text(window).startswith("Finished") and page.ownershipBadgeLabel.text() == "NOT DISPATCHING"
    assert window.generation_page.generateButton.isEnabled()
    page.queueItemsView.selectRow(1)
    assert "metadata.json" in page.itemOutputsLabel.text() and page.itemIdLabel.text()
    assert page.queueEventBrowser.toPlainText().count("\n") >= 1


def test_pause_resume_and_cancel_post_shared_controls_with_honest_wording(window, qtbot, gated):
    page = window.queue_page
    create(window, qtbot, count=3, cap=1)
    page.startQueueButton.click()
    qtbot.waitUntil(lambda: states(window)[:1] == ["running"], timeout=5000)
    page.pauseQueueButton.click()
    qtbot.waitUntil(lambda: state_text(window).startswith("Paused"), timeout=8000)
    gated(window)
    qtbot.waitUntil(lambda: states(window)[0] == "succeeded", timeout=8000)
    qtbot.wait(1200)
    assert states(window)[1:] == ["pending", "pending"]  # pause stopped refill; the active item completed
    page.resumeQueueButton.click()
    qtbot.waitUntil(lambda: states(window) == ["succeeded"] * 3, timeout=10000)

    create_second(window, qtbot)
    window.queue.confirm = lambda parent, title, text: False
    page.cancelQueueButton.click()
    qtbot.wait(300)
    assert "cancel" not in cancel_controls(window)  # declined: nothing was posted
    window.queue.confirm = lambda parent, title, text: True
    page.cancelQueueButton.click()
    qtbot.waitUntil(lambda: "cancel" in cancel_controls(window), timeout=5000)


def create_second(window, qtbot):
    page = window.queue_page
    page.queueCountSpinBox.setValue(2)
    page.createQueueButton.click()
    qtbot.waitUntil(lambda: page.queueList.count() == 2, timeout=5000)
    page.queueList.setCurrentRow(1)
    qtbot.waitUntil(lambda: states(window) == ["pending", "pending"], timeout=5000)


def cancel_controls(window):
    with sqlite3.connect(window.state_dir / "attempts.sqlite3") as db:
        return [action for (action,) in db.execute("SELECT action FROM queue_controls")]


def test_cancel_stops_waiting_locally_and_leaves_unknown_not_a_remote_claim(window, qtbot, gated):
    page = window.queue_page
    create(window, qtbot, count=3, cap=1)
    page.startQueueButton.click()
    qtbot.waitUntil(lambda: states(window)[:1] == ["running"], timeout=5000)
    page.cancelQueueButton.click()
    qtbot.waitUntil(
        lambda: state_text(window).startswith("Cancelled") and not window.queue.dispatching_here, timeout=10000
    )
    assert states(window) == ["unknown", "cancelled", "cancelled"]  # the active attempt may have reached the server
    page.queueItemsView.selectRow(0)
    assert page.uncertaintyLabel.text().startswith("Outcome unknown")
    assert "cancelled on the server" not in page.queueMessageLabel.text().lower()


def test_unresolved_reservation_blocks_and_override_never_replays(window, qtbot, tmp_path):
    state = window.state_dir
    window.runner.submit(
        create_queue(
            state,
            {"name": "fake", "endpoint": None, "scenario": "lost"},
            expand_items(ResolvedGenerationRequest("b", "fixture", "fake-fixture", 0), 2),
            1,
        ),
        lambda *_: None,
    )
    page = window.queue_page
    qtbot.waitUntil(lambda: (page.refreshQueuesButton.click() or True) and page.queueList.count() == 1, timeout=5000)
    page.queueList.setCurrentRow(0)
    page.startQueueButton.click()
    qtbot.waitUntil(
        lambda: state_text(window).startswith("Blocked") and not window.queue.dispatching_here, timeout=10000
    )
    assert states(window) == ["unknown", "pending"]
    assert page.overrideReservationButton.isEnabled() and not page.resumeQueueButton.isEnabled()
    assert not page.startQueueButton.isEnabled()  # blocked: Start/Resume cannot clear uncertainty
    page.queueItemsView.selectRow(0)
    assert "Resume does not clear it" in page.uncertaintyLabel.text()

    window.queue.confirm = lambda parent, title, text: False
    page.overrideReservationButton.click()
    qtbot.wait(200)
    assert "override" not in cancel_controls(window)  # declined: nothing recorded
    window.queue.confirm = lambda parent, title, text: True
    page.overrideReservationButton.click()
    qtbot.waitUntil(lambda: "override" in cancel_controls(window), timeout=5000)
    qtbot.waitUntil(lambda: page.startQueueButton.isEnabled(), timeout=5000)
    page.startQueueButton.click()  # the next start applies the override, then runs the pending item (also "lost")
    qtbot.waitUntil(lambda: states(window)[1] == "unknown" and not window.queue.dispatching_here, timeout=10000)
    assert states(window)[0] == "unknown (overridden)"
    with sqlite3.connect(state / "attempts.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2  # one attempt per item, none replayed


def test_restart_restores_a_reserved_item_as_pending_in_a_paused_queue(window, qtbot):
    from dtflow.persistence.store import SQLiteAttempts

    async def crash_then_reopen(state):
        queue_id, _ = await create_queue(
            state,
            {"name": "fake", "endpoint": None},
            expand_items(ResolvedGenerationRequest("b", "x", "fake-fixture", 0), 2),
        )
        store = SQLiteAttempts(state)
        await store.open()
        await store.set_running(queue_id)
        await store.claim_next(queue_id, 4)  # reserved, then the process "dies" before an attempt exists
        await store.aclose()
        reopened = SQLiteAttempts(state)
        await reopened.open()  # the next owner reconciles: reservation back to pending, queue paused
        await reopened.aclose()

    done = []
    window.runner.submit(crash_then_reopen(window.state_dir), lambda *_: done.append(1))
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    page = window.queue_page
    page.refreshQueuesButton.click()
    qtbot.waitUntil(lambda: page.queueList.count() == 1, timeout=5000)
    page.queueList.setCurrentRow(0)
    qtbot.waitUntil(lambda: states(window) == ["pending", "pending"], timeout=5000)
    assert state_text(window).startswith("Paused") and page.startQueueButton.isEnabled()


def test_concurrency_threshold_color_and_shared_setting(window, qtbot):
    page = window.queue_page
    page.concurrencySpinBox.setValue(3)
    assert page.concurrencySpinBox.property("level") == "elevated"
    page.concurrencySpinBox.setValue(2)
    assert page.concurrencySpinBox.property("level") == "normal"
    assert not (window.state_dir / "settings.json").exists()  # fake/local never write the shared cloud maximum
    window.form.backendCombo.setCurrentText("cloud")
    page.concurrencySpinBox.setValue(3)
    qtbot.waitUntil(lambda: (window.state_dir / "settings.json").exists(), timeout=5000)
    assert load_cloud_concurrency(window.state_dir) == 3
    assert "memory budget" in page.concurrencyHintLabel.text()


def test_closing_during_dispatch_asks_first_and_records_the_local_stop(window, qtbot, gated):
    page = window.queue_page
    create(window, qtbot, count=2, cap=1)
    page.startQueueButton.click()
    qtbot.waitUntil(lambda: states(window)[:1] == ["running"], timeout=5000)
    window.queue.confirm = lambda parent, title, text: False
    window.form.close()
    assert window.queue.dispatching_here  # declined: still dispatching
    window.queue.confirm = lambda parent, title, text: True
    window.form.close()
    window.runner.shutdown()
    with sqlite3.connect(window.state_dir / "attempts.sqlite3") as db:
        rows = db.execute("SELECT state, remote_outcome FROM attempts").fetchall()
    assert [outcome for _, outcome in rows] == ["unknown"]  # recorded as unknown, never claimed cancelled
    assert rows[0][0] in ("possibly_submitted", "unknown")


def test_a_busy_state_directory_reports_another_owner(window, qtbot, gated):
    from dtflow.persistence.store import SQLiteAttempts

    page = window.queue_page
    create(window, qtbot, count=1)
    owner = SQLiteAttempts(window.state_dir)
    opened = []
    window.runner.submit(owner.open(), lambda r, f: opened.append(f))
    qtbot.waitUntil(lambda: bool(opened), timeout=3000)
    page.startQueueButton.click()
    qtbot.waitUntil(lambda: not window.queue.dispatching_here, timeout=5000)
    assert page.queueMessageLabel.text().startswith("Dispatch is owned by another process")
    assert states(window) == ["pending"]
    window.runner.submit(owner.aclose(), lambda *_: None)
    qtbot.wait(100)


def test_plan_area_dry_run_and_create_use_the_shared_planner(window, qtbot):
    """6 wiring: widget values reach the planner, and the created queue matches the dry-run table."""
    page = window.queue_page
    page.planBox.setChecked(True)
    page.planPromptsEdit.setPlainText("a cat\nb dog")
    page.planStepsEdit.setText("4; 8")
    assert page.planCountLabel.text() == "4 items" and not page.queueCountSpinBox.isEnabled()
    page.planDryRunButton.click()
    model = page.planTableView.model()
    table = [[model.data(model.index(r, c)) for c in range(model.columnCount())] for r in range(model.rowCount())]
    assert table[0] == ["1", "a cat", "4", "0"] and table[3] == ["4", "b dog", "8", "0"]
    page.planStepsEdit.setText("4; 500")
    page.createQueueButton.click()
    assert page.planStepsEdit.property("invalid") is True and page.planCountLabel.text().startswith("Item 2: steps")
    assert page.queueList.count() == 0  # a rejected plan creates nothing
    page.planStepsEdit.setText("4; 8")
    page.createQueueButton.click()
    qtbot.waitUntil(lambda: page.queueList.count() == 1 and page.queueItemsView.model().rowCount() == 4, timeout=5000)
    assert page.planStepsEdit.property("invalid") is False
    with sqlite3.connect(window.queue._state / "attempts.sqlite3") as db:
        stored = [json.loads(raw) for (raw,) in db.execute("SELECT request_json FROM queue_items ORDER BY position")]
    assert [[str(r["prompt"]), str(r["steps"]), str(r["seed"])] for r in stored] == [
        [row[1], row[2], row[3]] for row in table
    ]

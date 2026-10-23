"""Queues page composition against the fake backend and real SQLite state (5B-O1 to O5 presentation).

Scheduling, reservation and no-replay semantics are owned by the Phase 3B queue tests; these check what the page shows
and which shared operation each action calls. Dispatch uses the same dispatcher the CLI runs.
"""

import functools
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QAbstractItemModelTester  # noqa: E402
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from support.fake_vault import Vault  # noqa: E402

from dtflow.core.models import ResolvedGenerationRequest  # noqa: E402
from dtflow.persistence.settings import load_cloud_concurrency  # noqa: E402
from dtflow.services import queue_session as queue_service  # noqa: E402
from dtflow.services.credentials import SavedCredentials  # noqa: E402
from dtflow.services.queue import QueueDispatcher  # noqa: E402
from dtflow.services.queue_session import create_queue, expand_items  # noqa: E402
from dtflow.ui import queue_page as queue_ui  # noqa: E402
from dtflow.ui import queue_thumbnails as thumbnail_ui  # noqa: E402
from dtflow.ui.main_window import MainWindow  # noqa: E402
from dtflow.ui.queue_model import STATUS_COLUMN, QueueItemModel  # noqa: E402
from dtflow.ui.queue_presenter import ItemRow  # noqa: E402


@pytest.mark.parametrize("lookup", ("excluded", "different-path", "unavailable"))
def test_queue_thumbnail_never_falls_back_to_an_unverified_path(tmp_path, monkeypatch, lookup):
    calls = []

    def record(*_):
        if lookup == "unavailable":
            raise OSError("Live database unavailable")
        return {"path": "another-output.png"} if lookup == "different-path" else None

    monkeypatch.setattr(thumbnail_ui.gallery_storage, "get_record", record)
    monkeypatch.setattr(thumbnail_ui, "_image", lambda *args, **kwargs: calls.append(args))
    if lookup == "unavailable":
        with pytest.raises(OSError):
            thumbnail_ui._decode("historical-output.png", "attempt:0", tmp_path)
    else:
        assert thumbnail_ui._decode("historical-output.png", "attempt:0", tmp_path)[0] is None
    assert not calls


def test_queue_thumbnail_revalidates_cached_availability_and_owns_shutdown(qtbot, tmp_path, monkeypatch):
    class Runner(QObject):
        shutdown_started = Signal()
        closed = False

        def __init__(self):
            super().__init__()
            self.jobs = []

        def submit(self, coroutine, callback):
            coroutine.close()  # controlled completions, not a real decode/thread
            self.jobs.append(callback)

    runner = Runner()
    thumbnails = thumbnail_ui.QueueThumbnails(runner, tmp_path)
    observations = []
    monkeypatch.setattr(thumbnail_ui, "dev_enabled", lambda: True)
    monkeypatch.setattr(thumbnail_ui, "measurement", lambda _log, name, **data: observations.append((name, data)))
    now = [0.0]
    monkeypatch.setattr(thumbnail_ui, "monotonic", lambda: now[0])
    thumbnails._diagnostic_since = 0.0
    thumbnails._expiry.start()
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0)
    assert thumbnails.pixmap("output.png", identity="a:0") is None
    runner.jobs[0]((image, ""), None)
    assert thumbnails.available("output.png", "a:0")
    now[0] = 4.0
    thumbnails._expire()
    assert not thumbnails.available("output.png", "a:0")
    runner.jobs[1]((None, "Saved image is no longer available"), None)
    assert thumbnails.is_failed("output.png", identity="a:0")
    assert not observations  # repeated completions/polls stay aggregated
    assert thumbnails._expiry.parent() is thumbnails
    thumbnails.clear()
    assert len(observations) == 1 and observations[0][0] == "queue.thumbnail_summary"
    assert observations[0][1]["available"] == observations[0][1]["unavailable"] == 1
    assert "output.png" not in repr(observations) and "a:0" not in repr(observations)
    thumbnails.pixmap("busy.png", identity="b:0")
    thumbnails.clear()  # an obsolete decode still occupies a slot until completion
    assert thumbnails._active == 1
    runner.closed = True
    runner.shutdown_started.emit()
    count = len(runner.jobs)
    runner.jobs[-1]((image, ""), None)
    thumbnails._expire()
    assert not thumbnails._expiry.isActive() and not thumbnails.available("busy.png", "b:0")
    assert len(runner.jobs) == count


def test_item_inspector_uses_shared_availability_and_hides_old_evidence_in_composer(window, qtbot, monkeypatch):
    window.form.show()
    window.form.queueNavigationButton.click()
    done = []
    window.runner.submit(
        create_queue(
            window.state_dir,
            {"name": "fake", "endpoint": None},
            expand_items(ResolvedGenerationRequest("b", "fixture", "fake-fixture", 0), 1),
        ),
        lambda *_: done.append(True),
    )
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    page, queue = window.queue_page, window.queue
    page.refreshQueuesButton.click()
    qtbot.waitUntil(lambda: len(queues(window)) == 1, timeout=5000)
    page.queueList.setCurrentItem(queues(window)[0])
    qtbot.waitUntil(lambda: queue.model.rowCount() == 1, timeout=5000)
    recorded = ItemRow("mapped-item", 1, "succeeded", "mapped-attempt", 123, (r"C:\recorded\final.png",), None, None)
    from dataclasses import replace

    queue.model.apply((replace(recorded, gallery_identity="mapped-attempt:0"),))
    page.queueItemsView.setCurrentIndex(queue.model.index(0, 0))
    monkeypatch.setattr(queue.model.thumbnails, "available", lambda path, identity: identity == "mapped-attempt:0")
    opened = []
    queue.set_images_hook(opened.append)
    queue._show_item()
    assert page.itemOpenImagesButton.isEnabled()  # raw recorded Windows path need not exist in this runtime
    page.itemOpenImagesButton.click()
    assert opened == ["mapped-attempt:0"]
    assert not page.itemDetailsBox.isHidden()
    queue.open_composer()
    assert page.itemDetailsBox.isHidden() and page.itemIdentityBox.isHidden()
    assert not page.composerSummaryBox.isHidden()


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    # Production polls (1 s page reads, 0.5 s dispatcher control checks) only set how soon a change becomes visible;
    # shorter intervals keep these tests from waiting on wall-clock polls without changing what is asserted.
    monkeypatch.setattr(queue_ui, "POLL_MS", 100)
    monkeypatch.setattr(queue_service, "QueueDispatcher", functools.partial(QueueDispatcher, poll=0.05))
    shell = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=tmp_path / "state")
    qtbot.addWidget(shell.form)
    shell.form.backendCombo.setCurrentText("fake")
    shell.generation_page.promptEdit.setPlainText("a cat")
    yield shell
    shell.runner.shutdown()


def queues(window):
    return window.queue.queue_items()  # list rows without the group headings


def state_text(window):
    return window.queue_page.queueStateLabel.text()


def states(window):
    model = window.queue_page.queueItemsView.model()
    return [model.data(model.index(row, STATUS_COLUMN)) for row in range(model.rowCount())]


def row(item_id, position, state, outputs=()):
    return ItemRow(item_id, position, state, None, position, tuple(outputs), None, None)


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


def test_unknown_outcome_does_not_block_and_is_never_replayed(window, qtbot, tmp_path):
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
    qtbot.waitUntil(lambda: (page.refreshQueuesButton.click() or True) and len(queues(window)) == 1, timeout=5000)
    page.queueList.setCurrentItem(queues(window)[0])
    page.queuePrimaryButton.click()
    qtbot.waitUntil(lambda: not window.queue.dispatching_here and states(window) == ["no result"] * 2, timeout=10000)
    assert state_text(window).startswith("Finished")
    page.queueItemsView.selectRow(0)
    assert "most likely failed" in page.uncertaintyLabel.text()
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
    qtbot.waitUntil(lambda: len(queues(window)) == 1, timeout=5000)
    page.queueList.setCurrentItem(queues(window)[0])
    qtbot.waitUntil(lambda: states(window) == ["pending", "pending"], timeout=5000)
    assert state_text(window).startswith("Paused") and page.queuePrimaryButton.isEnabled()


def test_concurrency_threshold_color_and_shared_setting(window, qtbot):
    page = window.queue_page
    assert page.newQueueCapSpinBox.maximum() == page.concurrencySpinBox.maximum() == 10
    page.newQueueCapSpinBox.setValue(3)
    assert page.newQueueCapSpinBox.property("level") == "elevated"
    page.newQueueCapSpinBox.setValue(2)
    assert page.newQueueCapSpinBox.property("level") == "normal"
    assert not (window.state_dir / "settings.json").exists()  # fake/local never write the shared cloud maximum
    window.form.backendCombo.setCurrentText("cloud")
    page.newQueueCapSpinBox.setValue(3)
    qtbot.waitUntil(lambda: (window.state_dir / "settings.json").exists(), timeout=5000)
    assert load_cloud_concurrency(window.state_dir) == 3
    page.newQueueCapSpinBox.setValue(10)
    qtbot.waitUntil(lambda: load_cloud_concurrency(window.state_dir) == 10, timeout=5000)
    assert "memory budget" in page.concurrencySpinBox.toolTip()


def test_new_queue_selection_resets_filters_and_reads_items_without_idle_polling(window, qtbot):
    """Selection after creation clears queue-scoped filters and loads detail even while the page is hidden."""
    page = window.queue_page
    page.createQueueButton.click()
    qtbot.waitUntil(lambda: page.queueItemsView.model().rowCount() == 1, timeout=5000)
    first = window.queue._selected
    page.failedFilterButton.setChecked(True)
    page.queueCountSpinBox.setValue(2)
    page.createQueueButton.click()
    qtbot.waitUntil(
        lambda: window.queue._selected != first and page.queueItemsView.model().rowCount() == 2, timeout=5000
    )
    assert not page.failedFilterButton.isChecked()
    assert not any(page.queueItemsView.isRowHidden(row) for row in range(2))
    assert not window.queue._timer.isActive()


def test_queue_timing_reaches_bar_and_tooltip_without_changing_real_progress(window, qtbot, monkeypatch):
    from PySide6.QtCore import QRect

    from dtflow.ui.activity_strip import activity_strip

    window.show()
    page, queue = window.queue_page, window.queue
    strip = activity_strip(page)
    monkeypatch.setattr(strip, "_bar_rect", lambda: QRect(12, 2, 760, 20))
    page.queueCountSpinBox.setValue(3)
    page.createQueueButton.click()
    qtbot.waitUntil(lambda: queue.model.rowCount() == 3, timeout=5000)
    monkeypatch.setattr(queue, "refresh", lambda: None)
    tick = [10.0]
    monkeypatch.setattr(queue_ui, "monotonic", lambda: tick[0])
    current = queue._view() | queue._detail
    current.update(
        status="running",
        owner_active=True,
        successful_durations=[30],
        started_at_utc=(datetime.now(UTC) - timedelta(seconds=65)).isoformat(),
        summary=dict(current["summary"], succeeded=1, running=1, pending=1),
    )
    queue._dispatching = current["queue_id"]
    window.activity.set(dispatching=True)
    queue._read_done(([current], current, 4), None)
    row = strip.rows["run"]
    assert row.property("inBar") and strip.bar.isVisible()
    assert strip.bar.strip.model.elapsed == queue._activity.run_state.elapsed.removeprefix("Elapsed ")
    assert strip.bar.strip.model.eta == "~1:00 left"
    assert "Elapsed" not in row.activityDetail.text() and "estimate" not in row.activityDetail.text()
    assert "estimate" in window.shell.runControl.toolTip()
    assert (row.activityProgress.value(), row.activityProgress.maximum()) == (1, 3)
    assert queue._timing_timer.isActive() and queue._timing_timer.interval() == 1000
    # The global Stop belongs to the displayed dispatch, never the independently selected queue.
    waiting = dict(current, queue_id="waiting-queue", status="queued")
    queue._views[waiting["queue_id"]] = waiting
    queue._selected = waiting["queue_id"]
    posted, ordered = [], []
    monkeypatch.setattr(queue, "_post", lambda action, queue_id=None: posted.append((action, queue_id)))
    monkeypatch.setattr(queue, "_order", ordered.append)
    strip.bar.button.click()
    assert posted == [("cancel", current["queue_id"])] and not ordered
    assert queue._selected == waiting["queue_id"]
    tick[0] += 1
    queue._timing_tick()
    assert strip.bar.strip.model.eta == "~0:59 left"
    queue._read_done(([current], current, 4), None)
    assert strip.bar.strip.model.eta == "~0:59 left"  # an unchanged database poll preserves the countdown
    assert (row.activityProgress.value(), row.activityProgress.maximum()) == (1, 3)
    paused = dict(current, status="paused")
    queue._read_done(([paused], paused, 4), None)
    assert not strip.bar.strip.model.eta and strip.bar.strip.model.elapsed
    # Auto-play switches the displayed identity: the next queue gets a fresh Stop, not A's disabled state.
    following = dict(current, queue_id="following-queue")
    queue._views[following["queue_id"]] = following
    queue._dispatching = following["queue_id"]
    queue._publish_run_state()
    assert strip.bar.button.isEnabled() and not strip.bar.strip.model.stopping
    strip.bar.button.click()
    assert posted[-1] == ("cancel", following["queue_id"])
    queue._dispatching = "missing-owned-queue"
    queue.cancel_run()
    assert len(posted) == 2  # a missing owned view never falls back to the selected queue
    queue._dispatching = None
    queue.cancel_run()
    assert len(posted) == 2  # no live owned run: never fall back to selection
    window.activity.set(dispatching=False)
    queue._timer.stop()
    assert not queue._timing_timer.isActive()


def test_composer_keeps_its_source_prompt_and_only_copies_to_generate_explicitly(window, qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication

    window.show()
    window.form.queueNavigationButton.click()
    queue, page = window.queue, window.queue_page
    window.generation_page.promptEdit.setPlainText("Keep this Generate draft")
    queue.open_composer()
    page.composerPromptEdit.setPlainText("a {cat|dog}")
    page.planModeCombo.setCurrentText("Cross")
    page.planVariationCombo.setCurrentText("Every combination")
    window.generation_page.promptEdit.setPlainText("Unrelated Generate edit")
    queue.shown()
    assert page.composerPromptEdit.toPlainText() == "a {cat|dog}"
    queue.create("save")
    qtbot.waitUntil(lambda: not queue.composing and queue._view() is not None, timeout=5000)
    identity = queue._view()["queue_id"]
    assert window.generation_page.promptEdit.toPlainText() == "Unrelated Generate edit"
    page.queueCopyPromptButton.click()
    assert QApplication.clipboard().text() == "a cat"
    queue.edit_as_new(identity)
    qtbot.waitUntil(lambda: queue.composing, timeout=5000)
    assert page.composerPromptEdit.toPlainText() == "a {cat|dog}"
    assert page.planVariationCombo.currentText() == "Every combination"
    assert window.generation_page.promptEdit.toPlainText() == "Unrelated Generate edit"
    page.copyToGenerateButton.click()
    assert window.generation_page.promptEdit.toPlainText() == "a {cat|dog}"
    window.generation_page.promptEdit.setPlainText("Explicit replacement")
    page.editInGenerateButton.click()
    assert page.composerPromptEdit.toPlainText() == "Explicit replacement"
    # A Generate backend change must not relabel or change the captured draft's cap policy.
    monkeypatch.setattr(queue, "_backend", lambda: ("cloud", None))
    assert "Backend: fake" in queue.quick_disclosure(plan=True)
    assert "Backend: cloud" in queue.quick_disclosure(plan=False)
    shared_epoch = queue._shared_epoch
    page.newQueueCapSpinBox.setValue(3)
    queue._show_new_cap()
    assert queue._shared_epoch == shared_epoch
    assert page.newQueueCapSpinBox.value() == 3
    window.queue_popover.plan_option.setChecked(True)
    window.queue_popover.refresh()
    assert "composer's draft" in window.queue_popover.note_label.text()
    assert "Backend: fake" in window.queue_popover.note_label.text()

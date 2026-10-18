"""Gallery read completions keep the committed page usable during preparation."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from dtflow.core.gallery import GalleryItem
from dtflow.core.gallery import GalleryPage as GalleryResults
from dtflow.ui import gallery_page as gallery_ui
from dtflow.ui.gallery_page import GalleryPage
from dtflow.ui.main_window import FORMS_DIR, load_ui
from dtflow.ui.runner import AsyncRunner


@pytest.fixture
def presentation(qtbot, tmp_path, monkeypatch, fake_fixture_image):
    form = load_ui(FORMS_DIR / "gallery_detail_page.ui")
    runner = AsyncRunner(form)
    qtbot.addWidget(form, before_close_func=lambda _widget: runner.shutdown())
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}")
    base = GalleryItem(
        "0",
        "fixture",
        "fixture",
        str(fake_fixture_image),
        str(metadata),
        "2026-10-18T00:00:00+00:00",
        "fake",
        "fixture",
        "Offline UI fixture",
        "",
        96,
        64,
        (),
        "{}",
        fake_fixture_image.stat().st_size,
    )
    records = tuple(replace(base, identity=str(index)) for index in range(400))
    state = {"records": records[:200]}

    async def read(_state, query):
        rows = state["records"]
        return GalleryResults(rows[query.offset : query.offset + query.limit], len(rows), query.offset, query.limit)

    async def choices(_state):
        return (), ()

    monkeypatch.setattr(gallery_ui, "query_outputs", read)
    monkeypatch.setattr(gallery_ui, "filter_choices", choices)
    gallery = GalleryPage(form, runner=runner, state_dir=tmp_path / "state")
    # Existing presentation fixtures control their own append requests. Pause
    # automatic neighbor buffering here; specific tests explicitly enable it.
    gallery._neighbor_prefetch_timer.stop()
    monkeypatch.setattr(gallery, "_queue_neighbor_prefetch", lambda: None)
    gallery.auto_button.setChecked(False)
    form.resize(1200, 800)
    form.show()
    gallery.set_active(True)
    qtbot.waitUntil(
        lambda: (
            len(gallery.items) == 100
            and not gallery._loading
            and gallery._grid_mount_keys is None
            and len(gallery._grid_layout_keys) == 100
        ),
        timeout=5000,
    )
    # The initial page prepares with the production budget; the tests below observe preparation mid-flight, so
    # from here it is deterministically one record per event-loop turn.
    gallery.preparation.budget_ms = 0
    yield form, gallery, runner, records, state
    runner.shutdown()


def test_append_100_to_200_yields_to_input_and_retains_widgets(presentation, qtbot):
    _form, gallery, _runner, records, _state = presentation
    first = gallery._grid_buttons["0"]
    gallery._check_boxes["0"].setChecked(True)
    bar = gallery.grid_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: not gallery.reflow_timer.isActive() and bar.maximum() > 0, timeout=3000)
    qtbot.wait(20)
    bar.setValue(bar.maximum())
    qtbot.wait(10)
    viewport = gallery.grid_viewport
    point = viewport.rect().center()
    wheel = QWheelEvent(
        QPointF(point),
        QPointF(viewport.mapToGlobal(point)),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    # This interaction test needs the real neighbor scheduler, unlike the
    # otherwise manually driven presentation fixture.
    gallery._queue_neighbor_prefetch = GalleryPage._queue_neighbor_prefetch.__get__(gallery)
    QApplication.sendEvent(viewport, wheel)
    qtbot.waitUntil(lambda: gallery.preparation.active, timeout=3000)
    assert tuple(gallery.items) == records[:100]
    assert len(gallery._grid_buttons) == 100
    delivered = []
    QTimer.singleShot(0, lambda: delivered.append((len(gallery.items), first.isVisible())))
    qtbot.waitUntil(lambda: len(gallery.items) == 200 and not gallery._adjacent_loading, timeout=5000)
    assert delivered == [(100, True)]
    assert gallery._grid_buttons["0"] is first
    assert gallery.selection.checked == {"0"}
    assert tuple(gallery.items) == records[:200]
    assert gallery.table.rowCount() == len(gallery._film_entries) == 200
    qtbot.waitUntil(
        lambda: (
            len(gallery._grid_layout_keys) == 200
            and gallery._grid_mount_keys is None
            and not gallery.reflow_timer.isActive()
        ),
        timeout=3000,
    )
    # The append fast path must leave retained cards in their existing cells
    # while placing both new and old cards in the expected row/column.
    columns = gallery._grid_layout_columns
    for index in (0, 50, 99, 100, 150, 199):
        card = gallery._grid_buttons[str(index)].parentWidget()
        layout_index = gallery.cards_layout.indexOf(card)
        assert layout_index >= 0
        assert gallery.cards_layout.getItemPosition(layout_index)[:2] == divmod(index, columns)


def test_grid_mount_yields_before_all_new_cells_are_inserted(presentation, qtbot):
    """A normal 100-card append must not monopolize one Qt callback."""
    _form, gallery, _runner, records, _state = presentation
    first = gallery._grid_buttons["0"]
    gallery._grid_mount_budget_ms = 0.0  # one layout insertion per GUI turn
    gallery.items, gallery.total = list(records[:200]), 200
    gallery._render_page()
    gallery._rendered_page = (0, records[:200])
    gallery.reflow_timer.stop()
    gallery._start_grid_mount()
    assert gallery._grid_mount_keys is not None
    assert gallery._grid_buttons["0"] is first
    observed = []
    QTimer.singleShot(0, lambda: observed.append(gallery._grid_mount_cursor))
    qtbot.waitUntil(lambda: gallery._grid_mount_keys is None, timeout=5000)
    assert observed and observed[0] < 200
    assert len(gallery._grid_layout_keys) == 200
    assert gallery.cards_layout.indexOf(first.parentWidget()) >= 0


def test_refresh_during_preparation_commits_only_latest_result(presentation, qtbot):
    _form, gallery, _runner, records, state = presentation
    state["records"] = records[100:200]
    gallery.refresh(reset=True, preserve=False)
    qtbot.waitUntil(lambda: gallery.preparation.active, timeout=3000)
    state["records"] = records[200:300]
    gallery.refresh(reset=True, preserve=False)
    assert tuple(gallery.items) == records[:100]
    qtbot.waitUntil(lambda: not gallery._loading and tuple(gallery.items) == records[200:300], timeout=5000)
    assert set(gallery._grid_buttons) == {item.identity for item in records[200:300]}
    assert not gallery._prepared_entries


def test_shutdown_does_not_restart_viewer_save(presentation, qtbot, monkeypatch):
    _form, gallery, runner, _records, _state = presentation
    runner.shutdown()
    monkeypatch.setattr(gallery, "_state_document", lambda: pytest.fail("Read UI after shutdown"))
    gallery._schedule_save()
    gallery._flush_save()
    qtbot.wait(30)
    assert not gallery.save_timer.isActive()


def test_forward_eviction_keeps_grid_anchor_after_layout_settles(presentation, qtbot):
    _form, gallery, _runner, records, state = presentation
    state["records"] = records
    gallery.items, gallery.total, gallery.offset = list(records[:300]), 400, 0
    gallery._render_page()
    gallery._rendered_page = (0, records[:300])
    qtbot.waitUntil(
        lambda: gallery._grid_mount_keys is None and len(gallery._grid_layout_keys) == 300,
        timeout=5000,
    )
    gallery.cards_layout.activate()
    gallery.grid_cards.adjustSize()
    qtbot.wait(80)
    bar = gallery.grid_scroll.verticalScrollBar()
    anchor = records[249].identity
    card = gallery._grid_buttons[anchor].parentWidget()
    bar.setValue(card.y())
    qtbot.wait(60)
    before = card.y() - bar.value()
    retained = gallery._grid_buttons[anchor]
    # The same asynchronous read-completion seam used by wheel prefetch.
    gallery._load_adjacent(1)
    qtbot.waitUntil(
        lambda: gallery.offset == 100 and not gallery._adjacent_loading and not gallery._suppress_batch_scroll,
        timeout=5000,
    )
    qtbot.wait(150)  # include deferred child visibility/layout/deletion events
    assert gallery._grid_buttons[anchor] is retained
    assert card.y() - bar.value() == before
    assert card.isVisible()
    assert len(gallery.items) == gallery.table.rowCount() == 300


def test_shutdown_drops_pending_anchor_restoration(presentation, qtbot, monkeypatch):
    _form, gallery, runner, _records, _state = presentation
    original_prepare = gallery._prepare_page

    def prepare(records, generation, done):
        def committed():
            done()
            assert len(gallery.items) == 200
            runner.shutdown()  # close at the commit seam, before queued geometry restoration

        original_prepare(records, generation, committed)

    original_capture = gallery._capture_scroll

    def capture():
        assert not runner.closed, "Read Qt scroll controls after shutdown"
        original_capture()

    monkeypatch.setattr(gallery, "_prepare_page", prepare)
    monkeypatch.setattr(gallery, "_capture_scroll", capture)
    gallery._load_adjacent(1)
    qtbot.waitUntil(lambda: runner.closed, timeout=5000)
    qtbot.wait(80)
    assert not gallery._suppress_batch_scroll


def test_shutdown_cancels_uncommitted_widgets(presentation, qtbot):
    _form, gallery, runner, records, state = presentation
    state["records"] = records[100:200]
    gallery.refresh(reset=True, preserve=False)
    qtbot.waitUntil(lambda: gallery.preparation.active, timeout=3000)
    runner.shutdown()
    qtbot.wait(30)
    assert tuple(gallery.items) == records[:100]
    assert not gallery.preparation.active
    assert not gallery._prepared_entries


def test_empty_filter_then_same_grid_cards_are_mounted_again(presentation, qtbot):
    """Empty filter results must invalidate the previous cell-key cache."""
    _form, gallery, _runner, records, state = presentation
    original = gallery._grid_buttons["0"].parentWidget()
    state["records"] = ()
    gallery.refresh(reset=True, preserve=False)
    qtbot.waitUntil(
        lambda: (
            not gallery._loading
            and not gallery.items
            and gallery._grid_layout_keys == ()
            and gallery._grid_mount_keys is None
        ),
        timeout=5000,
    )
    assert gallery.cards_layout.indexOf(original) == -1
    state["records"] = records[:200]
    gallery.refresh(reset=True, preserve=False)
    qtbot.waitUntil(
        lambda: (
            not gallery._loading
            and len(gallery.items) == 100
            and gallery._grid_mount_keys is None
            and len(gallery._grid_layout_keys) == 100
        ),
        timeout=5000,
    )
    first = gallery._grid_buttons["0"].parentWidget()
    assert gallery.cards_layout.indexOf(first) >= 0
    assert first.isVisible()
    assert first is not original


def test_empty_result_interrupts_mount_and_releases_callbacks(presentation, qtbot):
    """An interrupted mount cannot strand anchor callbacks or hide restored cards."""
    _form, gallery, _runner, records, _state = presentation
    gallery._grid_mount_budget_ms = 0.0
    gallery.items, gallery.total = list(records[:200]), 200
    gallery._render_page()
    gallery._rendered_page = (0, records[:200])
    gallery.reflow_timer.stop()
    gallery._start_grid_mount()
    assert gallery._grid_mount_keys is not None
    fired = []
    gallery._grid_mount_callbacks.append(lambda: fired.append(True))
    gallery.items, gallery.total = [], 0
    gallery._render_page()
    gallery._rendered_page = (0, ())
    gallery.reflow_timer.stop()
    gallery._start_grid_mount()
    assert gallery._grid_mount_keys is None
    assert gallery._grid_layout_keys == ()
    assert not gallery._grid_mount_callbacks
    assert not gallery._grid_mount_timer.isActive()
    qtbot.waitUntil(lambda: fired == [True], timeout=2000)

    gallery.items, gallery.total = list(records[:200]), 200
    gallery._render_page()
    gallery._rendered_page = (0, records[:200])
    qtbot.waitUntil(
        lambda: gallery._grid_mount_keys is None and len(gallery._grid_layout_keys) == 200,
        timeout=5000,
    )
    assert gallery.cards_layout.indexOf(gallery._grid_buttons["0"].parentWidget()) >= 0
    assert gallery._grid_buttons["0"].parentWidget().isVisible()


def test_stale_adjacent_prepare_does_not_clear_new_generation_scroll_guard(presentation, qtbot):
    """Old queued restoration must not change the newer generation's flag."""
    _form, gallery, _runner, records, _state = presentation
    gallery._grid_mount_budget_ms = 0.0
    gallery._load_adjacent(1)
    qtbot.waitUntil(
        lambda: gallery._suppress_batch_scroll and bool(gallery._grid_mount_callbacks),
        timeout=5000,
    )
    obsolete_prepare = gallery._grid_mount_callbacks[-1]
    gallery._query_generation += 1
    gallery._suppress_batch_scroll = True  # emulate newer generation anchor restoration
    obsolete_prepare()
    assert gallery._suppress_batch_scroll is True
    gallery._suppress_batch_scroll = False


def test_neighbor_prefetch_keeps_previous_and_next_zones_buffered(presentation, qtbot):
    """The active 100-card zone has both neighbors without reaching an edge."""
    _form, gallery, _runner, records, state = presentation
    state["records"] = records
    gallery.refresh(reset=True, preserve=False)
    qtbot.waitUntil(lambda: not gallery._loading and gallery.total == 400, timeout=5000)
    # Restore the actual automatic scheduling for this test.
    gallery._queue_neighbor_prefetch = GalleryPage._queue_neighbor_prefetch.__get__(gallery)
    gallery._queue_neighbor_prefetch()
    qtbot.waitUntil(
        lambda: (
            len(gallery.items) == 200
            and gallery._grid_mount_keys is None
            and not gallery._suppress_batch_scroll
            and not gallery._adjacent_loading
        ),
        timeout=5000,
    )
    assert gallery.offset == 0
    gallery._jump_to_page(2)
    qtbot.waitUntil(
        lambda: (
            len(gallery.items) == 300
            and gallery._grid_mount_keys is None
            and not gallery._suppress_batch_scroll
            and not gallery._adjacent_loading
        ),
        timeout=8000,
    )
    assert gallery.offset == 0
    assert tuple(gallery.items) == records[:300]
    assert len(gallery._grid_buttons) == 300
    assert gallery.cards_layout.indexOf(gallery._grid_buttons["299"].parentWidget()) >= 0


def test_direct_jump_preloads_both_neighbor_zones(presentation, qtbot):
    """Jumping into a middle zone preloads both neighbors, not only forward."""
    _form, gallery, _runner, records, state = presentation
    state["records"] = records
    gallery.refresh(reset=True, preserve=False)
    qtbot.waitUntil(lambda: not gallery._loading and gallery.total == 400, timeout=5000)
    gallery._jump_to_page(3)  # query page 3, which is not resident
    qtbot.waitUntil(
        lambda: (
            not gallery._loading
            and gallery.offset == 200
            and len(gallery.items) == 100
            and gallery._grid_mount_keys is None
            and not gallery.reflow_timer.isActive()
        ),
        timeout=5000,
    )
    gallery._queue_neighbor_prefetch = GalleryPage._queue_neighbor_prefetch.__get__(gallery)
    gallery._queue_neighbor_prefetch()
    qtbot.waitUntil(
        lambda: (
            gallery.offset == 100
            and len(gallery.items) == 300
            and gallery._grid_mount_keys is None
            and not gallery._adjacent_loading
            and not gallery._suppress_batch_scroll
        ),
        timeout=9000,
    )
    assert tuple(gallery.items) == records[100:400]
    assert gallery._grid_buttons["200"].parentWidget().isVisible()
    assert gallery.cards_layout.indexOf(gallery._grid_buttons["100"].parentWidget()) >= 0
    assert gallery.cards_layout.indexOf(gallery._grid_buttons["399"].parentWidget()) >= 0


def test_page_jump_while_neighbor_is_loading_replays_latest_request(presentation, qtbot):
    """Eager preloading must not disable/ignore a user page-chip request."""
    _form, gallery, _runner, records, state = presentation
    state["records"] = records
    gallery.refresh(reset=True, preserve=False)
    qtbot.waitUntil(lambda: not gallery._loading and gallery.total == 400, timeout=5000)
    gallery._load_adjacent(1)
    assert gallery._adjacent_loading
    gallery._sync_pager()
    assert gallery.pager.buttons[3].isEnabled()
    gallery._jump_to_page(2)
    gallery._jump_to_page(3)  # latest intent supersedes the earlier click
    assert gallery._pending_page_jump == 3
    qtbot.waitUntil(
        lambda: (
            gallery._pending_page_jump is None
            and not gallery._loading
            and not gallery._adjacent_loading
            and gallery.offset == 200
            and len(gallery.items) == 100
        ),
        timeout=9000,
    )
    assert tuple(gallery.items) == records[200:300]

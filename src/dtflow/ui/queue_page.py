"""Queues page controller: widgets in, shared queue services out. Owns no scheduling, reservation or persistence rules.

Reads are read-only polls of the durable state: the list reads queue summaries only, and items and events are read for
the selected queue alone. Every control is posted through the same function ``dtflow queue`` uses, so a window and a
CLI process drive a queue identically. Only Start makes this window the dispatch owner, which holds the state ownership
lock for the run (see ``Activity``). The header shows one status chip and one state-dependent primary action;
transient outcomes go to the shared bottom overlay. *New queue* (top of the center) swaps the selected queue for
the composer in the center: Repeat or Pair/Cross values picked from inventory, a live count and preview, then Start now,
Add to queue or Save paused. Wording after cancel comes from recorded evidence, and cancel never
claims remote cancellation.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QListWidgetItem, QMenu, QWidget

from dtflow.core.models import BackendEvent, ValidationError
from dtflow.persistence.settings import SettingsError, load_cloud_concurrency, validate_concurrency
from dtflow.persistence.store import StateBusyError
from dtflow.services.connection import Presentation, present_generation_failure
from dtflow.services.image_preparation import prepare_edit_inputs
from dtflow.services.plan_errors import PlanError
from dtflow.services.planning import Plan, PlanTable, describe_plan, expand_plan, new_rng_seed, plan_size, resolve_plan
from dtflow.services.wildcards import bind_wildcards, fingerprint, list_names
from dtflow.services.queue_session import (
    change_queue_order,
    clear_queues,
    create_edit_queue,
    create_queue,
    dispatch_queue,
    expand_items,
    post_queue_control,
    read_queue_list,
    read_queue_views,
    relaunch_queue,
    remove_queue,
    set_concurrency,
    RELAUNCH_STATES,
)
from dtflow.services.session import bind_prepared, load_prepared, selection_for
from dtflow.ui.activity import Activity, RunState
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.generation_presenter import FormError
from dtflow.ui.inline_feedback import confirm_action, feedback
from dtflow.ui.planning_presenter import (
    ERROR_FIELDS,
    DRAW_SPINS,
    NO_LORA,
    candidate_counts,
    PlanForm,
    build_plan,
    lora_set_value,
    count_text,
    plan_error,
    sentence,
    SEED_POLICY_TEXT,
    summary_text,
)
from dtflow.ui.queue_composer import SIZE_PRESETS, ActionsFit
from dtflow.ui.queue_model import ITEM_ID_ROLE, QueueItemModel
from dtflow.ui.queue_presenter import (
    CONCURRENCY_HINT,
    FILTERS,
    GROUPS,
    NEW_CAP_HINT,
    STATUS_TEXT,
    QueueCountdown,
    concurrency_level,
    controls,
    elapsed_text,
    eta_text,
    event_lines,
    filter_counts,
    filter_states,
    has_started,
    item_rows,
    matches,
    needs_recovery,
    outcome_note,
    outcome_text,
    primary_action,
    progress_text,
    queue_entries,
    lora_text,
    queue_facts,
    queue_title,
    segments,
    status_chip,
    summarize,
    up_next_ids,
)
from dtflow.ui.queue_widgets import ENTRY_ROLE, HEADING_ROLE, QueueListDelegate
from dtflow.ui.runner import AsyncRunner, EventRelay

POLL_MS = 1000  # read interval while anything runs; the dispatch owner applies posted controls every 0.5 s
IDLE_POLL_MS = 3000  # light summary polling while the tab is visible, so queues created elsewhere appear
PLAN_WIDGETS = tuple(dict.fromkeys(ERROR_FIELDS.values()))
RANDOM_INPUTS = (
    "planVariationCombo",
    "planVariationCountSpin",
    "planShuffleCombo",
    "planDropSpin",
    "planAddSpin",
    "planRerollButton",
    *(f"planDraw{suffix}Spin" for suffix in DRAW_SPINS.values()),
)
PLAN_INPUTS = (*PLAN_WIDGETS, "planModeCombo", "planIncrementCheck", "queueCountSpinBox", *RANDOM_INPUTS)
PREVIEW_MS = 250  # the live preview follows edits after this pause, so typing a prompt list stays smooth
REPEAT = "Repeat"
FILTER_BUTTONS = {key: f"{key}FilterButton" for key, *_ in FILTERS}
WIRED_CONTROLS = (
    "queueList",
    "queueFilterEdit",
    "createQueueButton",
    "addQueueButton",
    "startQueueButton",
    "newQueueButton",
    "closeComposerButton",
    "editInGenerateButton",
    "refreshQueuesButton",
    "clearQueuesButton",
    "removeQueueButton",
    "newQueueCapSpinBox",
    "concurrencySpinBox",
    "queuePrimaryButton",
    "cancelQueueButton",
    "earlierQueueButton",
    "laterQueueButton",
    "relaunchQueueButton",
    "emptyRelaunchButton",
    *FILTER_BUTTONS.values(),
    "queueItemsView",
    *PLAN_INPUTS,
)
CLEAR_TEXT = (
    "Finished and cancelled queues will be removed from this list. Their attempts and saved images are kept. "
    "Paused, running and up-next queues stay."
)
REMOVE_TEXT = "This queue will be removed from the list. Its attempts and saved images are kept."
POSTED = "Posted. The dispatch owner applies it at its next poll, or at the next Start if nothing is dispatching."


GENERATE_LORAS = "Generate's LoRAs"  # LoRA-set choice: the set currently on the Generate form


def _dimension_text(name: str, count: int) -> str:
    """``2 step counts``-style wording of one plan dimension for the composer summary."""
    if name.startswith("lora_weight."):
        return f"{count} weights of {name.split('.', 1)[1]}"
    nouns = {"steps": "step count", "guidance": "guidance value", "strength": "strength", "loras": "LoRA set"}
    noun = nouns.get(name, name)
    return f"{count} {noun}{'s' if count != 1 else ''}"


def _clip(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class _QueueAdvance:
    """Auto-play moved on: ``settled`` is the finished queue's view and ``following`` the queue now starting."""

    settled: dict
    following: str
    kind: str = "queue_advanced"
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class _QueueBackendEvent:
    queue_id: str
    item_id: str
    event: BackendEvent

    @property
    def kind(self) -> str:
        return self.event.kind

    @property
    def sequence(self) -> int:
        return self.event.sequence


class QueuePage:
    def __init__(
        self,
        page: QWidget,
        *,
        runner: AsyncRunner,
        backend_source: Callable[[], tuple[str, str | None]],
        saved_source: Callable,
        state_dir: Path,
        request_source: Callable,
        activity: Activity,
        simulation_source: Callable[[], dict] = dict,
        edit_source: Callable = lambda: None,
        editing_source: Callable[[], bool] = lambda: False,
        created_hook: Callable[[], None] = lambda: None,
        choices_source: Callable[[], dict] = dict,
        generate_hook: Callable[[], None] = lambda: None,
    ):
        self.page = page
        self._runner, self._backend, self._saved = runner, backend_source, saved_source
        self._state, self._request_source, self._activity = Path(state_dir), request_source, activity
        self._simulation, self._created_hook = simulation_source, created_hook
        self._choices, self._generate_hook = choices_source, generate_hook
        self._composing = False
        self.plan_hook: Callable[[], None] = lambda: None  # the Queue popover's trigger follows plan edits
        self._after_generation: list[str] = []  # popover queues waiting for the running single generation (#135)
        self._checking_waiting = False  # one fresh status read at a time decides the next waiting queue
        self._waiting_timer = QTimer(page)  # owned by the page, so it dies with the window
        self._waiting_timer.setSingleShot(True)
        self._waiting_timer.timeout.connect(self._start_after_generation)
        self._edit_source, self._editing = edit_source, editing_source
        self._views: dict[str, dict] = {}
        self._selected: str | None = None
        self._select_after: str | None = None
        self._dispatching: str | None = None
        self._dispatch_started = False
        self._future = None
        self._reading = False
        self._syncing = False
        self._detail: dict | None = None
        self._varied: dict[str, dict] = {}  # items never change after creation, so their variation is read once
        self._shared_cap: int | None = None
        self._shared_epoch = 0  # bumped by each save, so a read that started before it cannot undo it
        self._resume_fallback: str | None = None
        self._list_key: tuple | None = None
        self._output_folder: Path | None = None
        self._run_progress: dict[tuple[str, str], tuple[int, int]] = {}
        self._countdown = QueueCountdown()
        self._event_relay = EventRelay(self._queue_backend_event, page)
        self._advance_relay = EventRelay(self._advanced, page)
        self.model = QueueItemModel(page)
        page.queueItemsView.setModel(self.model)
        for view in (page.queueItemsView, page.planTableView):
            view.verticalHeader().hide()  # the "#" column already numbers rows
            view.horizontalHeader().setStretchLastSection(True)
            view.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for name in WIRED_CONTROLS:
            getattr(page, name).setEnabled(True)
        page.concurrencySpinBox.setToolTip(CONCURRENCY_HINT)
        page.concurrencyLabel.setToolTip(CONCURRENCY_HINT)
        page.newQueueCapSpinBox.setToolTip(NEW_CAP_HINT)
        page.newQueueCapLabel.setToolTip(NEW_CAP_HINT)
        refresh_icon = Path(__file__).parents[1] / "resources" / "images" / "gallery" / "refresh.svg"
        page.refreshQueuesButton.setIcon(QIcon(str(refresh_icon)))
        page.refreshQueuesButton.setIconSize(QSize(16, 16))
        page.refreshQueuesButton.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        page.queueList.setItemDelegate(QueueListDelegate(page.queueList))
        page.queueList.setTextElideMode(Qt.TextElideMode.ElideRight)
        for key, symbol, _, label in FILTERS:
            button = getattr(page, FILTER_BUTTONS[key])
            button.setToolTip(f"Show only {label.lower()} items; click again to show all")
            button.setAccessibleName(f"{label} items filter")
            button.setText(symbol)
            button.toggled.connect(lambda *_: self._apply_item_filter())
        self._timer = QTimer(page)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timing_timer = QTimer(page)
        self._timing_timer.setInterval(1000)
        self._timing_timer.timeout.connect(self._timing_tick)
        page.refreshQueuesButton.clicked.connect(self.refresh)
        page.clearQueuesButton.clicked.connect(self.clear_queues)
        page.removeQueueButton.clicked.connect(self.remove)
        page.createQueueButton.clicked.connect(lambda: self.create("save"))
        page.addQueueButton.clicked.connect(lambda: self.create("add"))
        page.startQueueButton.clicked.connect(lambda: self.create("start"))
        page.newQueueButton.clicked.connect(self.open_composer)
        page.closeComposerButton.clicked.connect(self.close_composer)
        page.editInGenerateButton.clicked.connect(lambda: self._generate_hook())
        page.queuePrimaryButton.clicked.connect(self.primary)
        page.earlierQueueButton.clicked.connect(lambda: self._order("earlier"))
        page.laterQueueButton.clicked.connect(lambda: self._order("later"))
        page.cancelQueueButton.clicked.connect(self.cancel)
        page.queueList.currentRowChanged.connect(self._select_row)
        # While composing, the shown queue stays current in the list; a click on it must still return to it.
        page.queueList.itemClicked.connect(lambda item: self._select_row(page.queueList.row(item)))
        page.queueList.customContextMenuRequested.connect(self._list_menu)
        page.queueFilterEdit.textChanged.connect(lambda *_: self._render_list())
        page.queueItemsView.selectionModel().currentRowChanged.connect(lambda *_: self._show_item())
        page.itemOpenFolderButton.clicked.connect(self.open_output_folder)
        page.itemRetryButton.clicked.connect(self.retry_item)
        page.emptyRelaunchButton.clicked.connect(self.relaunch_last)
        page.relaunchQueueButton.clicked.connect(
            lambda: self._relaunch_menu(
                page.relaunchQueueButton.mapToGlobal(page.relaunchQueueButton.rect().bottomLeft())
            )
        )
        page.concurrencySpinBox.valueChanged.connect(self._cap_changed)
        page.newQueueCapSpinBox.valueChanged.connect(self._new_cap_changed)
        self.plan_model = QStandardItemModel(page)
        page.planTableView.setModel(self.plan_model)
        self._preview_key: tuple | None = None  # (plan form, request) of the preview shown or being drawn
        self._preview_token = 0  # a prompt-variation preview resolves off the GUI thread; only the latest token shows
        self._previewing = False
        self._preview_active = False  # one computation at a time ...
        self._preview_waiting: tuple | None = None  # ... plus the latest request that arrived meanwhile
        self._preview_timer = QTimer(page)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_MS)
        self._preview_timer.timeout.connect(self.preview)
        self._configure_dimensions()
        self._actions_fit = ActionsFit(
            page.queueComposerScroll,
            page.queueComposer.layout().itemAt(0).layout(),  # the title row
            page.queueComposer.layout(),
            (
                page.newQueueCapLabel,
                page.newQueueCapSpinBox,
                page.createQueueButton,
                page.addQueueButton,
                page.startQueueButton,
            ),
            (page.closeComposerButton,),
        )
        for name in PLAN_WIDGETS:
            getattr(page, name).textChanged.connect(self._plan_changed)
        page.planModeCombo.currentTextChanged.connect(self._mode_changed)
        page.planIncrementCheck.toggled.connect(self._plan_changed)
        page.queueCountSpinBox.valueChanged.connect(self._plan_changed)
        for name in RANDOM_INPUTS:
            widget = getattr(page, name)
            if name == "planRerollButton":
                widget.clicked.connect(self._reroll)
            elif hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self._plan_changed)
            else:
                widget.currentTextChanged.connect(self._plan_changed)
        self._mode_changed()
        activity.listen(self._sync)
        activity.listen(self._after_generation_ended)
        page.destroyed.connect(self._unlisten_activity)
        self._render()
        self.refresh()

    def _unlisten_activity(self) -> None:
        """Keep late activity notifications away from destroyed page widgets."""
        self._activity.unlisten(self._sync)
        self._activity.unlisten(self._after_generation_ended)

    # -- helpers ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _show(label, level: str, text: str) -> None:
        label.setProperty("state", level)
        label.style().unpolish(label)
        label.style().polish(label)
        label.setText(text)

    def _message(self, presentation: Presentation) -> None:
        """Transient outcomes go to the shared bottom overlay; the header only shows the queue's own status."""
        strip = activity_strip(self.page)
        if strip is not None:
            state = {"ok": "success", "error": "error"}.get(presentation.level, "warning")
            strip.complete("queue-message", presentation.headline, presentation.detail, state)

    def _view(self) -> dict | None:
        return self._views.get(self._selected or "")

    def _detail_for(self, view: dict | None) -> dict | None:
        """The selected queue's full read (items and events), when it belongs to ``view``."""
        if view is not None and self._detail is not None and self._detail["queue_id"] == view["queue_id"]:
            return self._detail
        return None

    @property
    def dispatching_here(self) -> bool:
        return self._dispatching is not None

    # -- reading ---------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if self._runner.closed:
            self._timer.stop()
            return
        if self._reading:
            return
        self._reading = True
        epoch = self._shared_epoch
        self._runner.submit(self._read(self._selected, tuple(self._varied)), lambda r, f: self._read_done(r, f, epoch))

    async def _read(self, selected: str | None, described: tuple[str, ...] = ()):
        views = await read_queue_list(self._state, described)
        detail = None
        if selected and any(view["queue_id"] == selected for view in views):
            detail = (await read_queue_views(self._state, selected))[0]
        try:
            shared = await asyncio.to_thread(load_cloud_concurrency, self._state)
        except SettingsError:
            shared = None  # an unreadable settings file leaves the box as it is; saving reports the reason
        return views, detail, shared

    def _read_done(self, result, failure, epoch: int | None = None) -> None:
        self._reading = False
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        views, self._detail, shared = result
        if epoch is None or epoch == self._shared_epoch:
            self._shared_cap = shared
        for view in views:
            if view.get("varied") is not None:
                self._varied[view["queue_id"]] = view["varied"]
        self._views = {
            view["queue_id"]: view | {"varied": self._varied.get(view["queue_id"], view.get("varied"))}
            for view in views
        }
        self._varied = {key: value for key, value in self._varied.items() if key in self._views}
        detail_running = (
            {item["item_id"] for item in self._detail["items"] if item["state"] == "running"} if self._detail else None
        )
        self._run_progress = {
            (queue_id, item_id): value
            for (queue_id, item_id), value in self._run_progress.items()
            if queue_id in self._views
            and self._views[queue_id]["summary"]["running"]
            and (detail_running is None or self._detail["queue_id"] != queue_id or item_id in detail_running)
        }
        if self._dispatching is not None:
            active = self._views.get(self._dispatching)
            if active is not None and active["status"] == "running":
                self._dispatch_started = True
        if self._select_after in self._views:
            self._set_selected(self._select_after)
            self._select_after = None
        elif self._selected not in self._views:
            self._set_selected(None)
        self._render()
        busy = self.dispatching_here or any(view["status"] in ("running", "queued") for view in views)
        if busy or self.page.queueWorkspace.isVisible():
            self._timer.start(POLL_MS if busy else IDLE_POLL_MS)
        else:
            self._timer.stop()
        if self._view() is not None and self._detail_for(self._view()) is None:
            self.refresh()  # selection changed during this read: fetch its items even when idle polling is stopped

    # -- rendering -------------------------------------------------------------------------------------------------
    def _render(self) -> None:
        page = self.page
        self._render_list()
        view = self._view()
        page.queueComposerScroll.setVisible(self._composing)
        page.newQueueButton.setVisible(not self._composing)
        page.emptyStateCard.setVisible(view is None and not self._composing)
        page.emptyRelaunchButton.setVisible(self._last_queue() is not None)
        page.queueTableHost.setVisible(view is not None and not self._composing)
        page.queueHeader.setVisible(view is not None and not self._composing)
        detail = self._detail_for(view)
        if view is None:
            self.model.apply(())
            page.queueEventBrowser.clear()
        else:
            self._render_header(view)
            self.model.apply(item_rows(detail) if detail is not None else ())
            self._apply_item_filter()
            self._select_default_item()
            browser = page.queueEventBrowser
            shown = event_lines(detail) if detail is not None else ""
            if browser.toPlainText() != shown:
                scrollbar = browser.verticalScrollBar()
                position = scrollbar.value()
                at_bottom = position >= scrollbar.maximum() - 2
                browser.setPlainText(shown)
                scrollbar.setValue(scrollbar.maximum() if at_bottom else position)
        self._show_cap(view)
        self._show_new_cap()
        self._show_item()
        for name in ("queueInspectorEyebrow", "itemIdentityBox", "queueEventBrowser"):
            getattr(page, name).setVisible(not self._composing)  # evidence of the old queue is not about the new one
        page.composerSummaryBox.setVisible(self._composing)
        if self._composing:
            page.uncertaintyBox.setVisible(False)
        self._sync()

    def _render_list(self) -> None:
        """Grouped rich rows (Running, Up next, Paused, History) matching the filter; rebuilt only when they change."""
        page = self.page
        query = page.queueFilterEdit.text()
        entries = tuple(entry for entry in queue_entries(self._views.values()) if matches(entry, query))
        key = (entries, self._selected)
        if key == self._list_key:
            return
        self._list_key = key
        scroll = page.queueList.verticalScrollBar().value()
        page.queueList.blockSignals(True)
        page.queueList.clear()
        titles = dict(GROUPS)
        group = None
        for entry in entries:
            if entry.group != group:
                group = entry.group
                heading = QListWidgetItem(titles[group].upper())
                heading.setData(HEADING_ROLE, titles[group].upper())
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                page.queueList.addItem(heading)
            item = QListWidgetItem(entry.text)
            item.setData(ITEM_ID_ROLE, entry.queue_id)
            item.setData(ENTRY_ROLE, entry)
            item.setToolTip(f"{entry.title}\n{entry.subtitle}\nQueue {entry.queue_id}")
            page.queueList.addItem(item)
            if entry.queue_id == self._selected:
                page.queueList.setCurrentItem(item)
        if not entries and self._views:
            empty = QListWidgetItem("No queue matches the filter.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            page.queueList.addItem(empty)
        page.queueList.blockSignals(False)
        page.queueList.verticalScrollBar().setValue(scroll)

    def queue_items(self) -> list[QListWidgetItem]:
        """The list's queue rows, without group headings."""
        items = (self.page.queueList.item(row) for row in range(self.page.queueList.count()))
        return [item for item in items if item.data(ITEM_ID_ROLE)]

    def _render_header(self, view: dict) -> None:
        page = self.page
        summary = summarize(view)
        page.queueTitleLabel.setText(queue_title(view))
        page.queueTitleLabel.setToolTip((view.get("first") or {}).get("prompt") or "")
        for name, value in queue_facts(view).items():
            getattr(page, f"queueFact{name}Label").setText(value)
        page.queueFactPlanLabel.setToolTip(f"Queue {view['queue_id']}")
        level, chip = status_chip(view, up_next_ids(self._views.values()))
        if self._dispatching == view["queue_id"]:
            owner = "This window dispatches it."
        elif needs_recovery(view):
            owner = "No dispatch owner is active. Resume recovers pending items without replaying uncertain attempts."
        elif view["status"] == "running":
            owner = "Dispatched by another process; its controls are posted to that process."
        else:
            owner = ""
        self._show(page.queueStateLabel, level, chip)
        page.queueStateLabel.setToolTip(" ".join(part for part in (STATUS_TEXT.get(view["status"], ""), owner) if part))
        page.queueProgressBar.set_counts(segments(view["summary"]), summary.settled, summary.total)
        page.queueProgressLabel.setText(progress_text(summary))
        counts = filter_counts(view["summary"])
        for key, symbol, _, _ in FILTERS:
            button = getattr(page, FILTER_BUTTONS[key])
            button.setText(f"{symbol} {counts[key]}")
            button.setVisible(bool(counts[key]) or button.isChecked())

    def _apply_item_filter(self) -> None:
        """The checked count chips narrow the items table to their states; none checked shows every item."""
        shown = filter_states(key for key, name in FILTER_BUTTONS.items() if getattr(self.page, name).isChecked())
        table = self.page.queueItemsView
        for index in range(self.model.rowCount()):
            row = self.model.row_at(index)
            table.setRowHidden(index, bool(shown) and row.state not in shown)

    @staticmethod
    def _editable(view: dict | None) -> bool:
        return view is not None and view["status"] in ("paused", "queued", "running")

    def _show_cap(self, view: dict | None) -> None:
        """The header box is this queue's own maximum; it is fixed once the queue has finished or was cancelled."""
        spin = self.page.concurrencySpinBox
        spin.setEnabled(self._editable(view))
        if view is None:
            return
        self._syncing = True
        spin.setValue(view["cap"])
        self._syncing = False
        self._set_level(spin, view["cap"])

    def _show_new_cap(self) -> None:
        """With cloud chosen, the new-queue maximum shows the shared cloud default; other providers keep the box."""
        if self._backend()[0] != "cloud" or self._shared_cap is None:
            return
        spin = self.page.newQueueCapSpinBox
        self._syncing = True
        spin.setValue(self._shared_cap)
        self._syncing = False
        self._set_level(spin, self._shared_cap)

    @staticmethod
    def _set_level(spin, value: int) -> None:
        spin.setProperty("level", concurrency_level(value))
        spin.style().unpolish(spin)
        spin.style().polish(spin)

    def _started(self, view: dict | None) -> bool:
        owns_view = bool(view and self._dispatching == view["queue_id"])
        return owns_view or has_started(self._detail_for(view)) or bool(view and view["status"] == "running")

    def _controls_for(self, view: dict | None):
        return controls(
            view,
            dispatching_here=self.dispatching_here,
            generating=self._activity.generating,
            started=self._started(view),
            owned=bool(view and self._dispatching == view["queue_id"]),
        )

    def _run_view(self) -> dict | None:
        if self._dispatching is not None:
            active = self._views.get(self._dispatching)
            if active is not None:
                return active
        return self._view()

    def _publish_run_state(self) -> None:
        if self._activity.generating:
            self._timing_timer.stop()
            self._countdown.reset()
            return
        view = self._run_view()
        if view is None:
            self._timing_timer.stop()
            self._countdown.reset()
            self._activity.present_run(RunState())
            return

        available = self._controls_for(view)
        queue_id = view["queue_id"]
        dispatching_here = self._dispatching == queue_id
        status = view["status"]
        phase, action = "idle", None

        if dispatching_here:
            if status == "paused" and self._dispatch_started:
                phase, action = "paused", "resume" if available.resume else None
            else:
                phase = "running"
                if status == "running" and available.pause:
                    action = "pause"
        elif status == "running" and not needs_recovery(view):
            # Another process owns this queue. Show activity, but keep the compact
            # control status-only so a header click never controls foreign work.
            phase = "running"
        elif available.start:
            # With no local dispatcher, Start is the only useful immediate action.
            # Posting Resume here would remain pending until a dispatcher starts.
            phase, action = "ready", "start"

        summary = summarize(view)
        completed = total = None
        running = view["summary"]["running"]
        items = (self._detail_for(view) or {}).get("items", ())
        running_items = [item for item in items if item["state"] == "running"]
        detail = f"Queue {queue_id[:8]} · {summary.headline}"
        waiting = len(up_next_ids(self._views.values())) if dispatching_here else 0

        if phase == "running":
            # The ring counts settled items, which is meaningful however many items run at once.
            completed, total = summary.settled, summary.total
            detail += f" {summary.settled}/{summary.total} settled."
            progress = [value for (owner, _), value in self._run_progress.items() if owner == queue_id]
            if running == 1 and len(progress) == 1:
                step, steps = progress[0]
                # The item number is known only for the selected queue, whose items are read.
                position = f"Item #{running_items[0]['position']}" if len(running_items) == 1 else "Item"
                detail += f" {position} step {step}/{steps}."
            elif running:
                detail += f" {running} items running."
            if waiting:
                detail += f" {waiting} up next."
        elif status in ("finished", "cancelled"):
            # Settled/problem states stay on the Queues page. The global control
            # returns to its quiet idle presentation instead of becoming a
            # persistent completion/error badge for whichever queue is selected.
            detail = "No runnable queue selected."

        remaining = None
        if phase in ("running", "paused"):
            remaining = self._countdown.remaining(view, monotonic())
            timing = " · ".join(part for part in (elapsed_text(view), eta_text(view, remaining=remaining)) if part)
            if timing:
                detail += " " + timing
                if not self._timing_timer.isActive() and not self._runner.closed:
                    self._timing_timer.start()
            else:
                self._timing_timer.stop()
        else:
            self._timing_timer.stop()
            self._countdown.reset()

        self._activity.present_run(
            RunState(
                owner="queue" if phase != "idle" else "none",
                phase=phase,
                action=action,
                completed=completed,
                total=total,
                queue_id=queue_id if phase != "idle" else None,
                detail=detail,
            )
        )

    def _timing_tick(self) -> None:
        """Update timing presentation once a second without waiting for or initiating a database read."""
        if self._runner.closed:
            self._timing_timer.stop()
            return
        self._publish_run_state()

    def sync_edit_fields(self) -> None:
        """Strengths apply to Inpaint edits only: shown while Generate is in Inpaint mode and a plan is composed."""
        editing = self._editing() and self._planned()
        changed = self.page.planStrengthsEdit.isHidden() == editing
        self.page.planStrengthsLabel.setVisible(editing)
        self.page.planStrengthsEdit.setVisible(editing)
        self.page.planDrawStrengthsLabel.setVisible(editing)  # the draw count follows its candidate row
        self.page.planDrawStrengthsSpin.setVisible(editing)
        # Polling reaches here unchanged: only a shown or hidden Strength row changes the planning inputs.
        self._schedule_preview(invalidate=changed)

    def _removable(self, view: dict | None) -> bool:
        return (
            view is not None
            and view["status"] in ("paused", "finished", "cancelled")
            and not view["summary"]["running"]
            and view["queue_id"] != self._dispatching
        )

    def _sync(self) -> None:
        page = self.page
        self.sync_edit_fields()
        view = self._view()
        available = self._controls_for(view)
        action = primary_action(
            view, available, started=self._started(view), owned=bool(view and self._dispatching == view["queue_id"])
        )
        page.queuePrimaryButton.setEnabled(action is not None)
        page.queuePrimaryButton.setText(action.text if action else "Start")
        page.queuePrimaryButton.setToolTip(action.tooltip if action else "Nothing to start: no item is pending.")
        page.cancelQueueButton.setEnabled(available.cancel)
        for button in (page.earlierQueueButton, page.laterQueueButton):
            button.setVisible(available.reorder)
            button.setEnabled(available.reorder)
        removable = self._removable(view)
        if not removable and feedback(page)._armed and feedback(page)._armed[0] is page.removeQueueButton:
            feedback(page).dismiss()  # the queue started or went away: the pending consent no longer applies
        page.removeQueueButton.setEnabled(removable)
        page.relaunchQueueButton.setEnabled(view is not None and bool(sum(view["summary"].values())))
        # Saving only persists a paused queue (over a short shared connection while a run owns the state).
        page.createQueueButton.setEnabled(True)
        running = self._live_run()
        foreign = running and not self.dispatching_here
        page.addQueueButton.setEnabled(self.dispatching_here or running)
        page.addQueueButton.setToolTip(
            "Saves it up next: it starts by itself when the running queue ends."
            if self.dispatching_here or running
            else "Nothing is running to wait behind; use Start now or Save paused."
        )
        can_start = not self.dispatching_here and not self._activity.generating and not foreign
        page.startQueueButton.setEnabled(can_start)
        page.startQueueButton.setToolTip(
            "Creates the queue and starts it from this window. Real, possibly billable generation."
            if can_start
            else "Another process dispatches a queue; Add to queue starts the new one after it."
            if foreign
            else "This window is busy with a run; Add to queue starts the new queue after it."
        )
        page.clearQueuesButton.setEnabled(
            any(
                v["status"] in ("finished", "cancelled") and v["queue_id"] != self._dispatching
                for v in self._views.values()
            )
        )
        self._publish_run_state()

    def _live_run(self) -> bool:
        """A queue runs with a live owner; one whose owner is gone (needs recovery) has nobody to wait behind."""
        return any(v["status"] == "running" and not needs_recovery(v) for v in self._views.values())

    def _select_default_item(self) -> None:
        """Show evidence without a click: the first unknown or failed item, else the first item."""
        table = self.page.queueItemsView
        if table.currentIndex().isValid() or not self.model.rowCount():
            return
        rows = [self.model.row_at(index) for index in range(self.model.rowCount())]
        preferred = next((i for i, row in enumerate(rows) if row.state in ("unknown", "failed", "save_failed")), 0)
        table.setCurrentIndex(self.model.index(preferred, 0))

    def _show_item(self) -> None:
        page = self.page
        row = self.model.row_at(page.queueItemsView.currentIndex().row())
        view = self._view()
        note = outcome_note(row)
        page.uncertaintyBox.setVisible(note is not None)
        if note is not None:
            self._show(page.uncertaintyLabel, *note)
        page.itemRetryButton.setEnabled(row is not None and row.state in RELAUNCH_STATES)
        if row is None:
            for name in ("itemIdLabel", "attemptIdLabel", "itemBackendLabel", "itemOutputsLabel"):
                getattr(page, name).setText("—")
            page.itemOutputsLabel.setToolTip("")
            self._output_folder = None
            page.itemOpenFolderButton.setEnabled(False)
            return
        page.itemIdLabel.setText(row.item_id[:8])
        page.itemIdLabel.setToolTip(row.item_id)
        page.attemptIdLabel.setText(row.attempt_id[:8] if row.attempt_id else "—")  # the full id is in the tooltip
        page.attemptIdLabel.setToolTip(row.attempt_id or "")
        page.itemBackendLabel.setText(summarize(view).backend if view else "—")
        outputs = [*row.outputs, *([row.metadata] if row.metadata else [])]
        # Full paths stay in the tooltip; the panel shows a summary and a button to the attempt's folder.
        parts = [f"{len(row.outputs)} image(s)"] if row.outputs else []
        detail = ", ".join([*parts, *(["metadata"] if row.metadata else [])]) or (row.failure_code or "—")
        page.itemOutputsLabel.setText(detail)
        page.itemOutputsLabel.setToolTip("\n".join(outputs))
        self._output_folder = Path(outputs[0]).parent if outputs else None
        page.itemOpenFolderButton.setEnabled(self._output_folder is not None)

    def open_output_folder(self) -> None:
        if self._output_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_folder)))

    def _select_row(self, row: int) -> None:
        item = self.page.queueList.item(row) if row >= 0 else None
        queue_id = item.data(ITEM_ID_ROLE) if item is not None else None
        if queue_id is None or (queue_id == self._selected and not self._composing):
            return  # a group heading, or the queue already shown
        self._composing = False
        self._set_selected(queue_id)
        self._render()
        self.refresh()

    def _set_selected(self, queue_id: str | None) -> None:
        """Reset queue-scoped presentation for both user selection and selection after creation."""
        if queue_id == self._selected:
            return
        if feedback(self.page)._armed:
            feedback(self.page).dismiss()
        for name in FILTER_BUTTONS.values():
            getattr(self.page, name).setChecked(False)  # item filters belong to the queue they were set on
        self._selected = queue_id
        if self._detail is not None and self._detail["queue_id"] != queue_id:
            self._detail = None

    def select_queue(self, queue_id: str) -> None:
        item = next((item for item in self.queue_items() if item.data(ITEM_ID_ROLE) == queue_id), None)
        if item is not None:
            self.page.queueList.setCurrentItem(item)

    def _list_menu(self, position) -> None:
        item = self.page.queueList.itemAt(position)
        queue_id = item.data(ITEM_ID_ROLE) if item is not None else None
        if queue_id is None:
            return
        menu = QMenu(self.page.queueList)
        relaunch = menu.addMenu("Relaunch")
        self._relaunch_actions(relaunch, queue_id)
        remove = menu.addAction("Remove queue…")
        remove.setEnabled(self._removable(self._views.get(queue_id)))
        if menu.exec(self.page.queueList.viewport().mapToGlobal(position)) is remove:
            self.select_queue(queue_id)
            self.remove()

    # -- relaunch and retry ----------------------------------------------------------------------------------------
    def _relaunch_menu(self, at) -> None:
        if self._selected is None:
            return
        menu = QMenu(self.page.relaunchQueueButton)
        self._relaunch_actions(menu, self._selected)
        menu.exec(at)

    def _relaunch_actions(self, menu: QMenu, queue_id: str) -> None:
        """Same or new seeds, every item or only those that settled without a saved image."""
        view = self._views.get(queue_id)
        retryable = bool(view) and any(view["summary"].get(state, 0) for state in RELAUNCH_STATES)
        for text, new_seeds, failed_only in (
            ("Same seeds", False, False),
            ("New seeds", True, False),
            ("Only failed items, same seeds", False, True),
            ("Only failed items, new seeds", True, True),
        ):
            action = menu.addAction(text)
            action.setEnabled(view is not None and (retryable or not failed_only))
            action.triggered.connect(
                lambda _=False, n=new_seeds, f=failed_only: self.relaunch(queue_id, new_seeds=n, failed_only=f)
            )
            if failed_only:
                action.setToolTip("Failed, cancelled and no-result items only.")
        menu.setToolTipsVisible(True)

    def _last_queue(self) -> str | None:
        """The most recently created queue with items, for the empty state's Relaunch last."""
        views = [v for v in self._views.values() if sum(v["summary"].values())]
        return max(views, key=lambda v: (v["created_at_utc"], v["queue_id"]))["queue_id"] if views else None

    def relaunch_last(self) -> None:
        queue_id = self._last_queue()
        if queue_id is not None:
            self.relaunch(queue_id)

    def retry_item(self) -> None:
        """Retry item: the selected item again, as a one-item queue with its own new attempt (possibly billable)."""
        row = self.model.row_at(self.page.queueItemsView.currentIndex().row())
        if row is None or self._selected is None or row.state not in RELAUNCH_STATES:
            return
        self.relaunch(self._selected, positions=(row.position,))

    def relaunch(
        self,
        queue_id: str,
        *,
        new_seeds: bool = False,
        failed_only: bool = False,
        positions: tuple[int, ...] | None = None,
    ) -> None:
        """A new queue from ``queue_id``'s recorded items: started now, up next behind this window's run, or paused."""
        up_next = self.dispatching_here

        async def work():
            new_id, cap = await relaunch_queue(
                self._state,
                queue_id,
                new_seeds=new_seeds,
                failed_only=failed_only,
                positions=positions,
                up_next=up_next,
            )
            return new_id, cap, (await read_queue_views(self._state, new_id))[0]["status"]

        retry = positions is not None
        self._runner.submit(work(), lambda result, failure: self._relaunched(result, failure, retry))

    def _relaunched(self, result, failure, retry: bool) -> None:
        noun = "Item retried" if retry else "Queue relaunched"
        if failure is not None:
            failed = Presentation("error", "Not retried" if retry else "Not relaunched", str(failure))
            self._message(failed if isinstance(failure, ValidationError) else present_generation_failure(failure))
            return
        queue_id, _, status = result
        self._created_hook()
        self._composing = False
        self._select_after = queue_id
        if status == "queued":
            self._message(
                Presentation("ok", noun, "It waits up next and starts by itself when the running queue ends.")
            )
        elif not self.dispatching_here and not self._activity.generating and not self._live_run():
            self._dispatch(queue_id, resume_fallback=None)
        else:
            self._message(
                Presentation("warning", f"{noun}, paused", "Saved paused; press Start when the current run ends.")
            )
        self._render()
        self.refresh()

    # -- composer --------------------------------------------------------------------------------------------------
    def open_composer(self) -> None:
        """Show the composer in the center instead of the selected queue; its values are kept between openings."""
        if feedback(self.page)._armed:
            feedback(self.page).dismiss()
        self._composing = True
        self._invalidate_preview()
        self._refresh_choices()
        self._render()
        self.preview()

    def close_composer(self) -> None:
        self._composing = False
        self._invalidate_preview()
        self._preview_timer.stop()
        self._render()

    def shown(self) -> None:
        """Returning to Queues (for example from Edit in Generate): the composer follows the Generate form."""
        if self._composing:
            self._refresh_choices()
            self._invalidate_preview()  # the backend or Inpaint source may have changed without changing the request
            self.preview()

    @property
    def composing(self) -> bool:
        return self._composing

    def _configure_dimensions(self) -> None:
        page = self.page
        page.planModelsEdit.configure(
            "search", "Model", options=lambda: self._choices().get("models", []), custom="Not listed"
        )
        page.planSamplersEdit.configure("search", "Sampler", options=lambda: self._choices().get("samplers", []))
        page.planSizesEdit.configure("choice", "Size", options=self._size_options)
        page.planStepsEdit.configure("int", "Steps", minimum=1, maximum=150)
        page.planGuidanceEdit.configure("float", "Guidance", minimum=0, maximum=50, step=0.5, decimals=2)
        page.planStrengthsEdit.configure("float", "Strength", minimum=0, maximum=1, step=0.05, decimals=2)
        page.planLoraSetsEdit.configure(
            "search",
            "LoRA",
            options=self._lora_set_options,
            custom="Not listed",
            weights=lambda: self._choices().get("loras", {}),
            convert=self._lora_set_chip,
        )

    def _lora_set_options(self) -> list[str]:
        return [NO_LORA, GENERATE_LORAS, *self._choices().get("loras", {})]

    def _lora_set_chip(self, chosen) -> str:
        """A picked LoRA (with its weight), None, or the Generate form's whole set, as a LoRA-set chip."""
        identifier, weight = chosen
        if identifier == NO_LORA:
            return NO_LORA
        if ";" in identifier:
            self._message(Presentation("error", "LoRA not added", "A LoRA set cannot name an identifier with ';'."))
            return ""
        if identifier == GENERATE_LORAS:
            try:
                request, *_ = self._request_source(preview=True)
            except (FormError, ValidationError):
                return ""
            if any(";" in item.identifier for item in request.loras):
                self._message(Presentation("error", "LoRA not added", "A LoRA set cannot name an identifier with ';'."))
                return ""
            return " + ".join(lora_set_value(item.identifier, item.weight) for item in request.loras) or NO_LORA
        return lora_set_value(identifier, weight)

    def _size_options(self) -> list[str]:
        current = self._choices().get("size")
        return [*([current] if current else []), *SIZE_PRESETS]

    def _refresh_choices(self) -> None:
        """Offered values follow the Generate form: its size first, its steps/guidance/strength as the box values."""
        page = self.page
        page.planSizesEdit.refresh_options()
        try:
            request, *_ = self._request_source(preview=True)
        except (FormError, ValidationError):
            return
        page.planStepsEdit.refresh_options(str(request.steps))
        page.planGuidanceEdit.refresh_options(str(request.guidance))
        page.planStrengthsEdit.refresh_options(str(request.strength))

    def _planned(self) -> bool:
        return self.page.planModeCombo.currentText() != REPEAT

    def _mode_changed(self, *_) -> None:
        """Repeat shows the item count; Pair and Cross show the dimensions and the preview table."""
        page = self.page
        planned = self._planned()
        page.queueCountLabel.setVisible(not planned)
        page.queueCountSpinBox.setVisible(not planned)
        for name in PLAN_WIDGETS:
            label = getattr(page, name.replace("Edit", "Label"))
            label.setVisible(planned)
            getattr(page, name).setVisible(planned)
        page.planIncrementCheck.setVisible(planned)
        page.planTableView.setVisible(planned)
        for name in ("planDrawLabel", "planDrawBox", "planVariationLabel", "planVariationBox"):
            getattr(page, name).setVisible(planned)
        self._sync_random_rows()
        self.sync_edit_fields()
        self._plan_changed()

    def _draws(self) -> tuple[tuple[str, int], ...]:
        spins = ((name, getattr(self.page, f"planDraw{suffix}Spin")) for name, suffix in DRAW_SPINS.items())
        # A hidden spin (Strength outside Inpaint) belongs to a row that does not apply, so it counts for nothing.
        return tuple((name, spin.value()) for name, spin in spins if spin.value() and not spin.isHidden())

    def _random_active(self) -> bool:
        page = self.page
        mode = page.planVariationCombo.currentText()
        edits = (
            page.planShuffleCombo.currentIndex() > 0
            or page.planDropSpin.value() > 0
            or (page.planAddSpin.value() > 0 and bool(page.planAddPartsEdit.text().strip()))
        )
        return self._planned() and (bool(self._draws()) or mode == "Random sample" or (mode != "Off" and edits))

    def _sync_draw_controls(self) -> None:
        """Each draw count shows how many values its row lists ("2 of 5"). The count the user chose is never changed.

        An impossible request (more than the row lists, or a row without values) is reported inline by the planner,
        so emptying and refilling a row, or pasting a new list, keeps the draw count.
        """
        page = self.page
        form = self._plan_form()
        counts = candidate_counts(form)
        for key, suffix in DRAW_SPINS.items():
            spin = getattr(page, f"planDraw{suffix}Spin")
            available = counts.get(key)  # None: seeds, drawn from the whole seed range
            spin.blockSignals(True)
            spin.setSuffix(f" of {available}" if available else "")
            spin.setSpecialValueText("Listed" if available or available is None else "No values")
            spin.setEnabled(available is None or available > 0 or spin.value() > 0)  # a stale count can still be zeroed
            spin.blockSignals(False)
        page.planAddSpin.setEnabled(bool(form.add_parts.strip()) or page.planAddSpin.value() > 0)

    def _sync_random_rows(self) -> None:
        """The wildcard, edit and seed rows appear only when they matter; Reroll and the folder note follow the seed."""
        page = self.page
        varied = self._planned() and page.planVariationCombo.currentText() != "Off"
        for name in ("planWildcardsLabel", "planWildcardsEdit", "planMutationLabel", "planMutationBox"):
            getattr(page, name).setVisible(varied)
        for name in ("planAddPartsLabel", "planAddPartsEdit"):
            getattr(page, name).setVisible(varied)
        page.planVariationCountSpin.setVisible(page.planVariationCombo.currentText() == "Random sample")
        seeded = self._random_active()
        for name in ("planRngSeedLabel", "planRngSeedEdit", "planRerollButton"):
            getattr(page, name).setVisible(seeded)
        folder = self._wildcards_dir
        names = list_names(folder)
        page.planWildcardsFolderLabel.setVisible(varied)
        page.planWildcardsFolderLabel.setText(
            f"Lists in {folder}: {', '.join(names)}" if names else f"Wildcard files go in {folder} as NAME.txt."
        )

    @property
    def _wildcards_dir(self) -> Path:
        return self._state / "wildcards"

    def _reroll(self, *_) -> None:
        self.page.planRngSeedEdit.setText(str(new_rng_seed()))  # textChanged refreshes the preview

    def _ensure_rng_seed(self) -> None:
        """A random plan always has one recorded seed: draw it the first time it is needed."""
        edit = self.page.planRngSeedEdit
        if self._random_active() and not edit.text().strip():
            edit.blockSignals(True)
            edit.setText(str(new_rng_seed()))
            edit.blockSignals(False)

    def _count(self) -> tuple[str, str]:
        return count_text(self._plan_form(), self._choices().get("prompt"), self._wildcards_dir)

    def _base_text(self, request) -> str:
        loras = len(request.loras)
        parts = [
            _clip(request.prompt.strip().splitlines()[0]) if request.prompt.strip() else "(no prompt)",
            request.model_id,
            f"{request.width_px}x{request.height_px}",
            f"{request.steps} steps",
            f"guidance {request.guidance:g}",
            str(getattr(request.sampler_id, "value", request.sampler_id)),
            f"seed {request.seed}",
        ]
        if loras:
            parts.append(f"{loras} LoRA{'s' if loras != 1 else ''}")
        if self._edit_source() is not None:
            parts.append(f"Inpaint · strength {request.strength:g}")
        return " · ".join(parts)

    def preview(self) -> None:
        """Live count and items for the composer: pure planning against the Generate form, nothing persisted or sent."""
        self._preview_timer.stop()
        if not self._composing or not self.page.queueWorkspace.isVisible():
            self._invalidate_preview()
            return  # reading the request marks and snaps Generate fields: only while Queues is shown (see ``shown``)
        page = self.page
        try:
            request, *_ = self._request_source(preview=True)
        except FormError as exc:
            self._invalidate_preview()  # a validity change must not be overwritten later
            page.composerBaseLabel.setText("Generate settings are not valid yet.")
            self._show(page.planCountLabel, "error", sentence(f"Generate: {exc}"))
            self.plan_model.clear()
            self._clear_summary()
            return
        form = self._plan_form()
        lists = fingerprint(self._wildcards_dir) if form.variation != "Off" else ()  # an edited list changes the items
        key = (form, self._request_key(request), lists)
        if key == self._preview_key:
            return  # polling and presentation syncs reach here unchanged: keep what is shown or being drawn
        self._invalidate_preview()
        self._preview_key = key
        page.composerBaseLabel.setText(self._base_text(request))
        page.composerBaseLabel.setToolTip(request.prompt)
        if not self._planned():
            self._mark_plan(None)
            count = page.queueCountSpinBox.value()
            first = request.seed
            seeds = f"seed {first}" if count == 1 else f"seeds {first}–{first + count - 1}"
            self._show(page.planCountLabel, "ok", f"{count} item{'s' if count != 1 else ''} · {seeds}")
            self._show_summary(request, count, "Nothing (repeats the Generate settings)", seeds.capitalize())
            return
        if self._plan_form().variation != "Off" and not self._runner.closed:
            self._preview_in_background(request)
            return
        plan = self._plan_for(request)
        if plan is None:
            self._show_summary(request, None, "—", "—")
            return
        self._summarize(request, plan, self.plan_model.rowCount())

    @staticmethod
    def _request_key(request):
        """The request without the id every read draws afresh; the retained random seed stays, so a new one shows."""
        return replace(request, request_id="preview")

    @property
    def previewing(self) -> bool:
        """True while a prompt-variation preview is still being resolved in the background."""
        return self._previewing

    def _invalidate_preview(self) -> None:
        """Whatever is being drawn, or waits to be, is out of date: its result must never reach the composer."""
        self._preview_token += 1
        self._preview_key = None
        self._previewing = False
        self._preview_waiting = None

    def _preview_in_background(self, request) -> None:
        """Sampling thousands of prompts must not freeze typing: resolve on a worker and drop outdated results.

        At most one computation runs; a newer request waits in a single slot (replacing any older one), so repeated
        edits or rerolls never pile up work in front of the newest preview.
        """
        job = (self._preview_token, request, self._plan_inputs(request))
        self._previewing = True
        self._show(self.page.planCountLabel, "unknown", "Resolving prompts…")
        if self._preview_active:
            self._preview_waiting = job
            return
        self._start_preview(job)

    def _start_preview(self, job: tuple) -> None:
        token, request, inputs = job
        self._preview_active = True

        async def work():
            return await asyncio.to_thread(self._compute_plan, *inputs)

        self._runner.submit(work(), lambda result, failure: self._preview_finished(token, request, result, failure))

    def _preview_finished(self, token: int, request, result, failure) -> None:
        self._preview_active = False
        waiting, self._preview_waiting = self._preview_waiting, None
        if waiting is not None and waiting[0] == self._preview_token and self._composing:
            self._start_preview(waiting)  # the newest request; this older result is not worth showing
            return
        self._preview_resolved(token, request, result, failure)

    def _preview_resolved(self, token: int, request, result, failure) -> None:
        if token != self._preview_token or not self._composing:
            return
        if isinstance(failure, asyncio.CancelledError):
            return  # cancelled work has no result to show; a newer preview is on its way or the window is closing
        self._previewing = False
        self._mark_plan(None)
        if failure is not None:
            if isinstance(failure, ValidationError):
                self._plan_failed(failure)
            else:
                self._show(self.page.planCountLabel, "error", sentence(str(failure) or "The preview failed."))
                self.plan_model.clear()
            self._show_summary(request, None, "—", "—")
            return
        plan, table = result
        self._show_table(table)
        self._show(self.page.planCountLabel, "ok", summary_text(table))
        self._summarize(request, plan, table.total)

    def _summarize(self, request, plan: Plan, total: int) -> None:
        varies = (
            ", ".join(_dimension_text(name, len(values)) for name, values in plan.dimensions if len(values) > 1)
            or "Nothing"
        )
        policy = (
            "increment"
            if plan.seed_policy == "increment"
            else "dimension"
            if any(name == "seed" for name, _ in plan.dimensions)
            else "fixed"
        )
        sets = dict(plan.dimensions).get("loras")
        self._show_summary(
            request,
            total,
            varies,
            sentence(SEED_POLICY_TEXT[policy]),
            loras=None if sets is None else f"{len(sets)} LoRA set{'s' if len(sets) != 1 else ''}, per item",
        )

    def _show_summary(self, request, count: int | None, varies: str, seeds: str, *, loras: str | None = None) -> None:
        """The right column while composing: what the queue being built will be, before anything is saved."""
        page = self.page
        name, endpoint = self._backend()
        page.composerSummaryBackendLabel.setText(f"{name} · {endpoint}" if endpoint else name)
        page.composerSummaryModelLabel.setText(request.model_id)
        loras = loras or "\n".join(lora_text(lora.identifier, lora.weight) for lora in request.loras)
        page.composerSummaryLorasLabel.setText(loras or "None")
        page.composerSummaryItemsLabel.setText("—" if count is None else str(count))
        page.composerSummaryVariesLabel.setText(varies)
        page.composerSummarySeedsLabel.setText(seeds)
        page.composerSummaryCapLabel.setText(str(page.newQueueCapSpinBox.value()))

    def _clear_summary(self) -> None:
        """Generate is not valid yet: the summary shows no stale values from an earlier request."""
        page = self.page
        for name in ("Model", "Loras", "Items", "Varies", "Seeds"):
            getattr(page, f"composerSummary{name}Label").setText("—")
        page.composerSummaryCapLabel.setText(str(page.newQueueCapSpinBox.value()))

    def _schedule_preview(self, *, invalidate: bool = True) -> None:
        if invalidate:
            self._invalidate_preview()  # the inputs changed: a result still being drawn is already stale
        if self._composing and self.page.queueWorkspace.isVisible():
            self._preview_timer.start()

    # -- creation --------------------------------------------------------------------------------------------------
    def create(self, action: str = "save", *, count: int | None = None, quick: bool = False) -> None:
        """Create from the composer: ``save`` keeps it paused, ``add`` queues it up next, ``start`` dispatches it.

        ``quick`` is the Queue popover (#135): ``count`` repeats Generate's request, and ``None`` uses the composer's
        current plan. Its outcome goes to the shared overlay, so nothing leaves the current tab."""
        report = self._quick_report if quick else self._message
        if action == "start" and (self.dispatching_here or self._activity.generating):
            if quick:
                report(Presentation("warning", "Queue not created", "A run started meanwhile; use Add to queue."))
            return
        try:
            request, name, endpoint, asset_ids = self._request_source()
        except FormError as exc:
            # From another tab the marked field is out of sight, so say where it is.
            report(Presentation("error", "Queue not created", f"On Generate: {exc}" if quick else str(exc)))
            return
        plan, resolving = None, None
        if (count is None) if quick else self._planned():
            if self._plan_form().variation != "Off":
                resolving = self._plan_inputs(request)  # prompt sampling runs on the worker, with the creation
            else:
                plan = self._plan_for(request)
                if plan is None:
                    error = self.page.planCountLabel.text()
                    report(Presentation("error", "Queue not created", error))
                    return
        count, cap = count or self.page.queueCountSpinBox.value(), self.page.newQueueCapSpinBox.value()
        backend = {"name": name, "endpoint": endpoint, "scenario": "success", **self._simulation()}
        job = self._edit_source()
        # Add to queue during a single generation: remember the intent now, since the generation may end before the
        # new queue is saved (nothing then runs to follow, but the user still asked for it to start by itself).
        after_generation = quick and action == "add" and self._activity.generating

        async def work():
            drawn = plan
            if resolving is not None:
                drawn = await asyncio.to_thread(self._resolve_only, *resolving)
            queue_id, used = await self._create(
                backend, request, asset_ids, count, cap, drawn, job, up_next=action == "add"
            )
            status = (await read_queue_views(self._state, queue_id))[0]["status"] if action == "add" else None
            return queue_id, used, status

        if quick:
            self._runner.submit(
                work(), lambda result, failure: self._quick_created(result, failure, action, after_generation)
            )
        else:
            self._runner.submit(work(), lambda result, failure: self._created(result, failure, action))

    # -- one-click Queue popover (#135) ----------------------------------------------------------------------------
    def last_plan(self) -> tuple[str, str]:
        """(level, text) of the composer's Pair/Cross plan for the popover; ``ok`` when it can be used as is."""
        if not self._planned():
            return "none", "Set up Pair or Cross in the Queues composer to reuse it here."
        level, text = self._count()
        return level, f"{self.page.planModeCombo.currentText()} · {text}" if level == "ok" else sentence(text)

    def last_plan_total(self) -> int | None:
        if not self._planned():
            return None
        try:
            prompt = self._choices().get("prompt")
            plan = bind_wildcards(build_plan(self._plan_form()), prompt or "", self._wildcards_dir)
            return plan_size(plan, prompt)
        except ValidationError:
            return None

    def quick_state(self) -> tuple[bool, bool, str]:
        """(Start now, Add to queue, reason): a new request waits behind the running work (#133)."""
        if self._activity.generating:
            return False, True, "A generation is running: Add to queue starts this one when it ends."
        if self.dispatching_here:
            return False, True, "A queue is running: Add to queue starts this one next, by itself."
        if self._live_run():
            return False, True, "Another process runs a queue: Add to queue starts this one after it."
        return True, False, "Start now creates the queue and starts it at once."

    def quick_cap(self) -> int:
        """The new queue's Max at once: the composer's value (the shared cloud default with cloud chosen)."""
        return self.page.newQueueCapSpinBox.value()

    def quick_create(self, action: str, *, count: int | None) -> None:
        self.create(action, count=count, quick=True)

    def _after_generation_ended(self) -> None:
        if self._after_generation and not self._activity.generating and not self._activity.dispatching:
            self._waiting_timer.start(0)  # once the generation's own result has settled

    def _start_after_generation(self) -> None:
        """Start the oldest popover queue that waited for a single generation; the next one follows its dispatch."""
        if self._runner.closed:
            self._after_generation.clear()  # shutting down: nothing may be submitted or dispatched
            return
        if self._activity.generating or self._activity.dispatching or not self._after_generation:
            return
        if self._checking_waiting:
            return
        self._checking_waiting = True
        queue_id = self._after_generation.pop(0)

        async def status():
            views = await read_queue_views(self._state, queue_id)
            return views[0]["status"] if views else None

        self._runner.submit(status(), lambda result, failure: self._start_waiting(queue_id, result, failure))

    def _start_waiting(self, queue_id: str, status, failure) -> None:
        """Read fresh: a queue started, cancelled or removed meanwhile is no longer ours to start."""
        self._checking_waiting = False
        if self._runner.closed:
            return
        if failure is not None or status != "paused":
            self._start_after_generation()
        elif self._activity.generating or self._activity.dispatching:
            self._after_generation.insert(0, queue_id)  # other work started during the read; wait for it again
        else:
            self._dispatch(queue_id, resume_fallback=None)

    def _quick_report(self, presentation: Presentation) -> None:
        state = {"ok": "success", "warning": "partial"}.get(presentation.level, "error")
        activity_strip(self.page).complete("queue-quick", presentation.headline, presentation.detail, state)

    def _quick_created(self, result, failure, action: str, after_generation: bool = False) -> None:
        """Like ``_created``, but the composer and the current tab stay as they are."""
        if failure is not None:
            if isinstance(failure, PlanError | FormError):
                self._plan_failed(failure)
                self._quick_report(Presentation("error", "Queue not created", sentence(str(failure))))
                return
            self._quick_report(present_generation_failure(failure))
            return
        queue_id, _, status = result
        self._created_hook()
        if action == "start" and not self.dispatching_here and not self._activity.generating:
            self._dispatch(queue_id, resume_fallback=None)  # progress and results use the shared overlay
        elif action == "start":
            self._quick_report(
                Presentation("warning", "Saved paused", "A run started meanwhile; press Start when it ends.")
            )
        elif status != "queued" and after_generation:
            # No queue runs to follow, so this window starts it once the generation ends (now, if it already has).
            self._after_generation.append(queue_id)
            self._quick_report(
                Presentation(
                    "ok",
                    "Added to queue",
                    "It starts by itself when the running generation ends, if this window stays open.",
                )
            )
            self._after_generation_ended()
        elif status != "queued":
            self._quick_report(
                Presentation(
                    "warning", "Saved paused", "The running queue had already ended, so this queue waits for Start."
                )
            )
        else:
            self._quick_report(
                Presentation(
                    "ok", "Added to queue", "It waits up next and starts by itself when the running queue ends."
                )
            )
        self.refresh()

    def queue_behind(self, request, name, endpoint, asset_ids, job=None) -> None:
        """Generate while this window dispatches: a one-item queue that waits up next and starts by itself.

        If the running queue ended meanwhile, nothing would start it, so it is saved paused and the outcome says so.
        """
        backend = {"name": name, "endpoint": endpoint, "scenario": "success", **self._simulation()}

        async def work():
            queue_id, _ = await self._create(backend, request, asset_ids, 1, None, None, job, up_next=True)
            return queue_id, (await read_queue_views(self._state, queue_id))[0]["status"]

        self._runner.submit(work(), self._behind)

    def _behind(self, result, failure) -> None:
        strip = activity_strip(self.page)
        if failure is not None:
            presentation = present_generation_failure(failure)
            strip.complete("queue-behind", presentation.headline, presentation.detail, "error")
            return
        _, status = result
        if status == "queued":
            strip.complete(
                "queue-behind", "Queued behind the running queue", "It starts by itself when that queue ends."
            )
        else:
            strip.complete(
                "queue-behind",
                "Saved paused",
                "The running queue had already ended, so this queue waits for Start.",
                "partial",
            )
        self.refresh()

    async def _create(
        self, backend, request, asset_ids, count, cap, plan: Plan | None = None, job=None, *, up_next: bool = False
    ):
        request = await bind_prepared(self._state, backend["name"], request, asset_ids)
        receipts = ()
        sets = dict(plan.dimensions).get("loras") if plan is not None else None
        if sets and backend["name"] == "cloud":
            # LoRA sets may name custom cloud LoRAs that are not on Generate: bind their confirmed receipts too.
            known = self._choices().get("receipts", {})
            named = {identifier for chosen in sets for identifier, _ in chosen} - {x.identifier for x in request.loras}
            receipts = await load_prepared(
                self._state, "cloud", [known[name] for name in sorted(named) if name in known]
            )
        if job is not None:
            # An Inpaint edit: stage the prepared source and mask once; nothing is sent until Start.
            selection = await asyncio.to_thread(
                selection_for, backend["name"], backend["endpoint"], self._state, **self._simulation()
            )
            prepared = await asyncio.to_thread(
                prepare_edit_inputs,
                job.source,
                job.mask,
                request.width_px,
                request.height_px,
                grow=job.grow,
                feather=job.feather,
            )
            return await create_edit_queue(
                self._state,
                backend,
                selection,
                request,
                prepared,
                strength=job.strength,
                preserve_outside=job.keep_outside,
                plan=plan,
                count=count,
                cap=cap,
                up_next=up_next,
                receipts=receipts,
            )
        items = expand_plan(request, plan, receipts=receipts) if plan is not None else expand_items(request, count)
        return await create_queue(self._state, backend, items, cap, up_next=up_next)

    # -- planning --------------------------------------------------------------------------------------------------
    def _plan_form(self) -> PlanForm:
        page = self.page
        return PlanForm(
            page.planModeCombo.currentText(),
            page.planPromptsEdit.toPlainText(),
            page.planModelsEdit.text(),
            page.planSeedsEdit.text(),
            page.planStepsEdit.text(),
            page.planGuidanceEdit.text(),
            page.planSamplersEdit.text(),
            page.planSizesEdit.text(),
            page.planLorasEdit.toPlainText(),
            page.planIncrementCheck.isChecked(),
            "" if page.planStrengthsEdit.isHidden() else page.planStrengthsEdit.text(),
            page.planLoraSetsEdit.text(),
            self._draws(),
            page.planVariationCombo.currentText(),
            page.planVariationCountSpin.value(),
            page.planWildcardsEdit.toPlainText(),
            page.planShuffleCombo.currentText(),
            page.planDropSpin.value(),
            page.planAddSpin.value(),
            page.planAddPartsEdit.text(),
            page.planRngSeedEdit.text(),
        )

    def _mark_plan(self, widget_name: str | None) -> None:
        for name in PLAN_WIDGETS:
            widget = getattr(self.page, name)
            widget.setProperty("invalid", name == widget_name)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _plan_changed(self, *_) -> None:
        """The arithmetic count at once (pure); the validated preview follows after a short pause."""
        self._sync_draw_controls()
        self._sync_random_rows()
        self._ensure_rng_seed()
        if self._planned():
            level, text = self._count()
        else:
            count = self.page.queueCountSpinBox.value()
            level, text = "ok", f"{count} item{'s' if count != 1 else ''}"
        self._show(self.page.planCountLabel, level, sentence(text))
        self.page.startQueueButton.setText(f"Start now ({text})" if level == "ok" else "Start now")
        self._actions_fit.refit()
        self._schedule_preview()
        self.plan_hook()

    def _show_table(self, table: PlanTable) -> None:
        self.plan_model.clear()
        self.plan_model.setHorizontalHeaderLabels(list(table.columns))
        for row in table.rows:
            self.plan_model.appendRow([QStandardItem(cell) for cell in row])
        self.page.planTableView.resizeColumnsToContents()

    def _plan_for(self, request) -> Plan | None:
        """Build and fully validate the plan against the form's request; shows the preview table.

        Wildcard files are read only when the template names them, and again only when they change on disk.
        """
        self._mark_plan(None)
        try:
            plan, table = self._compute_plan(*self._plan_inputs(request))
        except ValidationError as exc:
            self._plan_failed(exc)
            return None
        self._show_table(table)
        self._show(self.page.planCountLabel, "ok", summary_text(table))
        return plan

    def _plan_inputs(self, request) -> tuple:
        """Everything the pure planning step needs, read on the GUI thread so a worker can run it."""
        return self._plan_form(), request, self._wildcards_dir, self._edit_source() is not None

    @staticmethod
    def _compute_plan(form: PlanForm, request, folder: Path, edit: bool) -> tuple[Plan, PlanTable]:
        """Pure but for wildcard files: the drawn plan (what the summary and creation use) and its preview table."""
        plan = resolve_plan(bind_wildcards(build_plan(form), request.prompt, folder), request.prompt)
        return plan, describe_plan(request, plan, edit=edit)

    @staticmethod
    def _resolve_only(form: PlanForm, request, folder: Path, edit: bool) -> Plan:
        """The drawn plan without the preview table: creation expands it itself."""
        return resolve_plan(bind_wildcards(build_plan(form), request.prompt, folder), request.prompt)

    def _plan_failed(self, exc: ValidationError) -> None:
        """One place for a planning error: beside the plan, with the owning field marked."""
        error = plan_error(exc)
        self._mark_plan(error.widget)
        self._show(self.page.planCountLabel, "error", sentence(str(error)))
        self.page.startQueueButton.setText("Start now")
        self.plan_model.clear()

    def _created(self, result, failure, action: str = "save") -> None:
        if failure is not None:
            if isinstance(failure, PlanError | FormError):
                self._plan_failed(failure)
                self._message(Presentation("error", "Queue not created", sentence(str(failure))))
                return
            self._message(present_generation_failure(failure))
            return
        queue_id, cap, status = result
        self._created_hook()
        self._composing = False
        self._preview_timer.stop()
        self._select_after = queue_id  # selected once the next read lists it, so no empty state flashes meanwhile
        if action == "start" and not self.dispatching_here and not self._activity.generating:
            self._dispatch(queue_id, resume_fallback=None)
        elif action == "start":
            self._message(Presentation("warning", "Saved paused", "A run started meanwhile; press Start when it ends."))
        elif action == "add" and status == "queued":
            self._message(
                Presentation(
                    "ok", "Added to queue", "It waits up next and starts by itself when the running queue ends."
                )
            )
        elif action == "add":
            self._message(
                Presentation(
                    "warning", "Saved paused", "The running queue had already ended, so this queue waits for Start."
                )
            )
        else:
            self._message(Presentation("ok", "Queue created", f"Paused with a maximum of {cap} at once. Press Start."))
        self._render()
        self.refresh()

    # -- dispatch and controls -------------------------------------------------------------------------------------
    def _queue_backend_event(self, wrapped: _QueueBackendEvent) -> None:
        event = wrapped.event
        if wrapped.queue_id == self._dispatching:
            self._dispatch_started = True
        key = (wrapped.queue_id, wrapped.item_id)
        if event.kind == "progress" and type(event.completed) is int and type(event.total) is int and event.total > 0:
            self._run_progress[key] = (max(0, event.completed), event.total)
        elif event.kind == "terminal":
            self._run_progress.pop(key, None)
        self._publish_run_state()

    def trigger_run_control(self) -> None:
        """Route the compact header control through the same Start/Pause/Resume paths as the Queues page."""
        if self._activity.generating:
            return
        view = self._run_view()
        available = self._controls_for(view)
        if view is None:
            return
        if needs_recovery(view) and available.start and view is self._view():
            self.start()
        elif available.pause:
            self._post("pause", view["queue_id"])
        elif available.resume:
            self._post("resume", view["queue_id"])
        elif available.start and view is self._view():
            self.start()

    def primary(self) -> None:
        """The header's one action, whichever of Start, Start next, Pause or Resume applies now."""
        view = self._view()
        available = self._controls_for(view)
        owned = bool(view and self._dispatching == view["queue_id"])
        action = primary_action(view, available, started=self._started(view), owned=owned)
        if action is None:
            return
        if action.key == "start":
            self.start()
        elif action.key in ("enqueue", "dequeue"):
            self._order(action.key)
        else:
            self._post(action.key)

    def start(self) -> None:
        view = self._view()
        if view is not None and self._controls_for(view).enqueue:
            self._order("enqueue")
            return
        if view is None or self.dispatching_here or self._activity.generating:
            return
        # A queue that ran before may still be owned by another process; if so, Resume is posted to it instead.
        self._dispatch(view["queue_id"], resume_fallback=view["queue_id"] if self._started(view) else None)

    def _dispatch(self, queue_id: str, *, resume_fallback: str | None) -> None:
        """Make this window the dispatch owner of ``queue_id`` (an explicit Start or Start now)."""
        self._resume_fallback = resume_fallback
        self._dispatching = queue_id
        self._dispatch_started = False
        self._activity.set(dispatching=True)
        self._render()
        saved = self._saved()
        self._future = self._runner.submit(
            dispatch_queue(
                self._state,
                queue_id,
                saved=saved,
                on_event=lambda item_id, event: self._event_relay(_QueueBackendEvent(queue_id, item_id, event)),
                on_next=lambda settled, following: self._advance_relay(_QueueAdvance(settled, following)),
            ),
            self._dispatched,
        )
        self._timer.start()

    def _advanced(self, advance: _QueueAdvance) -> None:
        """Auto-play: the settled queue reports like a finished dispatch, and this window now owns the next one."""
        if self._dispatching is None:
            return  # a late delivery after dispatch ended
        presentation = Presentation(*outcome_text(advance.settled))
        activity_strip(self.page).complete(
            "queue-result",
            "Queue · " + presentation.headline,
            presentation.detail + f" Next: {advance.following[:8]}.",
            "success" if presentation.level == "ok" else "partial",
        )
        self._dispatching, self._dispatch_started = advance.following, False
        self._message(Presentation("ok", "Up next started", f"{advance.following[:8]} started by itself."))
        self.refresh()

    def _dispatched(self, final, failure) -> None:
        self._dispatching, self._future = None, None
        self._dispatch_started = False
        waiting = bool(self._after_generation)
        if failure is not None:
            # A dispatch-level failure (busy state, backend, inventory, persistence) stops auto-play, as everywhere: the
            # queues waiting for a generation stay paused. Cleared before idle is published, which would start the next.
            self._after_generation.clear()
        self._activity.set(dispatching=False)
        fallback, self._resume_fallback = self._resume_fallback, None
        if failure is not None and waiting:
            reason = (
                "Another process owns dispatch"
                if isinstance(failure, StateBusyError)
                else "The previous queue could not be dispatched"
            )
            activity_strip(self.page).complete(
                "queue-quick", "Waiting queues saved paused", f"{reason}; press Start later.", "partial"
            )
        if isinstance(failure, StateBusyError) and fallback is not None:
            self._post("resume", fallback)
            presentation = Presentation(
                "warning",
                "Dispatch is owned by another process",
                "Resume was posted to it; nothing was dispatched from this window.",
            )
        elif isinstance(failure, StateBusyError):
            presentation = Presentation(
                "warning",
                "Dispatch is owned by another process",
                "Controls still work; Start is available when it stops. Nothing was dispatched.",
            )
        elif failure is not None:
            presentation = present_generation_failure(failure)
        else:
            presentation = Presentation(*outcome_text(final))
        state = (
            "error"
            if failure
            else "success"
            if presentation.level == "ok"
            else "partial"
            if final["status"] == "finished"
            else "stopped"
        )
        activity_strip(self.page).complete(
            "queue-result", "Queue · " + presentation.headline, presentation.detail, state
        )
        self._render()
        self.refresh()

    def cancel(self) -> None:
        """A waiting queue holds no reservation, so it is cancelled at once; any other queue gets a posted control."""
        view = self._view()
        if view is not None and view["status"] == "queued":
            self._order("cancel")
        else:
            self._post("cancel")

    def pause(self) -> None:
        view = self._view()
        if view is not None and view["status"] == "queued":
            self._order("dequeue")
        else:
            self._post("pause")

    def _order(self, action: str) -> None:
        view = self._view()
        if view is None:
            return
        self._runner.submit(change_queue_order(self._state, view["queue_id"], action), self._ordered(action))

    def _ordered(self, action: str):
        def done(_, failure) -> None:
            if failure is not None:
                self._message(present_generation_failure(failure))
                return
            text = {
                "enqueue": "Waits up next and starts by itself when the running queue ends.",
                "dequeue": "Left the up-next order and stays paused until Start.",
                "cancel": "Cancelled. It left the up-next order and nothing of it ran.",
                "earlier": "Moved earlier in the up-next order.",
                "later": "Moved later in the up-next order.",
            }[action]
            self._message(Presentation("ok", "Up next", text))
            self.refresh()

        return done

    def _post(self, action: str, queue_id: str | None = None) -> None:
        view = self._view()
        target = queue_id or (view["queue_id"] if view is not None else None)
        if target is None:
            return
        self._runner.submit(post_queue_control(self._state, target, action), self._posted(action))

    def _posted(self, action: str):
        def done(_, failure) -> None:
            if failure is not None:
                self._message(present_generation_failure(failure))
                return
            text = f"{action.capitalize()} requested. " + (
                "The dispatch owner applies it at its next poll." if self.dispatching_here else POSTED
            )
            self._message(Presentation("ok", "Control posted", text))
            self.refresh()

        return done

    def clear_queues(self) -> None:
        panel = feedback(self.page)
        if panel.commit(self.page.clearQueuesButton):
            return

        def proceed():
            keep = (self._dispatching,) if self._dispatching else ()
            self._runner.submit(clear_queues(self._state, keep), self._cleared)

        confirm_action(self.page, self.page.clearQueuesButton, "Clear history", CLEAR_TEXT, proceed, danger=True)

    def _cleared(self, removed, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        text = f"{removed} queue(s) removed." if removed else "No finished or cancelled queue to remove."
        self._message(Presentation("ok", "History cleared", text))
        self.refresh()

    def remove(self) -> None:
        """Remove the selected queue record after inline consent; its attempts and outputs stay."""
        panel = feedback(self.page)
        if panel.commit(self.page.removeQueueButton):
            return
        view = self._view()
        if not self._removable(view):
            return
        queue_id = view["queue_id"]

        def proceed():
            keep = (self._dispatching,) if self._dispatching else ()
            self._runner.submit(remove_queue(self._state, queue_id, keep), self._removed)

        confirm_action(
            self.page,
            self.page.removeQueueButton,
            "Remove queue",
            f"{queue_title(view)}: {REMOVE_TEXT}",
            proceed,
            danger=True,
        )

    def _removed(self, _, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        self._message(Presentation("ok", "Queue removed", "Its attempts and saved images are kept."))
        self.refresh()

    # -- concurrency -----------------------------------------------------------------------------------------------
    def _cap_changed(self, value: int) -> None:
        """The header maximum posts a ``cap`` control for the selected queue only (active work is kept)."""
        if self._syncing:
            return
        try:
            validate_concurrency(value)
        except ValidationError as exc:
            self._message(Presentation("error", "Concurrency not changed", str(exc)))
            return
        self._set_level(self.page.concurrencySpinBox, value)
        view = self._view()
        if not self._editable(view):
            return
        self._runner.submit(set_concurrency(self._state, value, queue_id=view["queue_id"]), self._cap_done)

    def _new_cap_changed(self, value: int) -> None:
        """The new-queue maximum; with cloud chosen it is also saved as the shared default for new cloud queues."""
        self.page.composerSummaryCapLabel.setText(str(value))
        if self._syncing:
            return
        self._set_level(self.page.newQueueCapSpinBox, value)
        if self._backend()[0] != "cloud":
            return
        self._shared_epoch += 1
        self._shared_cap = value
        self._runner.submit(set_concurrency(self._state, value, save_shared=True), self._cap_done)

    def _cap_done(self, value, failure) -> None:
        """A saved maximum shows in its own box; only a refusal reaches the overlay."""
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        self.refresh()

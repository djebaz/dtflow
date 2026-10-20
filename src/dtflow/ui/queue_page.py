"""Queues page controller: widgets in, shared queue services out. Owns no scheduling, reservation or persistence rules.

Reads are read-only polls of the durable state; every control is posted through the same function ``dtflow queue``
uses, so a window and a CLI process drive a queue identically. Only Start makes this window the dispatch owner, which
holds the state ownership lock for the run (see ``Activity``). Wording after cancel comes from recorded evidence,
and cancel never claims remote cancellation.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QListWidgetItem, QWidget

from dtflow.core.models import BackendEvent, ValidationError
from dtflow.persistence.settings import SettingsError, load_cloud_concurrency, validate_concurrency
from dtflow.persistence.store import StateBusyError
from dtflow.services.connection import Presentation, present_generation_failure
from dtflow.services.image_preparation import prepare_edit_inputs
from dtflow.services.planning import Plan, PlanTable, describe_plan, expand_plan
from dtflow.services.queue_session import (
    change_queue_order,
    clear_queues,
    create_edit_queue,
    create_queue,
    dispatch_queue,
    expand_items,
    post_queue_control,
    read_queue_views,
    set_concurrency,
)
from dtflow.services.session import bind_prepared, selection_for
from dtflow.ui.activity import Activity, RunState
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.generation_presenter import FormError
from dtflow.ui.inline_feedback import confirm_action, feedback
from dtflow.ui.planning_presenter import (
    ERROR_FIELDS,
    PlanForm,
    build_plan,
    count_text,
    plan_error,
    sentence,
    summary_text,
)
from dtflow.ui.queue_model import ITEM_ID_ROLE, QueueItemModel
from dtflow.ui.queue_presenter import (
    CONCURRENCY_HINT,
    concurrency_level,
    controls,
    event_lines,
    has_started,
    item_rows,
    outcome_note,
    outcome_text,
    ownership_badge,
    progress_text,
    summarize,
    up_next_ids,
)
from dtflow.ui.runner import AsyncRunner, EventRelay

POLL_MS = 1000  # read interval while anything runs; the dispatch owner applies posted controls every 0.5 s
PLAN_WIDGETS = tuple(dict.fromkeys(ERROR_FIELDS.values()))
PLAN_INPUTS = (*PLAN_WIDGETS, "planModeCombo", "planIncrementCheck")
WIRED_CONTROLS = (
    "queueList",
    "createQueueButton",
    "refreshQueuesButton",
    "clearQueuesButton",
    "queueCountSpinBox",
    "concurrencySpinBox",
    "startQueueButton",
    "pauseQueueButton",
    "resumeQueueButton",
    "cancelQueueButton",
    "earlierQueueButton",
    "laterQueueButton",
    "queueItemsView",
    "planBox",
    "planDryRunButton",
    *PLAN_INPUTS,
)
CLEAR_TEXT = (
    "Finished, cancelled and paused queues will be removed from this list. Their attempts and saved images are kept. "
    "Queues that are running or still hold an unresolved outcome are not removed."
)
POSTED = "Posted. The dispatch owner applies it at its next poll, or at the next Start if nothing is dispatching."


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
    ):
        self.page = page
        self._runner, self._backend, self._saved = runner, backend_source, saved_source
        self._state, self._request_source, self._activity = Path(state_dir), request_source, activity
        self._simulation, self._created_hook = simulation_source, created_hook
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
        self._shared_cap: int | None = None
        self._output_folder: Path | None = None
        self._run_progress: dict[tuple[str, str], tuple[int, int]] = {}
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
        page.concurrencyHintLabel.setText(CONCURRENCY_HINT)
        self._timer = QTimer(page)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        page.refreshQueuesButton.clicked.connect(self.refresh)
        page.clearQueuesButton.clicked.connect(self.clear_queues)
        page.createQueueButton.clicked.connect(self.create)
        page.startQueueButton.clicked.connect(self.start)
        page.pauseQueueButton.clicked.connect(self.pause)
        page.earlierQueueButton.clicked.connect(lambda: self._order("earlier"))
        page.laterQueueButton.clicked.connect(lambda: self._order("later"))
        page.resumeQueueButton.clicked.connect(lambda: self._post("resume"))
        page.cancelQueueButton.clicked.connect(self.cancel)
        page.queueList.currentRowChanged.connect(self._select_row)
        page.queueItemsView.selectionModel().currentRowChanged.connect(lambda *_: self._show_item())
        page.itemOpenFolderButton.clicked.connect(self.open_output_folder)
        page.concurrencySpinBox.valueChanged.connect(self._cap_changed)
        self.plan_model = QStandardItemModel(page)
        page.planTableView.setModel(self.plan_model)
        page.planBoxToggle.toggled.connect(page.planBox.setChecked)
        page.planBox.toggled.connect(self._plan_toggled)
        page.planDryRunButton.clicked.connect(self.dry_run)
        for name in PLAN_WIDGETS:
            getattr(page, name).textChanged.connect(self._plan_changed)
        page.planModeCombo.currentTextChanged.connect(self._plan_changed)
        page.planIncrementCheck.toggled.connect(self._plan_changed)
        self._plan_toggled(page.planBox.isChecked())
        activity.listen(self._sync)
        page.destroyed.connect(self._unlisten_activity)
        self._render()
        self.refresh()

    def _unlisten_activity(self) -> None:
        """Keep late activity notifications away from destroyed page widgets."""
        self._activity.unlisten(self._sync)

    # -- helpers ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _show(label, level: str, text: str) -> None:
        label.setProperty("state", level)
        label.style().unpolish(label)
        label.style().polish(label)
        label.setText(text)

    def _message(self, presentation: Presentation) -> None:
        self._show(
            self.page.queueMessageLabel,
            presentation.level,
            f"{presentation.headline}. {presentation.detail}".strip(),
        )

    def _view(self) -> dict | None:
        return self._views.get(self._selected or "")

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
        self._runner.submit(self._read(self._selected), self._read_done)

    async def _read(self, selected: str | None):
        views = await read_queue_views(self._state)
        detail = None
        if selected and any(view["queue_id"] == selected for view in views):
            detail = (await read_queue_views(self._state, selected))[0]
        try:
            shared = await asyncio.to_thread(load_cloud_concurrency, self._state)
        except SettingsError:
            shared = None  # an unreadable settings file leaves the box as it is; saving reports the reason
        return views, detail, shared

    def _read_done(self, result, failure) -> None:
        self._reading = False
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        views, self._detail, self._shared_cap = result
        self._views = {view["queue_id"]: view for view in views}
        running = {
            (view["queue_id"], item["item_id"])
            for view in views
            for item in view["items"]
            if item["state"] == "running"
        }
        self._run_progress = {key: value for key, value in self._run_progress.items() if key in running}
        if self._dispatching is not None:
            active = self._views.get(self._dispatching)
            if active is not None and active["status"] == "running":
                self._dispatch_started = True
        if self._select_after in self._views:
            self._selected, self._select_after = self._select_after, None
        elif self._selected not in self._views:
            self._selected = None
        self._render()
        busy = self.dispatching_here or any(view["status"] in ("running", "queued") for view in views)
        if busy:
            self._timer.start()
        else:
            self._timer.stop()

    # -- rendering -------------------------------------------------------------------------------------------------
    def _render(self) -> None:
        page = self.page
        page.queueList.blockSignals(True)
        page.queueList.clear()
        order = up_next_ids(self._views.values())
        for queue_id, view in self._views.items():
            summary = summarize(view)
            status = f"up next {order.index(queue_id) + 1}" if queue_id in order else summary.status
            item = QListWidgetItem(f"{queue_id[:8]} · {summary.backend} · {status} · {summary.settled}/{summary.total}")
            item.setData(ITEM_ID_ROLE, queue_id)
            page.queueList.addItem(item)
            if queue_id == self._selected:
                page.queueList.setCurrentItem(item)
        page.queueList.blockSignals(False)
        view = self._view()
        page.emptyStateCard.setVisible(view is None)
        page.queueTableHost.setVisible(view is not None)
        page.queueProgressBar.setVisible(view is not None)
        if view is None:
            self._show_cap(None)
            self.model.apply(())
            self._show(page.queueStateLabel, "unknown", "No queue selected")
            page.queueProgressBar.setRange(0, 1)
            page.queueProgressBar.setValue(0)
            page.queueProgressBar.setFormat("0 of 0 settled")
            page.queueEventBrowser.clear()
        else:
            summary = summarize(view)
            self.model.apply(item_rows(view))
            self._select_default_item()
            self._show(page.queueStateLabel, summary.level, summary.headline)
            page.queueProgressBar.setRange(0, max(summary.total, 1))
            page.queueProgressBar.setValue(summary.settled)
            page.queueProgressBar.setFormat(progress_text(summary))
            events = event_lines(self._detail) if self._detail and self._detail["queue_id"] == view["queue_id"] else ""
            page.queueEventBrowser.setPlainText(events)
            self._show_cap(view)
        page.ownershipBadgeLabel.setText(ownership_badge(view, dispatching_here=self.dispatching_here))
        self._show_item()
        self._sync()

    @staticmethod
    def _editable(view: dict | None) -> bool:
        return view is not None and view["status"] in ("paused", "queued", "running")

    def _show_cap(self, view: dict | None) -> None:
        """An editable queue shows its own maximum; with the cloud provider chosen and no editable queue the box edits
        the shared cloud default for new queues. Other providers never write that default."""
        spin = self.page.concurrencySpinBox
        cloud = self._backend()[0] == "cloud"
        if self._editable(view):
            value, tip = view["cap"], "Maximum for this queue and, on cloud, for new queues."
        elif cloud:
            value, tip = (
                self._shared_cap,
                "Default maximum for new cloud queues. A finished queue's own maximum is fixed.",
            )
        elif view is not None:
            value, tip = (
                view["cap"],
                "This queue is finished, so its maximum is fixed. Choose cloud to edit the default.",
            )
        else:
            value, tip = None, "Choose cloud to edit the default maximum for new cloud queues."
        spin.setToolTip(tip)
        if value is None:
            return
        self._syncing = True
        spin.setValue(value)
        self._syncing = False
        self._set_level(value)

    def _set_level(self, value: int) -> None:
        spin = self.page.concurrencySpinBox
        spin.setProperty("level", concurrency_level(value))
        spin.style().unpolish(spin)
        spin.style().polish(spin)

    def _controls_for(self, view: dict | None):
        detail = self._detail if view and self._detail and self._detail["queue_id"] == view["queue_id"] else None
        owns_view = bool(view and self._dispatching == view["queue_id"])
        return controls(
            view,
            dispatching_here=self.dispatching_here,
            generating=self._activity.generating,
            started=owns_view or has_started(detail) or bool(view and view["status"] == "running"),
            owned=owns_view,
        )

    def _run_view(self) -> dict | None:
        if self._dispatching is not None:
            active = self._views.get(self._dispatching)
            if active is not None:
                return active
        return self._view()

    def _publish_run_state(self) -> None:
        if self._activity.generating:
            return
        view = self._run_view()
        if view is None:
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
        elif status == "running":
            # Another process owns this queue. Show activity, but keep the compact
            # control status-only so a header click never controls foreign work.
            phase = "running"
        elif available.start:
            # With no local dispatcher, Start is the only useful immediate action.
            # Posting Resume here would remain pending until a dispatcher starts.
            phase, action = "ready", "start"

        summary = summarize(view)
        completed = total = None
        running_items = [item for item in view["items"] if item["state"] == "running"]
        detail = f"Queue {queue_id[:8]} · {summary.headline}"
        waiting = len(up_next_ids(self._views.values())) if dispatching_here else 0

        if phase == "running":
            # The ring counts settled items, which is meaningful however many items run at once.
            completed, total = summary.settled, summary.total
            detail += f" {summary.settled}/{summary.total} settled."
            if len(running_items) == 1:
                progress = self._run_progress.get((queue_id, running_items[0]["item_id"]))
                if progress is not None:
                    step, steps = progress
                    detail += f" Item #{running_items[0]['position']} step {step}/{steps}."
            elif running_items:
                detail += f" {len(running_items)} items running."
            if waiting:
                detail += f" {waiting} up next."
        elif status in ("finished", "cancelled"):
            # Settled/problem states stay on the Queues page. The global control
            # returns to its quiet idle presentation instead of becoming a
            # persistent completion/error badge for whichever queue is selected.
            detail = "No runnable queue selected."

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

    def sync_edit_fields(self) -> None:
        """Strengths apply to Inpaint edits only: shown while Generate is in Inpaint mode."""
        editing = self._editing()
        self.page.planStrengthsLabel.setVisible(editing)
        self.page.planStrengthsEdit.setVisible(editing)

    def _sync(self) -> None:
        page = self.page
        self.sync_edit_fields()
        view = self._view()
        available = self._controls_for(view)
        page.startQueueButton.setEnabled(available.start or available.enqueue)
        page.startQueueButton.setText("Start next" if available.enqueue else "Start")
        page.startQueueButton.setToolTip(
            "Waits up next and starts by itself when the running queue ends." if available.enqueue else ""
        )
        page.pauseQueueButton.setEnabled(available.pause)
        page.pauseQueueButton.setToolTip(
            "Leaves the up-next order; the queue stays paused until Start."
            if view is not None and view["status"] == "queued"
            else ""
        )
        page.resumeQueueButton.setEnabled(available.resume)
        page.cancelQueueButton.setEnabled(available.cancel)
        page.earlierQueueButton.setEnabled(available.reorder)
        page.laterQueueButton.setEnabled(available.reorder)
        # Creating only persists a paused queue (over a short shared connection while a run owns the state).
        page.createQueueButton.setEnabled(True)
        page.clearQueuesButton.setEnabled(
            any(
                v["status"] in ("finished", "cancelled", "paused") and v["queue_id"] != self._dispatching
                for v in self._views.values()
            )
        )
        self._publish_run_state()

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
        if feedback(self.page)._armed:
            feedback(self.page).dismiss()
        item = self.page.queueList.item(row) if row >= 0 else None
        self._selected = item.data(ITEM_ID_ROLE) if item is not None else None
        self._detail = None
        self._render()
        self.refresh()

    # -- creation --------------------------------------------------------------------------------------------------
    def create(self) -> None:
        try:
            request, name, endpoint, asset_ids = self._request_source()
        except FormError as exc:
            self._message(Presentation("error", "Queue not created", str(exc)))
            return
        plan = None
        if self.page.planBox.isChecked():
            plan = self._plan_for(request, "Queue not created")
            if plan is None:
                return
        count, cap = self.page.queueCountSpinBox.value(), self.page.concurrencySpinBox.value()
        self._message(Presentation("warning", "Creating", "Persisting a paused queue; no backend is contacted."))
        backend = {"name": name, "endpoint": endpoint, "scenario": "success", **self._simulation()}
        job = self._edit_source()
        self._runner.submit(self._create(backend, request, asset_ids, count, cap, plan, job), self._created)

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
            self._message(presentation)
            strip.complete("queue-behind", presentation.headline, presentation.detail, "error")
            return
        queue_id, status = result
        if status == "queued":
            presentation = Presentation("ok", "Queued behind", f"{queue_id[:8]} waits up next and starts by itself.")
            strip.complete(
                "queue-behind", "Queued behind the running queue", "It starts by itself when that queue ends."
            )
        else:
            presentation = Presentation(
                "warning", "Saved paused", f"The running queue had already ended, so {queue_id[:8]} waits for Start."
            )
            strip.complete("queue-behind", presentation.headline, presentation.detail, "partial")
        self._message(presentation)
        self.refresh()

    async def _create(
        self, backend, request, asset_ids, count, cap, plan: Plan | None = None, job=None, *, up_next: bool = False
    ):
        request = await bind_prepared(self._state, backend["name"], request, asset_ids)
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
            )
        items = expand_plan(request, plan) if plan is not None else expand_items(request, count)
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
            page.planStrengthsEdit.text(),
        )

    def _mark_plan(self, widget_name: str | None) -> None:
        for name in PLAN_WIDGETS:
            widget = getattr(self.page, name)
            widget.setProperty("invalid", name == widget_name)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _plan_toggled(self, enabled: bool) -> None:
        # An unchecked QGroupBox disables children; its custom header must stay operable.
        self.page.planBoxHeader.setEnabled(True)
        self.page.planBoxToggle.setEnabled(True)
        self.page.planBoxToggle.setChecked(enabled)
        self.page.planBoxContents.setVisible(enabled)
        self.page.planBody.setVisible(enabled)
        self.page.queueCountSpinBox.setEnabled(not enabled)  # the count box is the seed-sweep shortcut
        self._plan_changed()

    def _plan_changed(self, *_) -> None:
        level, text = count_text(self._plan_form())
        self._show(self.page.planCountLabel, level, sentence(text))
        planned = self.page.planBox.isChecked()
        self.page.createQueueButton.setText(
            f"Create queue from plan ({text})"
            if planned and level == "ok"
            else "Create queue from plan"
            if planned
            else "Create queue"
        )

    def _show_table(self, table: PlanTable) -> None:
        self.plan_model.clear()
        self.plan_model.setHorizontalHeaderLabels(list(table.columns))
        for row in table.rows:
            self.plan_model.appendRow([QStandardItem(cell) for cell in row])
        self.page.planTableView.resizeColumnsToContents()

    def _plan_for(self, request, headline: str) -> Plan | None:
        """Build and fully validate the plan against the form's request (pure, no I/O); shows the dry-run table."""
        self._mark_plan(None)
        try:
            plan = build_plan(self._plan_form())
            table = describe_plan(request, plan, edit=self._edit_source() is not None)
        except ValidationError as exc:
            # One place for the error: beside the plan, with the owning field marked.
            error = plan_error(exc)
            self._mark_plan(error.widget)
            self._show(self.page.planCountLabel, "error", sentence(str(error)))
            return None
        self._show_table(table)
        self._message(Presentation("ok", "Plan", summary_text(table)))
        return plan

    def dry_run(self) -> None:
        """Show the exact items the plan would create; persists nothing and contacts no backend."""
        try:
            request, *_ = self._request_source(preview=True)
        except FormError as exc:
            self._message(Presentation("error", "Dry run unavailable", str(exc)))
            return
        self._plan_for(request, "Plan not valid")

    def _created(self, result, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        queue_id, cap = result
        self._created_hook()
        self._select_after = queue_id
        self._message(
            Presentation("ok", "Queue created", f"{queue_id[:8]} is paused with a maximum of {cap}. Press Start.")
        )
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
        if available.pause:
            self._post("pause", view["queue_id"])
        elif available.resume:
            self._post("resume", view["queue_id"])
        elif available.start and view is self._view():
            self.start()

    def start(self) -> None:
        view = self._view()
        if view is not None and self._controls_for(view).enqueue:
            self._order("enqueue")
            return
        if view is None or self.dispatching_here or self._activity.generating:
            return
        self._dispatching = view["queue_id"]
        self._dispatch_started = False
        self._activity.set(dispatching=True)
        self._message(Presentation("ok", "Dispatching", "This window owns dispatch until the queue stops."))
        self._render()
        saved = self._saved()
        queue_id = view["queue_id"]
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
        self._activity.set(dispatching=False)
        if isinstance(failure, StateBusyError):
            presentation = Presentation(
                "warning",
                "Dispatch is owned by another process",
                "Controls still work; Start is available when it stops. Nothing was dispatched.",
            )
        elif failure is not None:
            presentation = present_generation_failure(failure)
        else:
            presentation = Presentation(*outcome_text(final))
        self._message(presentation)
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

        confirm_action(self.page, self.page.clearQueuesButton, "Clear queues", CLEAR_TEXT, proceed, danger=True)

    def _cleared(self, removed, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        text = f"{removed} queue(s) removed." if removed else "No queue could be removed."
        self._message(Presentation("ok", "Queues cleared", text))
        self.refresh()

    # -- concurrency -----------------------------------------------------------------------------------------------
    def _cap_changed(self, value: int) -> None:
        if self._syncing:
            return
        try:
            validate_concurrency(value)
        except ValidationError as exc:
            self._message(Presentation("error", "Concurrency not changed", str(exc)))
            return
        self._set_level(value)
        view = self._view()
        backend = view["backend"]["name"] if view else self._backend()[0]
        queue_id = view["queue_id"] if self._editable(view) else None
        # Without an editable queue the box edits the shared cloud default, which only the cloud provider writes.
        save_shared = (backend if queue_id is not None else self._backend()[0]) == "cloud"
        self._runner.submit(
            set_concurrency(self._state, value, queue_id=queue_id, save_shared=save_shared), self._cap_done
        )

    def _cap_done(self, value, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        self._message(Presentation("ok", "Concurrency set", f"Maximum {value} (a client policy; active work is kept)."))
        self.refresh()

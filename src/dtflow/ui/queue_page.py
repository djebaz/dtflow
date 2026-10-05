"""Queues page controller: widgets in, shared queue services out. Owns no scheduling, reservation or persistence rules.

Reads are read-only polls of the durable state; every control is posted through the same function ``dtflow queue``
uses, so a window and a CLI process drive a queue identically. Only Start makes this window the dispatch owner, which
holds the state ownership lock for the run (see ``Activity``). Wording after cancel or override comes from recorded
evidence, and neither action claims remote cancellation or resolution.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QListWidgetItem, QMessageBox, QWidget

from dtflow.core.models import ValidationError
from dtflow.persistence.settings import SettingsError, load_cloud_concurrency, validate_concurrency
from dtflow.persistence.store import StateBusyError
from dtflow.services.connection import Presentation, present_generation_failure
from dtflow.services.planning import Plan, PlanTable, describe_plan, expand_plan
from dtflow.services.queue_session import (
    create_queue,
    dispatch_queue,
    expand_items,
    post_queue_control,
    read_queue_views,
    set_concurrency,
)
from dtflow.services.session import bind_prepared
from dtflow.ui.activity import Activity
from dtflow.ui.generation_presenter import FormError
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
    CANCEL_TEXT,
    CONCURRENCY_HINT,
    OVERRIDE_TEXT,
    concurrency_level,
    controls,
    event_lines,
    has_started,
    item_rows,
    outcome_text,
    ownership_badge,
    progress_text,
    summarize,
    uncertainty_text,
)
from dtflow.ui.runner import AsyncRunner

POLL_MS = 1000  # read interval while anything runs; the dispatch owner applies posted controls every 0.5 s
PLAN_WIDGETS = tuple(dict.fromkeys(ERROR_FIELDS.values()))
PLAN_INPUTS = (*PLAN_WIDGETS, "planModeCombo", "planIncrementCheck")
WIRED_CONTROLS = (
    "queueList",
    "createQueueButton",
    "refreshQueuesButton",
    "queueCountSpinBox",
    "concurrencySpinBox",
    "startQueueButton",
    "pauseQueueButton",
    "resumeQueueButton",
    "cancelQueueButton",
    "queueItemsView",
    "overrideReservationButton",
    "planBox",
    "planDryRunButton",
    *PLAN_INPUTS,
)
POSTED = "Posted. The dispatch owner applies it at its next poll, or at the next Start if nothing is dispatching."


def ask_confirmation(parent: QWidget, title: str, text: str) -> bool:
    answer = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


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
    ):
        self.page = page
        self._runner, self._backend, self._saved = runner, backend_source, saved_source
        self._state, self._request_source, self._activity = Path(state_dir), request_source, activity
        self._simulation = simulation_source
        self.confirm = ask_confirmation
        self._views: dict[str, dict] = {}
        self._selected: str | None = None
        self._select_after: str | None = None
        self._dispatching: str | None = None
        self._future = None
        self._reading = False
        self._syncing = False
        self._detail: dict | None = None
        self._shared_cap: int | None = None
        self._output_folder: Path | None = None
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
        page.createQueueButton.clicked.connect(self.create)
        page.startQueueButton.clicked.connect(self.start)
        page.pauseQueueButton.clicked.connect(lambda: self._post("pause"))
        page.resumeQueueButton.clicked.connect(lambda: self._post("resume"))
        page.cancelQueueButton.clicked.connect(self.cancel)
        page.overrideReservationButton.clicked.connect(self.override)
        page.queueList.currentRowChanged.connect(self._select_row)
        page.queueItemsView.selectionModel().currentRowChanged.connect(lambda *_: self._show_item())
        page.itemOpenFolderButton.clicked.connect(self.open_output_folder)
        page.concurrencySpinBox.valueChanged.connect(self._cap_changed)
        self.plan_model = QStandardItemModel(page)
        page.planTableView.setModel(self.plan_model)
        page.planBox.toggled.connect(self._plan_toggled)
        page.planDryRunButton.clicked.connect(self.dry_run)
        for name in PLAN_WIDGETS:
            getattr(page, name).textChanged.connect(self._plan_changed)
        page.planModeCombo.currentTextChanged.connect(self._plan_changed)
        page.planIncrementCheck.toggled.connect(self._plan_changed)
        self._plan_toggled(page.planBox.isChecked())
        activity.listen(self._sync)
        self._render()
        self.refresh()

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

    def confirm_close(self) -> bool:
        """Window close while this window dispatches needs an explicit yes: active attempts stop waiting locally."""
        if not self.dispatching_here:
            return True
        return self.confirm(
            self.page,
            "Stop dispatching?",
            "Closing stops dispatch. Active attempts stop waiting locally and may end as outcome unknown; remote "
            "cancellation is not confirmed.",
        )

    # -- reading ---------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
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
        if self._select_after in self._views:
            self._selected, self._select_after = self._select_after, None
        elif self._selected not in self._views:
            self._selected = None
        self._render()
        busy = self.dispatching_here or any(view["status"] == "running" for view in views)
        if busy:
            self._timer.start()
        else:
            self._timer.stop()

    # -- rendering -------------------------------------------------------------------------------------------------
    def _render(self) -> None:
        page = self.page
        page.queueList.blockSignals(True)
        page.queueList.clear()
        for queue_id, view in self._views.items():
            summary = summarize(view)
            item = QListWidgetItem(
                f"{queue_id[:8]} · {summary.backend} · {summary.status} · {summary.settled}/{summary.total}"
            )
            item.setData(ITEM_ID_ROLE, queue_id)
            page.queueList.addItem(item)
            if queue_id == self._selected:
                page.queueList.setCurrentItem(item)
        page.queueList.blockSignals(False)
        view = self._view()
        page.emptyStateCard.setVisible(view is None)
        page.queueTableHost.setVisible(view is not None)
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
        return view is not None and view["status"] in ("paused", "running", "blocked")

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

    def _sync(self) -> None:
        page = self.page
        view = self._view()
        detail = self._detail if view and self._detail and self._detail["queue_id"] == view["queue_id"] else None
        available = controls(
            view,
            dispatching_here=self.dispatching_here,
            generating=self._activity.generating,
            started=has_started(detail),
        )
        page.startQueueButton.setEnabled(available.start)
        page.pauseQueueButton.setEnabled(available.pause)
        page.resumeQueueButton.setEnabled(available.resume)
        page.cancelQueueButton.setEnabled(available.cancel)
        page.overrideReservationButton.setEnabled(available.override)
        page.createQueueButton.setEnabled(not self.dispatching_here and not self._activity.generating)

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
        level, text = uncertainty_text(row)
        self._show(page.uncertaintyLabel, level, text)
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
        self._runner.submit(self._create(backend, request, asset_ids, count, cap, plan), self._created)

    async def _create(self, backend, request, asset_ids, count, cap, plan: Plan | None = None):
        request = await bind_prepared(self._state, backend["name"], request, asset_ids)
        items = expand_plan(request, plan) if plan is not None else expand_items(request, count)
        return await create_queue(self._state, backend, items, cap)

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
        )

    def _mark_plan(self, widget_name: str | None) -> None:
        for name in PLAN_WIDGETS:
            widget = getattr(self.page, name)
            widget.setProperty("invalid", name == widget_name)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _plan_toggled(self, enabled: bool) -> None:
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
            table = describe_plan(request, plan)
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
            request, *_ = self._request_source()
        except FormError as exc:
            self._message(Presentation("error", "Dry run unavailable", str(exc)))
            return
        self._plan_for(request, "Plan not valid")

    def _created(self, result, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        queue_id, cap = result
        self._select_after = queue_id
        self._message(
            Presentation("ok", "Queue created", f"{queue_id[:8]} is paused with a maximum of {cap}. Press Start.")
        )
        self.refresh()

    # -- dispatch and controls -------------------------------------------------------------------------------------
    def start(self) -> None:
        view = self._view()
        if view is None or self.dispatching_here or self._activity.generating:
            return
        self._dispatching = view["queue_id"]
        self._activity.set(dispatching=True)
        self._message(Presentation("ok", "Dispatching", "This window owns dispatch until the queue stops."))
        self._render()
        saved = self._saved()
        self._future = self._runner.submit(dispatch_queue(self._state, view["queue_id"], saved=saved), self._dispatched)
        self._timer.start()

    def _dispatched(self, final, failure) -> None:
        self._dispatching, self._future = None, None
        self._activity.set(dispatching=False)
        if isinstance(failure, StateBusyError):
            self._message(
                Presentation(
                    "warning",
                    "Dispatch is owned by another process",
                    "Controls still work; Start is available when it stops. Nothing was dispatched.",
                )
            )
        elif failure is not None:
            self._message(present_generation_failure(failure))
        else:
            self._message(Presentation(*outcome_text(final)))
        self._render()
        self.refresh()

    def _post(self, action: str) -> None:
        view = self._view()
        if view is None:
            return
        self._runner.submit(post_queue_control(self._state, view["queue_id"], action), self._posted(action))

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

    def cancel(self) -> None:
        if self._view() is not None and self.confirm(self.page, "Cancel queue", CANCEL_TEXT):
            self._post("cancel")

    def override(self) -> None:
        if self._view() is not None and self.confirm(self.page, "Acknowledge uncertain reservation", OVERRIDE_TEXT):
            self._post("override")

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

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
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any
from uuid import uuid4
from html import escape
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QFormLayout, QListWidgetItem, QMenu, QPlainTextEdit, QWidget

from dtflow.core.models import BackendEvent, LoRASelection, ValidationError
from dtflow.core.request_records import request_from_record
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.assets import AssetRegistry
from dtflow.persistence.queues import MANUAL_ORIGIN, QueueError
from dtflow.persistence.store import StateBusyError
from dtflow.services.connection import Presentation, present_generation_failure
from dtflow.services.dynamic_prompts import decode_runs
from dtflow.services.preflight import checkpoint_picker_versions
from dtflow.services.lora_naming import display_label, lora_offer, lora_offer_notice, relabel_text
from dtflow.services.image_preparation import prepare_edit_inputs
from dtflow.services.image_inputs import restore_image_inputs
from dtflow.services.plan_errors import PlanError
from dtflow.services.planning import Plan, PlanTable, describe_plan, expand_plan, new_rng_seed, plan_size, resolve_plan
from dtflow.services.wildcards import bind_wildcards, fingerprint, list_names
from dtflow.services.queue_session import (
    change_queue_order,
    clear_paused_queues,
    clear_queues,
    create_edit_queue,
    create_pose_queue,
    create_saved_pose_queue,
    create_queue,
    create_script_queue,
    ManualSink,
    dispatch_queue,
    expand_items,
    post_queue_control,
    read_queue_list,
    read_queue_records,
    read_composer_source,
    read_queue_views,
    relaunch_queue,
    remove_queue,
    retry_queue_item,
    RELAUNCH_STATES,
)
from dtflow.services.queue_templates import TemplatePose
from dtflow.services.session import bind_prepared, script_asset_choices, selection_for
from dtflow.ui.activity import Activity, RunState
from dtflow.ui.controlnet_editor import PoseJob
from dtflow.ui.performance import profile_slow_gui_callback
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.generation_presenter import FormError
from dtflow.ui.inline_feedback import confirm_action, feedback
from dtflow.ui.planning_presenter import (
    ERROR_FIELDS,
    DRAW_SPINS,
    NO_LORA,
    parse_lora_set,
    queue_edit_from_records,
    queue_edit_from_source,
    candidate_counts,
    PlanForm,
    build_plan,
    lora_set_value,
    count_text,
    images_requests,
    readout_text,
    plan_error,
    sentence,
    SEED_POLICY_TEXT,
    summary_text,
)
from dtflow.ui.rail_fit import fit_rail
from dtflow.ui.queue_composer import SIZE_PRESETS, ActionsFit, ComposerGridFit, SummaryLabelHeightFit
from dtflow.ui.queue_item_delegate import ROW_HEIGHT, ItemTableHeader, ItemTableLayout, QueueItemDelegate
from dtflow.ui.queue_model import ITEM_ID_ROLE, QueueItemModel
from dtflow.ui.queue_thumbnails import HoverPreview, QueueThumbnails
from dtflow.ui.queue_templates import QueueTemplates
from dtflow.ui.queue_pose import QueuePoseEditor
from dtflow.ui.queue_presenter import (
    FILTERS,
    item_counts,
    GROUPS,
    STATUS_TEXT,
    QueueCountdown,
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
    script_progress,
    workflow_bar,
    workflow_of,
    workflow_stage,
    queue_entries,
    lora_text,
    queue_facts,
    queue_title,
    queue_title_runs,
    segments,
    status_chip,
    summarize,
    up_next_ids,
)
from dtflow.ui.prompt_tags import PROMPT_RUNS_ROLE, TagCellDelegate, style_prompt_tags, tag_spans, tagged_html
from dtflow.ui.submit_shortcut import SubmitShortcut
from dtflow.ui.queue_widgets import ENTRY_ROLE, HEADING_ROLE, QueueListDelegate
from dtflow.ui.runner import AsyncRunner, EventRelay

log = logging.getLogger(__name__)
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
PLAN_INPUTS = (
    *PLAN_WIDGETS,
    "planFixedLorasEdit",
    "planModeCombo",
    "planIncrementCheck",
    "queueCountSpinBox",
    *RANDOM_INPUTS,
)
PLAN_TABLE_MAX_ROWS = 1000  # the composer preview table shows its rows in full up to this many; the composer scrolls
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
    "copyToGenerateButton",
    "saveQueueTemplateButton",
    "loadQueueTemplateButton",
    "composerPromptEdit",
    "composerPromptHintsButton",
    "queueCopyPromptButton",
    "refreshQueuesButton",
    "clearQueuesButton",
    "clearPausedQueuesButton",
    "removeQueueButton",
    "queuePrimaryButton",
    "cancelQueueButton",
    "earlierQueueButton",
    "laterQueueButton",
    "relaunchQueueButton",
    "editAsNewQueueButton",
    "emptyRelaunchButton",
    *FILTER_BUTTONS.values(),
    "queueItemsView",
    *PLAN_INPUTS,
)
CLEAR_TEXT = (
    "Finished and cancelled queues will be removed from this list. Their attempts and saved images are kept. "
    "Paused, running and up-next queues stay."
)
# Queues whose single item is a script or workflow run: started again from Scripts or Workflows, not copied here.
RUN_KINDS = ("script", "workflow")
RUN_KIND_NOTES = {
    "script": " (a script queue: press Start on the script)",
    "workflow": " (a workflow queue: press Run in Workflows)",
}
REMOVE_TEXT = "This queue will be removed from the list. Its attempts and saved images are kept."
POSTED = "Posted. The dispatch owner applies it at its next poll, or at the next Start if nothing is dispatching."


def _dimension_text(name: str, count: int) -> str:
    """``2 step counts``-style wording of one plan dimension for the composer summary."""
    if name.startswith("lora_weight."):
        return f"{count} weights of {display_label(name.split('.', 1)[1])}"
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
class _QueueOverlap:
    """Auto-play started ``queue_id`` early, beside the still-running queue."""

    queue_id: str
    kind: str = "queue_overlap"
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

    @property
    def transient_key(self) -> tuple[str, str, str]:
        return self.queue_id, self.item_id, self.event.kind


@dataclass(slots=True)
class _ManualRun:
    """The one Generate click this page is running (#558): its queue, and where its events and result are shown.

    ``sink`` is the Generate page: ``created(queue_id, request_id)``, ``event(event)``, ``finished(outcome, failure)``
    (no outcome and no failure is a stop) and ``refused(presentation)``."""

    sink: Any
    queue_id: str
    ready: bool = False  # the queue exists (created, or reset for Retry) and is being handed to dispatch
    stopping: bool = False


@dataclass(frozen=True, slots=True)
class _ManualDelivery:
    """A Generate queue's saved result or failure (``ended`` False), or the end of a queue's task (``ended`` True)."""

    queue_id: str
    outcome: Any = None
    error: BaseException | None = None
    ended: bool = False
    kind: str = "manual_delivery"
    sequence: int = 0


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
        models_source: Callable[[], dict] = dict,
        generate_hook: Callable[[], None] = lambda: None,
        reuse_hook: Callable[[dict], None] = lambda request: None,
        pose_controls_source: Callable = tuple,
    ):
        self.page = page
        self._runner, self._backend, self._saved = runner, backend_source, saved_source
        self._state, self._request_source, self._activity = Path(state_dir), request_source, activity
        self._simulation, self._created_hook = simulation_source, created_hook
        self._live_choices, self._generate_hook = choices_source, generate_hook
        self._live_models = models_source  # every known model, read on each use: not narrowed to Generate's family
        self._choices = self._composer_choices
        self._reuse_hook = reuse_hook
        self._draft = None  # immutable request/backend/receipts snapshot; Generate is only an explicit copy source
        self._draft_choices: dict = {}  # initial inventory suggestions, not a live view of later Generate edits
        self._draft_job = None
        self._draft_template_pose = None  # verified body-map bytes/control for Save template after Edit as new
        self._authoring_reconstructed = False
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
        self._detail: dict | None = None
        self._run_detail: dict | None = None  # the dispatched script queue's items, when it is not the selected one
        self._reading_run = False
        self._varied: dict[str, dict] = {}  # items never change after creation, so their variation is read once
        self._resume_fallback: str | None = None
        self._list_key: tuple | None = None
        self._output_folder: Path | None = None
        self._images_hook = None  # set_images_hook wires Open in Images to the Images workspace
        self._open_workflow_hook = None  # set_open_workflow_hook(fn(queue_id)) shows a workflow run in Workflows
        self._run_progress: dict[tuple[str, str], tuple[int, int]] = {}
        self._run_stages: dict[tuple[str, str], str] = {}
        self._video_progress: dict[tuple[str, str], object] = {}
        self._run_stage_started: dict[tuple[str, str], float] = {}
        self._countdown = QueueCountdown()
        self._event_relay = EventRelay(self._queue_backend_event, page)
        self._advance_relay = EventRelay(self._advanced, page)
        self._overlap_relay = EventRelay(self._overlapped, page)
        self._manual: _ManualRun | None = None  # the Generate click Generate shows, if any (#558)
        self._manual_runs: dict[str, _ManualRun] = {}  # every click's queue not yet reported, shown or not
        self._manual_queues: set[str] = set()  # queues Generate created: Generate reports them, not the queue strip
        self._manual_relay = EventRelay(self._manual_delivered, page)
        self._overlapping: set[str] = set()  # queues auto-started early by this window's dispatch
        self.model = QueueItemModel(page)
        page.queueItemsView.setModel(self.model)
        for view in (page.queueItemsView, page.planTableView):
            view.verticalHeader().hide()  # the "#" column already numbers rows
            view.horizontalHeader().setStretchLastSection(True)
            view.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Items table: painted cells, fixed-height rows and proportional columns (Prompt about a third of the width).
        items_view = page.queueItemsView
        items_view.setHorizontalHeader(ItemTableHeader(items_view))
        items_view.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        items_view.setItemDelegate(QueueItemDelegate(items_view))
        items_view.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        items_view.horizontalHeader().setStretchLastSection(False)
        items_view.horizontalHeader().setSectionResizeMode(items_view.horizontalHeader().ResizeMode.Interactive)
        items_view.setWordWrap(False)
        items_view.setShowGrid(False)
        items_view.setVerticalScrollMode(items_view.ScrollMode.ScrollPerPixel)
        self._item_layout = ItemTableLayout(items_view)
        self.model.thumbnails = QueueThumbnails(self._runner, self._state, items_view)
        self.model.thumbnails.changed.connect(self._thumbnail_changed)
        self._hover_preview = HoverPreview(items_view, self.model.thumbnails)
        for name in WIRED_CONTROLS:
            getattr(page, name).setEnabled(True)
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
        page.clearPausedQueuesButton.clicked.connect(self.clear_paused)
        page.removeQueueButton.clicked.connect(self.remove)
        page.createQueueButton.clicked.connect(lambda: self.create("save"))
        page.addQueueButton.clicked.connect(lambda: self.create("add"))
        page.startQueueButton.clicked.connect(lambda: self.create("start"))
        page.newQueueButton.clicked.connect(self.open_composer)
        page.closeComposerButton.clicked.connect(self.close_composer)
        page.editInGenerateButton.clicked.connect(self.use_generate_settings)
        page.copyToGenerateButton.clicked.connect(self.copy_to_generate)
        page.composerPromptEdit.textChanged.connect(self._plan_changed)
        self._prompt_tags = style_prompt_tags(page.composerPromptEdit)
        self._submit_shortcut = SubmitShortcut(page.composerPromptEdit, self._shortcut_button)
        page.queueCopyPromptButton.setIcon(QIcon(str(refresh_icon.with_name("copy.svg"))))
        page.queueCopyPromptButton.setIconSize(QSize(16, 16))
        page.queueCopyPromptButton.clicked.connect(self.copy_queue_prompt)
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
        page.itemOpenImagesButton.clicked.connect(self.open_in_images)
        page.itemRetryButton.clicked.connect(self.retry_item)
        page.emptyRelaunchButton.clicked.connect(self.relaunch_last)
        page.editAsNewQueueButton.clicked.connect(lambda: self._selected and self.edit_as_new(self._selected))
        page.relaunchQueueButton.clicked.connect(
            lambda: self._relaunch_menu(
                page.relaunchQueueButton.mapToGlobal(page.relaunchQueueButton.rect().bottomLeft())
            )
        )
        self.plan_model = QStandardItemModel(page)
        page.planTableView.setModel(self.plan_model)
        page.planTableView.setItemDelegate(TagCellDelegate(page.planTableView))
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
        self._align_plan_labels()
        self._actions_fit = ActionsFit(
            page.queueComposerScroll,
            page.queueComposer.layout().itemAt(0).layout(),  # the title row
            page.queueComposer.layout(),
            (
                page.createQueueButton,
                page.addQueueButton,
                page.startQueueButton,
            ),
            (page.closeComposerButton,),
        )
        self._composer_grid_fit = ComposerGridFit(
            page.queueComposerScroll,
            page.planDrawGrid,
            [
                (getattr(page, f"planDraw{name}Label"), getattr(page, f"planDraw{name}Spin"))
                for name in (
                    "Models",
                    "Seeds",
                    "Steps",
                    "Guidance",
                    "Samplers",
                    "Sizes",
                    "Strengths",
                    "LoraSets",
                    "Loras",
                )
            ],
            page.planMutationLayout,
            [
                (page.planShuffleCombo,),
                (page.planDropLabel, page.planDropSpin),
                (page.planAddLabel, page.planAddSpin),
            ],
        )
        self._summary_fit = SummaryLabelHeightFit(
            page.composerSummaryBoxContents,
            [
                getattr(page, f"composerSummary{name}Label")
                for name in ("Backend", "Model", "Loras", "Items", "Varies", "Seeds", "Note")
            ],
        )
        for name in PLAN_WIDGETS:
            getattr(page, name).textChanged.connect(self._plan_changed)
        page.planFixedLorasEdit.textChanged.connect(self._plan_changed)
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
        self.templates = QueueTemplates(self)
        self.pose_editor = QueuePoseEditor(self, pose_controls_source)
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

    @property
    def generate_settling(self) -> bool:
        """The owner is only ending after Generate reported its click's queue: nothing else of this window runs."""
        return (
            self._manual is None
            and not self._manual_runs
            and self._dispatching in self._manual_queues
            and self._overlapping <= self._manual_queues
        )

    @property
    def _generate_presents(self) -> bool:
        """A Generate run that is not a one-item queue (inpaint, Controlnet, video) shows its own progress."""
        return self._activity.generating and self._manual is None and not self._manual_runs

    def _owns(self, queue_id: str | None) -> bool:
        """This window dispatches ``queue_id``: the primary queue or one auto-started early beside it."""
        return queue_id is not None and (queue_id == self._dispatching or queue_id in self._overlapping)

    # -- reading ---------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if self._runner.closed:
            self._timer.stop()
            return
        if self._reading:
            return
        self._reading = True
        self._runner.submit(self._read(self._selected, tuple(self._varied)), self._read_done)
        self._refresh_script_run()

    def _refresh_script_run(self) -> None:
        """The running script's or workflow's steps are read for the global bar whichever queue is selected."""
        run = self._dispatching
        # A queue with observed video clips is read too, so its running items are known whichever queue is selected.
        video = run is not None and any(owner == run for owner, _ in self._video_progress)
        if (
            run is None
            or self._reading_run
            or (
                (self._views.get(run) or {}).get("kind")
                not in (
                    "script",
                    "workflow",
                )
                and not video
            )
        ):
            self._run_detail = None
            return
        if run == self._selected:
            return  # the selected queue's own detail already carries the steps
        self._reading_run = True

        async def work():
            views = await read_queue_views(self._state, run)
            return views[0] if views else None

        def done(result, failure) -> None:
            self._reading_run = False
            self._run_detail = result if failure is None else None
            self._publish_run_state()

        self._runner.submit(work(), done)

    async def _read(self, selected: str | None, described: tuple[str, ...] = ()):
        views = await read_queue_list(self._state, described)
        detail = None
        if selected and any(view["queue_id"] == selected for view in views):
            detail = (await read_queue_views(self._state, selected))[0]
        return views, detail

    @profile_slow_gui_callback("queue.read_done")
    def _read_done(self, result, failure) -> None:
        self._reading = False
        if failure is not None:
            logging.getLogger(__name__).warning("Queue refresh failed (%s).", type(failure).__name__)
            self._message(present_generation_failure(failure))
            return
        previous_views, previous_detail = self._views, self._detail
        previous_selected = self._selected
        views, self._detail = result
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
        active_keys = set(self._run_progress) | set(self._run_stages)
        valid_keys = {
            key
            for key in active_keys
            if key[0] in self._views
            and self._views[key[0]]["summary"]["running"]
            and (detail_running is None or self._detail["queue_id"] != key[0] or key[1] in detail_running)
        }
        self._run_stages = {key: value for key, value in self._run_stages.items() if key in valid_keys}
        self._video_progress = {key: value for key, value in self._video_progress.items() if key in valid_keys}
        self._run_stage_started = {key: value for key, value in self._run_stage_started.items() if key in valid_keys}
        if self._dispatching is not None:
            active = self._views.get(self._dispatching)
            if active is not None and active["status"] == "running":
                self._dispatch_started = True
        if self._select_after in self._views:
            self._set_selected(self._select_after)
            self._select_after = None
        elif self._selected not in self._views:
            self._set_selected(None)
        busy = self.dispatching_here or any(view["status"] in ("running", "queued") for view in views)
        if (
            not busy
            and self._views == previous_views
            and tuple(self._views) == tuple(previous_views)
            and self._detail == previous_detail
            and self._selected == previous_selected
        ):
            # Skip redundant idle-only rendering. Live backend progress is not part of these snapshots:
            # running queues must still redraw their header and items on each poll.
            self._sync()
        else:
            self._render()
        if busy or self.page.queueWorkspace.isVisible():
            self._timer.start(POLL_MS if busy else IDLE_POLL_MS)
        else:
            self._timer.stop()
        if self._view() is not None and self._detail_for(self._view()) is None:
            self.refresh()  # selection changed during this read: fetch its items even when idle polling is stopped

    # -- rendering -------------------------------------------------------------------------------------------------
    @profile_slow_gui_callback("queue.render")
    def _render(self) -> None:
        page = self.page
        if not page.queueWorkspace.isVisible():
            # Scripts/Workflows still need shared run state, not a hidden queue list,
            # item table and event browser rebuilt on each background refresh.
            self._sync()
            return
        self._render_list()
        view = self._view()
        page.queueComposerScroll.setVisible(self._composing)
        if not self._composing and page.composerPromptHintsButton.isChecked():
            page.composerPromptHintsButton.setChecked(False)
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
            self.model.apply(
                tuple(
                    replace(row, live_stage=self._run_stages.get((view["queue_id"], row.item_id), ""))
                    for row in item_rows(detail)
                )
                if detail is not None
                else ()
            )
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
        self._show_item()
        for name in ("queueInspectorEyebrow", "itemIdentityBox", "itemDetailsBox", "queueEventBrowser"):
            getattr(page, name).setVisible(not self._composing)  # evidence of the old queue is not about the new one
        page.composerSummaryBox.setVisible(self._composing)
        if self._composing:
            page.uncertaintyBox.setVisible(False)
            self._actions_fit.refit()
            self._composer_grid_fit.refit()
            self._summary_fit.schedule()
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
        title, title_runs = queue_title(view), queue_title_runs(view)
        page.queueTitleLabel.setTextFormat(Qt.TextFormat.RichText if title_runs else Qt.TextFormat.PlainText)
        page.queueTitleLabel.setText(tagged_html(title, title_runs) if title_runs else title)
        first = view.get("first") or {}
        prompt = first.get("prompt") or ""
        runs = decode_runs(prompt, first.get("prompt_runs"))
        page.queueTitleLabel.setToolTip(
            f"<p style='white-space:pre-wrap'>{tagged_html(prompt, runs)}</p>" if prompt else ""
        )
        for name, value in queue_facts(view).items():
            getattr(page, f"queueFact{name}Label").setText(value)
        page.queueFactPlanLabel.setToolTip(f"Queue {view['queue_id']}")
        level, chip = status_chip(view, up_next_ids(self._views.values()))
        if self._owns(view["queue_id"]):
            owner = "This window dispatches it."
        elif needs_recovery(view):
            owner = "No dispatch owner is active. Resume recovers pending items without replaying uncertain attempts."
        elif view["status"] == "running":
            owner = "Dispatched by another process; its controls are posted to that process."
        else:
            owner = ""
        self._show(page.queueStateLabel, level, chip)
        page.queueStateLabel.setToolTip(" ".join(part for part in (STATUS_TEXT.get(view["status"], ""), owner) if part))
        steps = script_progress(view)
        images = workflow_bar(self._detail_for(view) or view)
        if images is not None:
            page.queueProgressBar.set_counts((("ok", images[0]),) if images[0] else (), images[0], images[1])
        elif steps is not None:
            page.queueProgressBar.set_counts((("ok", steps[0]),) if steps[0] else (), steps[0], steps[1])
        else:
            page.queueProgressBar.set_counts(segments(item_counts(view)), summary.settled, summary.total)
        page.queueProgressLabel.setText(progress_text(summary, self._detail_for(view) or view))
        counts = filter_counts(item_counts(view))
        for key, symbol, _, _ in FILTERS:
            button = getattr(page, FILTER_BUTTONS[key])
            button.setText(f"{symbol} {counts[key]}")
            button.setVisible(bool(counts[key]) or button.isChecked())

    def _apply_item_filter(self) -> None:
        """The checked count chips narrow the items table to their states; none checked shows every item."""
        shown = filter_states(key for key, name in FILTER_BUTTONS.items() if getattr(self.page, name).isChecked())
        table = self.page.queueItemsView
        view = self._view()
        # A script is one queue item even when its table expands into child
        # attempts. Status chips count queue items, not their individual steps.
        script_states = (
            {item["state"] for item in (self._detail_for(view) or {}).get("items", ())}
            if view is not None and view.get("kind") == "script"
            else None
        )
        show_script = not shown or bool(shown.intersection(script_states or ()))
        for index in range(self.model.rowCount()):
            row = self.model.row_at(index)
            table.setRowHidden(
                index,
                not show_script if script_states is not None else bool(shown) and row.state not in shown,
            )

    @staticmethod
    def _editable(view: dict | None) -> bool:
        return view is not None and view["status"] in ("paused", "queued", "running")

    def _started(self, view: dict | None) -> bool:
        owns_view = bool(view and self._owns(view["queue_id"]))
        return owns_view or has_started(self._detail_for(view)) or bool(view and view["status"] == "running")

    def _controls_for(self, view: dict | None):
        return controls(
            view,
            dispatching_here=self.dispatching_here,
            generating=self._activity.generating,
            started=self._started(view),
            owned=bool(view and self._owns(view["queue_id"])),
        )

    def _run_view(self) -> dict | None:
        if self._dispatching is not None:
            active = self._views.get(self._dispatching)
            if active is not None:
                return active
        if self._manual is not None:  # a Generate click whose queue is not dispatched yet
            shown = self._views.get(self._manual.queue_id)
            if shown is not None:
                return shown
        return self._view()

    @profile_slow_gui_callback("queue.publish_run_state")
    def _publish_run_state(self) -> None:
        if self._generate_presents:  # a non-queue Generate run presents its own progress
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
        dispatching_here = self._owns(queue_id)
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
        shown = self._detail_for(view)
        if shown is None and self._run_detail is not None and self._run_detail["queue_id"] == queue_id:
            shown = self._run_detail
        items = (shown or {}).get("items", ())
        running_items = [item for item in items if item["state"] == "running"]
        detail = f"Queue {queue_id[:8]} · {summary.headline}"
        waiting = len(up_next_ids(self._views.values())) if dispatching_here else 0
        stage = wide = count = ""
        fraction = None

        if phase == "running":
            # The ring counts settled items, which is meaningful however many items run at once.
            completed, total = summary.settled, summary.total
            # Observed clips of this queue, even when it is not the one shown on screen (then no item list is read);
            # when the list is read, only items it still shows as running count.
            running_ids = {item["item_id"] for item in running_items} if shown is not None else None
            video_fractions = sorted(
                video.fraction()
                for (owner, item_id), video in self._video_progress.items()
                if owner == queue_id and (running_ids is None or item_id in running_ids)
            )
            if running_ids is None:
                # Without a read item list, settled clips may still be remembered: never count more than are running.
                video_fractions = video_fractions[: max(0, running)]
            if video_fractions:
                # Running clips add their estimated share to the settled items; the counts stay settled/total.
                # Whole percents only: the run state is compared for equality, and a value that moved with every call
                # would make each presentation trigger another one.
                fraction = min(1.0, round((summary.settled + sum(video_fractions)) / max(1, summary.total), 2))
                count = f"{summary.settled}/{summary.total}"
            detail += f" Progress: {summary.settled}/{summary.total}."
            # Only the currently running item may supply the single-item stage/progress.
            # Historical callbacks for other items must not take over the status.
            active_item_id = running_items[0]["item_id"] if len(running_items) == 1 else None
            progress = (
                [
                    value
                    for (owner, item_id), value in self._run_progress.items()
                    if owner == queue_id and item_id == active_item_id
                ]
                if active_item_id is not None
                else []
            )
            wide = f"Queue {queue_id[:8]} · {summary.headline.rstrip('.')}" + (
                f" · {running} items running" if running > 1 else " · 1 item running" if running else ""
            )
            stage = f"{running} running" if running else summary.headline.rstrip(".")
            if running:
                stage = (
                    self._run_stages.get((queue_id, active_item_id), "Starting; waiting for response")
                    if active_item_id is not None
                    else f"{running} items running"
                )
                # Keep the measurable settled/total queue progress even when an
                # individual backend has not reported sampling steps yet.
                wide = f"Queue {queue_id[:8]} · {stage} · {summary.settled}/{summary.total}"
            if active_item_id is not None:
                key = (queue_id, active_item_id)
                stage_started = self._run_stage_started.get(key)
                if stage_started is not None:
                    stage += f" · {int(max(0, monotonic() - stage_started))}s"
                if progress and key not in self._video_progress:
                    step, steps = progress[0]
                    stage += f" · step {step}/{steps}"
            if running:
                wide = f"Queue {queue_id[:8]} · {stage} · {summary.settled}/{summary.total}"
                if len(running_items) > 1:
                    phases = [
                        f"#{item['position']} {self._run_stages.get((queue_id, item['item_id']), 'Starting')}"
                        for item in running_items
                        if (queue_id, item["item_id"]) in self._video_progress
                    ]
                    if phases:
                        phase_names = {
                            "preparing": "preparing",
                            "waiting": "waiting",
                            "sampling": "sampling",
                            "second_pass": "second pass",
                            "receiving": "receiving",
                            "encoding": "finalizing",
                            "saving": "saving",
                        }
                        counts = {}
                        for item in running_items:
                            video = self._video_progress.get((queue_id, item["item_id"]))
                            if video is not None:
                                label = phase_names.get(video.phase, "preparing")
                                counts[label] = counts.get(label, 0) + 1
                        wide += " · " + " · ".join(f"{count} {label}" for label, count in counts.items())
                        detail += " " + "; ".join(phases)
            if view.get("kind") == "script":
                # A script is one item whose step count is known only as it runs: show steps, never "0/1 items".
                script = next((item.get("script") for item in items if item.get("script")), None)
                children = (script or {}).get("children") or []
                committed = sum(1 for child in children if child["state"] == "succeeded")
                expected = (script or {}).get("expected_steps")  # a static count, known only for plain scripts
                of = f"{expected}" if expected else "?"
                settled_states = (
                    "succeeded",
                    "failed",
                    "cancelled",
                    "save_failed",
                    "unknown",
                    "interrupted_before_submission",
                )
                in_flight = any(child["state"] not in settled_states for child in children)
                stage = (
                    f"step {len(children)}/{of} running · {committed} committed"
                    if in_flight
                    else f"{committed}/{of} steps committed"
                    if children
                    else "Script running"
                )
                wide = f"Script {queue_id[:8]} · {stage}"
                detail = f"Script {queue_id[:8]} · {stage}."
                completed, total = (committed, expected) if expected else (None, None)
            elif workflow_of(shown) is not None and workflow_bar(shown):
                # A workflow run is one item that makes many images: show images, never "0/1 items".
                stage = workflow_stage(workflow_of(shown))
                if progress and active_item_id is not None:
                    stage += f" · step {progress[0][0]}/{progress[0][1]}"
                wide = f"Workflow {queue_id[:8]} · {stage}"
                detail = f"Workflow {queue_id[:8]} · {stage}."
                completed, total = workflow_bar(shown)
            elif running == 1 and len(progress) == 1:
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
        elapsed = eta = ""
        if phase in ("running", "paused"):
            remaining = self._countdown.remaining(view, monotonic())
            elapsed, eta = elapsed_text(view), eta_text(view, remaining=remaining)
            stage_timed = len(running_items) == 1 and (queue_id, running_items[0]["item_id"]) in self._run_stage_started
            video_live = any(owner == queue_id for owner, _ in self._video_progress)
            if elapsed or eta or stage_timed or video_live:
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
                elapsed=elapsed,
                eta=eta,
                stage=stage,
                wide=wide,
                queued=waiting,
                fraction=fraction,
                count=count,
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
        self.pose_editor.sync()
        editing = (
            self._draft_job is not None and not isinstance(self._draft_job, PoseJob)
            if self._draft is not None
            else self._editing()
        ) and self._planned()
        changed = self.page.planStrengthsEdit.isHidden() == editing
        self.page.planStrengthsLabel.setVisible(editing)
        self.page.planStrengthsEdit.setVisible(editing)
        self.page.planDrawStrengthsLabel.setVisible(editing)  # the draw count follows its candidate row
        self.page.planDrawStrengthsSpin.setVisible(editing)
        if changed:
            self._composer_grid_fit.refit()
        # Polling reaches here unchanged: only a shown or hidden Strength row changes the planning inputs.
        self._schedule_preview(invalidate=changed)

    def _removable(self, view: dict | None) -> bool:
        return (
            view is not None
            and view["status"] in ("paused", "finished", "cancelled")
            and not view["summary"]["running"]
            and not self._owns(view["queue_id"])
        )

    def _sync(self) -> None:
        page = self.page
        self.sync_edit_fields()
        view = self._view()
        available = self._controls_for(view)
        action = primary_action(
            view, available, started=self._started(view), owned=bool(view and self._owns(view["queue_id"]))
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
        has_items = view is not None and bool(sum(view["summary"].values()))
        kind = view.get("kind") if view is not None else None
        # A script or workflow run is started again from its own page, never copied as plain requests.
        copyable = has_items and kind not in RUN_KINDS
        scripted = RUN_KIND_NOTES.get(kind, "") if has_items and not copyable else ""
        page.relaunchQueueButton.setEnabled(copyable)
        page.editAsNewQueueButton.setEnabled(copyable)
        page.relaunchQueueButton.setToolTip("Run this queue's requests again." + scripted)
        page.editAsNewQueueButton.setToolTip("Open this queue's settings as a new draft." + scripted)
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
                v["status"] in ("finished", "cancelled") and not self._owns(v["queue_id"]) for v in self._views.values()
            )
        )
        paused = self._paused_ids()
        page.clearPausedQueuesButton.setEnabled(bool(paused))
        page.clearPausedQueuesButton.setText(
            f"Clear paused queues ({len(paused)})" if paused else "Clear paused queues"
        )
        self._publish_run_state()

    def _paused_ids(self) -> tuple[str, ...]:
        """Paused queues from the latest read that this window could remove (not its own dispatch, nothing running)."""
        return tuple(
            queue_id for queue_id, view in self._views.items() if view["status"] == "paused" and self._removable(view)
        )

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
        kind = view.get("kind") if view is not None else None
        if kind == "workflow":
            # A workflow item retries its first failed or unconfirmed step with the original settings (#220).
            page.itemRetryButton.setEnabled(
                row is not None and self._workflow_retry_target(view, row.item_id) is not None
            )
        else:
            scripted = kind in RUN_KINDS
            page.itemRetryButton.setEnabled(row is not None and row.state in RELAUNCH_STATES and not scripted)
        if row is None:
            for name in (
                "itemIdLabel",
                "attemptIdLabel",
                "itemBackendLabel",
                "itemOutputsLabel",
                "itemSeedLabel",
                "itemDimensionsLabel",
                "itemOperationLabel",
                "itemOutcomeLabel",
            ):
                getattr(page, name).setText("—")
            page.itemOutputsLabel.setToolTip("")
            self._output_folder = None
            page.itemOpenFolderButton.setEnabled(False)
            page.itemOpenImagesButton.setEnabled(False)
            return
        page.itemIdLabel.setText(row.item_id[:8])
        page.itemIdLabel.setToolTip(row.item_id)
        page.attemptIdLabel.setText(row.attempt_id[:8] if row.attempt_id else "—")  # the full id is in the tooltip
        page.attemptIdLabel.setToolTip(row.attempt_id or "")
        page.itemBackendLabel.setText(summarize(view).backend if view else "—")
        page.itemSeedLabel.setText("—" if row.seed is None else str(row.seed))
        page.itemDimensionsLabel.setText(row.dimensions_text)
        page.itemOperationLabel.setText(row.operation_text)
        page.itemOutcomeLabel.setText(row.outcome_line)
        outputs = [*row.outputs, *([row.metadata] if row.metadata else [])]
        # Full paths stay in the tooltip; the panel shows a summary and a button to the attempt's folder.
        parts = (
            [f"{len(row.outputs)} step image(s)" if row.is_script_parent else f"{len(row.outputs)} image(s)"]
            if row.outputs
            else []
        )
        detail = (
            ", ".join([*parts, *(["metadata"] if row.metadata else [])]) or "—"
        )  # the reason is on the Outcome line
        page.itemOutputsLabel.setText(detail)
        page.itemOutputsLabel.setToolTip("\n".join(outputs))
        # The script parent aggregates its children's files; it has no output
        # attempt/folder of its own. Select an actual step for image actions.
        self._output_folder = Path(outputs[0]).parent if outputs and not row.is_script_parent else None
        page.itemOpenFolderButton.setEnabled(self._output_folder is not None)
        # Thumbnail refresh is not navigation authority: the Images workspace resolves the
        # committed identity against live history on click. Keep this action stable while
        # previews revalidate in the background, rather than flashing every few seconds.
        can_open = (
            not self._composing
            and page.queueItemsView.isVisible()
            and bool(row.outputs)
            and not row.is_script_parent
            and bool(row.gallery_identity)
            and self._images_hook is not None
        )
        page.itemOpenImagesButton.setEnabled(can_open)
        fit_rail(page.queueInspector)

    def _thumbnail_changed(self, path: str) -> None:
        if self._runner.closed or not self.page.queueItemsView.isVisible():
            return
        self.page.queueItemsView.viewport().update()
        row = self.model.row_at(self.page.queueItemsView.currentIndex().row())
        if row is not None and row.outputs and row.outputs[0] == path:
            self._show_item()

    def set_open_workflow_hook(self, hook) -> None:
        """``hook(queue_id)`` switches to Workflows and follows that workflow queue's run (steps, Retry, pauses)."""
        self._open_workflow_hook = hook

    def set_images_hook(self, hook) -> None:
        """Wire Open in Images: hook(identity) shows that committed output in the Images Detail view."""
        self._images_hook = hook
        self._show_item()

    def open_in_images(self) -> None:
        row = self.model.row_at(self.page.queueItemsView.currentIndex().row())
        if (
            self._images_hook is not None
            and row is not None
            and not row.is_script_parent
            and row.gallery_identity
            and row.outputs
        ):
            # Gallery.open_output performs the authoritative live identity lookup and
            # warns if this saved image has since become unavailable.
            self._images_hook(row.gallery_identity)

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
        self.model.thumbnails.clear()
        self._hover_preview.hide()
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
        edit = menu.addAction("Edit as new queue…")
        edit.triggered.connect(lambda _=False: self.edit_as_new(queue_id))
        if (self._views.get(queue_id) or {}).get("kind") == "workflow" and self._open_workflow_hook is not None:
            show = menu.addAction("Open in Workflows")
            show.triggered.connect(lambda _=False: self._open_workflow_hook(queue_id))
        if (self._views.get(queue_id) or {}).get("kind") in RUN_KINDS:
            relaunch.setEnabled(False)
            edit.setEnabled(False)
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

    def edit_as_new(self, queue_id: str) -> None:
        """Load authoring input into the independent composer; Generate and recorded items stay untouched."""

        async def work():
            records = await read_queue_records(self._state, queue_id)
            source = await read_composer_source(self._state, queue_id)
            view = (await read_queue_views(self._state, queue_id))[0]
            edited = queue_edit_from_source(source) if source is not None else queue_edit_from_records(records)
            template_pose = None
            # Edit as new must never silently drop a missing pose map or fall back to plain txt2img.
            if edited.base.get("pose_control") is not None:
                restored = request_from_record(edited.base)
                try:
                    prepared = await asyncio.to_thread(restore_image_inputs, self._state, restored.image_inputs)
                except (ValidationError, OSError) as exc:
                    raise ValidationError(f"The queue's retained pose map is unavailable: {exc}") from exc
                # Keep already verified body-map bytes for optional Save template, not an attempt reference.
                # Template size limits are checked only on Save, so a large pose can still be edited/relaunched.
                template_pose = (prepared.image, replace(restored.pose_control, evidence=()))
            if source is None:
                templates = {
                    dict(record.get("resolution_evidence", ())).get("plan.prompt_template") for record in records
                } - {None}
                if len(templates) == 1:
                    edited = replace(edited, base={**edited.base, "prompt": templates.pop()})
                edited = replace(
                    edited,
                    note="This older queue has no complete composer source; its plan uses resolved items. "
                    + edited.note,
                )
            asset_ids = (
                source.get("asset_ids", ())
                if source is not None
                else tuple(asset["receipt_id"] for asset in records[0].get("prepared_assets", ()))
            )
            if not isinstance(asset_ids, (list, tuple)) or any(type(value) is not str for value in asset_ids):
                raise ValidationError("Stored queue asset selections are unreadable.")
            return edited, view["backend"], tuple(asset_ids), template_pose

        self._runner.submit(work(), self._edit_loaded)

    def _edit_loaded(self, result, failure) -> None:
        if failure is not None:
            shown = Presentation("error", "Not loaded", str(failure))
            self._message(shown if isinstance(failure, ValidationError) else present_generation_failure(failure))
            return
        template_pose = result[3] if len(result) == 4 else None
        result, backend, asset_ids = result[:3]
        if not self.templates.applying:
            self.templates.detach()
        self._authoring_reconstructed = bool(result.note)
        page, form = self.page, result.form
        request = request_from_record(result.base)
        self._set_draft(
            request,
            backend["name"],
            backend.get("endpoint"),
            asset_ids,
            PoseJob(template_pose.content, (("origin", "queue_template"),), template_pose.control)
            if isinstance(template_pose, TemplatePose)
            else None,
        )
        if isinstance(template_pose, tuple):
            self._draft_template_pose = template_pose
        self.open_composer()
        page.planModeCombo.setCurrentText(form.mode)
        page.planPromptsEdit.setPlainText(form.prompts)
        for edit, text in (
            (page.planModelsEdit, form.models),
            (page.planSeedsEdit, form.seeds),
            (page.planStepsEdit, form.steps),
            (page.planGuidanceEdit, form.guidance),
            (page.planSamplersEdit, form.samplers),
            (page.planSizesEdit, form.sizes),
            (page.planLoraSetsEdit, form.lora_sets),
        ):
            edit.setText(text)
        chips = [
            f"{identifier.strip()}: {weight.strip()}"
            for line in form.loras.splitlines()
            for identifier, _, weights in [line.rpartition(":")]
            for weight in weights.split(";")
        ]
        page.planLorasEdit.setText("; ".join(chips))  # one chip per LoRA and weight
        page.planStrengthsEdit.setText(form.strengths)
        for name, suffix in DRAW_SPINS.items():
            getattr(page, f"planDraw{suffix}Spin").setValue(dict(form.draws).get(name, 0))
        page.planVariationCombo.setCurrentText(form.variation)
        page.planVariationCountSpin.setValue(form.variation_count)
        page.planShuffleCombo.setCurrentText(form.shuffle)
        page.planDropSpin.setValue(form.drop)
        page.planAddSpin.setValue(form.add)
        page.planAddPartsEdit.setText(form.add_parts)
        page.planWildcardsEdit.setPlainText(form.wildcards)
        page.planRngSeedEdit.setText(form.rng_seed)
        page.planIncrementCheck.setChecked(form.increment)
        if form.mode == REPEAT:
            page.queueCountSpinBox.setValue(result.count)
        self._plan_changed()
        self.preview()
        loaded = Presentation(
            "warning" if result.note else "ok",
            "Queue loaded",
            result.note
            or "Its settings are in the queue draft; Generate was not changed. Edit, then Add to queue or Start now.",
        )
        self._message(loaded)

    def _last_queue(self) -> str | None:
        """The most recently created queue with items, for the empty state's Relaunch last."""
        views = [v for v in self._views.values() if sum(v["summary"].values()) and v.get("kind") not in RUN_KINDS]
        return max(views, key=lambda v: (v["created_at_utc"], v["queue_id"]))["queue_id"] if views else None

    def relaunch_last(self) -> None:
        queue_id = self._last_queue()
        if queue_id is not None:
            self.relaunch(queue_id)

    @staticmethod
    def _workflow_retry_target(view: dict | None, row_id: str | None = None) -> tuple[int, tuple[int, int]] | None:
        """(node id, branch) a workflow Retry reruns (#277).

        An image row (``wf:<node>.<root>.<path>:<item>``) retries exactly that row's step, and the runner reruns only its
        branch plus the branches split from it. The run's summary row retries its first failed step, else its first step
        with no confirmed outcome."""
        from dtflow.core.workflows import is_descendant

        item = next((item for item in (view or {}).get("items", ()) if item.get("workflow")), None)
        run = (item or {}).get("workflow") or {}
        steps = run.get("steps") or ()
        # While the run is live the runner retries a step only once its branch is idle; offer only those steps.
        busy = {
            tuple(step.get("branch") or ())
            for step in steps
            if run.get("state") == "running" and step.get("state") in ("running", "paused")
        }
        chosen = None
        if row_id and row_id.startswith("wf:"):
            chosen = row_id[3:].split(":", 1)[0]
        for wanted in ("failed", "unknown"):
            for step in steps:
                if step.get("state") != wanted:
                    continue
                if chosen is not None:
                    try:
                        mine = f"{int(step['node_id'])}.{int(step['branch'][0])}.{int(step['branch'][1])}"
                    except (KeyError, TypeError, ValueError, IndexError):
                        continue
                    if mine != chosen:
                        continue
                try:
                    branch = (int(step["branch"][0]), int(step["branch"][1]))
                    node_id = int(step["node_id"])
                except (KeyError, TypeError, ValueError, IndexError):
                    return None
                generate = step.get("type") == "DTFGenerate"
                if any(is_descendant(other, branch) for other in busy) or (
                    generate and any(other[:1] == branch[:1] for other in busy)
                ):
                    continue
                return node_id, branch
        return None

    def _retry_workflow(self, queue_id: str) -> None:
        row = self.model.row_at(self.page.queueItemsView.currentIndex().row())
        target = self._workflow_retry_target(self._views.get(queue_id), row.item_id if row else None)
        if target is None:
            return
        node_id, branch = target

        async def work():
            from dtflow.services.workflow_queue import retry_step

            return await retry_step(self._state, queue_id, node_id, branch)

        def retried(new_id, failure) -> None:
            if failure is not None:
                failed = Presentation("error", "Not retried", str(failure))
                self._message(failed if isinstance(failure, ValidationError) else present_generation_failure(failure))
                return
            if new_id == queue_id:
                self._message(Presentation("ok", "Retry posted", "The running workflow retries that step next."))
                self.refresh()
                return
            self._select_after = new_id

            def started(how, error) -> None:
                if error is not None:
                    self._message(Presentation("warning", "Retry saved, paused", str(error)))
                else:
                    self._message(Presentation("ok", "Retry started", f"The failed step runs again ({how})."))
                self.refresh()

            self.start_created(new_id, started)

        self._runner.submit(work(), retried)

    def retry_item(self) -> None:
        """Retry item: the selected item reruns in place, same settings and seed, as a new (possibly billable) attempt.

        No new queue is made (#240). The earlier attempt stays recorded (attempts store and queue history); an unknown
        one stays unknown.
        """
        if (self._view() or {}).get("kind") == "workflow" and self._selected is not None:
            self._retry_workflow(self._selected)
            return
        row = self.model.row_at(self.page.queueItemsView.currentIndex().row())
        if row is None or self._selected is None or row.state not in RELAUNCH_STATES:
            return
        queue_id = self._selected
        unknown = row.state == "unknown"
        self.page.itemRetryButton.setEnabled(False)  # one click, one retry; the next read re-enables it
        self._runner.submit(
            retry_queue_item(self._state, queue_id, row.position),
            lambda how, failure: self._item_retried(queue_id, how, failure, unknown),
        )

    def _item_retried(self, queue_id: str, how, failure, unknown: bool = False) -> None:
        if failure is not None:
            failed = Presentation("error", "Not retried", str(failure))
            self._message(failed if isinstance(failure, ValidationError) else present_generation_failure(failure))
            self.refresh()
            return
        note = (
            " The earlier request may still finish on the server, so this can make a second image." if unknown else ""
        )
        if how == "joins":
            view = self._views.get(queue_id) or {}
            if view.get("status") == "paused":
                self._message(Presentation("ok", "Item retried", "It runs when this queue is resumed." + note))
            else:
                self._message(Presentation("ok", "Item retried", "It runs again in this queue." + note))
            self.refresh()
            return
        if queue_id == self._dispatching or queue_id in self._overlapping:
            # This window's dispatcher may still be looping on the queue (its run had just ended): resume it in place,
            # since putting it up next behind itself would never start it.
            self._post("resume", queue_id)
            self._message(Presentation("ok", "Item retried", "It runs again in this queue." + note))
            return

        def started(where, error) -> None:
            if error is not None:
                self._message(Presentation("warning", "Item retried, paused", str(error)))
            elif where == "started":
                self._message(Presentation("ok", "Item retried", "It runs again in this queue." + note))
            else:
                self._message(Presentation("ok", "Item retried", f"It runs again in this queue ({where})." + note))

        self.start_created(queue_id, started)

    def relaunch(
        self,
        queue_id: str,
        *,
        new_seeds: bool = False,
        failed_only: bool = False,
    ) -> None:
        """A new queue from ``queue_id``'s recorded items: started now, up next behind this window's run, or paused."""
        up_next = self.dispatching_here

        async def work():
            new_id = await relaunch_queue(
                self._state,
                queue_id,
                new_seeds=new_seeds,
                failed_only=failed_only,
                up_next=up_next,
            )
            return new_id, (await read_queue_views(self._state, new_id))[0]["status"]

        self._runner.submit(work(), self._relaunched)

    def _relaunched(self, result, failure) -> None:
        noun = "Queue relaunched"
        if failure is not None:
            failed = Presentation("error", "Not relaunched", str(failure))
            self._message(failed if isinstance(failure, ValidationError) else present_generation_failure(failure))
            return
        queue_id, status = result
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
    def _composer_backend(self):
        return self._draft[1:3] if self._draft is not None else self._backend()

    def quick_disclosure(self, *, plan: bool) -> str:
        name, _ = self._composer_backend() if plan else self._backend()
        source = "the new queue's draft and plan" if plan else "Generate's current request"
        if plan:
            pose = self._draft is not None and (
                isinstance(self._draft_job, PoseJob) or self._draft[0].pose_control is not None
            )
        else:
            pose = isinstance(self._edit_source(), PoseJob)
        if pose:
            return f"Uses {source}. Provider: {name}. Start sends the pose map to DT+ Cloud and may use credits."
        from dtflow.core.video import is_video_model

        video = (
            self._draft[0].num_frames is not None
            if plan and self._draft is not None
            else (not plan and is_video_model(self._live_choices().get("model", "")))
        )
        if video:
            return f"Uses {source}. DT+ video: up to 10 concurrent clips, from Connection's default. Start may use credits; Retry is manual."
        return f"Uses {source}. Provider: {name}. A cloud run may use credits."

    def _set_draft(self, request, name, endpoint, asset_ids, job) -> None:
        self._draft = request, name, endpoint, tuple(asset_ids)
        # Generate is an explicit one-way source, including its offered LoRA/model choices.
        # Keeping these plain dict/list snapshots avoids changing an existing draft when
        # Generate changes checkpoint, key or inventory.
        self._draft_choices = self._live_choices()
        self._draft_job = job
        self._draft_template_pose = None
        fixed = self.page.planFixedLorasEdit
        fixed.blockSignals(True)  # the draft's LoRAs are the row's values; callers refresh the preview
        fixed.setText("; ".join(lora_set_value(item.identifier, item.weight) for item in request.loras))
        fixed.blockSignals(False)
        edit = self.page.composerPromptEdit
        edit.blockSignals(True)
        edit.setPlainText(request.prompt)
        edit.blockSignals(False)
        self.pose_editor.draft_changed()

    def _composer_choices(self) -> dict:
        if self._draft is None:
            return self._with_all_models(self._live_choices())
        request, name, endpoint, _ = self._draft
        choices = self._draft_choices
        if self._backend() == (name, endpoint):
            # The model list follows the inventory, not the Generate family view captured when the draft started.
            choices = self._with_all_models(choices)
        else:
            # Inventory and account-bound receipt choices cannot migrate to a different captured backend.
            choices = {
                **choices,
                "models": [request.model_id],
                "loras": {item.identifier: item.weight for item in request.loras},
                "receipts": {},
            }
        # Keep exact, imported or now-unlisted choices visible for editing the draft;
        # their presence here does not prove provider availability or authorize uploads.
        models = list(choices.get("models", ()))
        if request.model_id not in models:
            models.append(request.model_id)
        loras = dict(choices.get("loras", {}))
        for item in request.loras:
            loras.setdefault(item.identifier, item.weight)
        return {
            **choices,
            "models": models,
            "loras": loras,
            "prompt": self.page.composerPromptEdit.toPlainText(),
            "size": f"{request.width_px}x{request.height_px}",
        }

    def _with_all_models(self, choices: dict) -> dict:
        """``choices`` with every inventory model (and its versions) in place of Generate's one-family list."""
        everything = self._live_models()
        models = everything.get("models")
        if not models:
            return choices
        return {
            **choices,
            "models": list(models),
            "model_versions": {**choices.get("model_versions", {}), **everything.get("model_versions", {})},
        }

    def use_generate_settings(self, *, pose_only: bool = False) -> bool:
        """An explicit one-way copy, never a continuing binding to Generate's mutable form."""
        try:
            job = self._edit_source()
            keep_base = pose_only and self._draft is not None
            if pose_only and not isinstance(job, PoseJob):
                raise FormError("Choose a valid Controlnet pose before creating this plan.", None)
            if keep_base:
                request, name, endpoint, assets = self._composer_request(preview=True)
                if (name, endpoint) != self._backend():
                    raise FormError(
                        "The queue draft uses another connection. Use Generate settings explicitly before planning.",
                        None,
                    )
                if request.operation != "txt2img" or (
                    self._draft_job is not None and not isinstance(self._draft_job, PoseJob)
                ):
                    raise FormError("Use Generate settings to replace the Inpaint draft before a pose plan.", None)
                # Reuse the existing copy seam without reading/rerolling Generate's seed or changing plan rows.
                # A recorded pose's execution references must not survive the explicit fresh-map capture.
                request = replace(request, schema_version=2, pose_control=None, image_inputs=None)
            else:
                request, name, endpoint, assets = self._request_source(preview=True)
            self._set_draft(request, name, endpoint, assets, job)
            if not keep_base:
                self._authoring_reconstructed = False
        except ValidationError as exc:
            self._message(Presentation("error", "Draft not replaced", str(exc)))
            return False
        self.sync_edit_fields()
        self._plan_changed()
        self.preview()
        self.templates.changed()
        return True

    def _composer_request(self, *, preview=False):
        if self._draft is None:
            request, name, endpoint, assets = self._request_source(preview=True)
            self._set_draft(request, name, endpoint, assets, self._edit_source())
        self.pose_editor.sync()
        self.pose_editor.validate()
        request, name, endpoint, assets = self._draft
        prompt = self.page.composerPromptEdit.toPlainText()
        if not prompt.strip():
            raise FormError("Enter a base prompt for the new queue.", "composerPromptEdit")
        # A changed fixed selection must not retain receipts for LoRAs that were removed.
        loras = self._fixed_loras()
        selected = {item.identifier for item in loras}
        bound = tuple(item for item in request.prepared_assets if item.identifier in selected)
        changes = {"prompt": prompt, "loras": loras}
        if bound != request.prepared_assets:
            evidence = tuple(pair for pair in request.resolution_evidence if pair[0] != "prepared_assets")
            if bound:
                evidence += (("prepared_assets", ",".join(item.receipt_id for item in bound)),)
            changes.update(prepared_assets=bound, resolution_evidence=evidence)
        return replace(request, **changes), name, endpoint, assets

    def copy_to_generate(self) -> None:
        try:
            request, *_ = self._composer_request(preview=True)
        except FormError as exc:
            self._message(Presentation("error", "Not copied", str(exc)))
            return
        self._reuse_hook(asdict(request))
        self._generate_hook()

    def copy_queue_prompt(self) -> None:
        view = self._view()
        if view is not None:
            QApplication.clipboard().setText((view.get("first") or {}).get("prompt") or "")

    def _prompt_source(self) -> str:
        return (
            self.page.composerPromptEdit.toPlainText() if self._draft is not None else self._choices().get("prompt", "")
        )

    def open_composer(self) -> None:
        """Show the composer in the center instead of the selected queue; its values are kept between openings."""
        if feedback(self.page)._armed:
            feedback(self.page).dismiss()
        self._composing = True
        if self._draft is None:
            self.use_generate_settings()
        self._invalidate_preview()
        self._refresh_choices()
        self._render()
        self.preview()

    def close_composer(self) -> None:
        self.templates.navigation_changed()
        self._composing = False
        self._invalidate_preview()
        self._preview_timer.stop()
        self._render()

    def shown(self) -> None:
        """Render the latest queue state when Queues becomes visible again."""
        self.model.thumbnails.clear()
        self._hover_preview.hide()
        self._render()
        self.page.queueItemsView.viewport().update()
        if self._composing:
            self._refresh_choices()
            self._invalidate_preview()  # the backend or Inpaint source may have changed without changing the request
            self.preview()

    @property
    def composing(self) -> bool:
        return self._composing

    def _align_plan_labels(self) -> None:
        """Sit each composer label on its field's first line: the form layout top-aligns labels, which then read a few
        pixels above the text of the (padded) input beside them."""
        form = self.page.planForm
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)  # labels sit next to inputs
        for row in range(form.rowCount()):
            label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            label = label_item.widget() if label_item is not None else None
            field = field_item.widget() if field_item is not None else None
            if label is not None and field is not None:
                label.setContentsMargins(0, 6 if isinstance(field, QPlainTextEdit) else 3, 0, 0)

    def _plan_models(self, choices: dict) -> tuple[list[str], list[str | None]]:
        models = [m for m in self.page.planModelsEdit.values() if m] or [choices.get("model") or ""]
        models = [model for model in models if model]
        versions = choices.get("model_versions", {})
        # Generate lists versions only for the checkpoints in its current family view; a plan model outside it is
        # resolved here from the bundled specs and catalogue.
        return models, [versions.get(model) or checkpoint_picker_versions(model) for model in models]

    def _offered_loras(self) -> dict:
        """LoRA choices for the models this plan uses (Generate's checkpoint when none is chosen), as on Generate."""
        choices = self._choices()
        models, versions = self._plan_models(choices)
        if not models and choices.get("family_versions"):  # no model anywhere: follow Generate's chosen family
            versions = [choices["family_versions"]]
        offered = lora_offer(choices.get("loras", {}), choices.get("lora_versions", {}), versions)
        diagnostic_event(
            log,
            "ui.queue_lora_picker",
            data={
                "offered": len(offered),
                "total": len(choices.get("loras", {})),
                "models": models,
                "versions": [sorted(item) for item in versions],
                "family": sorted(choices.get("family_versions") or ()),
            },
        )
        return offered

    def _loras_notice(self) -> str | None:
        choices = self._choices()
        models, versions = self._plan_models(choices)
        if not models and choices.get("family_versions"):
            return None
        return lora_offer_notice(models, versions)

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
        page.planFixedLorasEdit.configure(
            "search",
            "Fixed LoRA",
            weights=self._offered_loras,
            families=lambda: self._choices().get("lora_families", {}),
            notice=self._loras_notice,
            initial=self._fixed_loras,
            convert=self._lora_weight_chips,
            label=self._lora_set_label,
            editor_title="Fixed LoRAs",
            replace_all=True,
            editor_hint="Check the LoRAs every item of the new queue uses and set their weights. Apply replaces the list.",
        )
        page.planLoraSetsEdit.configure(
            "search",
            "LoRA",
            weights=self._offered_loras,
            families=lambda: self._choices().get("lora_families", {}),
            notice=self._loras_notice,
            convert=self._lora_set_chip,
            label=self._lora_set_label,
            initial=self._generate_loras,
            edit_chip=lambda chip: tuple(LoRASelection(name, weight) for name, weight in parse_lora_set(chip)),
            editor_title="LoRA set",
        )
        page.planLorasEdit.configure(
            "search",
            "LoRA weight",
            weights=self._offered_loras,
            families=lambda: self._choices().get("lora_families", {}),
            notice=self._loras_notice,
            initial=self._lora_weight_selection,
            convert=self._lora_weight_chips,
            editor_title="Edit LoRA weights",
            multi_weight=True,
            replace_all=True,
            group=self._lora_weight_group,
            editor_hint=(
                "Check a LoRA and press + or click its slider to collect each weight to try for it (click a weight to drop it). "
                "A checked LoRA without collected weights uses its slider value. Enter or Apply replaces the weights."
            ),
        )

    def _fixed_loras(self) -> tuple[LoRASelection, ...]:
        """The Fixed LoRAs row as selections, in the order shown."""
        found: dict[str, float] = {}
        for chip in self.page.planFixedLorasEdit.values():
            for identifier, weight in parse_lora_set(chip):
                found.setdefault(identifier, weight)
        return tuple(LoRASelection(identifier, weight) for identifier, weight in found.items())

    def _generate_loras(self) -> tuple[LoRASelection, ...]:
        """The Generate form's LoRAs, which the LoRA-set editor starts from."""
        try:
            request, *_ = self._composer_request(preview=True)
        except (FormError, ValidationError):
            return ()
        return tuple(request.loras)

    def _lora_weight_chips(self, chosen) -> list[str]:
        """One ``IDENTIFIER: weight`` chip per checked LoRA."""
        if any(";" in identifier for identifier, _ in chosen):
            self._message(Presentation("error", "LoRA not added", "A LoRA weight cannot name an identifier with ';'."))
            return []
        return [lora_set_value(identifier, weight) for identifier, weight in chosen]

    def _lora_weight_groups(self) -> dict[str, list[str]]:
        """The weight chips grouped per LoRA, in the order first added."""
        grouped: dict[str, list[str]] = {}
        for chip in self.page.planLorasEdit.values():
            identifier, separator, weight = chip.rpartition(":")
            key, value = (identifier.strip(), weight.strip()) if separator else (chip, "")
            grouped.setdefault(key, []).append(value)
        return grouped

    @staticmethod
    def _with_weighted_loras(request, identifiers):
        """The request with every weighted LoRA present, so the planner can replace its weight (the LoRA need not be
        on Generate). Added at 1.0; every plan item overrides that weight."""
        have = {item.identifier for item in request.loras}
        extra = tuple(LoRASelection(name, 1.0) for name in dict.fromkeys(identifiers) if name not in have)
        return replace(request, loras=(*request.loras, *extra)) if extra else request

    @staticmethod
    def _lora_weight_group(chip: str) -> tuple[str, str]:
        """A weight chip ``IDENTIFIER: weight`` as (LoRA, weight), so a LoRA's weights share one line."""
        identifier, separator, weight = chip.rpartition(":")
        return (identifier.strip(), weight.strip()) if separator else (chip, "")

    def _lora_weight_selection(self) -> tuple[LoRASelection, ...]:
        """Every weight chip as a selection, for the editor to start from (a LoRA may repeat)."""
        pairs = []
        for identifier, weights in self._lora_weight_groups().items():
            for weight in weights:
                try:
                    pairs.append(LoRASelection(identifier, float(weight)))
                except ValueError:
                    continue
        return tuple(pairs)

    def _lora_weight_lines(self) -> str:
        """The weight chips as the planner's lines: one ``IDENTIFIER: w1; w2`` per LoRA, in the order first added."""
        grouped = self._lora_weight_groups()
        return "\n".join(f"{key}: {'; '.join(values)}" if values != [""] else key for key, values in grouped.items())

    @staticmethod
    def _lora_set_label(chip: str) -> str:
        """A LoRA-set chip as ``ShortName[Tag] 0.8 + Other[Tag] 0.4``; anything unreadable shows as written."""
        pairs = parse_lora_set(chip)
        return " + ".join(f"{display_label(name)} {weight:g}" for name, weight in pairs) if pairs else chip

    def _lora_set_chip(self, chosen) -> str:
        """The checked (identifier, weight) pairs as one LoRA-set chip; none checked is the set without LoRAs."""
        if any(";" in identifier for identifier, _ in chosen):
            self._message(Presentation("error", "LoRA not added", "A LoRA set cannot name an identifier with ';'."))
            return ""
        return " + ".join(lora_set_value(identifier, weight) for identifier, weight in chosen) or NO_LORA

    def _size_options(self) -> list[str]:
        current = self._choices().get("size")
        return [*([current] if current else []), *SIZE_PRESETS]

    def _refresh_choices(self) -> None:
        """Offered values follow the Generate form: its size first, its steps/guidance/strength as the box values."""
        page = self.page
        page.planSizesEdit.refresh_options()
        try:
            request, *_ = self._composer_request(preview=True)
        except (FormError, ValidationError):
            return
        page.planStepsEdit.refresh_options(str(request.steps))
        page.planGuidanceEdit.refresh_options(str(request.guidance))
        page.planStrengthsEdit.refresh_options(str(request.strength))

    def _planned(self) -> bool:
        return self.page.planModeCombo.currentText() != REPEAT

    def _sync_preview_table_visibility(self) -> None:
        """Only show the table when the active comparison plan has actual preview rows."""
        self.page.planTableView.setVisible(self._planned() and self.plan_model.rowCount() > 0)

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
        self.plan_model.clear()  # a mode change invalidates the previous mode's preview.
        self._sync_preview_table_visibility()
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
        return self._plan_form().uses_randomness

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
        return count_text(self._plan_form(), self._prompt_source(), self._wildcards_dir)

    def _base_html(self, request) -> str:
        """``_base_text`` as rich text, the base prompt's variation keywords styled as tags."""
        parts = self._base_parts(request)
        head = parts[0] if request.prompt.strip() else ""
        return (tagged_html(head, tag_spans(head)) if head else escape(parts[0])) + escape(
            " · " + " · ".join(parts[1:])
        )

    def _base_text(self, request) -> str:
        return " · ".join(self._base_parts(request))

    def _base_parts(self, request) -> list[str]:
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
        if request.num_frames is not None:
            from dtflow.core.video import duration_seconds

            parts.append(
                f"Video · {duration_seconds(request.model_id, request.num_frames):.2f} s · {request.num_frames} frames"
            )
            parts.append("Image to video · one start image" if request.start_image else "Text to video")
        pose = self._draft_job.control if isinstance(self._draft_job, PoseJob) else request.pose_control
        if pose is not None:
            parts.append(
                f"Pose · {pose.checkpoint} · strength {pose.strength:g} · {pose.control_mode} "
                f"· {pose.guidance_start:g}–{pose.guidance_end:g}"
            )
        elif self._draft_job is not None:
            parts.append(f"Inpaint · strength {request.strength:g}")
        return parts

    def preview(self) -> None:
        """Live count and items for the composer: pure planning against the Generate form, nothing persisted or sent."""
        self._preview_timer.stop()
        if not self._composing or not self.page.queueWorkspace.isVisible():
            self._invalidate_preview()
            return  # reading the request marks and snaps Generate fields: only while Queues is shown (see ``shown``)
        page = self.page
        try:
            request, *_ = self._composer_request(preview=True)
        except FormError as exc:
            self._invalidate_preview()  # a validity change must not be overwritten later
            page.composerBaseLabel.setTextFormat(Qt.TextFormat.PlainText)
            page.composerBaseLabel.setText("Queue draft settings are not valid yet.")
            self._show(page.planCountLabel, "error", sentence(str(exc)))
            self.plan_model.clear()
            self._sync_preview_table_visibility()
            self._clear_summary()
            return
        form = self._plan_form()
        lists = fingerprint(self._wildcards_dir) if form.variation != "Off" else ()  # an edited list changes the items
        key = (form, self._request_key(request), lists)
        if key == self._preview_key:
            return  # polling and presentation syncs reach here unchanged: keep what is shown or being drawn
        self._invalidate_preview()
        self._preview_key = key
        page.composerBaseLabel.setTextFormat(Qt.TextFormat.RichText)
        page.composerBaseLabel.setText(self._base_html(request))
        page.composerBaseLabel.setToolTip(
            f"<p style='white-space:pre-wrap'>{tagged_html(request.prompt, tag_spans(request.prompt))}</p>"
            if request.prompt
            else ""
        )
        if not self._planned():
            self._mark_plan(None)
            count = page.queueCountSpinBox.value()
            first = request.seed
            seeds = f"seed {first}" if count == 1 else f"seeds {first}–{first + count - 1}"
            self._show(page.planCountLabel, "ok", f"{images_requests(count)} · {seeds}")
            self._show_summary(request, count, "Nothing (repeats the queue draft)", seeds.capitalize())
            return
        if self._plan_form().variation != "Off" and not self._runner.closed:
            self._preview_in_background(request)
            return
        planned = self._plan_for(request)
        if planned is None:
            self._show_summary(request, None, "—", "—")
            return
        plan, table = planned
        self._summarize(request, plan, table.total, table)

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
                self._sync_preview_table_visibility()
            self._show_summary(request, None, "—", "—")
            return
        plan, table = result
        self._show_table(table)
        self._show(self.page.planCountLabel, "ok", summary_text(table))
        self._summarize(request, plan, table.total, table)

    def _summarize(self, request, plan: Plan, total: int, table: PlanTable) -> None:
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
        stacks = tuple(dict.fromkeys(table.lora_stacks))
        loras = (
            ("None" if stacks[0] == "none" else stacks[0])
            if len(stacks) == 1
            else f"Varies by item ({len(stacks)} LoRA combinations); see preview"
            if stacks
            else None
        )
        self._show_summary(request, total, varies, sentence(SEED_POLICY_TEXT[policy]), loras=loras)

    def _show_summary(self, request, count: int | None, varies: str, seeds: str, *, loras: str | None = None) -> None:
        """The right column while composing: what the queue being built will be, before anything is saved."""
        page = self.page
        name, endpoint = self._draft[1:3] if self._draft is not None and self._composing else self._backend()
        page.composerSummaryBackendLabel.setText(f"{name} · {endpoint}" if endpoint else name)
        page.composerSummaryModelLabel.setText(request.model_id)
        labels = self._choices().get("lora_families", {})
        loras = loras or "\n".join(
            f"{lora_text(lora.identifier, lora.weight)} · {labels.get(lora.identifier, 'Unknown')}"
            for lora in request.loras
        )
        page.composerSummaryLorasLabel.setText(loras or "None")
        page.composerSummaryItemsLabel.setText("—" if count is None else str(count))
        page.composerSummaryVariesLabel.setText(varies)
        page.composerSummarySeedsLabel.setText(seeds)
        self._summary_fit.schedule()

    def _clear_summary(self) -> None:
        """Generate is not valid yet: the summary shows no stale values from an earlier request."""
        page = self.page
        for name in ("Model", "Loras", "Items", "Varies", "Seeds"):
            getattr(page, f"composerSummary{name}Label").setText("—")
        self._summary_fit.schedule()

    def _schedule_preview(self, *, invalidate: bool = True) -> None:
        if invalidate:
            self._invalidate_preview()  # the inputs changed: a result still being drawn is already stale
        if self._composing and self.page.queueWorkspace.isVisible():
            self._preview_timer.start()

    def _shortcut_button(self):
        """Choose an active Start action, never Save paused, Pause or Resume."""
        page = self.page
        if self._composing:
            buttons = (page.startQueueButton, page.addQueueButton)
            return next((button for button in buttons if button.isEnabled()), None)
        view = self._view()
        action = primary_action(
            view,
            self._controls_for(view),
            started=self._started(view),
            owned=bool(view and self._owns(view["queue_id"])),
        )
        return page.queuePrimaryButton if action is not None and action.text in ("Start", "Start next") else None

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
            request, name, endpoint, asset_ids = (
                self._request_source() if quick and count is not None else self._composer_request()
            )
            if not (quick and count is not None):
                if (name, endpoint) != self._backend():
                    raise ValidationError(
                        "The connection changed since this Queue draft was captured. "
                        "Use Generate settings or reload a template for the current backend before creating a queue."
                    )
                if self.templates.loaded is not None:
                    asset_ids = self.templates.validate_use(request, name, endpoint)
        except ValidationError as exc:
            # From another tab the marked field is out of sight, so say where it is.
            report(Presentation("error", "Queue not created", f"On Generate: {exc}" if quick else str(exc)))
            return
        plan, resolving = None, None
        if (count is None) if quick else self._planned():
            if self._plan_form().variation != "Off":
                resolving = self._plan_inputs(request)  # prompt sampling runs on the worker, with the creation
            else:
                planned = self._plan_for(request)
                if planned is None:
                    error = self.page.planCountLabel.text()
                    report(Presentation("error", "Queue not created", error))
                    return
                plan, _table = planned
        source_form = PlanForm("Repeat") if quick and count is not None else self._plan_form()
        count = count or self.page.queueCountSpinBox.value()
        backend = {"name": name, "endpoint": endpoint, "scenario": "success", **self._simulation()}
        job = self._edit_source() if quick and source_form.mode == "Repeat" else self._draft_job
        composer_source = {
            "version": 1,
            "base": asdict(replace(request, prepared_assets=())),
            "form": asdict(source_form),
            "count": count,
            "asset_ids": list(asset_ids),
        }
        # Add to queue during a single generation: remember the intent now, since the generation may end before the
        # new queue is saved (nothing then runs to follow, but the user still asked for it to start by itself).
        after_generation = quick and action == "add" and self._activity.generating

        async def work():
            drawn = plan
            if resolving is not None:
                drawn = await asyncio.to_thread(self._resolve_only, *resolving)
            queue_id = await self._create(
                backend,
                request,
                asset_ids,
                count,
                drawn,
                job,
                up_next=action == "add",
                composer_source=composer_source,
            )
            status = (await read_queue_views(self._state, queue_id))[0]["status"] if action == "add" else None
            return queue_id, status

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
            return "none", "Set up Pair or Cross in New queue to reuse it here."
        level, text = self._count()
        return level, f"{self.page.planModeCombo.currentText()} · {text}" if level == "ok" else sentence(text)

    def last_plan_total(self) -> int | None:
        if not self._planned():
            return None
        try:
            prompt = self._prompt_source()
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

    def quick_create(self, action: str, *, count: int | None) -> None:
        self.create(action, count=count, quick=True)

    def prepare_script(self, source, source_name, cap, done, *, inventory=None, expected_steps=None, owner=None):
        """Prepare the form without creating/admitting a queue or opening any provider."""
        from dtflow.services.script_setup import prepare_script

        try:
            request, name, endpoint, asset_ids = self._request_source()
        except (ValidationError, FormError) as exc:
            done(None, ValidationError(f"On Generate: {exc}"))
            return
        backend = {"name": name, "endpoint": endpoint, "scenario": "success", **self._simulation()}

        async def work():
            base = await bind_prepared(self._state, name, request, asset_ids)
            return await prepare_script(
                self._state,
                source,
                source_name,
                base,
                backend,
                cap,
                inventory=inventory,
                expected_steps=expected_steps,
                owner=owner,
            )

        self._runner.submit(work(), done)

    @profile_slow_gui_callback("queue.start_prepared_script")
    def start_prepared_script(self, run_id, ordinal, answers, done):
        """Run Script is the sole point that creates a queue for a preloaded form."""
        from dtflow.services.script_setup import reserve_setup, release_setup
        from dtflow.services.script_form_inputs import resolve_form_inputs
        from dtflow.services.script_runs import read_open_prompt

        if self.dispatching_here or self._activity.generating or self._runner.closed:
            done(None, ValidationError("Finish the active generation or queue before Run Script."))
            return
        try:
            _, current_name, current_endpoint, _ = self._request_source()
        except (ValidationError, FormError) as exc:
            done(None, ValidationError(f"On Generate: {exc}"))
            return

        async def work():
            setup = reserve_setup(self._state, run_id)
            try:
                # A changed backend must not inherit a previous provider's authoring context.
                if (current_name, current_endpoint) != (setup.backend["name"], setup.backend.get("endpoint")):
                    raise ValidationError("The selected backend changed. Reload Setup before Run Script.")
                if ordinal is not None:
                    prompt = await asyncio.to_thread(read_open_prompt, self._state, run_id)
                    if prompt is None or prompt.ordinal != ordinal:
                        raise ValidationError("This prepared form has closed. Reload Setup.")
                    await asyncio.to_thread(
                        resolve_form_inputs, self._state, run_id, ordinal, prompt.components, answers
                    )
                return await create_script_queue(
                    self._state,
                    setup.backend,
                    setup.source,
                    setup.source_name,
                    setup.base,
                    max_generations=setup.cap,
                    expected_steps=setup.expected_steps,
                    require_start=True,
                    prepared_run_id=run_id,
                    setup_ordinal=ordinal,
                    setup_answers=answers,
                )
            except BaseException:
                release_setup(setup)
                raise

        @profile_slow_gui_callback("queue.prepared_script_created")
        def created(result, failure):
            if failure is not None:
                done(None, failure)
                return
            queue_id, actual_run = result
            self._created_hook()
            if self.dispatching_here or self._activity.generating:
                done((queue_id, actual_run, "paused"), None)
            else:
                self._dispatch(queue_id, resume_fallback=None)
                done((queue_id, actual_run, "started"), None)
            self.refresh()

        self._runner.submit(work(), created)

    @profile_slow_gui_callback("queue.start_script")
    def start_script(
        self, source: str, source_name: str, max_generations: int, done, expected_steps: int | None = None
    ) -> None:
        """Scripts Setup: prepare one script queue from Generate's captured settings, with generation gated by input.

        ``done(result, failure)`` gets ``(queue id, run id, "started" | "paused")``. Nothing is created while this window
        generates or dispatches; the script run is an ordinary queue the user can also see in Queues."""
        if self.dispatching_here or self._activity.generating or self._runner.closed:
            done(None, ValidationError("A generation or queue is running in this window. Press Start when it ends."))
            return
        try:
            request, name, endpoint, asset_ids = self._request_source()
        except (ValidationError, FormError) as exc:
            done(None, ValidationError(f"On Generate: {exc}"))
            return
        backend = {"name": name, "endpoint": endpoint, "scenario": "success", **self._simulation()}

        async def work():
            base = await bind_prepared(self._state, backend["name"], request, asset_ids)
            return await create_script_queue(
                self._state,
                backend,
                source,
                source_name,
                base,
                max_generations=max_generations,
                answers=None,  # interactive Scripts runs park on the persisted requestFromUser bridge
                require_start=True,  # Setup may build forms, but generation needs explicit Run Script consent
                expected_steps=expected_steps,
            )

        def created(result, failure) -> None:
            if failure is not None:
                done(None, failure)
                return
            queue_id, run_id = result
            self._created_hook()
            if self.dispatching_here or self._activity.generating:
                done((queue_id, run_id, "paused"), None)
            else:
                self._dispatch(queue_id, resume_fallback=None)
                done((queue_id, run_id, "started"), None)
            self.refresh()

        self._runner.submit(work(), created)

    def start_workflow(self, workflow: dict, name: str, done) -> None:
        """Workflows Run: one workflow queue on the current connection, started like Scripts' Run Script.

        ``workflow`` is the ``dump_workflow`` document. ``done(result, failure)`` gets ``(queue id, run id, how)``
        where ``how`` is ``"started"``, ``"up next"`` (behind the queue this window runs) or ``"after generation"``.
        The run is an ordinary queue, also visible in Queues; creation itself never generates."""
        if self._runner.closed:
            done(None, ValidationError("DTFlow is closing; nothing was queued."))
            return
        backend_name, endpoint = self._backend()
        backend = {"name": backend_name, "endpoint": endpoint, "scenario": "success", **self._simulation()}

        async def work():
            from dtflow.services.workflow_queue import create_workflow_queue

            return await create_workflow_queue(self._state, backend, workflow, name)

        def created(result, failure) -> None:
            if failure is not None:
                done(None, failure)
                return
            queue_id, run_id = result
            self.start_created(queue_id, lambda how, error: done(None if error else (queue_id, run_id, how), error))

        self._runner.submit(work(), created)

    def start_created(self, queue_id: str, done) -> None:
        """Start a just-created paused queue now, up next behind this window's run, or after the generation."""
        if self._runner.closed:
            done(None, ValidationError("DTFlow is closing; the queue stays paused."))
            return
        if self._activity.generating:
            self._after_generation.append(queue_id)
            done("after generation", None)
        elif self.dispatching_here:

            def ordered(_, failure) -> None:
                done(None if failure else "up next", failure)
                self.refresh()

            self._runner.submit(change_queue_order(self._state, queue_id, "enqueue"), ordered)
            return
        else:
            self._dispatch(queue_id, resume_fallback=None)
            done("started", None)
        self.refresh()

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
                self._quick_report(Presentation("error", "Queue not created", sentence(relabel_text(str(failure)))))
                return
            self._quick_report(present_generation_failure(failure))
            return
        queue_id, status = result
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
            queue_id = await self._create(backend, request, asset_ids, 1, job=job, up_next=True)
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

    # -- Generate clicks (#558) ------------------------------------------------------------------------------------
    def run_generate(self, request, name, endpoint, asset_ids, sink, job=None) -> None:
        """Generate's single image, edit, pose map or video as a visible one-item queue on the shared dispatch path.

        ``job`` is the Inpaint edit or the Controlnet pose Generate was asked to run; the queue stages its exact inputs
        before dispatch, like any queue of that kind.

        With this window dispatching, the queue waits up next and the owner starts it at once, ahead of ordinary queue
        work; otherwise it is saved paused and this window dispatches it. Another process's ownership is reported, never
        duplicated. Every click is its own queue: earlier ones keep running and settle in Queues, and ``sink`` (Generate)
        follows only the latest.
        """
        backend = {"name": name, "endpoint": endpoint, "scenario": "success", **self._simulation()}
        owner_here = self.dispatching_here
        # Named before anything exists, so the queue's first event or result can never arrive unrecognised.
        queue_id = uuid4().hex
        self._start_manual(_ManualRun(sink, queue_id))

        async def work():
            await self._create(
                backend, request, asset_ids, 1, job=job, up_next=owner_here, origin=MANUAL_ORIGIN, queue_id=queue_id
            )
            return await self._manual_ready(queue_id)

        self._runner.submit(work(), lambda result, failure: self._manual_created(queue_id, result, failure))

    def retry_generate(self, queue_id: str, sink) -> None:
        """Retry the Generate queue's item in place: same item and settings, a new request and attempt."""
        self._start_manual(_ManualRun(sink, queue_id))

        async def work():
            await retry_queue_item(self._state, queue_id, 1)
            return await self._manual_ready(queue_id)

        self._runner.submit(work(), lambda result, failure: self._manual_created(queue_id, result, failure))

    def _start_manual(self, run: _ManualRun) -> None:
        self._manual = run  # Generate shows this one from now on; an earlier click carries on unattended
        self._manual_runs[run.queue_id] = run
        self._manual_queues.add(run.queue_id)

    def _finish_manual(self, run: _ManualRun, outcome, failure) -> None:
        """A click's queue ended: Generate hears of it only while it is the one Generate shows."""
        self._manual_runs.pop(run.queue_id, None)
        if self._manual is run:
            self._manual = None
            run.sink.finished(outcome, failure)

    async def _manual_ready(self, queue_id: str):
        view = (await read_queue_views(self._state, queue_id))[0]
        return queue_id, view["items"][0]["request_id"], view["status"]

    def _manual_created(self, queue_id: str, result, failure) -> None:
        run = self._manual_runs.get(queue_id)
        if run is None:
            return
        if failure is not None:
            self._finish_manual(run, None, failure)
            return
        _, request_id, status = result
        run.ready = True
        if self._manual is run:
            run.sink.created(queue_id, request_id)
        if not self.dispatching_here:
            # No owner here (it may have ended after the status was read): nothing else would start this queue.
            self._dispatch(queue_id, resume_fallback=None)
        elif status != "queued":
            # Saved paused beside an owner that started meanwhile (or a Retry): put it up next for that owner.
            async def enqueue():
                try:
                    await change_queue_order(self._state, queue_id, "enqueue")
                    return True
                except QueueError:
                    return False

            self._runner.submit(enqueue(), lambda queued, _: self._manual_enqueued(queue_id, queued))
        if run.stopping and self._manual is run:
            self.stop_generate()
        self.refresh()

    def _manual_enqueued(self, queue_id: str, queued: bool) -> None:
        if not queued and queue_id in self._manual_runs and not self.dispatching_here:
            self._dispatch(queue_id, resume_fallback=None)  # the owner ended first; ending also hands it back (below)

    def stop_generate(self) -> None:
        """Stop the click Generate shows, only: cancel it while it waits up next, else post the cancel to its owner."""
        run = self._manual
        if run is None:
            return
        if not run.ready:
            run.stopping = True  # applied as soon as the queue exists
            return
        queue_id = run.queue_id

        async def work():
            try:
                await change_queue_order(self._state, queue_id, "cancel")
                return True
            except QueueError:
                await post_queue_control(self._state, queue_id, "cancel")
                return False

        self._runner.submit(work(), lambda waiting, failure: self._manual_stopped(run, waiting, failure))

    def _manual_stopped(self, run: _ManualRun, waiting, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        if waiting:  # it never started, so no owner will report its end
            self._finish_manual(run, None, None)
        self.refresh()

    def _manual_delivered(self, delivery: _ManualDelivery) -> None:
        """A click's saved result, or the end of a queue's task; only queues Generate created count."""
        run = self._manual_runs.get(delivery.queue_id)
        if run is None:
            return
        if delivery.ended and delivery.error is None:
            # A queue's end also follows a finished run, possibly after Retry started the next: only an item that
            # is no longer pending or running means this click's run ended without a result (stopped).
            async def item_state():
                return (await read_queue_views(self._state, delivery.queue_id))[0]["items"][0]["state"]

            self._runner.submit(item_state(), lambda state, failure: self._manual_ended(run, state, failure))
            return
        self._finish_manual(run, delivery.outcome, delivery.error)

    def _manual_ended(self, run: _ManualRun, state, failure) -> None:
        if self._manual_runs.get(run.queue_id) is not run:
            return
        if failure is None and state in ("pending", "running"):
            return
        self._finish_manual(run, None, failure)

    def _manual_handed_back(self, failure) -> None:
        """The owner ended while clicks' queues had not reported: they were restored paused, or none could start."""
        runs = [run for run in self._manual_runs.values() if run.ready]
        if not runs:
            return
        if isinstance(failure, StateBusyError):
            for run in runs:
                self._manual_runs.pop(run.queue_id, None)
                if self._manual is run:
                    self._manual = None
                    run.sink.refused(
                        Presentation(
                            "warning",
                            "Dispatch is owned by another process",
                            "The request is saved paused in Queues; press Start there when it stops. Nothing was sent.",
                        )
                    )
            return
        first, *rest = runs
        self._dispatch(first.queue_id, resume_fallback=None, waiting=tuple(run.queue_id for run in rest))

    async def _assets_in_use(self, request, asset_ids) -> tuple[str, ...]:
        """The selected receipts whose LoRA the request still names: one removed in the composer is not bound."""
        if not asset_ids:
            return tuple(asset_ids)
        selected = {item.identifier for item in request.loras}
        registry = AssetRegistry(self._state)
        await registry.open()
        try:
            kept = []
            for asset_id in asset_ids:
                try:
                    item = await registry.prepared(asset_id)
                except ValidationError:
                    kept.append(asset_id)  # unreadable here: the shared binding decides, as before
                    continue
                if item.identifier in selected:
                    kept.append(asset_id)
        finally:
            await registry.aclose()
        return tuple(kept)

    async def _create(
        self,
        backend,
        request,
        asset_ids,
        count,
        plan: Plan | None = None,
        job=None,
        *,
        up_next: bool = False,
        composer_source=None,
        origin: str | None = None,
        queue_id: str | None = None,
    ):
        receipts = ()
        if plan is None:
            # A simple Repeat preserves the explicit Generate/Queue receipt selection.
            asset_ids = await self._assets_in_use(request, asset_ids)
            request = await bind_prepared(self._state, backend["name"], request, asset_ids)
        else:
            dimensions = dict(plan.dimensions)
            sets = dimensions.get("loras")
            weighted = [name.partition(".")[2] for name in dimensions if name.startswith("lora_weight.")]
            request = self._with_weighted_loras(request, weighted)
            # Resolve only the receipts used by effective Pair/Cross items. A removed
            # Generate LoRA must not block a C/None plan through an unrelated stale receipt.
            request = replace(
                request,
                prepared_assets=(),
                resolution_evidence=tuple(pair for pair in request.resolution_evidence if pair[0] != "prepared_assets"),
            )
            if backend["name"] == "cloud":
                # LoRA sets replace the draft; weight sweeps add to its fixed LoRAs.
                names = (
                    {identifier for chosen in sets for identifier, _ in chosen}
                    if sets is not None
                    else {item.identifier for item in request.loras}
                )
                # Read the latest locally confirmed receipt for each effective name,
                # without binding an unused receipt or contacting DT+ before Start.
                if names:
                    candidates = await script_asset_choices(self._state, "cloud")
                    receipts = tuple(item for item in candidates if item.identifier in names)
        if isinstance(job, PoseJob) or request.pose_control is not None:
            selection = await asyncio.to_thread(
                selection_for,
                backend["name"],
                backend["endpoint"],
                self._state,
                **{key: backend[key] for key in ("scenario", "delay", "previews") if key in backend},
            )
            options = dict(
                saved=self._saved(),
                plan=plan,
                count=count,
                up_next=up_next,
                receipts=receipts,
                composer_source=composer_source,
                origin=origin,
                queue_id=queue_id,
            )
            if isinstance(job, PoseJob):
                return await create_pose_queue(
                    self._state,
                    backend,
                    selection,
                    request,
                    job.content,
                    job.evidence,
                    job.control,
                    **options,
                )
            return await create_saved_pose_queue(self._state, backend, selection, request, **options)
        if job is not None:
            # An Inpaint edit: stage the prepared source and mask once; nothing is sent until Start.
            selection = await asyncio.to_thread(
                selection_for,
                backend["name"],
                backend["endpoint"],
                self._state,
                **{key: backend[key] for key in ("scenario", "delay", "previews") if key in backend},
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
                up_next=up_next,
                receipts=receipts,
                composer_source=composer_source,
                origin=origin,
                queue_id=queue_id,
            )
        items = expand_plan(request, plan, receipts=receipts) if plan is not None else expand_items(request, count)
        return await create_queue(
            self._state,
            backend,
            items,
            up_next=up_next,
            composer_source=composer_source,
            origin=origin,
            queue_id=queue_id,
        )

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
            self._lora_weight_lines(),
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
        self._show(self.page.planCountLabel, level, sentence(readout_text(text)))
        if level != "ok":
            self.page.planTableView.hide()  # reject stale previews immediately on invalid inputs
        pose = self._draft is not None and (
            isinstance(self._draft_job, PoseJob) or self._draft[0].pose_control is not None
        )
        label = "Start pose now" if pose else "Start now"
        self.page.startQueueButton.setText(f"{label} ({text})" if level == "ok" else label)
        if pose:
            self.page.startQueueButton.setToolTip(
                "Start sends the staged pose map to DT+ Cloud and may consume credits."
            )
            self.page.addQueueButton.setToolTip(
                "Save up next; when it starts, the pose map is sent to DT+ Cloud and may consume credits."
            )
            self.page.createQueueButton.setToolTip(
                "Check DT+ inventory and save the map locally. Nothing is uploaded or generated until Start."
            )
        else:
            self.page.startQueueButton.setToolTip(
                "Creates the queue and starts it from this window. Real, possibly billable generation."
            )
            self.page.addQueueButton.setToolTip("Saves it up next: it starts by itself when the running queue ends.")
            self.page.createQueueButton.setToolTip(
                "Saves the queue paused, also while another queue runs. Contacts no provider and uploads nothing."
            )
        self._actions_fit.refit()
        self._schedule_preview()
        self.plan_hook()
        self.templates.changed()

    @staticmethod
    def _lora_set_hint(text: str) -> str:
        """Hover text of a LoRA-set cell: one line per LoRA, ``ShortName[Tag] 0.8`` then its filename."""
        lines = []
        for part in text.split(" + "):
            identifier, space, weight = part.rpartition(" ")
            if not space:
                return text
            label = display_label(identifier)
            lines.append(f"{label} {weight}" + (f"\n    {identifier}" if label != identifier else ""))
        return "\n".join(lines)

    @staticmethod
    def _lora_set_labels(text: str) -> str:
        """``a.ckpt 0.8 + b.ckpt 0.4`` with each identifier shown as its ShortName[Tag] label."""
        parts = []
        for part in text.split(" + "):
            identifier, space, weight = part.rpartition(" ")
            parts.append(f"{display_label(identifier)} {weight}" if space else part)
        return " + ".join(parts)

    @staticmethod
    def _column_hint(name: str) -> str:
        """Header hover: a managed LoRA's weight column names its label, then the filename."""
        if name.startswith("lora_weight."):
            identifier = name.removeprefix("lora_weight.")
            label = display_label(identifier)
            return f"{label} weight\n{identifier}" if label != identifier else f"{identifier} weight"
        return name

    @staticmethod
    def _column_title(name: str) -> str:
        """A preview column header: ShortName[Tag] for a managed LoRA weight, else the name without prefix and extension."""
        if name.startswith("lora_weight."):
            name = name.removeprefix("lora_weight.")
            shown = display_label(name)  # ShortName[Tag] like every picker
            if shown != name:
                return shown
        for suffix in (".ckpt", ".safetensors"):
            name = name.removesuffix(suffix)
        return name

    def _show_table(self, table: PlanTable) -> None:
        self.plan_model.clear()
        # The planner provides each item's full effective stack; fixed LoRAs must not
        # disappear from the preview just because only another LoRA's weight varies.
        show_stack = "loras" not in table.columns and any(stack != "none" for stack in table.lora_stacks)
        columns = (*table.columns, *(("Effective LoRAs",) if show_stack else ()))
        self.plan_model.setHorizontalHeaderLabels([self._column_title(name) for name in columns])
        for column, name in enumerate(columns):
            self.plan_model.setHeaderData(
                column,
                Qt.Orientation.Horizontal,
                "All effective LoRAs and weights" if name == "Effective LoRAs" else self._column_hint(name),
                Qt.ItemDataRole.ToolTipRole,
            )
        prompt_column = table.columns.index("prompt") if "prompt" in table.columns else None
        for number, row in enumerate(table.rows):
            values = (*row, table.lora_stacks[number]) if show_stack else row
            cells = [QStandardItem(cell) for cell in values]
            for column, item in enumerate(cells):
                full = item.text()
                if columns[column] in ("loras", "Effective LoRAs"):
                    item.setText(self._lora_set_labels(full))  # ShortName[Tag]; the tooltip keeps the filenames
                is_set = columns[column] in ("loras", "Effective LoRAs")
                item.setToolTip(self._lora_set_hint(full) if is_set else full)  # the cells elide to the panel width
            if prompt_column is not None and number < len(table.prompt_runs):
                runs = decode_runs(row[prompt_column], table.prompt_runs[number])
                if runs:  # the parts the prompt variation produced are painted as tags by the view's delegate
                    cells[prompt_column].setData(runs, PROMPT_RUNS_ROLE)
                    cells[prompt_column].setToolTip(
                        f"<p style='white-space:pre-wrap'>{tagged_html(row[prompt_column], runs)}</p>"
                    )
            self.plan_model.appendRow(cells)
        view = self.page.planTableView
        # Columns share the panel width (header and cell text elide) instead of widening the table past the panel.
        header = view.horizontalHeader()
        header.setMinimumSectionSize(40)
        header.setTextElideMode(Qt.TextElideMode.ElideRight)
        for column in range(len(columns)):
            header.setSectionResizeMode(
                column,
                header.ResizeMode.ResizeToContents if column == 0 else header.ResizeMode.Stretch,
            )
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Show every row in full, so the composer scrolls to them instead of a nested scroll inside the table.
        shown = min(len(table.rows), PLAN_TABLE_MAX_ROWS)
        height = (
            view.horizontalHeader().sizeHint().height()
            + shown * view.verticalHeader().defaultSectionSize()
            + 2 * view.frameWidth()
        )
        view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if len(table.rows) <= PLAN_TABLE_MAX_ROWS
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        view.setMinimumHeight(height)
        view.setMaximumHeight(height)
        self._sync_preview_table_visibility()

    def _plan_for(self, request) -> tuple[Plan, PlanTable] | None:
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
        return plan, table

    def _plan_inputs(self, request) -> tuple:
        """Everything the pure planning step needs, read on the GUI thread so a worker can run it."""
        request = self._with_weighted_loras(request, self._lora_weight_groups())
        return (
            self._plan_form(),
            request,
            self._wildcards_dir,
            self._draft_job is not None and not isinstance(self._draft_job, PoseJob),
            isinstance(self._draft_job, PoseJob) or request.pose_control is not None,
        )

    @staticmethod
    def _compute_plan(form: PlanForm, request, folder: Path, edit: bool, pose: bool) -> tuple[Plan, PlanTable]:
        """Pure but for wildcard files: the drawn plan (what the summary and creation use) and its preview table."""
        plan = resolve_plan(bind_wildcards(build_plan(form), request.prompt, folder), request.prompt)
        if pose and any(name == "size" for name, _ in plan.dimensions):
            raise PlanError("A pose queue shares one fitted map; size cannot vary.")
        return plan, describe_plan(request, plan, edit=edit)

    @staticmethod
    def _resolve_only(form: PlanForm, request, folder: Path, edit: bool, pose: bool) -> Plan:
        """The drawn plan without the preview table: creation expands it itself."""
        plan = resolve_plan(bind_wildcards(build_plan(form), request.prompt, folder), request.prompt)
        if pose and any(name == "size" for name, _ in plan.dimensions):
            raise PlanError("A pose queue shares one fitted map; size cannot vary.")
        return plan

    def _plan_failed(self, exc: ValidationError) -> None:
        """One place for a planning error: beside the plan, with the owning field marked."""
        error = plan_error(exc)
        self._mark_plan(error.widget)
        self._show(self.page.planCountLabel, "error", sentence(relabel_text(str(error))))
        self.page.startQueueButton.setText("Start now")
        self.plan_model.clear()
        self._sync_preview_table_visibility()

    def _created(self, result, failure, action: str = "save") -> None:
        if failure is not None:
            if isinstance(failure, PlanError | FormError):
                self._plan_failed(failure)
                self._message(Presentation("error", "Queue not created", sentence(relabel_text(str(failure)))))
                return
            self._message(present_generation_failure(failure))
            return
        queue_id, status = result
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
            self._message(Presentation("ok", "Queue created", "Paused. Press Start."))
        self._render()
        self.refresh()

    # -- dispatch and controls -------------------------------------------------------------------------------------
    @profile_slow_gui_callback("queue.backend_event")
    def _queue_backend_event(self, wrapped: _QueueBackendEvent) -> None:
        from dtflow.ui.progress_stages import event_stage

        if self._runner.closed:
            return
        event = wrapped.event
        if wrapped.queue_id in self._manual_queues:
            manual = self._manual
            if manual is not None and wrapped.queue_id == manual.queue_id:
                manual.sink.event(event)  # the click Generate shows: progress and previews appear in Generate
            if event.kind in ("assets_restored", "assets_failed", "assets_inconclusive"):
                return  # Generate reports these itself; the stage and step tracking below also feeds the global bar
        if event.kind in ("assets_restored", "assets_failed", "assets_inconclusive"):
            if event.stage:
                titles = {
                    "assets_restored": "LoRAs restored on DT+",
                    "assets_failed": "LoRA preparation failed",
                    "assets_inconclusive": "LoRA check inconclusive",
                }
                state = {
                    "assets_restored": "success",
                    "assets_failed": "error",
                    "assets_inconclusive": "warning",
                }
                activity_strip(self.page).complete(
                    f"queue-assets:{wrapped.queue_id}:{event.kind}",
                    titles[event.kind],
                    relabel_text(event.stage),
                    state[event.kind],
                )
            return
        if self._owns(wrapped.queue_id):
            self._dispatch_started = True
        key = (wrapped.queue_id, wrapped.item_id)
        label = event_stage(event)
        if event.stage in ("waiting_video_resources", "video_preparing") and key not in self._video_progress:
            from dtflow.ui.video_progress import VideoProgress

            self._video_progress[key] = VideoProgress()
        video = self._video_progress.get(key)
        if video is not None and video.observe(event):
            label = video.text
            if video.done is None or video.total is None:
                self._run_progress.pop(key, None)
        if label and label != self._run_stages.get(key):
            self._run_stages[key] = label
            self._run_stage_started[key] = (
                (video.sent_at if video.sent_at is not None else video.started) if video is not None else monotonic()
            )
        if (
            event.kind == "progress"
            and type(event.completed) is int
            and type(event.total) is int
            and event.total > 0
            and 0 <= event.completed <= event.total
        ):
            self._run_progress[key] = (max(0, event.completed), event.total)
        elif event.kind == "terminal":
            self._run_progress.pop(key, None)
            if video is None:
                self._run_stages.pop(key, None)
                self._run_stage_started.pop(key, None)
            # Video's terminal event precedes durable MP4/poster saving. Retain its Saving phase
            # until the ordinary read-side reconciliation sees the item settle.
        if self._detail is not None and self._detail["queue_id"] == wrapped.queue_id:
            self.model.set_live_stage(wrapped.item_id, label or "")
        self._publish_run_state()

    def trigger_run_control(self) -> None:
        """Route the compact header control through the same Start/Pause/Resume paths as the Queues page."""
        if self._generate_presents:
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
        owned = bool(view and self._owns(view["queue_id"]))
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

    @profile_slow_gui_callback("queue.dispatch")
    def _dispatch(self, queue_id: str, *, resume_fallback: str | None, waiting: tuple[str, ...] = ()) -> None:
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
                waiting=waiting,
                on_event=lambda item_id, event: self._event_relay(_QueueBackendEvent(queue_id, item_id, event)),
                on_next=lambda settled, following: self._advance_relay(_QueueAdvance(settled, following)),
                on_overlap=lambda started: self._overlap_relay(_QueueOverlap(started)),
                manual=ManualSink(
                    event=lambda queue, item_id, event: self._event_relay(_QueueBackendEvent(queue, item_id, event)),
                    result=lambda queue, item_id, outcome, error: self._manual_relay(
                        _ManualDelivery(queue, outcome, error)
                    ),
                    end=lambda queue, error: self._manual_relay(_ManualDelivery(queue, error=error, ended=True)),
                ),
            ),
            self._dispatched,
        )
        self._timer.start()

    def _overlapped(self, overlap: _QueueOverlap) -> None:
        """Auto-play started a queue beside the running one: this window owns both until the first one settles."""
        if self._dispatching is not None:
            self._overlapping.add(overlap.queue_id)
            self.refresh()

    def _advanced(self, advance: _QueueAdvance) -> None:
        """Auto-play: the settled queue reports like a finished dispatch, and this window now owns the next one."""
        if self._dispatching is None:
            return  # a late delivery after dispatch ended
        presentation = Presentation(*outcome_text(advance.settled))
        if advance.settled.get("queue_id") not in self._manual_queues:  # Generate reports its own queue
            activity_strip(self.page).complete(
                "queue-result",
                "Queue · " + presentation.headline,
                presentation.detail + f" Next: {advance.following[:8]}.",
                "success" if presentation.level == "ok" else "partial",
            )
        self._dispatching, self._dispatch_started = advance.following, False
        self._overlapping.discard(advance.following)
        if advance.following not in self._manual_queues:
            self._message(Presentation("ok", "Up next started", f"{advance.following[:8]} started by itself."))
        self.refresh()

    def _dispatched(self, final, failure) -> None:
        self._dispatching, self._future = None, None
        self._overlapping.clear()
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
        if failure is not None or final.get("queue_id") not in self._manual_queues:
            activity_strip(self.page).complete(
                "queue-result", "Queue · " + presentation.headline, presentation.detail, state
            )
        self._render()
        self.refresh()
        self._manual_handed_back(failure)

    def cancel_run(self) -> None:
        """Stop the locally owned run shown globally, independently of the selected queue."""
        if self._runner.closed or self._generate_presents or not self.dispatching_here:
            return
        view = self._run_view()
        if view is None or not self._owns(view["queue_id"]) or not self._controls_for(view).cancel:
            return
        self._post("cancel", view["queue_id"])

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
            keep = tuple(q for q in (self._dispatching, *self._overlapping) if q)
            self._runner.submit(clear_queues(self._state, keep), self._cleared)

        confirm_action(self.page, self.page.clearQueuesButton, "Clear history", CLEAR_TEXT, proceed, danger=True)

    def _cleared(self, removed, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        text = f"{removed} queue(s) removed." if removed else "No finished or cancelled queue to remove."
        self._message(Presentation("ok", "History cleared", text))
        self.refresh()

    def clear_paused(self) -> None:
        """Bulk-remove paused queues at once; each is revalidated when it is removed."""
        queue_ids = self._paused_ids()
        if not queue_ids:
            self._message(Presentation("ok", "Nothing to clear", "No paused queue to remove."))
            return
        keep = (self._dispatching,) if self._dispatching else ()
        self._runner.submit(clear_paused_queues(self._state, queue_ids, keep), self._paused_cleared)

    def _paused_cleared(self, result, failure) -> None:
        if failure is not None:
            self._message(present_generation_failure(failure))
            return
        removed, skipped = result
        text = f"{removed} paused queue(s) removed." if removed else "No paused queue was removed."
        if skipped:
            text += f" {skipped} changed state meanwhile and stayed."
        self._message(Presentation("ok" if not skipped or removed else "warning", "Paused queues cleared", text))
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
            keep = tuple(q for q in (self._dispatching, *self._overlapping) if q)
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

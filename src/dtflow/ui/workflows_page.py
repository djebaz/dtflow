"""Workflows workspace (#223): library, node palette, canvas, selected-node inspector and the run view.

Designer owns the static layouts (``workflows_context.ui``, ``workflows_workspace.ui``, ``workflows_inspector.ui``).
This controller mounts the canvas, builds the per-parameter controls of the selected node from its ``NodeSpec`` and
binds data. Library files, model checks and run services run off the Qt thread through the window's AsyncRunner.
A run is one ordinary workflow queue started through the Queues dispatch (``QueuePage.start_workflow``), so the
shared activity bar shows it; this page polls only the run state for per-step presentation.

The Library toggle folds the left panels (library, Add node, Branches) at any width; the window moves the shell's
brand and navigation into ``workflowShellHeader`` meanwhile. The choice is remembered in
``<state>/workflows/workspace.json``; until the user chooses, the library is open from 1100 px and folded below.
``workflows_node_picker.ui`` is the searchable add-node picker (double-click empty canvas, or Tab).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dtflow.core import workflows as wf
from dtflow.core.models import LoRASelection, ValidationError
from dtflow.services import workflow_library as library
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.inline_feedback import InlineNotice, confirm_action, edit_text, feedback
from dtflow.ui.search_select import SearchableComboBox

log = logging.getLogger(__name__)


class _DimensionSpinBox(QSpinBox):
    """Commit authored dimensions to the nearest 64 pixels, with ties upward."""

    def valueFromText(self, text: str) -> int:
        value = super().valueFromText(text)
        return max(self.minimum(), min(self.maximum(), ((value + 32) // 64) * 64))


POLL_MS = 1000
COMPACT_TOOLBAR_PX = 640  # below this canvas-column width the name moves above the buttons and Run shortens
THUMB = 72
LIVE_RUN = {"pending", "running"}
STATE_LABELS = {
    "pending": "Waiting",
    "running": "Running",
    "succeeded": "Done",
    "partial": "Failed, some results saved",
    "failed": "Failed",
    "unknown": "Unknown outcome",
    "skipped": "Skipped",
    "paused": "Paused",
    "stopped": "Stopped",
    "cancelled": "Cancelled",
}
# A node shows its most pressing branch state; "n/m images" counts the branches that are done.
NODE_PRIORITY = ("running", "paused", "failed", "unknown", "pending", "stopped", "cancelled", "skipped", "succeeded")
CHOICE_LABELS = {
    "face_skin": "Face skin only",
    "face_skin_and_hair": "Face skin and hair",
    "strongest": "Strongest person",
    "all": "All people",
}
_FINAL = re.compile(r"final-(\d+)\.png$", re.IGNORECASE)
PLACEHOLDER = "PLACEHOLDER_"  # checkpoint ids of the reference workflow that must be replaced before a run
PREFS = Path("workflows") / "workspace.json"  # per-user Workflows layout choices (not cache, not run state)
PREFS_SCHEMA = "dtflow.workflows.workspace"
RECENT_RUNS = 20


def step_message(step: dict) -> str:
    return "\n".join(filter(None, (step.get("message"), *step.get("warnings", ()))))


def _repolish(widget: QWidget, **properties) -> None:
    for name, value in properties.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _label(text: str, role: str | None = None, *, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(wrap)
    if role:
        label.setProperty("role", role)
    return label


def node_state(steps: list[dict]) -> tuple[str, int, int]:
    """(aggregated state, branches done, branches) of one node's steps."""
    states = {step["state"] for step in steps}
    state = next((name for name in NODE_PRIORITY if name in states), "pending")
    return state, sum(step["state"] == "succeeded" for step in steps), len(steps)


def step_name(step: dict) -> str:
    """``Inpaint #3``: the run state's title already carries the node id in current executors."""
    title = str(step.get("title") or step.get("type") or "Step")
    return title if "#" in title else f"{title} #{step['node_id']}"


def output_identity(path: str) -> str | None:
    """Images identity ``<attempt id>:<index>`` of a saved ``outputs/<attempt id>/final-N.png``, else None."""
    match = _FINAL.search(Path(path).name)
    return f"{Path(path).parent.name}:{int(match.group(1)) - 1}" if match else None


def _read_images(paths: tuple[str, ...], size: int) -> dict[str, QImage]:
    """Worker thread: scaled QImages (QImage, unlike QPixmap, is safe off the GUI thread)."""
    images = {}
    for path in paths:
        image = QImage(path)
        if not image.isNull():
            images[path] = image.scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
    return images


def read_prefs(state: Path) -> dict:
    """The remembered Workflows layout choices, or {} when absent, unreadable or of another schema."""
    try:
        path = Path(state) / PREFS
        if path.stat().st_size > 4096:
            return {}
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict) or document.get("schema") != PREFS_SCHEMA or document.get("version") != 1:
        return {}
    return document


def write_prefs(state: Path, **values) -> None:
    """Atomically remember layout choices; a file of another schema or version is left as it is."""
    path = Path(state) / PREFS
    if path.exists() and not read_prefs(state):
        return
    document = {**read_prefs(state), **values, "schema": PREFS_SCHEMA, "version": 1}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def checkpoint_problems(graph: wf.Graph, models) -> tuple[list[str], list[str]]:
    """(blocking, warnings) for the checkpoints of a graph: a ``PLACEHOLDER_`` id blocks Run ("Choose a checkpoint
    for <node>"); one absent from the in-memory inventory ``models`` (when it lists any) only warns."""
    known = set(models or ())
    blocking, warnings = [], []
    for node in graph.nodes:
        spec = wf.CATALOG.get(node.type)
        if spec is None:
            continue
        for param in spec.params:
            if param.kind != "checkpoint":
                continue
            value = str(node.params.get(param.name) or "")
            title = f"{spec.title} #{node.id}"
            if value.startswith(PLACEHOLDER):
                blocking.append(f"Choose a checkpoint for {title}.")
            elif value and known and value not in known:
                warnings.append(f"{title}: {value} is not in the selected backend's model list.")
    return blocking, warnings


def recent_workflow_runs(state: Path, limit: int = RECENT_RUNS) -> list[tuple[str, str]]:
    """Worker thread: (queue id, label) of the newest workflow queues, newest first; labels carry no ids."""
    from dtflow.persistence.queues import read_queues

    try:
        views = read_queues(Path(state))
    except Exception:  # no queue database yet, or it is busy: simply no earlier runs to offer
        return []
    runs = []
    for view in views:
        if view.get("kind") != "workflow":
            continue
        run = next((item.get("workflow") for item in view.get("items", ()) if item.get("workflow")), None)
        if run is None:
            continue
        created = str(view.get("created_at_utc") or "")
        try:
            when = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone().strftime("%d %b %H:%M")
        except ValueError:
            when = ""
        state_ = STATE_LABELS.get(str(run.get("state")), str(run.get("state") or ""))
        # The outcome first, so a narrow combo still shows it; the full label is the tooltip.
        label = " · ".join(part for part in (state_, str(run.get("name") or "Workflow"), when) if part)
        runs.append((created, view["queue_id"], label))
    runs.sort(key=lambda entry: entry[0], reverse=True)
    return [(queue_id, label) for _, queue_id, label in runs[:limit]]


def download_missing_models(page: QWidget, state: Path, entries, runner, on_done, *, button) -> None:
    """Adapter over the shared ``ui/model_download`` helper: one inline confirmation listing each model and its
    licence on ``button``, then a verified download; ``on_done(error)`` runs on the GUI thread (None on success)."""
    try:
        from dtflow.ui import model_download
    except ImportError:
        on_done(ValidationError("Model download is not available in this build."))
        return
    if model_download.is_downloading(page):
        on_done(ValidationError("A model download is already running; run the workflow when it ends."))
        return
    model_download.confirm_and_download(
        page, state, entries, runner, lambda _models, error: on_done(error), button=button
    )


def _same_document(current, saved) -> bool:
    try:
        return wf.dump_workflow(current) == wf.dump_workflow(saved)
    except (wf.WorkflowError, TypeError, ValueError):
        return False


def _run_shape(graph) -> tuple:
    """What a run depends on: node types and settings, and links; positions and view are left out."""
    nodes = tuple(sorted((node.id, node.type, repr(sorted(node.params.items()))) for node in graph.nodes))
    links = tuple(sorted((link.origin_id, link.origin_slot, link.target_id, link.target_slot) for link in graph.links))
    return nodes, links


class WorkflowsPage(QObject):
    def __init__(
        self,
        context: QWidget,
        workspace: QWidget,
        inspector: QWidget,
        *,
        runner,
        state_dir: Path,
        choices_source: Callable[[], dict] = dict,
        start_workflow: Callable | None = None,
        start_created: Callable | None = None,
        open_image: Callable[[str], None] | None = None,
    ):
        super().__init__(workspace)
        self.context, self.workspace, self.inspector = context, workspace, inspector
        self.runner, self.state = runner, Path(state_dir)
        self.choices_source = choices_source
        self.start_workflow = start_workflow  # QueuePage.start_workflow(document, name, done)
        self.start_created = start_created  # QueuePage.start_created(queue_id, done)
        self.open_image = open_image
        self.canvas = None
        self.path: Path | None = None
        self.name = ""
        self.entries: list[library.LibraryEntry] = []
        self._inspected: int | None = None
        self._active = False
        self._busy = False  # one library/run request at a time from this page
        self.queue_id: str | None = None
        self.run: dict | None = None
        self._run_path: Path | None = None
        self._reading = False
        self._pixmaps: dict[str, QPixmap] = {}
        self._loading_images: set[str] = set()
        self._pause_shown: tuple | None = None
        self._pause_dismissed: set[tuple] = set()
        self._missing: tuple = ()
        self._narrow = False
        remembered = read_prefs(self.state).get("library_open")
        self._library_open: bool | None = remembered if isinstance(remembered, bool) else None  # None: by width
        self.on_layout: Callable[[], None] = lambda: None  # the window re-places its rails and chrome
        # What the run widgets were last built from: rebuild only when it changes (clicks and focus survive polls).
        self._pause_sig: tuple | None = None
        self._results_sig: tuple | None = None
        self._retry_sig: tuple | None = None
        self._node_run_sig: tuple | None = None
        self._node_run_rows: dict[tuple, tuple[QLabel, QLabel]] = {}
        self._warning_run_id: str | None = None
        self._warning_follow_queue_id: str | None = None
        self._warning_seen: set[tuple[int, str]] = set()
        self._picker = None
        self._picker_at: QPointF | None = None
        self._compact_toolbar = False
        self._poll = QTimer(workspace)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._read_run)
        self._plan_timer = QTimer(workspace)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.setInterval(200)
        self._plan_timer.timeout.connect(self._update_plan)
        self._toolbar_timer = QTimer(workspace)  # re-place the toolbar after a resize, outside the resize event
        self._toolbar_timer.setSingleShot(True)
        self._toolbar_timer.setInterval(0)
        self._toolbar_timer.timeout.connect(self._fit_toolbar)
        runner.shutdown_started.connect(self._stop_timers)
        self._bind()

    # --- mounting ---------------------------------------------------------------------------------------------
    def _bind(self) -> None:
        c, w, i = self.context, self.workspace, self.inspector
        for page in (c, w, i):
            page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            for header in page.findChildren(QFrame):
                if header.property("role") == "sectionHeader":
                    header.setMaximumHeight(32)
        w.layout().setStretch(w.layout().indexOf(w.workflowCanvasFrame), 1)
        toolbar = w.workflowToolbar.layout()
        toolbar.setStretch(toolbar.indexOf(w.workflowName), 1)
        w.workflowName.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        w.workflowName.setMinimumWidth(48)
        for button in w.workflowToolbar.findChildren(QPushButton):
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        w.workflowResultsRow.layout().addStretch(1)
        w.workflowRetryActions.layout().addStretch(1)
        w.libraryToggleButton.toggled.connect(lambda on: self.set_library_open(on))
        w.workflowRecentRuns.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        w.workflowRecentRuns.setMinimumContentsLength(22)
        w.workflowRecentRuns.addItem("Choose a run…", None)
        w.workflowRecentRuns.activated.connect(self._recent_chosen)
        w.workflowTitleRow.layout().addStretch(1)
        w.installEventFilter(self)  # resize: compact toolbar when the canvas column is narrow
        c.newWorkflowButton.clicked.connect(self.new_workflow)
        c.openWorkflowButton.clicked.connect(self.open_selected)
        c.duplicateWorkflowButton.clicked.connect(self.duplicate)
        c.renameWorkflowButton.clicked.connect(self.rename)
        c.deleteWorkflowButton.clicked.connect(self.delete)
        c.importWorkflowButton.clicked.connect(self.import_file)
        c.exportWorkflowButton.clicked.connect(self.export_file)
        c.workflowList.itemDoubleClicked.connect(lambda _item: self.open_selected())
        c.workflowList.currentRowChanged.connect(lambda _row: self._sync_buttons())
        c.workflowSearch.textChanged.connect(lambda text: self._filter(c.workflowList, text))
        c.nodeSearch.textChanged.connect(lambda text: self._filter(c.nodePalette, text))
        c.nodePalette.itemActivated.connect(self._add_from_palette)
        c.nodePalette.itemDoubleClicked.connect(self._add_from_palette)
        for spec in wf.CATALOG.values():
            item = QListWidgetItem(spec.title)
            item.setData(Qt.ItemDataRole.UserRole, spec.type)
            ports = " + ".join(p.type for p in spec.inputs) or "source"
            outputs = " + ".join(p.type for p in spec.outputs) or "result"
            item.setToolTip(f"{ports} → {outputs}. {spec.description}")
            c.nodePalette.addItem(item)
        w.saveWorkflowButton.clicked.connect(self.save)
        w.fitWorkflowButton.clicked.connect(lambda: self.canvas and self.canvas.fit_view())
        w.undoWorkflowButton.clicked.connect(lambda: self.canvas and self.canvas.undo())
        w.redoWorkflowButton.clicked.connect(lambda: self.canvas and self.canvas.redo())
        w.runWorkflowButton.clicked.connect(self.run_workflow)
        w.stopWorkflowButton.clicked.connect(self.stop_run)
        for keys, action in (
            ("Ctrl+S", self.save),
            ("Ctrl+Z", lambda: self.canvas and self.canvas.undo()),
            ("Ctrl+Y", lambda: self.canvas and self.canvas.redo()),
            ("Ctrl+Shift+Z", lambda: self.canvas and self.canvas.redo()),
            ("Ctrl+L", lambda: self.set_library_open(not self.library_open())),
            ("Ctrl+O", self.reveal_library),
        ):
            shortcut = QShortcut(QKeySequence(keys), w)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        self._show_node(None, 0)
        self._present_run(None)
        self._sync_buttons()

    def mount(self, shell) -> None:
        self.shell = shell
        for stack, page in zip(
            (shell.leftStack, shell.centerStack, shell.rightStack),
            (self.context, self.workspace, self.inspector),
            strict=True,
        ):
            stack.addWidget(page)

    def ensure_canvas(self):
        if self.canvas is None:
            from dtflow.ui.workflows_canvas import WorkflowCanvas  # the graph library loads on first use

            self.canvas = WorkflowCanvas(self.workspace.workflowCanvasHost)
            self.workspace.workflowCanvasHost.layout().addWidget(self.canvas.widget)
            self.canvas.on_selection = self._show_node
            self.canvas.on_changed = self._changed
            self.canvas.on_restored = self._restored
            self.canvas.on_rejected = lambda message: activity_strip(self.workspace).complete(
                "workflow-link", "Connection refused", message.removeprefix("Connection refused: "), "warning"
            )
            self.canvas.on_add_request = self.show_node_picker
            self.canvas.on_pressed = self.hide_node_picker
        return self.canvas

    def activate(self) -> None:
        shell = getattr(self, "shell", None)
        if shell is not None:
            for stack, page in zip(
                (shell.leftStack, shell.centerStack, shell.rightStack),
                (self.context, self.workspace, self.inspector),
                strict=True,
            ):
                stack.setCurrentWidget(page)
        self.ensure_canvas()
        self.set_active(True)
        self._pause_dismissed.clear()
        self.refresh_library()
        self.refresh_recent_runs()
        self._update_plan()

    def set_active(self, active: bool) -> None:
        self._active = active
        if active and self.queue_id is not None and (self.run is None or self._run_live(self.run)):
            self._poll.start()
            self._read_run()
        elif not active:
            self._poll.stop()
            self._dismiss_pause_overlay()
            self.hide_node_picker()

    def sync_responsive(self, narrow: bool) -> None:
        self._narrow = narrow
        self.workspace.workflowGestureHint.setText(
            "Drag to pan · Wheel to zoom · Tab adds a node"
            if narrow
            else "Drag to pan · Wheel to zoom · Shift-drag to select · Tab adds a node · F to fit"
        )
        self._sync_library_button()

    # --- Library toggle ---------------------------------------------------------------------------------------
    def library_open(self, narrow: bool | None = None) -> bool:
        """Whether the left panels show: the remembered choice, else open from 1100 px and folded below."""
        if self._library_open is not None:
            return self._library_open
        return not (self._narrow if narrow is None else narrow)

    def set_library_open(self, shown: bool, *, remember: bool = True) -> None:
        changed = shown != self.library_open()
        self._library_open = bool(shown)
        self._sync_library_button()
        if remember:
            self._submit(asyncio.to_thread(write_prefs, self.state, library_open=bool(shown)), lambda *_: None)
        if changed:
            self.on_layout()
            self._update_plan()

    def reveal_library(self) -> None:
        """Open and Import live in the library: show it (remembered) and put the keyboard in its search."""
        if not self.library_open():
            self.set_library_open(True)
        self.context.workflowSearch.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _sync_library_button(self) -> None:
        button = self.workspace.libraryToggleButton
        shown = self.library_open()
        if button.isChecked() != shown:
            button.blockSignals(True)
            button.setChecked(shown)
            button.blockSignals(False)
        button.setToolTip(
            ("Fold" if shown else "Show")
            + " the workflow library, Add node and Branches panels (Ctrl+L). Tab on the canvas adds a node."
        )

    def _stop_timers(self) -> None:
        self._poll.stop()
        self._plan_timer.stop()
        self._toolbar_timer.stop()

    @staticmethod
    def _filter(widget, text) -> None:
        needle = text.casefold().strip()
        for index in range(widget.count()):
            item = widget.item(index)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _submit(self, coroutine, callback) -> None:
        if self.runner.closed:
            coroutine.close()
            return
        self.runner.submit(coroutine, callback)

    def _error(self, title: str, failure: Exception) -> None:
        if isinstance(failure, NotImplementedError):
            text = "This part of Workflows is not available in this build yet."
        else:
            text = str(failure) or failure.__class__.__name__
        InlineNotice.error(self.workspace, title, text)

    # --- library ----------------------------------------------------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self.canvas is not None and (self.canvas.modified or (self.path is None and bool(self.name)))

    def refresh_library(self) -> None:
        self._submit(asyncio.to_thread(library.list_library, self.state), self._library_read)

    def _library_read(self, entries, failure) -> None:
        if failure is not None:
            self.context.workflowLibraryNote.setText(f"The workflow library could not be read: {failure}")
            return
        self.show_library(entries)

    def show_library(self, entries: list[library.LibraryEntry]) -> None:
        self.entries = list(entries)
        widget = self.context.workflowList
        selected = self._selected_entry()
        keep = selected.path if selected is not None else self.path
        widget.blockSignals(True)
        widget.clear()
        for entry in self.entries:
            item = QListWidgetItem(entry.name if entry.problem is None else f"{entry.name} · cannot open")
            item.setData(Qt.ItemDataRole.UserRole, str(entry.path))
            item.setToolTip(entry.problem or f"{entry.name} · {entry.path.name}")
            widget.addItem(item)
            if keep is not None and entry.path == keep:
                widget.setCurrentItem(item)
        widget.blockSignals(False)
        self._filter(widget, self.context.workflowSearch.text())
        self.context.workflowLibraryNote.setText(
            "No saved workflows yet. New starts from the reference workflow; Import… adds a file."
            if not self.entries
            else f"{len(self.entries)} saved workflow{'s' if len(self.entries) != 1 else ''} · double-click to open."
        )
        self._sync_buttons()

    def _selected_entry(self) -> library.LibraryEntry | None:
        item = self.context.workflowList.currentItem()
        if item is None:
            return None
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        return next((entry for entry in self.entries if entry.path == path), None)

    def _guard(self, button, proceed: Callable[[], None]) -> None:
        """Ask once before replacing a workflow with unsaved changes (inline; Cancel keeps it)."""
        if self._busy:
            return
        panel = feedback(self.workspace)
        if panel.commit(button):
            return
        if not self.dirty:
            proceed()
            return
        confirm_action(
            self.workspace,
            button,
            "Discard unsaved changes",
            f"{self.name or 'This workflow'} has unsaved changes. Discard them?",
            proceed,
            confirm_text="Discard",
        )

    def new_workflow(self) -> None:
        def proceed():
            self._busy = True
            self._submit(asyncio.to_thread(library.create_from_reference, self.state), self._opened)

        self._guard(self.context.newWorkflowButton, proceed)

    def open_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.problem is not None:
            InlineNotice.error(self.workspace, f"{entry.name} cannot be opened", entry.problem)
            return

        def proceed():
            self._busy = True

            async def read():
                return entry.path, await asyncio.to_thread(library.load_file, entry.path)

            self._submit(read(), self._opened)

        self._guard(self.context.openWorkflowButton, proceed)

    def _opened(self, result, failure, *, title="Workflow could not be opened") -> None:
        self._busy = False
        if failure is not None:
            self._error(title, failure)
            self.refresh_library()
            return
        path, graph = result
        self.open_graph(graph, path)
        self.refresh_library()

    def open_graph(self, graph: wf.Graph, path: Path | None, name: str | None = None) -> None:
        canvas = self.ensure_canvas()
        self.path = Path(path) if path is not None else None
        self.name = name or (self.path.stem if self.path is not None else "")
        self._inspected = None
        canvas.load_graph(graph)
        self._apply_run_to_canvas()
        self._changed()

    def save(self) -> None:
        if self.canvas is None or self._busy or not self.name:
            return
        if self.path is None:
            edit_text(self.workspace, "Save workflow", "Workflow name", self._save_as, current=self.name)
            return
        self._save_as(self.path.stem)

    def _save_as(self, name: str) -> None:
        graph = self.canvas.current_graph()
        self._busy = True

        async def write():
            return await asyncio.to_thread(library.save_workflow, self.state, name, graph, current=self.path)

        def done(path, failure):
            self._busy = False
            if failure is not None:
                self._error("Workflow not saved", failure)
                return
            self.path, self.name = path, path.stem
            # Editing stays open during the write: only a canvas that still equals what was written is clean.
            if self.canvas is not None and _same_document(self.canvas.current_graph(), graph):
                self.canvas.mark_saved()
            self._changed()
            activity_strip(self.workspace).complete("workflow-save", "Workflow saved", f"{self.name} is saved.")
            self.refresh_library()

        self._submit(write(), done)

    def rename(self) -> None:
        entry = self._selected_entry()
        if entry is None or self._busy:
            return

        def apply(name):
            self._busy = True

            def done(path, failure):
                self._busy = False
                if failure is not None:
                    self._error("Workflow not renamed", failure)
                    return
                if self.path == entry.path:
                    self.path, self.name = path, path.stem
                    self._changed()
                self.refresh_library()

            self._submit(asyncio.to_thread(library.rename_workflow, self.state, entry.path, name), done)

        edit_text(self.workspace, f"Rename {entry.name}", "Workflow name", apply, current=entry.name)

    def duplicate(self) -> None:
        entry = self._selected_entry()
        if entry is None or self._busy:
            return

        def done(_path, failure):
            if failure is not None:
                self._error("Workflow not duplicated", failure)
            self.refresh_library()

        self._submit(asyncio.to_thread(library.duplicate_workflow, self.state, entry.path), done)

    def delete(self) -> None:
        button = self.context.deleteWorkflowButton
        panel = feedback(self.workspace)
        if panel.commit(button):
            return
        entry = self._selected_entry()
        if entry is None or self._busy:
            return

        def proceed():
            def done(_result, failure):
                if failure is not None:
                    self._error("Workflow not deleted", failure)
                elif self.path == entry.path:
                    self.path = None  # still open on the canvas, now unsaved
                    self._changed()
                self.refresh_library()

            self._submit(asyncio.to_thread(library.delete_workflow, entry.path), done)

        confirm_action(
            self.workspace,
            button,
            f"Delete {entry.name}",
            f"Delete the workflow file {entry.path.name} from the library? This cannot be undone; saved images stay.",
            proceed,
            danger=True,
            confirm_text="Delete",
        )

    def import_file(self) -> None:
        if not self.library_open():
            self.set_library_open(True)  # the imported workflow is listed and selected in the library

        def pick():
            source, _ = QFileDialog.getOpenFileName(
                self.workspace.window(), "Import workflow", "", "Workflow JSON (*.json);;All files (*)"
            )
            if not source:
                return
            self._busy = True
            self._submit(
                asyncio.to_thread(library.import_workflow, self.state, Path(source)),
                lambda result, failure: self._opened(result, failure, title="Workflow import refused"),
            )

        self._guard(self.context.importWorkflowButton, pick)

    def export_file(self) -> None:
        if self.canvas is None or not self.name:
            return
        target, _ = QFileDialog.getSaveFileName(
            self.workspace.window(), "Export workflow", f"{self.name}.json", "Workflow JSON (*.json)"
        )
        if not target:
            return
        graph = self.canvas.current_graph()

        def done(path, failure):
            if failure is not None:
                self._error("Workflow not exported", failure)
                return
            activity_strip(self.workspace).complete("workflow-export", "Workflow exported", str(path))

        self._submit(asyncio.to_thread(library.export_workflow, graph, Path(target)), done)

    def _sync_buttons(self) -> None:
        c, w = self.context, self.workspace
        selected = self._selected_entry() is not None
        for button in (c.openWorkflowButton, c.duplicateWorkflowButton, c.renameWorkflowButton, c.deleteWorkflowButton):
            button.setEnabled(selected)
        has = self.canvas is not None and bool(self.name)
        for button in (c.exportWorkflowButton, w.saveWorkflowButton, w.fitWorkflowButton, w.undoWorkflowButton):
            button.setEnabled(has)
        w.redoWorkflowButton.setEnabled(has)

    # --- editing ----------------------------------------------------------------------------------------------
    def _add_from_palette(self, item) -> None:
        if not self.name:
            self.open_graph(wf.Graph(), None, "Untitled workflow")
        self.ensure_canvas().add_node(item.data(Qt.ItemDataRole.UserRole))

    def _changed(self) -> None:
        w = self.workspace
        w.workflowName.setText(self.name or "No workflow open")
        w.workflowStateBadge.setText(
            ""
            if not self.name
            else "Not in the library yet"
            if self.path is None
            else "Unsaved changes"
            if self.canvas.modified
            else "Saved"
        )
        self._sync_buttons()
        self._plan_timer.start()

    def _restored(self) -> None:
        self._inspected = None  # undo/redo replaced the node objects: rebuild the controls with restored values

    def _update_plan(self) -> None:
        w, i = self.workspace, self.inspector
        if self.canvas is None or not self.name:
            w.workflowSummary.setText(
                "Open a workflow from the library, or press New."
                if self.library_open()
                else "Press Library to open a workflow or start a new one."
            )
            i.workflowPlanText.setText("")
            i.workflowDiagnostics.setText("")
            w.runWorkflowButton.setEnabled(False)
            return
        graph = self.canvas.current_graph()
        notes = graph.diagnostics()
        try:
            plan = wf.plan_workflow(graph)
        except wf.WorkflowError as exc:
            plan, reason = None, str(exc)
        # Checkpoints: a reference placeholder blocks Run; one the in-memory inventory does not list only warns.
        blocking, unknown = checkpoint_problems(graph, (self.choices_source() or {}).get("models"))
        if plan is not None and blocking:
            reason = " ".join(blocking)
        ready = plan is not None and not blocking
        branches = max((len(p.branches) for p in plan.nodes.values()), default=0) if plan else 0
        w.workflowSummary.setText(
            f"{len(graph.nodes)} nodes · {len(graph.links)} links"
            + (f" · {branches} image branch{'es' if branches != 1 else ''}" if plan else "")
            + (f" · Ready: {plan.summary()}" if ready else f" · Cannot run yet: {reason}")
            + (f" · Check {len(unknown)} checkpoint{'s' if len(unknown) != 1 else ''}" if ready and unknown else "")
        )
        if ready:
            text = f"Ready to run: {plan.summary()}."
            if plan.needs_face_models:
                text += " Face detection runs locally and needs the detection models."
            _repolish(i.workflowPlanText, workflowState="warning" if (self._missing or unknown) else "ok")
        else:
            text = f"Cannot run yet: {reason}"
            _repolish(i.workflowPlanText, workflowState="error")
        if unknown:
            text += "\n" + "\n".join(unknown)
        if self._missing:
            text += "\nMissing models: " + "; ".join(f"{e.name} ({e.licence})" for e in self._missing)
        i.workflowPlanText.setText(text)
        i.workflowDiagnostics.setText("\n".join(f"• {note}" for note in notes))
        i.workflowDiagnostics.setVisible(bool(notes))
        w.runWorkflowButton.setEnabled(ready and not (self.run is not None and self._run_live(self.run)))
        w.runWorkflowButton.setToolTip(
            f"Run on the selected backend: {plan.summary()}" if ready else f"Cannot run yet: {reason}"
        )

    # --- inspector --------------------------------------------------------------------------------------------
    def _show_node(self, node_id: int | None, count: int = 1) -> None:
        i, w = self.inspector, self.workspace
        params = self.canvas.node_params(node_id) if (self.canvas is not None and node_id is not None) else None
        self._sync_lock()
        if params is None:
            self._inspected = None
            _clear(i.workflowParamsHost.layout())
            _clear(i.workflowNodeRun.layout())
            self._node_run_sig, self._node_run_rows = None, {}
            i.workflowRunLockNote.hide()
            i.workflowNodeTitle.setText("No node selected" if count == 0 else f"{count} nodes selected")
            i.workflowNodeMeta.setText("Select one node on the canvas to edit its settings.")
            i.workflowNodeDescription.setText("")
            w.workflowSelectionCount.setText("" if count == 0 else f"{count} nodes selected")
            if self.canvas is not None and self.name and count == 0:
                self._add_concurrency(i.workflowParamsHost.layout(), None)
            return
        node = self.canvas.node(node_id)
        spec = node.SPEC
        w.workflowSelectionCount.setText(f"{spec.title} #{node_id} selected")
        self._show_node_run(node_id)
        if self._inspected == node_id:
            return
        self._inspected = node_id
        ports = " + ".join(p.type for p in spec.inputs) or "source"
        outputs = " + ".join(p.type for p in spec.outputs) or "result"
        i.workflowNodeTitle.setText(f"{spec.title} #{node_id}")
        i.workflowNodeMeta.setText(f"Node {node_id} · {ports} → {outputs}")
        i.workflowNodeDescription.setText(spec.description)
        layout = i.workflowParamsHost.layout()
        _clear(layout)
        choices = self.choices_source() or {}
        for param in spec.params:
            self._add_param(layout, node_id, param, params.get(param.name), choices)
        self._add_concurrency(layout, node_id)
        self._sync_lock()

    def _add_concurrency(self, layout, node_id: int | None) -> None:
        """The global concurrency (no node selected) or one node's (#286); 0 shows the default and is not saved."""
        from dtflow.persistence.settings import SettingsError, WORKFLOW_DEFAULTS, load_workflow_defaults

        host = self.inspector.workflowParamsHost
        node = self.canvas.node(node_id) if node_id is not None else None
        value = (node.concurrency if node is not None else self.canvas.concurrency) or 0
        try:
            defaults = load_workflow_defaults(self.state)
        except SettingsError:
            defaults = dict(WORKFLOW_DEFAULTS)
        spin = QSpinBox(host)
        spin.setRange(0, wf.MAX_CONCURRENCY)
        if node is None:
            label = _label("Global concurrency", "workflowParamLabel")
            spin.setSpecialValueText(f"Default (DT+ {defaults['cloud']}, local {defaults['local']})")
            tip = (
                "The most steps of this workflow that run at the same time, all nodes together. "
                "Default: set in the Connection tab. Saved in the workflow file."
            )
            spin.setAccessibleName("Global concurrency")
        else:
            label = _label("Concurrency", "workflowParamLabel")
            spin.setSpecialValueText(
                f"Default ({defaults['detect']})" if node.SPEC.type == wf.DETECT_FACE else "Default (global)"
            )
            tip = (
                "The most steps of this node that run at the same time, never above the global concurrency. "
                "Default: the global value (Detect face: set in the Connection tab). Saved in the workflow file."
            )
            spin.setAccessibleName(f"Concurrency for node {node_id}")
        layout.addWidget(label)
        spin.setValue(value)
        label.setToolTip(tip)
        spin.setToolTip(tip)

        def changed(number: int) -> None:
            chosen = number or None
            if node_id is None:
                self.canvas.set_concurrency(chosen)
            else:
                self.canvas.set_node_concurrency(node_id, chosen)

        spin.valueChanged.connect(changed)
        layout.addWidget(spin)

    def _run_on_canvas(self) -> bool:
        """The followed run belongs to the workflow on the canvas (its steps paint there and in the inspector)."""
        return self.run is not None and self._run_path == self.path

    def _sync_lock(self) -> None:
        """While the canvas workflow's run is live its settings are read-only: the run keeps the ones it started with."""
        locked = self._run_on_canvas() and self._run_live(self.run)
        self.inspector.workflowParamsHost.setEnabled(not locked)
        self.inspector.workflowRunLockNote.setVisible(locked and self._inspected is not None)

    def _set(self, node_id: int, name: str, value) -> None:
        if self.canvas is not None:
            self.canvas.set_node_param(node_id, name, value)

    def _add_param(self, layout, node_id: int, param: wf.Param, value, choices: dict) -> None:
        host = self.inspector.workflowParamsHost
        label = _label(param.label, "workflowParamLabel")
        if param.hint:
            label.setToolTip(param.hint)
        if param.kind != "bool":
            layout.addWidget(label)
        try:
            checked = wf.check_param(param, value, draft=True)
        except wf.WorkflowError as exc:
            # Never clamp or coerce: an out-of-contract value stays visible, read-only, with the reason.
            field = QLineEdit(repr(value), host)
            field.setReadOnly(True)
            layout.addWidget(field)
            layout.addWidget(_label(str(exc), "workflowParamError"))
            return
        value = checked
        if param.kind == "checkpoint":
            combo = SearchableComboBox(host)
            combo.setAccessibleName(f"{param.label} for node {node_id}")
            combo.custom_prompt = "Exact checkpoint file"
            models = list(dict.fromkeys(choices.get("models") or ()))
            combo.addItems(models)
            combo.setCurrentText(str(value))
            combo.setToolTip(str(value) or param.hint)
            combo.currentTextChanged.connect(lambda text: self._set(node_id, param.name, text))
            layout.addWidget(combo)
            if value and models and value not in models:
                layout.addWidget(_label("Not in the selected backend's model list.", "workflowParamError"))
            elif not models:
                layout.addWidget(_label("Refresh models on Generate to list this backend's checkpoints.", "secondary"))
        elif param.kind == "loras":
            loras = choices.get("loras") or {}
            text = ", ".join(f"{name} · {weight:g}" for name, weight in value) or "No LoRA"
            summary = _label(text, "secondary")
            summary.setToolTip(text)
            button = QPushButton("Edit LoRAs…", host)
            button.setAccessibleName(f"Edit LoRAs for node {node_id}")

            def edit(_=False, current=value):
                from dtflow.ui.lora_selection_editor import edit_lora_selection

                selected = [LoRASelection(name, float(weight)) for name, weight in current]
                candidates = list(dict.fromkeys([*loras, *(name for name, _ in current)]))
                edit_lora_selection(
                    host,
                    candidates=candidates,
                    selected=selected,
                    suggestions=dict(loras),
                    title=f"LoRAs · node {node_id}",
                    callback=lambda chosen: self._set_loras(node_id, param, chosen),
                )

            button.clicked.connect(edit)
            layout.addWidget(summary)
            layout.addWidget(button)
        elif param.kind == "text":
            edit = QPlainTextEdit(str(value), host)
            edit.setAccessibleName(f"{param.label} for node {node_id}")
            edit.setMaximumHeight(72)
            edit.setTabChangesFocus(True)
            edit.textChanged.connect(lambda: self._set(node_id, param.name, edit.toPlainText()))
            layout.addWidget(edit)
        elif param.kind == "string":
            line = QLineEdit(str(value), host)
            line.setAccessibleName(f"{param.label} for node {node_id}")
            line.textChanged.connect(lambda text: self._set(node_id, param.name, text))
            layout.addWidget(line)
        elif param.kind == "int" and param.maximum is not None and param.maximum > 2**31 - 1:
            line = QLineEdit(str(value), host)
            line.setAccessibleName(f"{param.label} for node {node_id}")
            problem = _label("", "workflowParamError")
            problem.hide()

            def changed(text):
                try:
                    number = wf.check_param(param, int(text.strip()))
                except (ValueError, wf.WorkflowError) as exc:
                    problem.setText(
                        str(exc) if isinstance(exc, wf.WorkflowError) else f"{param.label} must be a number."
                    )
                    problem.show()
                    return
                problem.hide()
                self._set(node_id, param.name, number)

            line.textChanged.connect(changed)
            layout.addWidget(line)
            layout.addWidget(problem)
        elif param.kind == "int":
            dimension = param.name in {"width", "height"} and param.step == 64
            spin = _DimensionSpinBox(host) if dimension else QSpinBox(host)
            spin.setRange(int(param.minimum), int(param.maximum))
            spin.setSingleStep(int(param.step or 1))
            spin.setValue(int(value))
            spin.setAccessibleName(f"{param.label} for node {node_id}")
            spin.setKeyboardTracking(False)
            if dimension:
                spin.setToolTip("Multiples of 64 pixels; typed values round to the nearest multiple (ties upward).")
            spin.valueChanged.connect(lambda number: self._set(node_id, param.name, int(number)))
            layout.addWidget(spin)
        elif param.kind == "float":
            spin = QDoubleSpinBox(host)
            step = float(param.step or 0.1)
            spin.setDecimals(max(1, len(f"{step:g}".partition(".")[2])))
            spin.setRange(float(param.minimum), float(param.maximum))
            spin.setSingleStep(step)
            spin.setValue(float(value))
            spin.setAccessibleName(f"{param.label} for node {node_id}")
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(lambda number: self._set(node_id, param.name, float(number)))
            layout.addWidget(spin)
        elif param.kind == "choice":
            combo = QComboBox(host)
            combo.setAccessibleName(f"{param.label} for node {node_id}")
            for choice in param.choices:
                combo.addItem(CHOICE_LABELS.get(choice, choice), choice)
            combo.setCurrentIndex(param.choices.index(value))
            combo.currentIndexChanged.connect(lambda index: self._set(node_id, param.name, param.choices[index]))
            layout.addWidget(combo)
        elif param.kind == "bool":
            box = QCheckBox(param.label, host)
            box.setChecked(bool(value))
            box.toggled.connect(lambda on: self._set(node_id, param.name, bool(on)))
            layout.addWidget(box)

    def _set_loras(self, node_id: int, param: wf.Param, chosen) -> None:
        value = [[name, float(weight)] for name, weight in chosen]
        try:
            wf.check_param(param, value)
        except wf.WorkflowError as exc:
            InlineNotice.error(self.workspace, "LoRAs not changed", str(exc))
            return
        self._set(node_id, param.name, value)
        self._inspected = None
        self._show_node(node_id)

    @staticmethod
    def _step_text(step: dict) -> str:
        text = f"{step['branch_label']} · {STATE_LABELS.get(step['state'], step['state'])}"
        if step.get("stage") and step["state"] == "running":
            text += f" · {step['stage']}"
        return text

    def _show_node_run(self, node_id: int) -> None:
        layout = self.inspector.workflowNodeRun.layout()
        steps = [step for step in self.run.get("steps", ())] if self._run_on_canvas() else []
        steps = [step for step in steps if step["node_id"] == node_id]
        # Rebuild only when the rows or their Retry buttons change; stage and message text update in place.
        signature = (
            node_id,
            self.queue_id,
            tuple(
                (
                    tuple(s["branch"]),
                    s["state"] in ("failed", "unknown"),
                    bool(s.get("message")),
                    tuple(s.get("warnings", ())),
                )
                for s in steps
            ),
        )
        if signature == self._node_run_sig:
            for step in steps:
                text, message = self._node_run_rows.get(tuple(step["branch"]), (None, None))
                if text is not None:
                    text.setText(self._step_text(step))
                if message is not None:
                    message.setText(step_message(step))
            return
        self._node_run_sig, self._node_run_rows = signature, {}
        _clear(layout)
        if steps:
            layout.addWidget(_label("This run", "workflowParamLabel"))
        for step in steps:
            row = QFrame(self.inspector.workflowNodeRun)
            row.setProperty("role", "workflowStep")
            box = QVBoxLayout(row)
            box.setContentsMargins(8, 6, 8, 6)
            box.setSpacing(4)
            text = _label(self._step_text(step))
            box.addWidget(text)
            message = None
            if step.get("message") or step.get("warnings"):
                message = _label(step_message(step), "secondary")
                box.addWidget(message)
            self._node_run_rows[tuple(step["branch"])] = (text, message)
            if step["state"] in ("failed", "unknown") and self.queue_id is not None:
                box.addWidget(self._retry_button(step, row, "Retry"), 0, Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(row)

    def _retry_button(self, step: dict, parent: QWidget, text: str) -> QPushButton:
        retry = QPushButton(text, parent)
        retry.setAccessibleName(f"Retry {step_name(step)} for {step['branch_label']}")
        retry.setToolTip("Retry this step with its original settings, then continue its branch.")
        retry.clicked.connect(lambda _=False, s=step: self.retry(s["node_id"], tuple(s["branch"])))
        return retry

    # --- run --------------------------------------------------------------------------------------------------
    @staticmethod
    def _run_live(run: dict) -> bool:
        return run.get("state") in LIVE_RUN or bool(run.get("pauses"))

    def run_workflow(self) -> None:
        button = self.workspace.runWorkflowButton
        if feedback(self.workspace).commit(button):
            return
        if self.canvas is None or self._busy or self.start_workflow is None:
            return
        for spin in self.inspector.workflowParamsHost.findChildren(_DimensionSpinBox):
            spin.interpretText()
        graph = self.canvas.current_graph()
        try:
            plan = wf.plan_workflow(graph)
            document = wf.dump_workflow(graph)
        except wf.WorkflowError as exc:
            InlineNotice.error(self.workspace, "Workflow cannot run", str(exc))
            return
        blocking, _unknown = checkpoint_problems(graph, (self.choices_source() or {}).get("models"))
        if blocking:
            InlineNotice.error(self.workspace, "Workflow cannot run", " ".join(blocking))
            return
        name = self.name or "Workflow"
        if not plan.needs_face_models:
            self._create_run(document, name)
            return
        self._busy = True

        async def check():
            from dtflow.services.model_provisioning import WORKFLOW_FACE_ROLES, missing_models

            return await asyncio.to_thread(missing_models, self.state, WORKFLOW_FACE_ROLES)

        def checked(entries, failure):
            self._busy = False
            if failure is not None:
                self._error("Detection models could not be checked", failure)
                return
            self._missing = tuple(entries)
            self._update_plan()
            if not entries:
                self._create_run(document, name)
                return

            def downloaded(error):
                if error is not None:
                    self._error("Workflow not started", error)
                    return
                self._missing = ()
                self._update_plan()
                self._create_run(document, name)

            download_missing_models(self.workspace, self.state, entries, self.runner, downloaded, button=button)

        self._submit(check(), checked)

    def _create_run(self, document: dict, name: str) -> None:
        self._busy = True
        path = self.path

        def done(result, failure):
            self._busy = False
            if failure is not None:
                self._error("Workflow not started", failure)
                return
            queue_id, _run_id, how = result
            self._follow(queue_id, path, fresh=True)
            text = {
                "started": "Running on the selected backend; progress is in the activity bar.",
                "up next": "Waits up next behind the running queue and starts by itself.",
                "after generation": "Starts by itself when the current generation ends.",
            }.get(how, how)
            activity_strip(self.workspace).complete("workflow-run", f"Workflow · {name}", text)
            self.refresh_recent_runs()

        self.start_workflow(document, name, done)

    def _follow(self, queue_id: str, path: Path | None, *, fresh: bool = False) -> None:
        self.queue_id, self._run_path = queue_id, path
        self._warning_follow_queue_id = queue_id if fresh else None
        self.run = None
        self._pause_shown = None
        self._pause_dismissed.clear()
        self._present_run(None)
        self._apply_run_to_canvas()
        self._sync_lock()
        if self._active:
            self._poll.start()
            self._read_run()

    def open_run(self, queue_id: str) -> None:
        """Follow that workflow queue's run here: its steps, Retry and waiting branches (Queues, Earlier runs).

        The run's own workflow snapshot is put on the canvas unless the open workflow is that same workflow, or has
        unsaved changes (then only the run panel follows the run; its Retry buttons still work)."""
        if not isinstance(queue_id, str) or not queue_id:
            return

        async def read():
            from dtflow.persistence.queues import read_queue_requests

            _backend, _cap, items = await asyncio.to_thread(read_queue_requests, self.state, queue_id)
            record = next((record for _, _, record in items if isinstance(record, dict)), None)
            if record is None or record.get("kind") != "workflow":
                raise ValidationError("That queue is not a workflow run.")
            return record.get("workflow"), str(record.get("name") or "Workflow")

        def done(result, failure):
            if failure is not None:
                self._error("Workflow run could not be shown", failure)
                return
            document, name = result
            try:
                graph = wf.load_workflow(document, draft=True)
            except (wf.WorkflowError, TypeError, ValueError):
                graph = None
            path = self.path
            # Only the exact snapshot the run used may show its statuses on the open canvas: same nodes, settings
            # and links (layout aside). A later edit of the same workflow gets the run's own snapshot instead.
            same = graph is not None and self.canvas is not None and bool(self.name)
            same = same and _run_shape(self.canvas.current_graph()) == _run_shape(graph)
            if not same:
                if graph is not None and not self.dirty:
                    self.open_graph(graph, None, name)
                    path = None
                else:
                    path = Path(f"<run {queue_id}>")  # matches no open workflow: the canvas keeps its own
            self._follow(queue_id, path)
            self._select_recent(queue_id)

        self._submit(read(), done)

    def refresh_recent_runs(self) -> None:
        self._submit(asyncio.to_thread(recent_workflow_runs, self.state), self._recent_read)

    def _recent_read(self, runs, failure) -> None:
        if failure is not None:
            return
        self.show_recent_runs(runs)

    def show_recent_runs(self, runs: list[tuple[str, str]]) -> None:
        """Fill Earlier runs with (queue id, label) pairs; also used by offline renders."""
        combo = self.workspace.workflowRecentRuns
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Choose a run…" if runs else "No earlier runs", None)
        for queue_id, label in runs:
            combo.addItem(label, queue_id)
            combo.setItemData(combo.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
        combo.blockSignals(False)
        combo.setEnabled(bool(runs))
        self._select_recent(self.queue_id)

    def _select_recent(self, queue_id: str | None) -> None:
        combo = self.workspace.workflowRecentRuns
        index = combo.findData(queue_id) if queue_id else -1
        combo.blockSignals(True)
        combo.setCurrentIndex(max(index, 0))
        combo.blockSignals(False)

    def _recent_chosen(self, index: int) -> None:
        queue_id = self.workspace.workflowRecentRuns.itemData(index)
        if queue_id and queue_id != self.queue_id:
            self.open_run(queue_id)

    def _read_run(self) -> None:
        if self._reading or self.queue_id is None or self.runner.closed:
            return
        self._reading = True
        queue_id = self.queue_id

        async def read():
            from dtflow.services.workflow_queue import read_workflow_run

            return await read_workflow_run(self.state, queue_id)

        def done(run, failure):
            self._reading = False
            if queue_id != self.queue_id:
                return
            if failure is not None:
                self._poll.stop()
                self._error("Workflow run could not be read", failure)
                return
            self.present_run(run)

        self._submit(read(), done)

    def present_run(self, run: dict | None) -> None:
        """Show one run-state snapshot (shape: ``services.workflow_queue``); also used by offline renders."""
        ended = self.run is not None and self._run_live(self.run) and (run is None or not self._run_live(run))
        self.run = run
        if run is None or not self._run_live(run):
            self._poll.stop()
        if ended:
            self.refresh_recent_runs()
        self._sync_lock()
        self._present_run(run)
        self._present_warnings(run)
        self._apply_run_to_canvas()
        if self.canvas is not None:
            selected = self.canvas.selected_node_id()
            if selected is not None:
                self._show_node_run(selected)
        self._sync_pause_overlay()
        self._update_plan()

    def _present_warnings(self, run: dict | None) -> None:
        run_id = run.get("run_id") if run else None
        if run_id != self._warning_run_id:
            self._warning_run_id = run_id
            self._warning_seen.clear()
        if not run or not self.workspace.isVisible():
            return
        if self._run_live(run):
            self._warning_follow_queue_id = self.queue_id
        if self.queue_id is None or self._warning_follow_queue_id != self.queue_id:
            return  # Historical outcomes retain warnings in outputs/details, without replaying an overlay.
        fresh = []
        for step in run.get("steps", ()):
            for warning in step.get("warnings", ()):
                key = (step["node_id"], warning)
                if key not in self._warning_seen:
                    self._warning_seen.add(key)
                    fresh.append(f"{step_name(step)}: {warning}")
        if fresh:
            text = "\n".join(fresh[:2])
            if len(fresh) > 2:
                text += f"\n{len(fresh) - 2} more warning(s) in workflow outputs."
            InlineNotice.warning(self.workspace, "Workflow LoRA warning", text)

    def _present_run(self, run: dict | None) -> None:
        w, i = self.workspace, self.inspector
        if run is None:
            self._set_results(())
            self._set_retries(())
            self._set_pauses(())
            w.workflowRunStatus.setText("No run started.")
            _repolish(w.workflowRunStatus, workflowState="")
            w.workflowOutputText.setText("Every step's image is saved in Images; Save image nodes mark the results.")
            w.stopWorkflowButton.setVisible(False)
            i.workflowBranch.setVisible(False)
            return
        state = run.get("state", "unknown")
        steps = run.get("steps", [])
        running = [step for step in steps if step["state"] == "running"]
        headline = f"{run.get('name') or 'Workflow'} · {STATE_LABELS.get(state, state)}"
        if run.get("pauses"):
            headline += f" · {len(run['pauses'])} branch{'es' if len(run['pauses']) != 1 else ''} waiting"
        w.workflowRunStatus.setText(headline)
        _repolish(w.workflowRunStatus, workflowState="paused" if run.get("pauses") and state == "running" else state)
        lines = [f"{run.get('submitted', 0)} of {run.get('planned', 0)} planned generations submitted."]
        for step in running[:3]:
            lines.append(f"{step_name(step)} · {step['branch_label']} · {step.get('stage') or 'Running'}")
        if run.get("message"):
            lines.append(run["message"])
        failed = [step for step in steps if step["state"] in ("failed", "unknown")]
        for step in failed[:3]:
            text = f"{step_name(step)} · {step['branch_label']}"
            message = step.get("message") or STATE_LABELS[step["state"]]
            # The run message already names the first problem and its cause; do not repeat that cause.
            if message not in (run.get("message") or ""):
                text += f": {message}"
            lines.append(text)
        warnings = {}
        for step in steps:
            for warning in step.get("warnings", ()):
                warnings[(step["node_id"], warning)] = f"Warning · {step_name(step)}: {warning}"
        lines.extend(list(warnings.values())[:6])
        if len(warnings) > 6:
            lines.append(f"{len(warnings) - 6} more node warning(s); select a node for details.")
        w.workflowOutputText.setText("\n".join(lines))
        w.stopWorkflowButton.setVisible(state in LIVE_RUN)
        w.stopWorkflowButton.setEnabled(self.queue_id is not None)
        self._set_results(run.get("results", []))
        self._set_retries(failed[:3] if self.queue_id is not None else ())
        # Inspector: branch-local pauses (Continue / Stop act on that image only) and sibling branches.
        i.workflowBranch.setVisible(True)
        pauses = run.get("pauses", [])
        if pauses:
            i.workflowBranchStatus.setText(
                "Paused · " + ("no face detected" if len(pauses) == 1 else f"{len(pauses)} branches")
            )
            _repolish(i.workflowBranchStatus, workflowState="warning")
            i.workflowBranchDetail.setText("Continue or Stop affects only that image branch; the others keep running.")
        else:
            i.workflowBranchStatus.setText(STATE_LABELS.get(state, state))
            _repolish(i.workflowBranchStatus, workflowState="normal")
            i.workflowBranchDetail.setText("Each generated image runs as its own branch.")
        self._set_pauses(pauses)
        branches: dict[tuple, list[dict]] = {}
        for step in steps:
            branches.setdefault(tuple(step["branch"]), []).append(step)
        lines = []
        for branch, items_ in branches.items():
            current = next((s for s in items_ if s["state"] in ("running", "paused", "failed", "unknown")), items_[-1])
            text = f"{wf.branch_label(branch)} · {STATE_LABELS.get(current['state'], current['state'])}"
            text += f" · {step_name(current)}"
            if current["state"] == "running" and current.get("stage"):
                text += f" · {current['stage']}"
            lines.append(text)
        i.workflowSiblingStatus.setText("\n".join(lines))

    # The run's buttons are rebuilt only when what they act on changes, never on an unchanged 1 s poll: a click that
    # spans a rebuild would be lost and keyboard focus would jump.
    def _set_results(self, items) -> None:
        w = self.workspace
        signature = tuple(
            (result["path"], result["label"], tuple(result["branch"]), result["path"] in self._pixmaps)
            for result in items
        )
        w.workflowResultsRow.setVisible(bool(items))
        if signature == self._results_sig:
            return
        self._results_sig = signature
        results = w.workflowResultsRow.layout()
        while results.count() > 1:  # keep the trailing stretch
            item = results.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for result in items:
            button = QToolButton(w.workflowResultsRow)
            button.setObjectName("workflowResultThumb")
            button.setIconSize(QSize(THUMB, THUMB))
            button.setFixedSize(THUMB + 8, THUMB + 8)
            path = result["path"]
            identity = output_identity(path)
            tip = f"{result['label']} · {wf.branch_label(tuple(result['branch']))}"
            button.setToolTip(tip + (" · open in Images" if identity and self.open_image else ""))
            button.setAccessibleName(f"Result {tip}")
            pixmap = self._pixmap(path)
            if pixmap is not None:
                button.setIcon(QIcon(pixmap))
            else:
                button.setText(result["label"])
            if identity and self.open_image:
                button.clicked.connect(lambda _=False, key=identity: self.open_image(key))
            results.insertWidget(results.count() - 1, button)

    def _set_retries(self, steps) -> None:
        """Retry in the Run panel too: reachable at every width, whichever node is selected."""
        w = self.workspace
        signature = (self.queue_id, tuple((s["node_id"], tuple(s["branch"]), s["state"]) for s in steps))
        w.workflowRetryActions.setVisible(bool(steps))
        if signature == self._retry_sig:
            return
        self._retry_sig = signature
        layout = w.workflowRetryActions.layout()
        while layout.count() > 1:  # keep the trailing stretch
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for step in steps:
            text = f"Retry {step_name(step)} · image {int(step['branch'][1]) + 1}"
            layout.insertWidget(layout.count() - 1, self._retry_button(step, w.workflowRetryActions, text))

    def _set_pauses(self, pauses) -> None:
        host = self.inspector.workflowPausesHost
        signature = (
            self.queue_id,
            tuple((p["node_id"], tuple(p["branch"]), p.get("branch_label"), p.get("reason")) for p in pauses),
        )
        if signature == self._pause_sig:
            return
        self._pause_sig = signature
        _clear(host.layout())
        for pause in pauses:
            frame = QFrame(host)
            frame.setProperty("role", "workflowPause")
            box = QVBoxLayout(frame)
            box.setContentsMargins(8, 6, 8, 6)
            box.setSpacing(4)
            box.addWidget(_label(f"{pause['branch_label']} · {pause['reason']}"))
            row = QHBoxLayout()
            row.setSpacing(4)
            for text, decision in (("Continue", "continue"), ("Stop branch", "stop")):
                button = QPushButton(text, frame)
                button.setAccessibleName(f"{text} {pause['branch_label']}")
                button.clicked.connect(lambda _=False, p=pause, d=decision: self.decide(p, d))
                row.addWidget(button)
            row.addStretch(1)
            box.addLayout(row)
            host.layout().addWidget(frame)

    def _apply_run_to_canvas(self) -> None:
        if self.canvas is None:
            return
        self.canvas.clear_status()
        run = self.run
        if run is None or self._run_path != self.path:
            return
        by_node: dict[int, list[dict]] = {}
        for step in run.get("steps", []):
            by_node.setdefault(step["node_id"], []).append(step)
        for node_id, steps in by_node.items():
            state, done, total = node_state(steps)
            text = STATE_LABELS.get(state, state)
            if total > 1:
                text += f" · {done}/{total} images"
            stage = next((s.get("stage") for s in steps if s["state"] == "running" and s.get("stage")), None)
            if stage:
                text += f" · {stage}"
            tooltip = "\n".join(
                f"{s['branch_label']}: {STATE_LABELS.get(s['state'], s['state'])}"
                + (f" · {s['message']}" if s.get("message") else "")
                for s in steps
            )
            self.canvas.set_status(node_id, state, text, tooltip)
            latest = [s for s in steps if s.get("output")]
            if latest:
                path = max(latest, key=lambda s: s.get("finished_at") or "")["output"]
                pixmap = self._pixmap(path)
                if pixmap is not None:
                    self.canvas.set_thumbnail(node_id, pixmap)

    def _pixmap(self, path: str) -> QPixmap | None:
        pixmap = self._pixmaps.get(path)
        if pixmap is None and path not in self._loading_images:
            self._loading_images.add(path)
            paths = (path,)

            def done(images, failure):
                for name in paths:
                    self._loading_images.discard(name)
                if failure is not None or not images:
                    return
                if len(self._pixmaps) > 96:
                    self._pixmaps.clear()
                for name, image in images.items():
                    self._pixmaps[name] = QPixmap.fromImage(image)
                if self.run is not None:
                    self._present_run(self.run)
                    self._apply_run_to_canvas()

            self._submit(asyncio.to_thread(_read_images, paths, 192), done)
        return pixmap

    @property
    def loading_images(self) -> bool:
        return bool(self._loading_images)

    def stop_run(self) -> None:
        if self.queue_id is None:
            return
        queue_id = self.queue_id

        async def post():
            from dtflow.services.queue_session import post_queue_control

            await post_queue_control(self.state, queue_id, "cancel")

        def done(_result, failure):
            if failure is not None:
                self._error("Stop not requested", failure)
                return
            activity_strip(self.workspace).complete(
                "workflow-run",
                "Workflow · stop requested",
                "The run stops now. A step already sent ends unknown: its image may still complete; Retry sends it again.",
                "stopped",
            )
            self._read_run()

        self._submit(post(), done)

    def decide(self, pause: dict, decision: str) -> None:
        if self.queue_id is None:
            return
        queue_id, key = self.queue_id, (pause["node_id"], tuple(pause["branch"]))
        if self._pause_shown == key:
            self._dismiss_pause_overlay()
        self._pause_dismissed.add(key)  # answered: do not show it again while the next read is pending

        async def post():
            from dtflow.services.workflow_queue import decide_branch

            await decide_branch(self.state, queue_id, pause["node_id"], tuple(pause["branch"]), decision)

        def done(_result, failure):
            if failure is not None:
                self._pause_dismissed.discard(key)
                self._error("Branch decision not sent", failure)
                return
            self._read_run()

        self._submit(post(), done)

    def retry(self, node_id: int, branch: tuple[int, int]) -> None:
        if self.queue_id is None:
            return
        queue_id, path = self.queue_id, self._run_path

        async def post():
            from dtflow.services.workflow_queue import retry_step

            return await retry_step(self.state, queue_id, node_id, branch)

        def done(new_id, failure):
            if failure is not None:
                self._error("Retry not started", failure)
                return
            if new_id == queue_id or self.start_created is None:
                self._follow(queue_id, path, fresh=True)
                activity_strip(self.workspace).complete("workflow-run", "Workflow · retry", "Retry started.")
                return

            def started(how, error):
                if error is not None:
                    self._error("Retry not started", error)
                    return
                self._follow(new_id, path, fresh=True)
                self.refresh_recent_runs()
                activity_strip(self.workspace).complete(
                    "workflow-run",
                    "Workflow · retry",
                    "Retry started." if how == "started" else "Retry queued; it starts by itself.",
                )

            self.start_created(new_id, started)

        self._submit(post(), done)

    # --- branch pause overlay ---------------------------------------------------------------------------------
    def _sync_pause_overlay(self) -> None:
        pauses = (self.run or {}).get("pauses", [])
        keys = {(p["node_id"], tuple(p["branch"])): p for p in pauses}
        if self._pause_shown is not None and self._pause_shown not in keys:
            self._dismiss_pause_overlay()
        if self._pause_shown is not None or not self._active:
            return
        pending = [p for key, p in keys.items() if key not in self._pause_dismissed]
        panel = feedback(self.workspace)
        if not pending or panel.active():
            return
        self.show_pause_overlay(pending[0])

    def show_pause_overlay(self, pause: dict) -> None:
        """The shared inline overlay for one branch: "No face detected. Continue anyway?" with Continue and Stop."""
        panel = feedback(self.workspace)
        key = (pause["node_id"], tuple(pause["branch"]))
        reason = str(pause.get("reason") or "No face detected.").strip()
        title = reason if reason.endswith("?") else f"{reason.rstrip('.')}. Continue anyway?"
        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        box.addWidget(
            _label(
                f"{pause['branch_label']} paused at node #{pause['node_id']}. Continue or Stop affects only this "
                "image branch; the other images keep running.",
                "secondary",
            )
        )
        stop = QPushButton("Stop this branch", body)
        stop.setAccessibleName(f"Stop {pause['branch_label']}")
        stop.clicked.connect(lambda: (panel.dismiss(), self.decide(pause, "stop")))
        box.addWidget(stop, 0, Qt.AlignmentFlag.AlignLeft)

        def accept():
            panel.dismiss()
            self.decide(pause, "continue")

        self._pause_shown = key
        body.destroyed.connect(lambda *_: self._pause_closed(key))
        panel.editor(title, body, "Continue", accept, primary=True)

    def _pause_closed(self, key) -> None:
        if self._pause_shown == key:
            self._pause_shown = None
            self._pause_dismissed.add(key)  # Cancel/Escape: the inspector row keeps Continue/Stop

    def _dismiss_pause_overlay(self) -> None:
        if self._pause_shown is not None:
            key, self._pause_shown = self._pause_shown, None
            panel = feedback(self.workspace)
            if panel.active():
                panel.dismiss()
            self._pause_dismissed.discard(key)

    # --- add-node picker --------------------------------------------------------------------------------------
    def _node_picker(self):
        if self._picker is None:
            from dtflow.ui.main_window import FORMS_DIR, load_ui

            picker = load_ui(FORMS_DIR / "workflows_node_picker.ui", self.canvas.widget)
            picker.hide()
            for spec in wf.CATALOG.values():
                item = QListWidgetItem(spec.title)
                item.setData(Qt.ItemDataRole.UserRole, spec.type)
                item.setToolTip(spec.description)
                picker.workflowNodePickerList.addItem(item)
            picker.workflowNodePickerSearch.textChanged.connect(self._filter_picker)
            picker.workflowNodePickerSearch.installEventFilter(self)
            picker.workflowNodePickerList.installEventFilter(self)
            picker.workflowNodePickerList.itemActivated.connect(self._picked)
            picker.workflowNodePickerList.itemClicked.connect(self._picked)
            QApplication.instance().focusChanged.connect(self._picker_focus)
            self._picker = picker
        return self._picker

    def show_node_picker(self, scene_pos: QPointF, view_pos: QPoint) -> None:
        """Searchable add-node list at a canvas point (double-click on empty canvas, or Tab)."""
        if self.canvas is None:
            return
        if not self.name:
            self.open_graph(wf.Graph(), None, "Untitled workflow")
        picker = self._node_picker()
        self._picker_at = QPointF(scene_pos)
        picker.workflowNodePickerSearch.clear()
        self._filter_picker("")
        picker.adjustSize()
        host = self.canvas.widget
        point = self.canvas.view.viewport().mapTo(host, view_pos)
        x = max(0, min(point.x(), host.width() - picker.width()))
        y = max(0, min(point.y(), host.height() - picker.height()))
        picker.move(x, y)
        picker.show()
        picker.raise_()
        picker.workflowNodePickerSearch.setFocus(Qt.FocusReason.PopupFocusReason)

    def hide_node_picker(self) -> None:
        if self._picker is not None and self._picker.isVisible():
            self._picker.hide()

    def _filter_picker(self, text: str) -> None:
        widget = self._picker.workflowNodePickerList
        self._filter(widget, text)
        shown = [widget.item(i) for i in range(widget.count()) if not widget.item(i).isHidden()]
        widget.setCurrentItem(shown[0] if shown else None)
        row = widget.sizeHintForRow(0) if widget.count() else 24
        widget.setFixedHeight(row * max(1, min(len(shown), 6)) + 2 * widget.frameWidth() + 8)
        self._picker.adjustSize()

    def _picked(self, item) -> None:
        if item is None or item.isHidden() or self.canvas is None:
            return
        at = self._picker_at
        self.hide_node_picker()
        self.canvas.add_node(item.data(Qt.ItemDataRole.UserRole), at=at)
        self.canvas.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _picker_focus(self, _old, new) -> None:
        picker = self._picker
        if picker is not None and picker.isVisible() and (new is None or not picker.isAncestorOf(new)):
            picker.hide()

    def _fit_toolbar(self) -> None:
        """A narrow canvas column (Library open in a small window) puts the name above the buttons, so no button
        is clipped and nothing scrolls sideways."""
        w = self.workspace
        compact = w.width() < COMPACT_TOOLBAR_PX
        if compact == self._compact_toolbar:
            return
        self._compact_toolbar = compact
        toolbar, title = w.workflowToolbar.layout(), w.workflowTitleRow.layout()
        if compact:
            title.insertWidget(0, w.workflowName, 1)
            title.insertWidget(1, w.workflowStateBadge)
            toolbar.insertStretch(1, 1)  # keeps the buttons right-aligned, as beside the name
        else:
            self._drop_toolbar_stretch()
            toolbar.insertWidget(1, w.workflowName, 1)
            toolbar.insertWidget(2, w.workflowStateBadge)
        w.workflowTitleRow.setVisible(compact)
        w.runWorkflowButton.setText("Run" if compact else "Run workflow")

    def _drop_toolbar_stretch(self) -> None:
        toolbar = self.workspace.workflowToolbar.layout()
        for index in range(toolbar.count()):
            item = toolbar.itemAt(index)
            if item.spacerItem() is not None:
                toolbar.takeAt(index)
                return

    def eventFilter(self, watched, event) -> bool:
        if watched is self.workspace and event.type() == QEvent.Type.Resize:
            self._toolbar_timer.start()
            return False
        picker = self._picker
        if picker is not None and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            widget = picker.workflowNodePickerList
            if key == Qt.Key.Key_Escape:
                self.hide_node_picker()
                if self.canvas is not None:
                    self.canvas.view.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._picked(widget.currentItem())
                return True
            if watched is picker.workflowNodePickerSearch and key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                visible = [widget.item(i) for i in range(widget.count()) if not widget.item(i).isHidden()]
                if visible:
                    current = widget.currentItem()
                    index = visible.index(current) if current in visible else -1
                    index = min(len(visible) - 1, index + 1) if key == Qt.Key.Key_Down else max(0, index - 1)
                    widget.setCurrentItem(visible[index])
                return True
        return super().eventFilter(watched, event)

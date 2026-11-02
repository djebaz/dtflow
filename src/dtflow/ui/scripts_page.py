"""Generate > Scripts: native UI regions mounted in DTFlow's shared Qt shell.

This module owns presentation and local draft interaction only. Script execution,
provider dispatch, service admission, history writes and immutable-version storage
are intentionally not attached in this UI implementation slice.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dtflow.ui.brand import tinted_icon
from dtflow.ui.scripts_editor import ScriptEditor
from dtflow.ui.scripts_form import REFERENCE_FORM, ScriptForm


REFERENCE_SCRIPTS = (
    ("Wildcard prompt sweep.js", "9f3a1c2", "Tier 1", "supported"),
    ("Hires fix loop.js", "b81e07d", "Tier 1", "supported"),
    ("Outpaint ring.js", "4c0d9aa", "Tier 3", "unsupported"),
)

REFERENCE_HOOKS = (
    ("pipeline.run", "supported", "Each call is one generation attempt"),
    ("canvas.saveImage", "supported", "Stored in the run sandbox"),
    ("requestFromUser", "supported", "Rendered as an inline form"),
    ("canvas.moveCanvas", "deferred", "Virtual until a generation"),
    ("canvas.loadLayerFromPhotos", "unsupported", "No native picker mapping yet"),
)

# Illustrative source code is copied from the owner-approved Scripts UI kit,
# not inferred from a live user script. It never runs in the offline renderer.
EDITOR_STARTER = "\n".join(
    (
        "// New script: a small supported starter.",
        'const wild = ["red", "green", "blue"];',
        "const configuration = pipeline.configuration;",
        "",
        "for (const w of wild) {",
        '  pipeline.run({ configuration: configuration, prompt: "a lamp, " + w });',
        '  canvas.saveImage("lamp-" + w + ".png", true);',
        "}",
    )
)
EDITOR_BASE = "\n".join(
    (
        "// Wildcard prompt sweep",
        'const answers = requestFromUser("Sweep", "Start", function () {',
        "  return [",
        '    this.textField("red, green, blue, amber", "Wildcards", false, 100),',
        '    this.slider(75, this.slider.percent, 0, 100, "Strength")',
        "  ];",
        "});",
        "",
        "const configuration = pipeline.configuration;",
        "configuration.strength = answers[1] / 100;",
        'for (const w of answers[0].split(",")) {',
        '  pipeline.run({ configuration: configuration, prompt: "a lamp, " + w.trim() });',
        '  canvas.saveImage("sweep-" + w.trim() + ".png", true);',
        "}",
    )
)


RUN_STATUSES = {
    "queued": ("Queued behind 3 items", "Nothing submitted. Waiting 0:42 · Cancel is free."),
    "cancelled_before_start": ("Cancelled before start", "No provider submission and no charge."),
    "running": ("Running · Run #5", "Generation 3 submitted · latest committed result below."),
    "waiting_input": (
        "Waiting for your input",
        "The script is paused. A generation already submitted may still complete.",
    ),
    "stopped": ("Stopped locally", "Remote work may continue; inspect linked attempts."),
    "failed": ("Failed · Run #5", "See the durable error details in History."),
    "interrupted": ("Interrupted · Run #5", "Do not replay unresolved attempts."),
    "outcome_unknown": ("Outcome unknown · Run #5", "A submitted attempt may still complete or be billed."),
}


def _label(text: str, *, role: str = "", wrap: bool = False) -> QLabel:
    result = QLabel(text)
    result.setWordWrap(wrap)
    if role:
        result.setObjectName(role)
    return result


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("role", "sectionCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    heading = _label(title, role="scriptsCardTitle")
    heading.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    layout.addWidget(heading, 0, Qt.AlignmentFlag.AlignTop)
    return frame, layout


def _scroll(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    return scroll


def _line(layout: QVBoxLayout, left: str, right: str) -> QLabel:
    row = QWidget()
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(0, 0, 0, 0)
    name = _label(left, role="scriptsMuted")
    name.setFixedWidth(106)
    row_layout.addWidget(name, 0)
    value = _label(right, wrap=True)
    value.setMinimumWidth(0)
    value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    value.setToolTip(right)
    row_layout.addWidget(value, 1)
    layout.addWidget(row)
    return value


def _hook_item(hook: str, state: str, detail: str) -> QFrame:
    """Show a hook name, semantic compatibility label and one explanation."""
    row = QFrame()
    row.setObjectName("scriptsHookItem")
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 5, 0, 5)
    layout.setSpacing(3)
    heading = QHBoxLayout()
    import re

    wrapped_hook = re.sub(r"(?<=[a-z])(?=[A-Z])", "\u200b", hook).replace(".", ".\u200b")
    name = _label(wrapped_hook, role="scriptsHookName", wrap=True)
    name.setMinimumWidth(0)
    name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    name.setToolTip(hook)
    heading.addWidget(name, 1)
    verdict = _label(state, role="scriptsHookVerdict")
    verdict.setProperty("feedbackState", state)
    heading.addWidget(verdict)
    layout.addLayout(heading)
    explanation = _label(detail, role="scriptsMuted", wrap=True)
    explanation.setToolTip(detail)
    layout.addWidget(explanation)
    return row


class ScriptsPage:
    """UI controller for library, central content, inspector and narrow drawer."""

    def __init__(self, designer: QWidget, *, owner):
        self.designer = designer
        self.owner = owner
        self.left = designer.scriptsLibraryPanel
        self.center = designer.scriptsWorkPanel
        self.right = designer.scriptsInspectorPanel
        self.active_state = "scripts_empty"
        self._reference_mode = False
        self._draft_text = ""
        self._drawer_requested = False
        self._wide_details_requested = True
        self._current_script: tuple[str, str, str, str] | None = None
        self._build_library()
        self._build_center()
        self._build_inspector()
        self._build_drawer()
        self.clear()

    def mount(self, shell) -> None:
        """Reuse the three established shell stacks; never add a second top-level tab."""
        # The fixed-width left rail uses a scroll viewport with horizontal
        # overflow hidden; do not let Scripts' size hint widen the stack.
        self.left.setMinimumWidth(0)
        policy = self.left.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
        self.left.setSizePolicy(policy)
        self.panels = (self.left, self.center, self.right)
        for stack, panel in zip((shell.leftStack, shell.centerStack, shell.rightStack), self.panels, strict=True):
            panel.setParent(None)
            stack.addWidget(panel)
        self.shell = shell

    def activate(self) -> None:
        for stack, panel in zip(
            (self.shell.leftStack, self.shell.centerStack, self.shell.rightStack), self.panels, strict=True
        ):
            stack.setCurrentWidget(panel)
        self.scripts_mode.setChecked(True)

    def _build_library(self):
        ui = self.designer
        self.library = ui.scriptsLibraryList
        self.new_button = ui.newScriptButton
        self.import_button = ui.importScriptButton
        self.reload_button = ui.reloadScriptButton
        self.library_note = ui.libraryNote
        self.library.currentRowChanged.connect(self._selected)
        self.new_button.clicked.connect(self.new_draft)
        self.import_button.clicked.connect(self.import_preview)

    def _build_center(self):
        ui = self.designer
        self.text_mode, self.inpaint_mode, self.scripts_mode = (
            ui.textModeButton,
            ui.inpaintModeButton,
            ui.scriptsModeButton,
        )
        self.mode_group = QButtonGroup(self.center)
        self.mode_group.setExclusive(True)
        for button in (self.text_mode, self.inpaint_mode, self.scripts_mode):
            self.mode_group.addButton(button)
        self.scripts_mode.setChecked(True)
        self.details_button = ui.scriptsInspectorToggle
        self.details_button.setIcon(
            tinted_icon(
                Path(__file__).parents[1] / "resources" / "images" / "gallery" / "panel-right.svg",
                "color.glyphNeutral",
                16,
            )
        )
        self.details_button.clicked.connect(self.toggle_details)
        self.script_title, self.sha, self.status = ui.scriptTitle, ui.scriptSha, ui.scriptCompatibility
        self.run_identity, self.views = ui.runIdentity, ui.scriptsContentStack
        self.tab_buttons = [ui.inspectTab, ui.configureTab, ui.codeTab]
        for index, button in enumerate(self.tab_buttons):
            button.clicked.connect(lambda checked=False, target=index: self.show_view(target))
        self._build_inspect()
        self._build_form()
        self.editor = ScriptEditor(consent=self._confirm_discard)
        ui.scriptEditorHost.layout().addWidget(self.editor)
        self._build_run()
        self._build_results()
        self._build_empty()
        self.start_panel, self.cap, self.start = ui.scriptsStartPanel, ui.generationCap, ui.scriptsStartButton
        self.disclosure, self.acknowledge = ui.startDisclosure, ui.preflightAcknowledgement
        self.run_footer = ui.runFooter
        self.acknowledge.hide()
        self.run_footer.hide()
        self.show_view(0)

    def _build_empty(self):
        from dtflow.ui.brand import brand_mark

        logo = brand_mark()
        if not logo.isNull():
            self.designer.emptyMark.setPixmap(
                logo.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )

    def _build_inspect(self):
        ui = self.designer
        self.inspect_banner, self.inspect_card = ui.scriptsInspectBanner, ui.inspectionCard
        self.banner_title, self.banner_desc = ui.inspectBannerTitle, ui.inspectBannerDescription
        self.inspect_summary = ui.inspectSummary
        self.inspection_values = {
            key: getattr(ui, "inspection" + key + "Value")
            for key in ("Header", "Tier", "Dynamic", "Models", "Loras", "Method")
        }
        self.configure_from_inspect = ui.configureFromInspect
        self.configure_from_inspect.clicked.connect(lambda: self.show_view(1))
        ui.editFromInspect.clicked.connect(lambda: self.show_view(2))

    def _build_form(self):
        ui = self.designer
        self.unresolved_notice, self.reset_fields = ui.scriptsUnresolvedNotice, ui.resetFieldsButton
        self.source, self.source_note = ui.scriptSource, ui.scriptsSourceNote
        self.handoff, self.form_note = ui.handoffPanel, ui.formNote
        self.unresolved_notice.hide()
        self.handoff.hide()
        self.form = ScriptForm()
        ui.scriptFieldsHost.layout().addWidget(self.form)
        self.reset_fields.clicked.connect(self._reset_fields)
        ui.detachSourceButton.clicked.connect(self._detach_source)
        self.source.currentIndexChanged.connect(self._source_changed)
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from dtflow.ui.theme import load_tokens

        tokens = load_tokens()
        for label, mask in ((ui.sourceThumbnail, False), (ui.maskThumbnail, True)):
            tile = QPixmap(56, 56)
            tile.fill(QColor(tokens["color.canvas"]))
            painter = QPainter(tile)
            painter.fillRect(
                0, 0, 21 if mask else 56, 56 if mask else 28, QColor("#ffffff" if mask else tokens["color.accent"])
            )
            painter.end()
            label.setPixmap(tile)

    def _build_run(self):
        ui = self.designer
        self.run_status, self.run_detail = ui.runStatus, ui.runDetail
        self.result_image, self.result_caption = ui.scriptsResultTile, ui.resultCaption
        self.waiting_form, self.console = ui.waitingForm, ui.scriptsRunConsole
        self.waiting_form.hide()
        self.console.document().setMaximumBlockCount(500)
        self.console.setPlainText("[offline fixture] No script was executed.")

    def _build_results(self):
        self.results_header = self.designer.resultsHeader
        self.results_content = self.designer.resultsContent
        self.results_layout = self.results_content.layout()

    def _populate_results(self):
        """Build non-interactive attempt cards for the offline screenshot fixtures."""
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # deleteLater() alone leaves visible orphans until Qt handles
                # DeferredDelete. Hide and detach before rebuilding a live page.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for number, color in enumerate(("red", "green", "blue", "amber"), 1):
            card = QFrame()
            card.setObjectName("scriptsStepCard")
            row = QHBoxLayout(card)
            thumbnail = _label(color, role="scriptsStepThumbnail")
            thumbnail.setFixedSize(64, 64)
            thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(thumbnail)
            details = QVBoxLayout()
            details.addWidget(_label(f"Step {number} · {color} · committed", role="scriptsCardTitle"))
            details.addWidget(
                _label(
                    f"Created by Wildcard prompt sweep.js · run #4 · step {number}",
                    role="scriptsMuted",
                    wrap=True,
                )
            )
            row.addLayout(details, 1)
            open_button = QPushButton("Open in Images")
            open_button.setAccessibleName(f"Open run 4 step {number} in Images")
            open_button.setToolTip("Offline fixture: no durable attempt is linked.")
            open_button.setEnabled(False)
            row.addWidget(open_button)
            self.results_layout.addWidget(card)
        self.results_layout.addStretch(1)

    def _build_inspector(self):
        from dtflow.ui.main_window import FORMS_DIR, load_ui

        self.inspector = load_ui(FORMS_DIR / "scripts_inspector.ui", self.right)
        self.right.layout().addWidget(self.inspector)
        self.hook_status = self.inspector.hookStatus
        self.hook_list = self.inspector.hooksHost.layout()
        self.history = self.inspector.scriptsHistoryList
        self.inspector_panels = tuple(
            getattr(self.inspector, name) for name in ("compatibilityCard", "settingsCard", "sourceCard", "historyCard")
        )
        self.inspector_source_values = self._source_values(self.inspector)

    def _build_drawer(self):
        from dtflow.ui.main_window import FORMS_DIR, load_ui

        self.drawer = load_ui(FORMS_DIR / "scripts_details.ui", self.owner.form)
        self.drawer.closeDetailsButton.clicked.connect(self.toggle_details)
        self.drawer_inspector = load_ui(FORMS_DIR / "scripts_inspector.ui", self.drawer)
        self.drawer.drawerInspectorHost.layout().addWidget(self.drawer_inspector)
        self.drawer_summary = self.drawer_inspector.hookStatus
        self.drawer_source_values = self._source_values(self.drawer_inspector)
        self.drawer.hide()

    def toggle_details(self):
        if self.owner.form.width() < 1100:
            self._drawer_requested = not self._drawer_requested
        else:
            self._wide_details_requested = not self._wide_details_requested
        self.sync_responsive()

    def sync_responsive(self):
        """Update Scripts layout without recycling unchanged Qt geometry."""
        narrow = self.owner.form.width() < 1100
        self.form.set_narrow(narrow)
        active = self.owner._scripts_active and (
            self.owner.form.workspaceStack.currentWidget() is self.owner.generation_page
        )
        rail = self.owner.shell.rightRail
        self.details_button.setVisible(active)
        self.details_button.setChecked(self._drawer_requested if narrow else self._wide_details_requested)
        if not active:
            if self.drawer.isVisible():
                self.drawer.hide()
            return
        # QStackedWidget otherwise inherits taller inactive pages' minimum hints.
        # Keep the library footer in the *actual* shared scroll viewport; its
        # QListWidget is the only vertically expanding part of this rail.
        available = self.owner.shell.leftScroll.viewport().height()
        if available > 0:
            for panel in (self.owner.shell.leftStack, self.left):
                if panel.minimumHeight() != available or panel.maximumHeight() != available:
                    panel.setFixedHeight(available)
        if narrow:
            if rail.isVisible():
                rail.hide()
            if self._drawer_requested:
                from PySide6.QtCore import QRect

                geometry = QRect(
                    max(0, self.owner.form.width() - 310),
                    55,
                    min(300, self.owner.form.width()),
                    max(300, self.owner.form.height() - 90),
                )
                if self.drawer.geometry() != geometry:
                    self.drawer.setGeometry(geometry)
                if not self.drawer.isVisible():
                    self.drawer.show()
                    self.drawer.raise_()
            elif self.drawer.isVisible():
                self.drawer.hide()
        else:
            if self.drawer.isVisible():
                self.drawer.hide()
            rail.setVisible(self._wide_details_requested)

    def _selected(self, row: int):
        if not self._reference_mode or row < 0 or row >= len(REFERENCE_SCRIPTS):
            return
        if self.editor.is_dirty():
            previous = (
                REFERENCE_SCRIPTS.index(self._current_script) if self._current_script in REFERENCE_SCRIPTS else -1
            )
            self.library.blockSignals(True)
            self.library.setCurrentRow(previous)
            self.library.blockSignals(False)
            self.owner.inline.notice(
                "Unsaved script draft", "Discard the draft explicitly before selecting another script.", "warning"
            )
            return
        self._set_script(REFERENCE_SCRIPTS[row])
        if self._current_script[0].startswith("Hires"):
            self.form.set_fields([])
            self.form_note.setText("This script asks for its settings when it runs.")
            self.form_note.show()
        else:
            self._load_reference_form()
        for panel in self.inspector_panels:
            panel.show()
        self.inspect_banner.show()
        self.inspect_card.show()
        self.show_view(0)

    def _set_inspect_banner(self, title: str, description: str, state: str) -> None:
        """Keep the inspection title and surface in the same semantic state.

        Qt does not reliably restyle children when only a parent dynamic
        property changes, so the title owns its state and is repolished.
        """
        if state not in ("ok", "warning", "error"):
            raise ValueError(f"Unsupported inspection feedback state: {state}")
        self.banner_title.setText(title)
        self.banner_desc.setText(description)
        for widget in (self.inspect_banner, self.banner_title):
            widget.setProperty("feedbackState", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.banner_title.update()

    def _set_script(self, script):
        self._current_script = script
        filename, sha, tier, support = script
        for button in self.tab_buttons:
            button.setEnabled(True)
        self._current_hooks = (
            tuple(REFERENCE_HOOKS[index] for index in (0, 3, 2)) if filename.startswith("Hires") else REFERENCE_HOOKS
        )
        for form in (self.inspector, self.drawer_inspector):
            for name in ("settingsCard", "sourceCard", "historyCard"):
                getattr(form, name).show()
        facts = {
            "Header": f"{filename} · by “example”",
            "Tier": tier,
            "Dynamic": "2 runtime-only paths" if filename.startswith("Hires") else "none",
            "Models": "z_image_turbo_1.0",
            "Loras": "none required",
            "Method": "Static parse · no generation, network or sign-in",
        }
        for name, label in self.inspection_values.items():
            label.setText(facts[name])
        self.script_title.setText(filename)
        self.script_title.setToolTip(filename)
        self.sha.setText(f"version {sha}")
        self.sha.setToolTip(f"SHA prefix: {sha}; exact identity requires a saved source.")
        metadata = {
            "File": filename,
            "Version SHA-256": f"{sha}…",
            "Size": "3.8 KB" if filename.startswith("Hires") else "6.2 KB",
            "Imported": "19 Oct 2026 09:12" if filename.startswith("Hires") else "21 Oct 2026 21:54",
        }
        for values in (self.inspector_source_values, self.drawer_source_values):
            for key, label in values.items():
                label.setText(metadata[key])
                label.setToolTip(metadata[key])
        self.status.setText(
            "● Syntax valid   ● API calls observed   ● No blocking issues found"
            if support == "supported"
            else "● Syntax valid   ● API calls observed   ● Not runnable · unsupported hooks"
        )
        partial = filename.startswith("Hires")
        if partial:
            self.status.setText("● Syntax valid   ● API calls observed   ● Preflight incomplete")
        self._saved_inspection = self.status.text()
        self.status.setProperty("feedbackState", "warning" if partial else "ok" if support == "supported" else "error")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        verdict = (
            "Preflight incomplete"
            if partial
            else "No blocking issues found"
            if support == "supported"
            else "Not runnable · unsupported hooks"
        )
        self.hook_status.setText(verdict)
        self.inspect_summary.setText(
            "Static inspection is incomplete: two paths are known only at run time. No script was executed."
            if partial
            else "Static inspection identified supported calls. Dynamic forms can appear at run time."
            if support == "supported"
            else "Cannot start: unsupported native picker hooks."
        )
        self.source_note.setText(
            "Required by this script: choose a source to Start; changing it needs a new run."
            if partial
            else "Optional. An immutable copy is taken at Start; changing it needs a new run."
        )
        self.source_note.setProperty("feedbackState", "error" if partial else "")
        self.source_note.style().unpolish(self.source_note)
        self.source_note.style().polish(self.source_note)
        self._set_inspect_banner(
            verdict,
            "Syntax valid, 3 hooks supported, 1 deferred. This is not a promise of success."
            if support == "supported"
            else "Required photo/file picker hooks are unsupported.",
            "warning" if partial else "ok" if support == "supported" else "error",
        )
        self.drawer_summary.setText(self.hook_status.text())
        while self.hook_list.count():
            item = self.hook_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # deleteLater() alone leaves visible orphans until Qt handles
                # DeferredDelete. Hide and detach before rebuilding a live page.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for hook, state, detail in self._current_hooks:
            self.hook_list.addWidget(_hook_item(hook, state, detail))
        self._sync_drawer_data()

    def clear(self):
        self._reference_mode = False
        self._current_script = None
        self.library.clear()
        self.script_title.setText("No script selected")
        self.sha.clear()
        self.status.setText("Import a JavaScript file or create a draft")
        self.inspect_summary.setText("Nothing has been inspected. Scripts do not run during inspection.")
        self.form.set_fields([])
        self.form_note.show()
        self.reset_fields.setEnabled(False)
        self.inspect_banner.hide()
        self.inspect_card.hide()
        self.hook_status.setText("No script inspected")
        for panel in self.inspector_panels[1:]:
            panel.hide()
        for name in ("settingsCard", "sourceCard", "historyCard"):
            getattr(self.drawer_inspector, name).hide()
        self.show_view(5)

    def new_draft(self):
        if self.editor.is_dirty():
            self._confirm_discard(self.new_button, self._new_draft)
            return
        self._new_draft()

    def _new_draft(self):
        """Open an unsaved draft without inherited compatibility or run evidence."""
        self._current_script = None
        self.library.blockSignals(True)
        self.library.setCurrentRow(-1)
        self.library.blockSignals(False)
        self.script_title.setText("Untitled.js · new draft")
        self.sha.clear()
        self.status.setText("Draft not inspected")
        self.status.setProperty("feedbackState", "")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.hook_status.setText("Select or import a script to see compatibility, source and history.")
        self.drawer_summary.setText("New draft · not inspected")
        for index in range(self.hook_list.count()):
            item = self.hook_list.itemAt(index)
            if item is not None and item.widget() is not None:
                item.widget().hide()
        for panel in self.inspector_panels[1:]:
            panel.hide()
        for name in ("settingsCard", "sourceCard", "historyCard"):
            getattr(self.drawer_inspector, name).hide()
        for child in self.drawer_inspector.hooksHost.findChildren(QFrame, "scriptsHookItem"):
            child.hide()
        self.editor.set_draft(EDITOR_STARTER, status="New draft · unsaved")
        self.show_view(2)
        self.sha.hide()
        self.status.hide()
        self.start_panel.hide()
        for button in self.tab_buttons[:2]:
            button.setEnabled(False)

    def import_preview(self):
        if self.editor.is_dirty():
            self._confirm_discard(self.import_button, self._import_preview)
            return
        self._import_preview()

    def _import_preview(self):
        filename, _ = QFileDialog.getOpenFileName(
            self.owner.form, "Open JavaScript preview", "", "JavaScript (*.js);;All files (*)"
        )
        if not filename:
            return
        path = Path(filename)
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("File is larger than the 1 MiB UI preview limit.")
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            self.status.setText(f"Import unavailable · {type(exc).__name__}")
            return
        sha = hashlib.sha256(contents.encode("utf-8")).hexdigest()
        self._new_draft()
        self._reference_mode = False
        self.library.addItem(f"{path.name}\n{sha[:7]} · local preview · not saved")
        self.script_title.setText(path.name)
        self.sha.setText(f"SHA-256 {sha[:7]}")
        self.sha.setToolTip(sha)
        self.status.setText("Source loaded for editing · compatibility not inspected")
        self.editor.set_draft(contents, status="Imported in memory · unsaved")
        self.show_view(2)

    def _load_reference_form(self):
        """Load explicitly illustrative fields for the offline screenshot harness."""
        self.form.set_fields(REFERENCE_FORM)
        self.form_note.hide()
        self.reset_fields.setEnabled(True)

    def _reset_fields(self):
        if self._reference_mode:
            self._load_reference_form()

    def show_view(self, index: int):
        self.views.setCurrentIndex(index)
        empty = index == 5
        configuring = index <= 2
        running_or_results = index in (3, 4)
        self.script_title.setVisible(not empty)
        self.sha.setVisible(not empty and configuring)
        self.status.setVisible(not empty and configuring)
        self.run_identity.setVisible(running_or_results)
        self.start_panel.setVisible(index in (0, 1))
        if self._current_script is not None:
            saved = getattr(self, "_saved_inspection", self.status.text())
            self.status.setText(f"Saved version {self._current_script[1]}: {saved}" if index == 2 else saved)
        self.run_footer.setVisible(running_or_results)
        for position, button in enumerate(self.tab_buttons):
            button.setChecked(index == position)
            button.setVisible(not empty and configuring)
        self.start.setVisible(configuring)
        self.cap.setEnabled(configuring)

    def preview(self, state: str):
        """Populate an explicitly offline screenshot state; never executes a script."""
        self.active_state = state
        self._reference_mode = True
        # One native window is reused across all screenshots. Restore every
        # mutable fixture choice so screenshots are independent of render order.
        self.source.setCurrentIndex(0)
        self.source_note.setProperty("feedbackState", "")
        self.source_note.style().unpolish(self.source_note)
        self.source_note.style().polish(self.source_note)
        self.source_note.setText("Optional. An immutable copy is taken at Start; changing it requires a new run.")
        self.start.setText("Start script")
        self.run_footer.setText(
            "Stop is in the shared activity bar. It ends local waiting; an already submitted generation may still complete."
        )
        self.unresolved_notice.hide()
        self.form_note.setText(
            "No form controls were discovered. Dynamic requestFromUser inputs appear only during a run."
        )
        self.library_note.setText("Local files only. Each version is copied and kept by SHA-256 (3 of 50 stored).")
        self.status.setProperty("feedbackState", "")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.library.blockSignals(True)
        self.library.clear()
        if state != "scripts_empty":
            for filename, sha, tier, support in REFERENCE_SCRIPTS:
                item = QListWidgetItem(f"{filename}\n{sha} · {tier} · {support}")
                self.library.addItem(item)
        row = (
            2
            if state == "scripts_imported_unsupported"
            else 1
            if state in ("scripts_inspect_partial", "scripts_narrow_details")
            else 0
        )
        self.library.setCurrentRow(row)
        self.library.blockSignals(False)
        if state == "scripts_empty":
            self.clear()
            self._reference_mode = True
            self.library_note.setText("Local files only · 0 of 50 versions stored.")
            return
        self._set_script(REFERENCE_SCRIPTS[row])
        for panel in self.inspector_panels:
            panel.show()
        self.inspect_banner.show()
        self.inspect_card.show()
        self._load_reference_form()
        self.handoff.hide()
        self.acknowledge.hide()
        self.waiting_form.hide()
        self.run_status.setText("No run started")
        self.run_detail.clear()
        self.result_caption.setText("No committed output yet")
        self.result_image.setText("No committed result")
        self.result_caption.setText("No committed output yet")
        self.console.setPlainText("[offline fixture] No provider was called.")
        if state.startswith("scripts_form") or state == "scripts_from_inpaint" or state.startswith("scripts_narrow"):
            self.show_view(1)
            if state == "scripts_from_inpaint":
                self.source.setCurrentText("Inpaint canvas")
                self.handoff.show()
            if state == "scripts_narrow_details":
                self.form.set_fields([])
                self.form_note.setText("This script asks for its settings when it runs.")
                self.form_note.show()
                self.source_note.setText(
                    "Required by this script: choose a source to Start; changing it needs a new run."
                )
                self.source_note.setProperty("feedbackState", "error")
                self.source_note.style().unpolish(self.source_note)
                self.source_note.style().polish(self.source_note)
                self.status.setText("● Syntax valid   ● API calls observed   ● Preflight incomplete")
                self.status.setProperty("feedbackState", "warning")
                self.status.style().unpolish(self.status)
                self.status.style().polish(self.status)
                self.hook_status.setText("Preflight incomplete")
            if state == "scripts_form_after_unknown":
                # Compatibility remains supported. A *separate* amber card
                # describes the unsettled previous run, without claiming a retry.
                self.unresolved_notice.show()
                self.start.setText("Start new run")
            if state == "scripts_form_long_values":
                fields = [dict(item) for item in REFERENCE_FORM]
                for item in fields:
                    if item.get("label") == "Wildcards":
                        item["label"] = "🎨 Wildcards to sweep through the positive prompt, comma separated, no limit"
                        item["value"] = "red velvet curtain, emerald moss, blue hour sky, amber light, rose quartz"
                    elif item.get("label") == "Export folder":
                        item["value"] = (
                            "C:\\Users\\me\\Pictures\\DrawThings\\Exports\\2026\\October\\wildcard-sweeps\\landscape-series-with-a-very-long-folder-name"
                        )
                self.form.set_fields(fields)
        elif state.startswith("scripts_editor"):
            self.show_view(2)
            self.editor.run_notice.hide()
            if state == "scripts_editor_new":
                self._new_draft()
                self.editor.set_draft(EDITOR_STARTER, status="New draft · unsaved")
            else:
                self.editor.set_draft(EDITOR_BASE, status="Version 9f3a1c2")
                # A draft belongs to a new version; the saved source stays intact.
                self.editor.editor.setPlainText(EDITOR_BASE + "\n// Draft change")
                if state == "scripts_editor_dirty":
                    self.editor.find_row.show()
                    self.editor.search.setText("pipeline")
                if state == "scripts_editor_syntax_error":
                    invalid = EDITOR_BASE + "\nconst value = ;"
                    self.editor.editor.setPlainText(invalid)
                    self.editor.set_error(15, 15, "Unexpected token ';'")
                elif state == "scripts_editor_run_active":
                    self.editor.run_notice.setText("Run #5 is active on version 9f3a1c2. Changes create a new version.")
                    self.editor.run_notice.show()
        elif state == "scripts_results":
            self.show_view(4)
            self.run_identity.setText("Run #4 · version 9f3a1c2 · completed 22 Oct 20:41")
            self._populate_results()
        elif state.removeprefix("scripts_") in RUN_STATUSES:
            self.show_view(3)
            self.run_identity.setText("Run #5 · version 9f3a1c2 · started 22 Oct 21:12")
            key = state.removeprefix("scripts_")
            if key in ("queued", "cancelled_before_start"):
                self.run_identity.setText("Run #5 · version 9f3a1c2 · queued 22 Oct 21:12")
                self.run_footer.setText(
                    "Cancel before admission is free. The cancelled run stays in History; nothing was submitted."
                )
            status, detail = RUN_STATUSES[key]
            self.run_status.setText(status)
            self.run_detail.setText(detail)
            if key in ("running", "waiting_input", "stopped", "failed", "interrupted", "outcome_unknown"):
                self.result_image.setText("generation 2 · green")
                self.result_caption.setText("Latest committed · generation 2 · 512 × 512 · seed 4182")
            if key == "waiting_input":
                self.waiting_form.show()
            self.console.setPlainText(
                "[0:00.0] Script started · version 9f3a1c2\n"
                "[0:02.1] Step 1 · red · committed\n"
                "[0:11.8] Step 2 · green · committed\n"
                "[offline fixture] No provider was called"
            )
            if key == "queued":
                self.console.setPlainText("[0:00.0] Waiting for admission behind 3 items. Nothing submitted.")
            elif key == "cancelled_before_start":
                self.console.setPlainText("[0:42.0] Cancelled before admission. Nothing submitted or charged.")
        else:
            self.show_view(0)
            if state == "scripts_inspect_partial":
                self.status.setText("● Syntax valid   ● API calls observed   ● Preflight incomplete")
                self.status.setProperty("feedbackState", "warning")
                self.status.style().unpolish(self.status)
                self.status.style().polish(self.status)
                self._set_inspect_banner(
                    "Preflight incomplete",
                    "Syntax valid, 2 hooks supported, 1 deferred. Two paths remain runtime-only.",
                    "warning",
                )
                self.hook_status.setText("Preflight incomplete")
                self.acknowledge.show()
            elif state == "scripts_imported_unsupported":
                self.inspect_summary.setText("Cannot start: unsupported native picker hooks.")
            else:
                self.inspect_summary.setText(
                    "Static inspection identified supported calls. Dynamic forms can appear at run time."
                )
        self.cap.setValue(8)
        self.start.setEnabled(False)
        self.drawer_summary.setText(self.hook_status.text() + "\nNo provider was called.")

    @staticmethod
    def _source_values(form):
        return {
            key: getattr(form, name)
            for key, name in (
                ("File", "sourceFileValue"),
                ("Version SHA-256", "sourceShaValue"),
                ("Size", "sourceSizeValue"),
                ("Imported", "sourceImportedValue"),
            )
        }

    def _sync_drawer_data(self):
        layout = self.drawer_inspector.hooksHost.layout()
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        for hook, state, detail in self._current_hooks:
            layout.addWidget(_hook_item(hook, state, detail))
        entries = (
            "#6 · Cancelled before start · 0:42 waited\n22 Oct 21:30 · 0 generations",
            "#4 · Succeeded · 4 generations\n22 Oct 20:41 · version 9f3a1c2",
            "#3 · Stopped locally · 2 generations\n22 Oct 19:05 · version 9f3a1c2",
            "#2 · Failed\n21 Oct 22:13 · version b81e07d",
            "#1 · Outcome unknown\n21 Oct 21:58 · version b81e07d",
        )
        for history in (self.history, self.drawer_inspector.scriptsHistoryList):
            history.clear()
            history.addItems(entries)
            for index in range(history.count()):
                history.item(index).setToolTip(history.item(index).text())

    def _confirm_discard(self, button, callback):
        if self.owner.inline.commit(button):
            return
        self.owner.inline.arm(
            button,
            "Discard unsaved script changes?",
            "The in-memory draft cannot be recovered after replacement. Nothing is run or submitted.",
            callback,
            danger=True,
            confirm_text="Discard",
        )

    def _source_changed(self, index):
        self.handoff.setVisible(index == 3)

    def _detach_source(self):
        self.source.setCurrentIndex(0)

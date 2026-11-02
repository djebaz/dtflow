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
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

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

RUN_STATUSES = {
    "queued": ("Queued behind 3 items", "Nothing submitted. Waiting 0:42 · Cancel is free."),
    "cancelled_before_start": ("Cancelled before start", "No provider submission and no charge."),
    "running": ("Running · Run #5", "Generation 3 submitted · latest committed result below."),
    "waiting_input": ("Waiting for your input", "The script is paused. A generation already submitted may still complete."),
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
    layout.addWidget(heading)
    return frame, layout


def _scroll(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    return scroll


def _line(layout: QVBoxLayout, left: str, right: str) -> None:
    row = QWidget()
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_layout.addWidget(_label(left, role="scriptsMuted"), 0)
    value = _label(right)
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    value.setToolTip(right)
    row_layout.addWidget(value, 1)
    layout.addWidget(row)


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
        self._current_script: tuple[str, str, str, str] | None = None
        self._build_library()
        self._build_center()
        self._build_inspector()
        self._build_drawer()
        self.clear()

    def mount(self, shell) -> None:
        """Reuse the three established shell stacks; never add a second top-level tab."""
        self.panels = (self.left, self.center, self.right)
        for stack, panel in zip(
            (shell.leftStack, shell.centerStack, shell.rightStack), self.panels, strict=True
        ):
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
        layout = self.left.layout()
        layout.addWidget(_label("Script library", role="scriptsCardTitle"))
        self.library = QListWidget()
        self.library.setObjectName("scriptsLibraryList")
        self.library.setAccessibleName("Script library")
        self.library.currentRowChanged.connect(self._selected)
        layout.addWidget(self.library, 1)
        new = QPushButton("New script…")
        new.clicked.connect(self.new_draft)
        layout.addWidget(new)
        actions = QHBoxLayout()
        self.import_button = QPushButton("Import…")
        self.import_button.clicked.connect(self.import_preview)
        actions.addWidget(self.import_button)
        self.reload_button = QPushButton("Reload from disk")
        self.reload_button.setToolTip("Persistent library discovery is not connected in this UI slice.")
        self.reload_button.setEnabled(False)
        actions.addWidget(self.reload_button)
        layout.addLayout(actions)
        self.library_note = _label(
            "Preview mode: imported files stay in memory. Immutable version storage is not connected.",
            role="scriptsMuted", wrap=True
        )
        layout.addWidget(self.library_note)

    def _build_center(self):
        layout = self.center.layout()
        mode = QHBoxLayout()
        mode.setSpacing(0)
        self.mode_group = QButtonGroup(self.center)
        self.mode_group.setExclusive(True)
        for text, attr in (
            ("Text to image", "text_mode"),
            ("Inpaint", "inpaint_mode"),
            ("Scripts", "scripts_mode"),
        ):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setProperty("role", "mode")
            setattr(self, attr, button)
            self.mode_group.addButton(button)
            mode.addWidget(button)
        self.scripts_mode.setChecked(True)
        mode.addStretch(1)
        self.details_button = QPushButton("Details")
        self.details_button.setAccessibleName("Show Scripts inspector details")
        self.details_button.clicked.connect(self.toggle_details)
        mode.addWidget(self.details_button)
        layout.addLayout(mode)
        title = QHBoxLayout()
        self.script_title = _label("No script selected", role="scriptsPageTitle")
        title.addWidget(self.script_title, 1)
        self.sha = _label("", role="scriptsMuted")
        title.addWidget(self.sha)
        layout.addLayout(title)
        self.status = _label("Select or import a script", role="scriptsCompatibility", wrap=True)
        layout.addWidget(self.status)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tab_buttons: list[QPushButton] = []
        for name, index in (("Inspect", 0), ("Configure", 1), ("Code", 2)):
            tab = QPushButton(name)
            tab.setCheckable(True)
            tab.setProperty("role", "mode")
            tab.clicked.connect(lambda checked=False, target=index: self.show_view(target))
            self.tab_buttons.append(tab)
            tabs.addWidget(tab)
        tabs.addStretch(1)
        layout.addLayout(tabs)
        self.views = QStackedWidget()
        self.views.setObjectName("scriptsContentStack")
        self._build_inspect()
        self._build_form()
        self.editor = ScriptEditor()
        self.views.addWidget(self.editor)
        self._build_run()
        self._build_results()
        layout.addWidget(self.views, 1)
        footer, footer_layout = _card("")
        footer.setObjectName("scriptsStartPanel")
        footer_layout.itemAt(0).widget().hide()
        cap_row = QHBoxLayout()
        cap_row.addWidget(_label("Generation cap"))
        self.cap = QSpinBox()
        self.cap.setRange(1, 32)
        self.cap.setValue(8)
        self.cap.setAccessibleName("Generation cap")
        cap_row.addWidget(self.cap)
        cap_row.addStretch(1)
        self.start = QPushButton("Start script")
        self.start.setObjectName("scriptsStartButton")
        self.start.setMinimumWidth(120)
        self.start.setEnabled(False)
        self.start.setToolTip("Script dispatch is not connected to this UI view yet.")
        cap_row.addWidget(self.start)
        footer_layout.addLayout(cap_row)
        self.disclosure = _label(
            "Cloud: up to 8 generations. An estimate covers only the starting settings; later calls may differ. "
            "Backend: not checked. Source images may be uploaded. No work starts in this UI preview.",
            role="scriptsMuted", wrap=True
        )
        footer_layout.addWidget(self.disclosure)
        self.acknowledge = QCheckBox("I understand preflight is incomplete")
        self.acknowledge.hide()
        footer_layout.addWidget(self.acknowledge)
        layout.addWidget(footer)
        self.show_view(0)

    def _build_inspect(self):
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(10)
        self.inspect_banner = QFrame()
        self.inspect_banner.setObjectName("scriptsInspectBanner")
        self.inspect_banner.setProperty("feedbackState", "ok")
        banner_layout = QVBoxLayout(self.inspect_banner)
        banner_layout.setContentsMargins(16, 12, 16, 12)
        self.banner_title = _label("No blocking issues found", role="scriptsBannerTitle")
        banner_layout.addWidget(self.banner_title)
        self.banner_desc = _label(
            "Syntax valid, 3 hooks supported, 1 deferred. Inspection is not a promise of success.",
            role="scriptsMuted", wrap=True
        )
        banner_layout.addWidget(self.banner_desc)
        outer.addWidget(self.inspect_banner)
        self.inspect_card, card = _card("Inspection")
        facts = QGridLayout()
        facts.setHorizontalSpacing(14)
        facts.setVerticalSpacing(6)
        for row, (name, value) in enumerate((
            ("Header", "Wildcard prompt sweep.js · by “example”"),
            ("Tier", "Tier 1"),
            ("Dynamic paths", "none"),
            ("Models", "z_image_turbo_1.0"),
            ("LoRAs", "none required"),
            ("Inspected with", "Static parse · no generation, network or sign-in"),
        )):
            label = _label(name, role="scriptsMuted")
            label.setAlignment(Qt.AlignmentFlag.AlignTop)
            facts.addWidget(label, row, 0)
            current = _label(value, wrap=True)
            current.setToolTip(value)
            facts.addWidget(current, row, 1)
        card.addLayout(facts)
        outer.addWidget(self.inspect_card)
        self.inspect_summary = _label(
            "Inspection is static: nothing is executed. Configure shows known inputs; "
            "a dynamically built form is requested only when its run asks.",
            role="scriptsMuted", wrap=True
        )
        outer.addWidget(self.inspect_summary)
        actions = QHBoxLayout()
        self.configure_from_inspect = QPushButton("Configure…")
        self.configure_from_inspect.clicked.connect(lambda: self.show_view(1))
        actions.addWidget(self.configure_from_inspect)
        edit = QPushButton("Edit code")
        edit.clicked.connect(lambda: self.show_view(2))
        actions.addWidget(edit)
        actions.addStretch(1)
        outer.addLayout(actions)
        outer.addStretch(1)
        self.views.addWidget(_scroll(body))

    def _build_form(self):
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(8)
        note = QHBoxLayout()
        note.addWidget(_label("Answers belong to this script version.", role="scriptsMuted"), 1)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._load_reference_form)
        note.addWidget(reset)
        outer.addLayout(note)
        source_title = _label("Source", role="scriptsFormSection")
        outer.addWidget(source_title)
        source = QHBoxLayout()
        source.addWidget(_label("Source image", role="scriptsMuted"))
        self.source = QComboBox()
        self.source.setAccessibleName("Source image")
        self.source.addItems(("No source", "Images gallery…", "Local file…", "Inpaint canvas"))
        source.addWidget(self.source, 1)
        outer.addLayout(source)
        self.source_note = _label(
            "Optional. An immutable copy is taken at Start; changing it requires a new run.",
            role="scriptsMuted", wrap=True
        )
        outer.addWidget(self.source_note)
        self.handoff = _label("Image + mask snapshot · 512 × 512 · Detach", role="scriptsMuted")
        self.handoff.hide()
        outer.addWidget(self.handoff)
        self.form = ScriptForm()
        outer.addWidget(self.form, 1)
        self.views.addWidget(_scroll(body))

    def _build_run(self):
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(8)
        self.run_title = _label("Run #5 · version 9f3a1c2", role="scriptsPageTitle")
        outer.addWidget(self.run_title)
        self.run_status = _label("No run started", role="scriptsCompatibility", wrap=True)
        outer.addWidget(self.run_status)
        self.run_detail = _label("", role="scriptsMuted", wrap=True)
        outer.addWidget(self.run_detail)
        preview, pv = _card("Latest committed result")
        self.result_image = _label("No committed result", role="scriptsCanvasPreview")
        self.result_image.setMinimumHeight(185)
        self.result_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv.addWidget(self.result_image)
        outer.addWidget(preview)
        self.waiting_form, wait = _card("Wildcards for the next pass")
        wait.addWidget(_label("Provide the next values to continue this script.", wrap=True))
        field = QLineEdit("amber, rose")
        field.setAccessibleName("Next wildcard values")
        wait.addWidget(field)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel input")
        cancel.setToolTip("Answer without a value. The script continues unless it chooses to stop.")
        cancel.setEnabled(False)
        buttons.addWidget(cancel)
        proceed = QPushButton("Continue")
        proceed.setEnabled(False)
        buttons.addWidget(proceed)
        wait.addLayout(buttons)
        outer.addWidget(self.waiting_form)
        self.waiting_form.hide()
        console, log_layout = _card("Console")
        from PySide6.QtWidgets import QPlainTextEdit
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(160)
        self.console.setObjectName("scriptsRunConsole")
        self.console.setPlainText("[UI preview] No script was executed.")
        log_layout.addWidget(self.console)
        outer.addWidget(console)
        outer.addStretch(1)
        self.views.addWidget(_scroll(body))

    def _build_results(self):
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.addWidget(_label("Run #4 · Succeeded · version 9f3a1c2", role="scriptsPageTitle"))
        outer.addWidget(_label("Ordered child attempts · saved outputs remain in Images", role="scriptsMuted"))
        self.results = QListWidget()
        self.results.setObjectName("scriptsResultsList")
        outer.addWidget(self.results)
        self.open_results = QPushButton("Open selected step in Images")
        self.open_results.setEnabled(False)
        outer.addWidget(self.open_results)
        self.views.addWidget(body)

    def _build_inspector(self):
        layout = self.right.layout()
        self.inspect_panels: list[QFrame] = []
        hooks, h = _card("Compatibility")
        self.hook_status = _label("No script inspected", wrap=True)
        h.addWidget(self.hook_status)
        self.hook_list = QVBoxLayout()
        h.addLayout(self.hook_list)
        self.inspect_panels.append(hooks)
        layout.addWidget(hooks)
        settings, s = _card("Starting settings")
        for key, value in (
            ("Checkpoint", "z_image_turbo_1.0_q6p"), ("Size", "320 × 192"),
            ("Steps", "4"), ("Sampler", "euler_a"), ("Seed", "0")
        ):
            _line(s, key, value)
        s.addWidget(_label(
            "Read-only, inherited from Text to image. A script may override values per generation.",
            role="scriptsMuted", wrap=True
        ))
        layout.addWidget(settings)
        source, section = _card("Source")
        for key, value in (("File", "Wildcard prompt sweep.js"), ("Version SHA-256", "9f3a1c2e4b7d08…"),
                           ("Size", "6.2 KB"), ("Imported", "21 Oct 2026 21:54")):
            _line(section, key, value)
        layout.addWidget(source)
        history, h = _card("History")
        self.history = QListWidget()
        self.history.setObjectName("scriptsHistoryList")
        for text in (
            "#6 · Cancelled before start · 0:42",
            "#4 · Succeeded · 4 generations",
            "#3 · Stopped locally · 2 generations",
            "#2 · Failed", "#1 · Outcome unknown"
        ):
            self.history.addItem(text)
        h.addWidget(self.history)
        layout.addWidget(history)
        layout.addStretch(1)

    def _build_drawer(self):
        # The overlay is native Qt, not a relocated QSplitter child.
        self.drawer = QFrame(self.owner.form)
        self.drawer.setObjectName("scriptsDetailsDrawer")
        self.drawer.setProperty("role", "sectionCard")
        panel = QVBoxLayout(self.drawer)
        close_row = QHBoxLayout()
        close_row.addWidget(_label("Details", role="scriptsCardTitle"), 1)
        close = QPushButton("Close")
        close.clicked.connect(self.toggle_details)
        close_row.addWidget(close)
        panel.addLayout(close_row)
        self.drawer_summary = _label("", wrap=True)
        panel.addWidget(self.drawer_summary)
        panel.addStretch(1)
        self.drawer.hide()

    def toggle_details(self):
        self._drawer_requested = not self._drawer_requested
        self.sync_responsive()

    def sync_responsive(self):
        narrow = self.owner.form.width() < 1100
        self.form.set_narrow(narrow)
        self.details_button.setVisible(narrow)
        if narrow:
            self.owner.shell.rightRail.hide()
            if self._drawer_requested and self.owner._scripts_active:
                self.drawer.setGeometry(
                    max(0, self.owner.form.width() - 310), 55,
                    min(300, self.owner.form.width()), max(300, self.owner.form.height() - 90)
                )
                self.drawer.raise_()
                self.drawer.show()
            else:
                self.drawer.hide()
        else:
            self.drawer.hide()
            if self.owner._scripts_active:
                self.owner.shell.rightRail.show()

    def _selected(self, row: int):
        if not self._reference_mode or row < 0 or row >= len(REFERENCE_SCRIPTS):
            return
        self._set_script(REFERENCE_SCRIPTS[row])

    def _set_script(self, script):
        self._current_script = script
        filename, sha, tier, support = script
        self.script_title.setText(filename)
        self.script_title.setToolTip(filename)
        self.sha.setText(f"version {sha}")
        self.sha.setToolTip(f"SHA prefix: {sha}; exact identity requires a saved source.")
        self.status.setText(
            "● Syntax valid   ● API calls observed   ● No blocking issues found"
            if support == "supported" else
            "● Syntax valid   ● API calls observed   ● Not runnable · unsupported hooks"
        )
        verdict = "No blocking issues found" if support == "supported" else "Not runnable · unsupported hooks"
        self.hook_status.setText(verdict)
        self.banner_title.setText(verdict)
        self.banner_desc.setText(
            "Syntax valid, 3 hooks supported, 1 deferred. This is not a promise of success."
            if support == "supported" else "Required photo/file picker hooks are unsupported."
        )
        self.inspect_banner.setProperty("feedbackState", "ok" if support == "supported" else "error")
        self.inspect_banner.style().unpolish(self.inspect_banner)
        self.inspect_banner.style().polish(self.inspect_banner)
        self.drawer_summary.setText(
            self.hook_status.text() + "\n" +
            "\n".join(f"{hook} · {status}" for hook, status, _ in REFERENCE_HOOKS)
        )
        while self.hook_list.count():
            item = self.hook_list.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for hook, state, detail in REFERENCE_HOOKS:
            name = _label(f"{hook} · {state}\n{detail}", role="scriptsHookRow", wrap=True)
            name.setToolTip(detail)
            self.hook_list.addWidget(name)

    def clear(self):
        self._reference_mode = False
        self.library.clear()
        self.script_title.setText("No script selected")
        self.sha.clear()
        self.status.setText("Import a JavaScript file or create a draft")
        self.inspect_summary.setText("Nothing has been inspected. Scripts do not run during inspection.")
        self.inspect_banner.hide()
        self.inspect_card.hide()
        self.hook_status.setText("No script inspected")
        self.show_view(0)

    def new_draft(self):
        self.script_title.setText("Untitled script")
        self.sha.setText("New draft")
        self.status.setText("Unsaved source · no compatibility check")
        self.editor.set_draft("//@api-1.0\n\n", status="New draft · unsaved")
        self.show_view(2)

    def import_preview(self):
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
        self._reference_mode = False
        self.library.addItem(f"{path.name}\n{sha[:7]} · local preview · not saved")
        self.script_title.setText(path.name)
        self.sha.setText(f"SHA-256 {sha[:7]}")
        self.sha.setToolTip(sha)
        self.status.setText("Source loaded for editing · compatibility not inspected")
        self.editor.set_draft(contents, status="Imported in memory · unsaved")
        self.show_view(2)

    def _load_reference_form(self):
        self.form.set_fields(REFERENCE_FORM)

    def show_view(self, index: int):
        self.views.setCurrentIndex(index)
        for position, button in enumerate(self.tab_buttons):
            button.setChecked(index == position)
        self.status.setVisible(index <= 2)
        self.start.setVisible(index <= 2)
        self.cap.setEnabled(index <= 2)

    def preview(self, state: str):
        """Populate an explicitly offline screenshot state; never executes a script."""
        self.active_state = state
        self._reference_mode = True
        self.library.blockSignals(True)
        self.library.clear()
        if state != "scripts_empty" and state != "scripts_editor_new":
            for filename, sha, tier, support in REFERENCE_SCRIPTS:
                item = QListWidgetItem(f"{filename}\n{sha} · {tier} · {support}")
                self.library.addItem(item)
        row = 2 if state == "scripts_imported_unsupported" else 1 if state == "scripts_inspect_partial" else 0
        self.library.setCurrentRow(row)
        self.library.blockSignals(False)
        if state == "scripts_empty":
            self.script_title.setText("No script selected")
            self.status.setText("Import a script to inspect its API calls")
            self.show_view(0)
            return
        self._set_script(REFERENCE_SCRIPTS[row])
        self.inspect_banner.show()
        self.inspect_card.show()
        self._load_reference_form()
        self.handoff.hide()
        self.acknowledge.hide()
        self.waiting_form.hide()
        self.result_image.setText("No committed result")
        self.console.setPlainText("[offline fixture] No provider was called.")
        if state.startswith("scripts_form") or state == "scripts_from_inpaint" or state.startswith("scripts_narrow"):
            self.show_view(1)
            if state == "scripts_from_inpaint":
                self.source.setCurrentText("Inpaint canvas")
                self.handoff.show()
            if state == "scripts_form_after_unknown":
                self.status.setText("Previous run #5 · outcome unknown · may be billed")
                self.start.setText("Start new run")
            if state == "scripts_form_long_values":
                fields = [dict(item) for item in REFERENCE_FORM]
                for item in fields:
                    if item.get("label") == "Wildcards":
                        item["label"] = "🎨 Wildcards to sweep through the positive prompt, comma separated, no limit"
                        item["value"] = "red velvet curtain, emerald moss, blue hour sky, amber light, rose quartz"
                    elif item.get("label") == "Export folder":
                        item["value"] = "C:\\Users\\me\\Pictures\\DrawThings\\Exports\\2026\\October\\wildcard-sweeps\\landscape-series-with-a-very-long-folder-name"
                self.form.set_fields(fields)
        elif state.startswith("scripts_editor"):
            self.show_view(2)
            self.editor.set_draft(
                "//@api-1.0\nconst config = pipeline.configuration;\n"
                "config.seed = -1;\npipeline.run({configuration: config});\n",
                status="Draft · unsaved changes"
            )
            self.editor.run_notice.hide()
            if state == "scripts_editor_new":
                self.new_draft()
            elif state == "scripts_editor_syntax_error":
                self.editor.set_error(4, 3, "Unexpected token ';'")
            elif state == "scripts_editor_run_active":
                self.editor.run_notice.setText(
                    "A run is active. Existing runs keep their saved version; edit a new draft."
                )
                self.editor.run_notice.show()
        elif state == "scripts_results":
            self.show_view(4)
            self.results.clear()
            for i, color in enumerate(("red", "green", "blue", "amber"), 1):
                self.results.addItem(f"Run #4 · step {i} · {color} · committed · Open in Images")
        elif state.removeprefix("scripts_") in RUN_STATUSES:
            self.show_view(3)
            key = state.removeprefix("scripts_")
            status, detail = RUN_STATUSES[key]
            self.run_status.setText(status)
            self.run_detail.setText(detail)
            if key in ("running", "waiting_input", "stopped", "failed", "interrupted", "outcome_unknown"):
                self.result_image.setText("Committed result · step 2 / 4")
            if key == "waiting_input":
                self.waiting_form.show()
            self.console.setPlainText(
                "[0:00.0] Script started · version 9f3a1c2\n"
                "[0:02.1] Step 1 · red · committed\n"
                "[0:11.8] Step 2 · green · committed\n"
                "[offline fixture] No provider was called"
            )
        else:
            self.show_view(0)
            if state == "scripts_inspect_partial":
                self.status.setText("● Syntax valid   ● API calls observed   ● Preflight incomplete")
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

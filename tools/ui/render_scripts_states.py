"""Render illustrative Scripts Qt states without importing or executing the scripting engine.

These screenshots exercise the Designer form and presentation only. No provider,
network, script execution, script-file mutations, or durable run is involved.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, Qt
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QListWidgetItem, QWidget

FORM = Path(__file__).resolve().parents[2] / "src/dtflow/ui/forms/scripts_workspace.ui"

STATES = (
    "scripts_empty", "scripts_imported_supported", "scripts_imported_unsupported",
    "scripts_inspect_partial", "scripts_form_default", "scripts_form_long_values",
    "scripts_running", "scripts_waiting_input", "scripts_queued",
    "scripts_cancelled_before_start", "scripts_stopped", "scripts_failed",
    "scripts_interrupted", "scripts_outcome_unknown", "scripts_results",
    "scripts_from_inpaint", "scripts_narrow", "scripts_narrow_details",
    "scripts_editor_new", "scripts_editor_dirty", "scripts_editor_syntax_error",
    "scripts_editor_run_active", "scripts_editor_narrow",
    "scripts_form_after_unknown",
)


def _load() -> QWidget:
    file = QFile(str(FORM))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"Could not read Scripts Designer form: {FORM}")
    try:
        form = QUiLoader().load(file)
    finally:
        file.close()
    if form is None:
        raise RuntimeError(f"Could not instantiate Scripts Designer form: {FORM}")
    return form


def _widget(root, name):
    value = root.findChild(QWidget, name)
    if value is None:
        raise RuntimeError(f"Scripts form missing {name}")
    return value


def _set(root, name, value):
    _widget(root, name).setText(value)


def _prepare(root, state):
    scripts = _widget(root, "scriptsLibraryList")
    if state != "scripts_empty":
        scripts.addItems(["Wildcard prompt sweep", "Hires fix loop", "Outpaint ring"])
        scripts.setCurrentRow(0)
        _set(root, "scriptsNameLabel", "Wildcard prompt sweep")
        _set(root, "scriptsIdentityLabel", "Version e2c7fa4 · illustrative fixture")
        _set(root, "scriptsStatusLabel", "Syntax valid · API calls observed · No blocking issues found")
        _set(root, "scriptsInspectDetails", "Selected script: three statically observed calls. Dynamic forms may appear only during a run.")
        _set(root, "scriptsHooksLabel", "existingConfiguration\ngenerateImage\nrequestFromUser")
        _widget(root, "scriptsHistoryList").addItem("Run #1 · completed · version e2c7fa4")
    if state == "scripts_imported_unsupported":
        _set(root, "scriptsNameLabel", "Outpaint ring")
        _set(root, "scriptsStatusLabel", "Not runnable · unsupported mask and canvas hooks")
    elif state == "scripts_inspect_partial":
        _set(root, "scriptsNameLabel", "Hires fix loop")
        _set(root, "scriptsStatusLabel", "Preflight incomplete · inspect runtime requirements")
    if state.startswith("scripts_form") or state == "scripts_from_inpaint":
        _widget(root, "scriptsContentStack").setCurrentIndex(1)
        _widget(root, "scriptsConfigureButton").setChecked(True)
        _widget(root, "scriptsInspectButton").setChecked(False)
        if state == "scripts_form_long_values":
            _set(root, "scriptsFormHeader", "Configure · very long input labels and values")
            _widget(root, "scriptsPromptInput").setText("Long fixture prompt with representative extended configuration values " * 3)
        if state == "scripts_form_after_unknown":
            _set(root, "scriptsStatusLabel", "Previous outcome unknown · a new run may be billed")
            _set(root, "scriptsStartButton", "Start new run")
        if state == "scripts_from_inpaint":
            _set(root, "scriptsFormHeader", "Configure · source from Inpaint canvas (immutable at Start)")
    if state.startswith("scripts_editor"):
        _widget(root, "scriptsContentStack").setCurrentIndex(2)
        _widget(root, "scriptsCodeButton").setChecked(True)
        _widget(root, "scriptsInspectButton").setChecked(False)
        _widget(root, "scriptsCodeEditor").setPlainText(
            "//@api-1.0\nconst config = pipeline.configuration;\n"
            "config.seed = -1;\npipeline.run({configuration: config});\n"
        )
        descriptions = {
            "scripts_editor_new": "New draft · unsaved",
            "scripts_editor_dirty": "Draft · unsaved changes",
            "scripts_editor_syntax_error": "Syntax error · line 4, column 3",
            "scripts_editor_run_active": "Run #1 active · editing creates a new version",
            "scripts_editor_narrow": "Draft · narrow editor",
        }
        _set(root, "scriptsEditorStatus", descriptions[state])
    if state in ("scripts_running", "scripts_waiting_input", "scripts_queued",
                 "scripts_cancelled_before_start", "scripts_stopped", "scripts_failed",
                 "scripts_interrupted", "scripts_outcome_unknown"):
        _widget(root, "scriptsContentStack").setCurrentIndex(3)
        statuses = {
            "scripts_running": "Running · Run #1 · step 2",
            "scripts_waiting_input": "Waiting for your input · previously submitted work may still complete",
            "scripts_queued": "Queued behind 3 items · nothing submitted",
            "scripts_cancelled_before_start": "Cancelled before start · no submission",
            "scripts_stopped": "Stopped locally · remote status not inferred",
            "scripts_failed": "Failed · review the durable attempt",
            "scripts_interrupted": "Interrupted · do not replay unresolved attempts",
            "scripts_outcome_unknown": "Outcome unknown · submitted work may be billed",
        }
        _set(root, "scriptsRunState", statuses[state])
        _set(root, "scriptsStatusLabel", "Run #1 · version e2c7fa4")
        _widget(root, "scriptsConsole").setPlainText(
            "[offline fixture] script started\n[offline fixture] result presentation only\n"
            "[offline fixture] no backend call"
        )
    if state == "scripts_results":
        _widget(root, "scriptsContentStack").setCurrentIndex(4)
        _widget(root, "scriptsAttemptsList").addItems([
            "Run #1 · completed", "Step 1 · attempt a266fb20 · final saved",
            "Step 2 · attempt 47170a57 · final saved",
        ])
    if state in ("scripts_narrow", "scripts_narrow_details", "scripts_editor_narrow"):
        root.resize(800, 680)
        _widget(root, "scriptsInspectorPanel").setVisible(state == "scripts_narrow_details")
    else:
        root.resize(1440, 900)


def render_scripts_states(out: Path, *, window_factory, state_names=STATES) -> list[Path]:
    """Capture Scripts panels mounted in the real DTFlow shell, not a fake top-level window.

    These remain *incomplete* layout fixtures until compared with all owner screenshots.
    No script or generation work is executed by this function.
    """
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("Create QApplication and apply the shared theme first.")
    out.mkdir(parents=True, exist_ok=True)
    window = window_factory()
    results = []
    try:
        window.form.resize(1600, 1000)
        window.show()
        window.form.workspaceStack.setCurrentWidget(window.generation_page)
        app.processEvents()
        rails = (
            (window.shell.leftStack, "scriptsLibraryPanel"),
            (window.shell.centerStack, "scriptsWorkPanel"),
            (window.shell.rightStack, "scriptsInspectorPanel"),
        )
        for state in state_names:
            if state not in STATES:
                raise ValueError(f"Unknown scripts render state: {state}")
            root = _load()
            widgets = []
            try:
                _prepare(root, state)
                for stack, name in rails:
                    panel = _widget(root, name)
                    panel.setParent(None)
                    stack.addWidget(panel)
                    stack.setCurrentWidget(panel)
                    widgets.append((stack, panel))
                narrow = state in ("scripts_narrow", "scripts_narrow_details", "scripts_editor_narrow")
                window.form.resize(800, 680) if narrow else window.form.resize(1600, 1000)
                window.shell.workspaceSplitter.setSizes([300, 700, 300])
                if narrow and state != "scripts_narrow_details":
                    window.shell.rightRail.hide()
                else:
                    window.shell.rightRail.show()
                # Screens with shared shell chrome are the actual Qt evidence; the
                # app's existing connection and activity fixtures remain isolated.
                app.processEvents()
                path = out / f"{state}.png"
                if not window.form.grab().save(str(path)):
                    raise RuntimeError(f"Failed to render {path.name}")
                results.append(path)
            finally:
                for stack, panel in reversed(widgets):
                    stack.setCurrentIndex(window.form.workspaceStack.currentIndex())
                    stack.removeWidget(panel)
                    panel.setParent(None)
                root.deleteLater()
                app.processEvents()
        return results
    finally:
        window.runner.shutdown()
        window.form.close()
        app.processEvents()

"""Workflows mockups in the real shell with the accepted POC canvas, no execution."""

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from dtflow.ui.workflows_preview import WorkflowsPreview

ROOT = Path(__file__).resolve().parents[2]
STATES = ("workflows-01-editing", "workflows-02-running", "workflows-03-branch-paused", "workflows-04-narrow")


def render_workflows_states(out, *, window_factory, state_names, capture):
    # This is a fixture-only reuse of the isolated POC, never a src -> tools import.
    sys.path.insert(0, str(ROOT / "tools/node_editor_poc"))
    import nodeeditor_app as poc
    import poc_contract as contract
    import poc_shell
    import json

    owner = window_factory()
    canvas_owner = poc.NodeEditorWindow()  # hidden POC shell; only its canvas is mounted
    graph = contract.load_workflow(json.loads(poc_shell.FIXTURE.read_text(encoding="utf-8")))
    positions = {1: (40, 80), 2: (40, 360), 3: (340, 80), 4: (340, 360), 5: (640, 360)}
    for node in graph.nodes:
        node.pos = positions[node.id]
    canvas_owner.load_graph(graph)
    palette = {spec.title: spec.type for spec in contract.CATALOG.values()}
    preview = WorkflowsPreview(
        owner,
        canvas_owner.editor,
        canvas_owner.fit_view,
        lambda title: canvas_owner.add_node(palette[title]),
        poc_shell.fixture_thumbnail(),
    )
    poc.REJECT[0] = lambda message: owner.progress_strip.status("workflow-link", message, lambda: None)
    try:
        owner.form.resize(1600, 1000)
        owner.show()
        # Markers, not a dispatcher: all status and thumbnails below are fixtures.
        for name in state_names:
            state = "paused" if "paused" in name else "running" if "running" in name else "editing"
            # Cross the shared inspector breakpoint and cover the compact Images
            # utility host as well as the readable, selected-node canvas.
            owner.form.resize(1040 if name.endswith("narrow") else 1600, 900 if name.endswith("narrow") else 1000)
            preview.workspace.workflowGestureHint.setText(
                "Drag to pan · Wheel to zoom · F to fit"
                if name.endswith("narrow")
                else "Drag background to pan · Wheel to zoom · Shift-drag to select"
            )
            preview.present(state)
            mask = state == "paused"
            node = canvas_owner._node(2 if mask else 3)
            canvas_owner.scene.grScene.clearSelection()
            node.grNode.setSelected(True)
            preview.show_node(
                node.SPEC.title,
                "Node 2 · IMAGE → MASK" if mask else "Node 3 · IMAGE + MASK → IMAGE",
                node.params,
                mask=mask,
            )
            for canvas_node in canvas_owner.scene.nodes:
                canvas_node.content.refresh()
            if state == "paused":
                node.content.summary.setText("Branch 01 · No face detected\nBranch 02 · Mask ready")
            if state == "running":
                node.content.summary.setText("Branch 01 · Inpaint running\nBranch 02 · Refining")
            QApplication.processEvents()
            canvas_owner.fit_view()
            if name.endswith("narrow"):
                canvas_owner.view.resetTransform()
                canvas_owner.view.centerOn(node.grNode)
            QApplication.processEvents()
            capture(owner.form, out, name)
    finally:
        owner.runner.shutdown()
        owner.form.hide()
        owner.form.deleteLater()
        canvas_owner.deleteLater()
        # Drain while the Python owners (including prompt highlighters) are
        # still alive, before a packed worker builds another MainWindow.
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

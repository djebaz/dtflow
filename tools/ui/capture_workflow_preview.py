"""Load a workflow file into the offline Workflows preview and save screenshots.

Unlike ``render_workflows_states.py`` (fixed mockup states with staged positions and fixture status text),
this keeps the file as written: its node positions, parameters and links come from the JSON, parsed by the
strict POC importer. It defaults to the reference test workflow. Offline only: no executor, provider,
network or file writes besides the PNGs. Run from the repository root:

    uv run --frozen python tools/ui/capture_workflow_preview.py --out tmp/workflow-preview
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW = ROOT / "tools/node_editor_poc/reference_workflow_v1.json"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW, help="ComfyUI v1.0 workflow JSON")
    parser.add_argument("--out", type=Path, default=ROOT / "tmp/workflow-preview", help="output directory")
    parser.add_argument("--select", type=int, help="node id to select (default: the first node in the file)")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    return parser.parse_args()


def offscreen_platform():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.environ["QT_QPA_PLATFORM"] == "offscreen":
        # Same fixed virtual screen as render_states.py (Qt defaults to 800x800); relative path avoids the
        # Windows drive colon being read as a platform-argument separator.
        os.environ["QT_QPA_PLATFORM"] = (
            f"offscreen:configfile={os.path.relpath(ROOT / 'tools/ui/offscreen-screen.json')}"
        )
        if sys.platform == "win32":
            os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))


def main():
    args = parse_args()
    workflow = args.workflow.resolve()
    offscreen_platform()
    sys.path.insert(0, str(ROOT / "tests"))
    from support.network_guard import install

    install()  # before any provider/client import
    # Fixture-only reuse of the accepted POC canvas, never a src -> tools import.
    sys.path.insert(0, str(ROOT / "tools/node_editor_poc"))

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from support.fake_vault import Vault

    import nodeeditor_app as poc
    import poc_contract as contract
    import poc_shell
    from dtflow.services.credentials import SavedCredentials
    from dtflow.ui.main_window import MainWindow
    from dtflow.ui.theme import apply_theme
    from dtflow.ui.workflows_preview import WorkflowsPreview

    apply_theme(app)
    # Strict all-or-nothing import: an invalid file fails the run with the importer's message.
    graph = contract.load_workflow(json.loads(workflow.read_text(encoding="utf-8")))
    if not graph.nodes:
        raise SystemExit(f"{workflow.name} has no nodes.")
    if args.select is not None and args.select not in {node.id for node in graph.nodes}:
        raise SystemExit(f"{workflow.name} has no node {args.select}.")

    import tempfile

    with tempfile.TemporaryDirectory(prefix="dtflow-workflow-preview-") as state:
        owner = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=Path(state), no_cache=True)
        canvas_owner = poc.NodeEditorWindow()  # hidden POC shell; only its canvas is mounted
        canvas_owner.load_graph(graph)
        palette = {spec.title: spec.type for spec in contract.CATALOG.values()}
        preview = WorkflowsPreview(
            owner,
            canvas_owner.editor,
            canvas_owner.fit_view,
            lambda title: canvas_owner.add_node(palette[title]),
            poc_shell.fixture_thumbnail(),
        )
        try:
            owner.form.resize(args.width, args.height)
            owner.show()
            preview.present("editing")
            selected = graph.node(args.select) if args.select is not None else graph.nodes[0]
            node = canvas_owner._node(selected.id)
            spec = node.SPEC
            canvas_owner.scene.grScene.clearSelection()
            node.grNode.setSelected(True)
            ports = " + ".join(port.type for port in spec.inputs) or "source"
            outputs = " + ".join(port.type for port in spec.outputs) or "sink"
            preview.show_node(
                spec.title,
                f"Node {selected.id} · {ports} → {outputs}",
                node.params,
                mask="checkpoint" not in node.params,
            )
            notes = graph.diagnostics()
            preview.workspace.workflowSummary.setText(
                f"{len(graph.nodes)} nodes · {len(graph.links)} links · {workflow.name}"
                + (f" · {len(notes)} draft notes" if notes else "")
            )
            QApplication.processEvents()
            canvas_owner.fit_view()
            QTest.qWait(50)
            args.out.mkdir(parents=True, exist_ok=True)
            stem = workflow.stem
            captures = {
                f"{stem}-window.png": owner.form.grab(),
                f"{stem}-canvas.png": canvas_owner.editor.grab(),
            }
            for name, pixmap in captures.items():
                target = args.out / name
                if not pixmap.save(str(target)):
                    raise SystemExit(f"Could not save {target}")
                small = QImage(str(target)).scaled(16, 16)
                if len({small.pixel(x, y) for x in range(16) for y in range(16)}) <= 1:
                    raise SystemExit(f"{target} is blank.")
                print(f"Saved {target} ({pixmap.width()}x{pixmap.height()})")
            print(
                f"Loaded {workflow.name}: {len(graph.nodes)} nodes, {len(graph.links)} links; selected node {selected.id}"
            )
            for note in notes:
                print(f"Draft note: {note}")
        finally:
            owner.runner.shutdown()
            owner.form.hide()
            owner.form.deleteLater()
            canvas_owner.deleteLater()
            QApplication.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QApplication.processEvents()


if __name__ == "__main__":
    main()

"""Load a workflow file into the Workflows workspace (offline) and save screenshots.

Unlike ``render_workflows_states.py`` (fixed fixture states), this keeps the file as written: its node positions,
parameters and links come from the JSON, parsed by the strict ``load_workflow`` importer (draft rules) the
Workflows Import uses. It defaults to the bundled reference workflow. Offline only: no executor, provider,
network or file writes besides the PNGs. Run from the repository root:

    uv run --frozen python tools/ui/capture_workflow_preview.py --out tmp/workflow-preview
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW = ROOT / "src/dtflow/resources/workflows/reference_workflow_v1.json"


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
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from support.fake_vault import Vault

    from dtflow.core.workflows import WorkflowError
    from dtflow.services.credentials import SavedCredentials
    from dtflow.services.workflow_library import load_file
    from dtflow.ui.main_window import MainWindow
    from dtflow.ui.theme import apply_theme

    apply_theme(app)
    # Strict all-or-nothing import, the same reader the Workflows Import uses: an invalid file fails the run.
    try:
        graph = load_file(workflow)
    except WorkflowError as exc:
        raise SystemExit(f"{workflow.name} was refused: {exc}") from None
    if not graph.nodes:
        raise SystemExit(f"{workflow.name} has no nodes.")
    if args.select is not None and args.select not in {node.id for node in graph.nodes}:
        raise SystemExit(f"{workflow.name} has no node {args.select}.")

    import tempfile

    with tempfile.TemporaryDirectory(prefix="dtflow-workflow-preview-") as state:
        owner = MainWindow(saved=SavedCredentials(Vault()), environ={}, state_dir=Path(state), no_cache=True)
        page = owner.workflows
        try:
            owner.form.resize(args.width, args.height)
            owner.show()
            owner.form.workflowsNavigationButton.click()
            QApplication.processEvents()
            page.open_graph(graph, None, workflow.stem)  # not saved into any library: the PNGs are the only output
            selected = graph.node(args.select) if args.select is not None else graph.nodes[0]
            page.canvas.select_node(selected.id)
            QApplication.processEvents()
            page._update_plan()
            page.canvas.fit_view()
            QTest.qWait(50)
            args.out.mkdir(parents=True, exist_ok=True)
            stem = workflow.stem
            captures = {
                f"{stem}-window.png": owner.form.grab(),
                f"{stem}-canvas.png": page.canvas.widget.grab(),
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
            print(f"Plan: {page.inspector.workflowPlanText.text()}")
            for note in graph.diagnostics():
                print(f"Draft note: {note}")
        finally:
            owner.runner.shutdown()
            owner.form.hide()
            owner.form.deleteLater()
            QApplication.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QApplication.processEvents()


if __name__ == "__main__":
    main()

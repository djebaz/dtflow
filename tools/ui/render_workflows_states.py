"""Workflows workspace renders: the production page in the real shell, driven by offline fixtures.

Fixture run-state dicts (the ``services.workflow_queue`` shape) and synthetic images stand in for a run; no
services, providers, downloads or network are used, and nothing is queued. No assertions: a wait that times out
only warns and the current state is captured.
"""

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[2]
STATES = (
    "workflows-01-empty-library",
    "workflows-02-editing",
    "workflows-03-import-rejected",
    "workflows-04-plan-summary",
    "workflows-05-missing-models",
    "workflows-06-running",
    "workflows-07-branch-paused",
    "workflows-08-partial-retry",
    "workflows-09-results",
    "workflows-10-narrow",
)
OLD_CONTRACT = ROOT / "tools/node_editor_poc/reference_workflow_v1.json"  # a POC-era file the importer refuses


def _wait(condition, label, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            print(f"warning: workflows render wait timed out: {label}", file=sys.stderr)
            return
        QTest.qWait(20)


def fixture_image(path: Path, seed: int, size: int = 384) -> str:
    """Deterministic synthetic 'generated image' (no model, no provider)."""
    image = QImage(size, size, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    sky = QLinearGradient(0, 0, 0, size)
    sky.setColorAt(0, QColor.fromHsv((seed * 37) % 360, 120, 230))
    sky.setColorAt(1, QColor.fromHsv((seed * 37 + 40) % 360, 160, 90))
    painter.fillRect(0, 0, size, size, sky)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 214, 160))
    painter.drawEllipse(QRectF(size * 0.34, size * 0.22, size * 0.32, size * 0.40))
    painter.setBrush(QColor(70, 45, 30))
    painter.drawChord(QRectF(size * 0.32, size * 0.16, size * 0.36, size * 0.30), 0, 180 * 16)
    painter.setBrush(QColor(40, 60, 90))
    painter.drawRoundedRect(QRectF(size * 0.22, size * 0.62, size * 0.56, size * 0.42), 30, 30)
    painter.setPen(QColor(255, 255, 255, 200))
    painter.setFont(QFont("Segoe UI", max(8, size // 20)))
    painter.drawText(QPointF(8, size - 10), f"fixture {seed}")
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path))
    return str(path)


def _step(node, title, branch, state, *, stage=None, message=None, output=None):
    return {
        "node_id": node, "type": "", "title": title, "branch": list(branch),
        "branch_label": f"Generate #{branch[0]} image {branch[1] + 1}", "state": state, "stage": stage,
        "message": message, "output": output, "mask": None, "parent": None, "settings": None, "retries": 0,
        "started_at": None, "finished_at": f"2026-11-05T10:0{node}:00Z" if output else None,
    }  # fmt: skip


def fixture_runs(images: Path) -> dict[str, dict]:
    """Run states for the reference workflow with two generated images (two branches)."""
    out = {key: fixture_image(images / f"attempt-{key}" / "final-1.png", seed) for seed, key in enumerate("abcdefgh")}
    titles = {1: "Generate #1", 2: "Detect face mask #2", 3: "Inpaint #3", 4: "Img2img refine #4", 5: "Save image #5"}
    b1, b2 = (1, 0), (1, 1)

    def run(state, steps, *, submitted, pauses=(), results=(), message=None):
        return {
            "run_id": "fixture-run", "name": "Portrait finishing", "state": state, "planned": 6,
            "submitted": submitted, "message": message, "steps": steps, "pauses": list(pauses),
            "results": list(results),
        }  # fmt: skip

    def s(node, branch, state, **kw):
        return _step(node, titles[node], branch, state, **kw)

    running = run(
        "running",
        [
            s(1, b1, "succeeded", output=out["a"]), s(1, b2, "succeeded", output=out["b"]),
            s(2, b1, "succeeded"), s(2, b2, "succeeded"),
            s(3, b1, "running", stage="Inpainting · step 12 of 20"), s(3, b2, "running", stage="Submitting"),
            s(4, b1, "pending"), s(4, b2, "pending"), s(5, b1, "pending"), s(5, b2, "pending"),
        ],
        submitted=4,
    )  # fmt: skip
    reason = "No face detected. Continue anyway?"
    paused = run(
        "running",
        [
            s(1, b1, "succeeded", output=out["a"]), s(1, b2, "succeeded", output=out["b"]),
            s(2, b1, "paused", message="No face box in this image."), s(2, b2, "succeeded"),
            s(3, b1, "pending"), s(3, b2, "running", stage="Inpainting · step 6 of 20"),
            s(4, b1, "pending"), s(4, b2, "pending"), s(5, b1, "pending"), s(5, b2, "pending"),
        ],
        submitted=3,
        pauses=[{"node_id": 2, "branch": list(b1), "branch_label": "Generate #1 image 1", "reason": reason}],
    )  # fmt: skip
    partial = run(
        "partial",
        [
            s(1, b1, "succeeded", output=out["a"]), s(1, b2, "succeeded", output=out["b"]),
            s(2, b1, "succeeded"), s(2, b2, "succeeded"),
            s(3, b1, "succeeded", output=out["c"]),
            s(3, b2, "failed", message="The backend refused the inpainting request: checkpoint does not support it."),
            s(4, b1, "succeeded", output=out["d"]), s(4, b2, "skipped"),
            s(5, b1, "succeeded", output=out["d"]), s(5, b2, "skipped"),
        ],
        submitted=5,
        results=[{"label": "result", "path": out["d"], "branch": list(b1), "node_id": 5}],
        message="1 of 2 branches finished; one step failed.",
    )  # fmt: skip
    done = run(
        "succeeded",
        [
            s(1, b1, "succeeded", output=out["a"]), s(1, b2, "succeeded", output=out["b"]),
            s(2, b1, "succeeded"), s(2, b2, "succeeded"),
            s(3, b1, "succeeded", output=out["c"]), s(3, b2, "succeeded", output=out["e"]),
            s(4, b1, "succeeded", output=out["d"]), s(4, b2, "succeeded", output=out["f"]),
            s(5, b1, "succeeded", output=out["d"]), s(5, b2, "succeeded", output=out["f"]),
        ],
        submitted=6,
        results=[
            {"label": "result", "path": out["d"], "branch": list(b1), "node_id": 5},
            {"label": "result", "path": out["f"], "branch": list(b2), "node_id": 5},
        ],
    )  # fmt: skip
    return {"running": running, "paused": paused, "partial": partial, "done": done}


def _fanout(graph):
    graph.node(1).params["batch_count"] = 2  # two images: two branches downstream
    return graph


def render_workflows_states(out, *, window_factory, state_names, capture):
    from dtflow.core import workflows as wf
    from dtflow.services import workflow_library as library
    from dtflow.services.model_provisioning import CatalogueEntry
    from dtflow.ui.inline_feedback import confirm_action

    owner = window_factory()
    page = owner.workflows
    state = owner.state_dir
    runs = fixture_runs(state / "outputs")
    reference = json.loads(library.REFERENCE.read_text(encoding="utf-8"))
    # Choices as the inventory would offer them (fixture names; no provider is contacted).
    page.choices_source = lambda: {
        "models": [
            "PLACEHOLDER_checkpoint_a.ckpt",
            "PLACEHOLDER_checkpoint_b_inpainting.ckpt",
            "PLACEHOLDER_checkpoint_c.ckpt",
        ],
        "loras": {"PLACEHOLDER_lora_a.safetensors": 0.8, "PLACEHOLDER_lora_b.safetensors": 0.6},
    }
    entries = (
        CatalogueEntry("segmentation", "YOLO26n person segmentation", "https://example.invalid/seg.onnx", "0" * 64,
                       6.2, "AGPL-3.0", "https://example.invalid/agpl", "https://example.invalid/seg"),
        CatalogueEntry("clothes", "Clothes and face parsing", "https://example.invalid/parse.onnx", "1" * 64,
                       98.0, "MIT", "https://example.invalid/mit", "https://example.invalid/parse"),
        CatalogueEntry("faces", "Faces detector", "https://example.invalid/faces.onnx", "2" * 64,
                       1.7, "Apache-2.0", "https://example.invalid/apache", "https://example.invalid/faces"),
    )  # fmt: skip

    def open_reference(name="Portrait finishing", *, fanout=True, saved=True):
        graph = wf.load_workflow(json.loads(json.dumps(reference)), draft=True)
        if fanout:
            _fanout(graph)
        existing = library.library_dir(state) / f"{name}.json"
        path = library.save_workflow(state, name, graph, current=existing) if saved else None
        page.open_graph(graph, path, name)
        page.canvas.mark_saved()
        page._changed()
        page.refresh_library()

    def reset():
        owner.inline.dismiss()
        page.queue_id, page.run, page._run_path = None, None, None
        page._pause_shown = None
        page._pause_dismissed.clear()
        page._missing = ()
        page.present_run(None)

    def show_run(run):
        page.queue_id, page._run_path = "fixture-queue", page.path
        page.present_run(run)
        page._poll.stop()  # fixtures only: nothing to poll
        _wait(lambda: not page.loading_images, "run thumbnails")
        page.present_run(run)
        page._poll.stop()

    try:
        owner.form.resize(1600, 1000)
        owner.show()
        owner.form.workflowsNavigationButton.click()
        QApplication.processEvents()
        _wait(lambda: page.canvas is not None, "canvas")
        for name in state_names:
            narrow = name.endswith("narrow")
            owner.form.resize(1040 if narrow else 1600, 900 if narrow else 1000)
            reset()
            if name.endswith("empty-library"):
                page.open_graph(wf.Graph(), None, "")
                page.name = ""
                page._changed()
                page.refresh_library()
                _wait(lambda: page.context.workflowLibraryNote.text().startswith("No saved"), "library listing")
            else:
                open_reference(fanout=not name.endswith("editing"))
                _wait(lambda: page.context.workflowList.count() > 0, "library listing")
                QApplication.processEvents()
                if page.context.workflowList.count() < 2:
                    library.save_workflow(state, "Product relight", wf.load_workflow(reference, draft=True))
                    page.refresh_library()
                _wait(lambda: page.context.workflowList.count() > 1, "second library entry")
                select = {"editing": 1, "plan-summary": 5, "missing-models": 2, "branch-paused": 2,
                          "partial-retry": 3, "narrow": 3}  # fmt: skip
                node = next((value for key, value in select.items() if name.endswith(key)), 3)
                page.canvas.select_node(node)
            if name.endswith("import-rejected"):
                try:
                    library.load_file(OLD_CONTRACT)
                    reason = "The file was accepted."
                except wf.WorkflowError as exc:
                    reason = str(exc)
                page._error("Workflow import refused", wf.WorkflowError(f"{OLD_CONTRACT.name}: {reason}"))
            elif name.endswith("missing-models"):
                page._missing = entries
                page._update_plan()
                button = page.workspace.runWorkflowButton
                try:
                    from dtflow.ui import model_download

                    model_download.confirm_and_download(
                        page.workspace, state, entries, owner.runner, lambda *_: None, button=button
                    )
                except ImportError:  # helper not in this build: the same shared confirmation, fixture text
                    lines = [f"• {e.name} (about {e.size_mib:.1f} MiB) · Licence: {e.licence}" for e in entries]
                    confirm_action(
                        page.workspace, button, "Download detection models",
                        "DTFlow will download these models directly from each publisher before the workflow is "
                        "queued:\n" + "\n".join(lines), lambda: None,
                    )  # fmt: skip
            elif name.endswith("running"):
                show_run(runs["running"])
            elif name.endswith("branch-paused"):
                show_run(runs["paused"])
            elif name.endswith("partial-retry"):
                show_run(runs["partial"])
                page.canvas.select_node(3)
            elif name.endswith("results"):
                show_run(runs["done"])
            QApplication.processEvents()
            page.canvas.fit_view()
            if narrow:
                page.canvas.center_on(3, reset_zoom=True)
            QTest.qWait(30)
            for _ in range(4):  # nested card/rail layouts settle one level per event turn
                QApplication.processEvents()
                QTest.qWait(20)
            capture(owner.form, out, name)
    finally:
        reset()
        owner.runner.shutdown()
        owner.form.hide()
        owner.form.deleteLater()
        # Drain while the Python owners (including prompt highlighters) are
        # still alive, before a packed worker builds another MainWindow.
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

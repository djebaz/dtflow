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
    "workflows-11-library-folded",
    "workflows-12-lora-warning",
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
    # The executor's wording (services/workflow_item): steps after a failed one on its branch are "stopped", and the
    # run message counts results and incomplete steps, then names the first problem with its own cause.
    cause = "The backend refused the inpainting request: checkpoint does not support it."
    stopped = "Stopped: an earlier step on this branch did not finish."
    partial_steps = [
        s(1, b1, "succeeded", output=out["a"]), s(1, b2, "succeeded", output=out["b"]),
        s(2, b1, "succeeded"), s(2, b2, "succeeded"),
        s(3, b1, "succeeded", output=out["c"]), s(3, b2, "failed", message=cause),
        s(4, b1, "succeeded", output=out["d"]), s(4, b2, "stopped", message=stopped),
        s(5, b1, "succeeded", output=out["d"]), s(5, b2, "stopped", message=stopped),
    ]  # fmt: skip
    partial_results = [{"label": "result", "path": out["d"], "branch": list(b1), "node_id": 5}]
    incomplete = [step for step in partial_steps if step["state"] in ("failed", "unknown", "stopped", "cancelled")]
    first = next(step for step in partial_steps if step["state"] == "failed")
    partial = run(
        "partial",
        partial_steps,
        submitted=5,
        results=partial_results,
        message=f"{len(partial_results)} result(s) saved; {len(incomplete)} step(s) failed, stopped or unconfirmed. "
        f"First problem: {first['title']} ({first['branch_label']}): {first['message']}",
    )
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
    from dtflow.services.model_provisioning import catalogue
    from dtflow.ui.inline_feedback import confirm_action

    owner = window_factory()
    page = owner.workflows
    state = owner.state_dir
    runs = fixture_runs(state / "outputs")
    reference = json.loads(library.REFERENCE.read_text(encoding="utf-8"))
    # The reference workflow ships PLACEHOLDER_ checkpoints, which block Run until chosen (editing and narrow show
    # that). Other states choose these fixture checkpoints, as the inventory would list them; no provider is
    # contacted. plan-summary also shows the warning for a checkpoint the inventory does not list.
    chosen = {
        "PLACEHOLDER_checkpoint_a.ckpt": "fixture_base.ckpt",
        "PLACEHOLDER_checkpoint_b_inpainting.ckpt": "fixture_inpainting.ckpt",
        "PLACEHOLDER_checkpoint_c.ckpt": "fixture_refiner.ckpt",
    }
    inventory = {"models": list(chosen.values()), "loras": {"PLACEHOLDER_lora_a.safetensors": 0.8,
                                                           "PLACEHOLDER_lora_b.safetensors": 0.6}}  # fmt: skip
    page.choices_source = lambda: inventory
    entries = catalogue()  # the shipped pinned catalogue: real names, sizes, licences and URLs (nothing downloads)
    # Earlier runs as Workflows lists them from the queue store (fixture labels; no store is read).
    recent = [("fixture-queue", "Failed, some results saved · Portrait finishing · 05 Nov 10:02"),
              ("fixture-earlier", "Done · Portrait finishing · 04 Nov 18:40")]  # fmt: skip

    def open_reference(name="Portrait finishing", *, fanout=True, saved=True, placeholders=False):
        graph = wf.load_workflow(json.loads(json.dumps(reference)), draft=True)
        if fanout:
            _fanout(graph)
        if not placeholders:
            for node in graph.nodes:
                for key, value in node.params.items():
                    if isinstance(value, str) and value in chosen:
                        node.params[key] = chosen[value]
        existing = library.library_dir(state) / f"{name}.json"
        path = library.save_workflow(state, name, graph, current=existing) if saved else None
        page.open_graph(graph, path, name)
        page.canvas.mark_saved()
        page._changed()
        page.refresh_library()

    def reset():
        owner.inline.dismiss()
        page.hide_node_picker()
        page.queue_id, page.run, page._run_path = None, None, None
        page._pause_shown = None
        page._pause_dismissed.clear()
        page._missing = ()
        page.present_run(None)
        page._library_open = None  # by width again; the fixture never writes the remembered choice
        page.show_recent_runs([])
        owner._responsive_workspace()

    def show_run(run):
        page.queue_id, page._run_path = "fixture-queue", page.path
        page.show_recent_runs(recent)
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
                plan_summary = name.endswith("plan-summary")
                inventory["models"] = [m for m in chosen.values() if not (plan_summary and "refiner" in m)]
                open_reference(fanout=not name.endswith("editing"), placeholders=name.endswith(("editing", "narrow")))
                _wait(lambda: page.context.workflowList.count() > 0, "library listing")
                QApplication.processEvents()
                if page.context.workflowList.count() < 2:
                    library.save_workflow(state, "Product relight", wf.load_workflow(reference, draft=True))
                    page.refresh_library()
                _wait(lambda: page.context.workflowList.count() > 1, "second library entry")
                select = {"editing": 1, "plan-summary": 5, "missing-models": 2, "branch-paused": 2,
                          "partial-retry": 3, "narrow": 3, "library-folded": 3}  # fmt: skip
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
            elif name.endswith("lora-warning"):
                warning = "fixture_custom_lora.ckpt: no confirmed prepared receipt; may not apply on DT+. Prepare the custom LoRA explicitly."
                run = runs["done"]
                run["steps"][0]["warnings"] = [warning]
                page.canvas.select_node(1)
                show_run(run)
            elif name.endswith("library-folded"):
                page._library_open = False  # as after pressing Library (not written to the fixture state)
                page._sync_library_button()
                owner._responsive_workspace()
                show_run(runs["partial"])
            QApplication.processEvents()
            QTest.qWait(30)
            for _ in range(4):  # nested card/rail layouts settle one level per event turn
                QApplication.processEvents()
                QTest.qWait(20)
            page.canvas.fit_view()  # at the canvas size the settled layout gave it
            if narrow:
                page.canvas.center_on(3, reset_zoom=True)
            if name.endswith("library-folded"):
                # The add-node picker as a double-click on empty canvas below the nodes opens it.
                view = page.canvas.view
                point = view.viewport().rect().center()
                point.setY(int(view.viewport().height() * 0.78))
                page.show_node_picker(view.mapToScene(point), point)
                page._picker.workflowNodePickerSearch.setText("in")
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

"""Offline render routing and workflow summaries, without Qt or Google Drive calls."""

import ast
import struct
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]


def renderer_functions():
    """Load routing functions without importing the renderer's Qt/application fixture graph."""
    source = ast.parse((ROOT / "tools/ui/render_states.py").read_text(encoding="utf-8"))
    names = {"wants", "render_category", "category_selected", "render_workspace_states"}
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in functions:
        node.decorator_list = []
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "render-routing", "exec"), namespace)
    return namespace


@pytest.mark.parametrize(
    "category,expected",
    [("generate", "generate"), ("queues", "queues"), ("images", "images"), ("image-navigation", "images")],
)
def test_category_runs_include_their_shared_workspace_fixtures(tmp_path, category, expected):
    namespace = renderer_functions()
    calls = []
    shell, app = MagicMock(), MagicMock()
    item_table = MagicMock()
    namespace.update(
        SELECTED_CATEGORY=category,
        MainWindow=MagicMock(return_value=shell),
        SavedCredentials=MagicMock(),
        Vault=MagicMock(),
        QTest=MagicMock(),
        render_item_table_states=item_table,
    )
    for group, name in (
        ("generate", "render_generate_configuration_states"),
        ("queues", "render_active_queue_states"),
        ("images", "render_gallery_composition_states"),
    ):
        namespace[name] = lambda *args, group=group: calls.append(group)
    namespace["render_workspace_states"](app, tmp_path, tmp_path)
    assert calls == [expected]
    if category == "queues":
        item_table.assert_called_once_with(shell, tmp_path, tmp_path)
    else:
        item_table.assert_not_called()
    shell.runner.shutdown.assert_called_once()

    selected = namespace["category_selected"]
    assert selected(Path("image-navigation/images-95-pager-preparing-next-100.png")) == (
        category in ("images", "image-navigation")
    )
    assert selected(Path("queues-33-elapsed-eta.png")) == (category == "queues")


def test_parallel_render_summary_keeps_failures_totals_and_per_process_timings(tmp_path, monkeypatch):
    workflow = (ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8")
    step = workflow.split("      - name: Summarize renders\n", 1)[1].split("\n      - name:", 1)[0]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    out = tmp_path / "tmp/ui-renders"
    out.mkdir(parents=True)
    for name in ("queues-33-elapsed-eta", "queues-34-eta-countdown", "images-01-grid"):
        (out / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 1600, 1000))
    (tmp_path / "tmp/render.log").write_text(
        "[queues] [  1] +  2.0s tmp/ui-renders/queues-33-elapsed-eta.png\n"
        "[images] [  1] +  8.0s tmp/ui-renders/images-01-grid.png\n"
        "[queues] [  2] +  3.0s tmp/ui-renders/queues-34-eta-countdown.png\n"
        "[images] Traceback (most recent call last):\n"
        "[images] RuntimeError: Offline fixture failed\n"
        "[queues] Rendered 2 states in 3.0s.\n"
        "Rendered 1 states in 8.0s.\n",
        encoding="utf-8",
    )
    summary = tmp_path / "summary.md"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("RENDER_STATUS", "123")
    exec(compile(script, "render-summary", "exec"), {})
    result = summary.read_text(encoding="utf-8")
    assert "Failed (exit 123)" in result
    assert "### Problems" in result and "RuntimeError: Offline fixture failed" in result
    assert "queues: Rendered 2 states in 3.0s.; Rendered 1 states in 8.0s." in result
    assert "| queues-34-eta-countdown | 1.0 |" in result
    assert "| images-01-grid | 8.0 |" in result
    assert "**3**" in result

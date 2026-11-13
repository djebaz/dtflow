"""Plan an offline UI preview request without importing Qt or executing captures."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from action_changes import decide

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/ui"))
from preview_catalog import CATEGORY_ORDER, Selection, load_catalog  # noqa: E402
from preview_changes import glob_regex, plan  # noqa: E402

DEFAULT_WORKFLOW = "src/dtflow/resources/workflows/reference_workflow_v1.json"
LORA_PATHS = (
    "src/dtflow/ui/prepare_lora_editor.py",
    "src/dtflow/ui/forms/prepare_lora_*.ui",
    "src/dtflow/ui/lora_*.py",
    "src/dtflow/ui/forms/lora_*.ui",
    "src/dtflow/ui/source_details_view.py",
    "src/dtflow/ui/forms/main_window.ui",
    "src/dtflow/ui/main_window.py",
    "tools/ui/preview_states.py",
    "tools/ui/preview_catalog.py",
    "tools/ui/preview_catalog.json",
)
WORKFLOW_PATHS = (
    "tools/ui/preview_workflow_file.py",
    "tools/ui/preview_workflows_states.py",
    "src/dtflow/ui/workflows_*.py",
    "src/dtflow/ui/forms/workflows_*.ui",
    "src/dtflow/services/workflow_library.py",
    "src/dtflow/core/workflows.py",
    "src/dtflow/resources/workflows/**",
    "src/dtflow/resources/styles/**",
)


def request(event_name, event, mode, category, workflow):
    automatic = event_name == "pull_request"
    gate = decide("preview", event_name, event)
    if automatic:
        pr = event["pull_request"]
        if pr["draft"]:
            return {"run": False, "reason": "automatic previews never run for drafts"}
        labels = {label["name"] for label in pr.get("labels", [])}
        if event.get("action") == "labeled" and event.get("label", {}).get("name") not in {
            "preview:PART",
            "preview:ALL",
        }:
            return {"run": False, "reason": "unrelated label"}
        mode = "all" if "preview:ALL" in labels else "changed"
        if not gate["run"]:
            return {"run": False, "reason": gate["reason"]}
    if mode not in {"changed", "category", "all", "workflow"}:
        raise ValueError(f"Unknown preview mode: {mode}")
    if category not in {*CATEGORY_ORDER, "image-navigation"}:
        raise ValueError(f"Unknown preview category: {category}")
    if mode == "changed" and not event.get("pull_request") and not os.environ.get("PR_NUMBER"):
        raise ValueError("Changed mode needs a PR number; use category/all/workflow for a branch")

    selection = {"mode": mode, "warnings": [], "reasons": [], "states": [], "fixtures": [], "changed": []}
    args = []
    workflow_capture = mode == "workflow"
    if mode == "changed":
        base = os.environ.get("BASE_SHA")
        if not base:
            raise ValueError("Changed mode needs an exact PR base commit")
        selection = plan(base, "HEAD")
        # Preserve the former scoped LoRA workflow's runtime coverage. Both explicit
        # category and change-selected LoRA requests derive these states from the catalogue.
        if any(glob_regex(pattern).match(path) for pattern in LORA_PATHS for path in selection["changed"]):
            catalog = load_catalog()
            lora = Selection(catalog, category="lora", level="f")
            ids = set(selection["states"]) | {state.id for state in lora.states}
            rows = [state for state in catalog if state.id in ids]
            selection.update(
                states=[state.id for state in rows],
                fixtures=list(dict.fromkeys(state.fixture for state in rows)),
                args=[arg for state in rows for arg in ("--state", state.id)],
            )
            selection["reasons"].append("LoRA input changed: retain the canonical fast runtime category")
        args = selection["args"]
        workflow_capture = any(
            glob_regex(pattern).match(path) for pattern in WORKFLOW_PATHS for path in selection["changed"]
        )
    elif mode != "workflow":
        selected = Selection(
            load_catalog(), category=category if mode == "category" else "all", level="f" if mode == "category" else "s"
        )
        args = ["--category", category, "--fast"] if mode == "category" else ["--slow"]
        selection.update(
            states=[state.id for state in selected.states],
            fixtures=sorted(selected.fixtures),
            reasons=["explicit category (fast level)" if mode == "category" else "explicit all registered states"],
        )
        workflow_capture = mode == "all" or category == "workflows"
    if workflow_capture:
        relative = Path(workflow)
        resolved = (ROOT / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not resolved.is_relative_to(ROOT):
            raise ValueError("Workflow must be a relative repository path without traversal")
        if not resolved.is_file() or resolved.suffix.lower() != ".json":
            raise ValueError("Workflow JSON not found at the selected commit")
    return {
        "run": True,
        "reason": gate["reason"],
        "mode": mode,
        "category": category if mode == "category" else None,
        "args": args,
        "workflow": workflow if workflow_capture else None,
        "selection": selection,
    }


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    result = request(
        os.environ["GITHUB_EVENT_NAME"],
        event,
        os.environ.get("INPUT_MODE") or "changed",
        os.environ.get("INPUT_CATEGORY") or "lora",
        os.environ.get("INPUT_WORKFLOW") or DEFAULT_WORKFLOW,
    )
    print(json.dumps(result, indent=2))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"run={str(result['run']).lower()}\n")
        output.write(f"plan={json.dumps(result, separators=(',', ':'))}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8", newline="\n") as summary:
        summary.write(f"## Preview request\n\n{result['reason']}.\n\n")
        if not result["run"]:
            summary.write("No captures requested; this is not a new validation pass.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Cheap stdlib-only CI/render gate; no dependencies, Qt, tests or screenshots.

Synchronize compares the previous PR head with the new head, not the cumulative
PR diff. Other PR events use the merge base. Missing history runs conservatively.
Manual dispatch and explicitly adding a render label bypass the content gate.
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).with_name("action_dependencies.json")


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def changed_paths(event):
    pr = event["pull_request"]
    head = pr["head"]["sha"]
    before = event.get("before") if event.get("action") == "synchronize" else pr["base"]["sha"]
    for sha in (before, head):
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha) or set(sha) == {"0"}:
            raise ValueError("missing or invalid comparison commit")
    if event.get("action") != "synchronize":
        before = git("merge-base", before, head).decode("ascii").strip()
    # Rename/delete changes include the old path as well as the new one.
    paths = git("diff", "--name-only", "--no-renames", "-z", before, head)
    return before, head, sorted(os.fsdecode(path) for path in paths.split(b"\0") if path)


def decide(kind, event_name, event):
    if event_name == "workflow_dispatch":
        return {"run": True, "reason": "explicit manual dispatch", "changed": []}
    if event_name != "pull_request":
        return {"run": True, "reason": "unrecognized event; run conservatively", "changed": []}
    if kind == "render" and event.get("action") == "labeled":
        if event.get("label", {}).get("name") in ("render", "render-all"):
            return {"run": True, "reason": "explicit render label request", "changed": []}
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported Action dependency schema")
    try:
        before, head, paths = changed_paths(event)
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError):
        return {"run": True, "reason": "comparison unavailable; run conservatively", "changed": []}
    relevant = []
    for path in paths:
        if matches(path, config["documentation"]):
            continue
        if matches(path, config["shared"] + config[kind]):
            relevant.append(path)
        elif kind == "ci" and not matches(path, config["ci_ignore"]):
            relevant.append(path)  # Unknown code/configuration must not silently bypass CI.
    comparison = "previous PR head" if event.get("action") == "synchronize" else "PR merge base"
    return {
        "run": bool(relevant),
        "reason": f"{len(relevant)} relevant paths against {comparison}"
        if relevant
        else f"no relevant changes against {comparison}",
        "before": before,
        "head": head,
        "changed": paths,
        "relevant": relevant,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", "--kind", choices=("ci", "render"), required=True)
    args = parser.parse_args()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    result = decide(args.kind, os.environ["GITHUB_EVENT_NAME"], event)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"run={str(result['run']).lower()}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        verdict = "Continue expensive steps" if result["run"] else "Skip expensive steps"
        with Path(summary).open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"## {args.kind.upper()} change gate: {verdict}\n\n{result['reason']}.\n\n")
            output.write("Skipped work is not a new validation pass. Manual dispatch bypasses this gate.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

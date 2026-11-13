"""Run planned offline captures and retain independent state/workflow-file outcomes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tmp/ui-previews"


def capture(name, command, seconds):
    log = OUT / f"{name}.log"
    with log.open("w", encoding="utf-8", newline="\n") as stream:
        process = subprocess.Popen(
            ["timeout", "--signal=ABRT", "--kill-after=10s", str(seconds), *command],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in process.stdout:
            stream.write(line)
            stream.flush()
            print(line, end="", flush=True)
        code = process.wait()
    return {"exit_code": code, "log": log.name, "timeout_seconds": seconds}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plan = json.loads((OUT / "request.json").read_text(encoding="utf-8"))
    outcomes = {}
    try:
        if plan["args"]:
            outcomes["states"] = capture(
                "states",
                [
                    sys.executable,
                    "tools/ui/preview_states.py",
                    *plan["args"],
                    "--jobs",
                    "2",
                    "--out",
                    str(OUT / "states"),
                ],
                300,
            )
        if plan["workflow"]:
            outcomes["workflow"] = capture(
                "workflow",
                [
                    sys.executable,
                    "tools/ui/preview_workflow_file.py",
                    "--workflow",
                    plan["workflow"],
                    "--out",
                    str(OUT / "workflow"),
                ],
                180,
            )
    finally:
        (OUT / "outcomes.json").write_text(json.dumps(outcomes, indent=2) + "\n", encoding="utf-8", newline="\n")
    return int(not outcomes or any(item["exit_code"] != 0 for item in outcomes.values()))


if __name__ == "__main__":
    raise SystemExit(main())

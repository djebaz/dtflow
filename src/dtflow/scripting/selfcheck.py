"""Non-generative engine self-check for Windows / legacy-CPU compatibility evidence.

Run from source with ``dtflow script --check-engine`` or from a frozen build with
``dtflow-<build>.exe --dtflow-script-selfcheck result.json``. It makes no network connection, touches no provider and
writes only the optional result file. Each check reports ok/fail and elapsed seconds; any failure exits non-zero.
"""

from __future__ import annotations

import json
import platform
import sys
import threading
import time
from pathlib import Path

CHECKS = (
    ("import quickjs", "import"),
    ("console and cancellable sleep through the child process", "console"),
    ("unsupported hook raises a clear JS error", "unsupported"),
    ("Stop terminates a busy JS loop", "stop"),
    ("deadline terminates a busy JS loop", "deadline"),
    ("memory limit stops runaway allocation", "memory"),
)


def _run(kind: str) -> str:
    from dtflow.scripting.engine import ScriptLimits
    from dtflow.scripting.runner import run_script

    if kind == "import":
        import quickjs

        return f"quickjs {getattr(quickjs, '__version__', 'imported')}"
    if kind == "console":
        result = run_script('console.log("hello"); __dtSleep(0.05); console.warn({a: 1})')
        assert result.status == "completed" and [text for _, text in result.console][0] == "hello", result
        return f"{result.host_calls} host calls"
    if kind == "unsupported":
        result = run_script('try { canvas.answer("m", "q") } catch (e) { console.log(e.message) }')
        assert result.status == "completed" and "not supported" in result.console[0][1], result
        return result.console[0][1]
    if kind == "stop":
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        result = run_script("while (true) {}", cancel=cancel)
        assert result.status == "stopped", result
        return "stopped"
    if kind == "deadline":
        result = run_script("while (true) {}", limits=ScriptLimits(deadline_seconds=1.0))
        assert result.status == "failed" and "time limit" in (result.error or ""), result
        return result.error or ""
    if kind == "memory":
        result = run_script(
            'let a = []; while (true) a.push("x".repeat(1000))', limits=ScriptLimits(memory_bytes=16 * 1024 * 1024)
        )
        assert result.status == "failed" and "memory" in (result.error or "").lower(), result
        return result.error or ""
    raise AssertionError(kind)


def run_selfcheck() -> dict:
    results = []
    for title, kind in CHECKS:
        started = time.monotonic()
        try:
            detail = _run(kind)
            status = "ok"
        except Exception as exc:  # report, never hide, the failure
            status, detail = "fail", f"{type(exc).__name__}: {exc}"
        results.append(
            {"check": title, "status": status, "seconds": round(time.monotonic() - started, 3), "detail": detail}
        )
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "ok": all(item["status"] == "ok" for item in results),
        "checks": results,
    }


def main(out: Path | None = None) -> int:
    report = run_selfcheck()
    text = json.dumps(report, indent=2)
    if out is not None:
        Path(out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report["ok"] else 1

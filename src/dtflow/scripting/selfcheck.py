"""Non-generative engine self-check for Windows / legacy-CPU compatibility evidence.

Run from source with ``dtflow script --check-engine`` or from a frozen build with
``dtflow-<build>.exe --dtflow-script-selfcheck result.json`` (use ``start /wait`` on Windows: the build is windowed).
It makes no network connection, touches no provider and writes only the result file (atomically). Each check reports
ok/fail and elapsed seconds; any failure exits non-zero. Stop, deadline and memory checks require the script to announce
that it is running before the loop starts, so a slow child start can never make them pass without proving anything.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

from dtflow.scripting.engine import ScriptLimits
from dtflow.scripting.runner import RunResult, run_script

CHECKS = (
    ("import quickjs", "import"),
    ("console and cancellable sleep through the child process", "console"),
    ("unsupported hook raises a clear JS error", "unsupported"),
    ("script wrapper: top-level return and facade-name shadowing", "wrapper"),
    ("Stop terminates a busy JS loop", "stop"),
    ("segment deadline terminates a busy JS loop", "deadline"),
    ("memory limit stops runaway allocation", "memory"),
)
MARKER = "started"
startup_samples: list[float] = []


def _record(result: RunResult) -> RunResult:
    if result.startup_seconds is not None:
        startup_samples.append(result.startup_seconds)
    return result


def _announced(result: RunResult) -> bool:
    return any(text == MARKER for _, text in result.console)


def _run(kind: str) -> str:
    if kind == "import":
        import quickjs

        return f"quickjs {getattr(quickjs, '__version__', 'imported')}"
    if kind == "console":
        result = _record(run_script('console.log("hello"); __dtSleep(0.05); console.warn({a: 1})'))
        assert result.status == "completed" and [text for _, text in result.console][0] == "hello", result
        return f"{result.host_calls} host calls; child ready in {result.startup_seconds}s"
    if kind == "unsupported":
        result = _record(run_script('try { canvas.answer("m", "q") } catch (e) { console.log(e.message) }'))
        assert result.status == "completed" and "not supported" in result.console[0][1], result
        return result.console[0][1]
    if kind == "wrapper":
        result = _record(run_script('const canvas = 1; console.log("w"); return; console.log("not reached")'))
        assert result.status == "completed" and [text for _, text in result.console] == ["w"], result
        return "ok"
    if kind == "stop":
        cancel, running = threading.Event(), threading.Event()

        def log(message="", kind=0):
            if message == MARKER:
                running.set()

        def stopper():
            running.wait(60)
            time.sleep(0.3)
            cancel.set()

        threading.Thread(target=stopper, daemon=True).start()
        started = time.monotonic()
        result = _record(run_script(f'console.log("{MARKER}"); while (true) {{}}', {"log": log}, cancel=cancel))
        assert running.is_set() and result.status == "stopped", (running.is_set(), result)
        return f"stopped {round(time.monotonic() - started - (result.startup_seconds or 0), 2)}s after start"
    if kind == "deadline":
        seen: list[str] = []
        result = _record(
            run_script(
                f'console.log("{MARKER}"); while (true) {{}}',
                {"log": lambda message="", kind=0: seen.append(message)},
                limits=ScriptLimits(js_segment_seconds=1.0),
            )
        )
        assert MARKER in seen and result.status == "failed" and "too long" in (result.error or ""), (seen, result)
        return result.error or ""
    if kind == "memory":
        seen = []
        result = _record(
            run_script(
                f'console.log("{MARKER}"); let a = []; while (true) a.push("x".repeat(1000))',
                {"log": lambda message="", kind=0: seen.append(message)},
                limits=ScriptLimits(memory_bytes=16 * 1024 * 1024),
            )
        )
        assert MARKER in seen and result.status == "failed" and "memory" in (result.error or "").lower(), (seen, result)
        return result.error or ""
    raise AssertionError(kind)


def _processor_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor()


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
        "processor": _processor_name(),
        "cpu_count": os.cpu_count(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "child_startup_seconds": {
            "samples": len(startup_samples),
            "min": min(startup_samples) if startup_samples else None,
            "max": max(startup_samples) if startup_samples else None,
        },
        "ok": all(item["status"] == "ok" for item in results),
        "checks": results,
    }


def main(out: Path | None = None) -> int:
    report = run_selfcheck()
    text = json.dumps(report, indent=2) + "\n"
    if out is not None:
        out = Path(out)
        temporary = out.with_name(out.name + ".part")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, out)  # never leave a half-written result file
    else:
        print(text, end="")
    return 0 if report["ok"] else 1

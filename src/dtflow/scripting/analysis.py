"""Conservative, non-generative script analysis (``dtflow script <file>``).

Nothing here runs the script: the source is read, parsed (never compiled or evaluated) by the vendored Acorn 8.14.0 in a
disposable hook-less child process, and scanned for facade calls. The facade-to-hook mapping is derived from the vendored upstream JS so it cannot drift.
A static scan cannot see calls reached through dynamic expressions; the report says so instead of promising readiness.
"""

from __future__ import annotations

import hashlib
import re
from functools import cache
from pathlib import Path


from dtflow.scripting.engine import MAX_SOURCE_BYTES, api_version, shared_script
from dtflow.scripting.hooks import HOOKS, IMPLEMENTED, PLANNED, UNSUPPORTED

_SCOPE = re.compile(r"^(?:const|class|function)\s+([A-Za-z_]\w*)")
_MEMBER = re.compile(
    r"^(?P<indent> +)(?:static +)?(?:(?:get|set) +)?(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*(?://.*)?$"
)
_NESTED = re.compile(r"^ {2}([A-Za-z_]\w*): \{$")
_HOOK = re.compile(r"__dtHooks\.([A-Za-z_]\w*)")
_FACADE_CALL = re.compile(r"\b(canvas|pipeline|filesystem|device)\.((?:pictures\.)?[A-Za-z_]\w*)")
_STRIP = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|//[^\n]*|/\*.*?\*/", re.DOTALL)
_GLOBALS = ("requestFromUser", "__dtSleep", "console.log", "console.warn", "console.error", "console.repl")
_DYNAMIC = (
    (re.compile(r"\b(?:canvas|pipeline|filesystem|device)\s*\["), "computed member access on a facade object"),
    (re.compile(r"\beval\s*\("), "eval"),
    (re.compile(r"\bnew\s+Function\b|\bFunction\s*\("), "Function constructor"),
    (re.compile(r"\bglobalThis\b|\bwindow\b"), "global object access"),
    (re.compile(r"\b(?:require|import)\s*\("), "module loading (not available)"),
)
_KEYWORDS = frozenset({"if", "for", "while", "switch", "catch", "function", "return"})


@cache
def facade_map() -> dict[str, tuple[str, ...]]:
    """``scope.member`` -> hooks it calls, derived from the vendored JS (one transitive pass over facade calls)."""
    scope = ""
    nested = ""
    current: str | None = None
    bodies: dict[str, list[str]] = {}
    for line in shared_script().splitlines():
        top = _SCOPE.match(line)
        if top:
            scope, nested, current = top.group(1), "", None
            continue
        inner = _NESTED.match(line)
        if inner and scope:
            nested = inner.group(1) + "."
            continue
        member = _MEMBER.match(line)
        if member and scope and member.group("name") not in _KEYWORDS:
            depth = len(member.group("indent"))
            name = member.group("name")
            if depth == 2:
                nested = ""
            if depth in (2, 4):
                current = f"{scope}.{nested}{name}"
                bodies.setdefault(current, [])
                continue
        if current:
            bodies[current].append(line)
    result: dict[str, set[str]] = {key: set(_HOOK.findall("\n".join(lines))) for key, lines in bodies.items()}
    for _ in range(3):  # facade methods calling other facade members (e.g. moveCanvasToRect -> moveCanvas)
        for key, lines in bodies.items():
            for owner, member in _FACADE_CALL.findall("\n".join(lines)):
                result[key] |= result.get(f"{owner}.{member}", set())
    return {key: tuple(sorted(hooks)) for key, hooks in result.items()}


def _state(hooks: tuple[str, ...]) -> tuple[str, str]:
    """Worst state wins: unsupported > planned > implemented; owner of the first non-implemented hook."""
    worst, owner = IMPLEMENTED, "#113"
    for name in hooks:
        spec = HOOKS[name]
        if spec.state == UNSUPPORTED:
            return UNSUPPORTED, spec.owner
        if spec.state == PLANNED and worst == IMPLEMENTED:
            worst, owner = PLANNED, spec.owner
    return worst, owner


def syntax_error(source: str) -> str | None:
    """Parse the script with the vendored Acorn; the text is data and is never compiled or evaluated.

    Top-level ``return`` is accepted like upstream. Acorn follows the latest ECMAScript, so syntax newer than the
    execution engine supports can parse here and still fail at run time. The parse runs in a memory-capped child that
    the parent kills after a few seconds."""
    from dtflow.scripting.runner import check_syntax

    return check_syntax(source)


def analyze_script(path: Path) -> dict:
    data = Path(path).read_bytes()
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Script source is too large.")
    source = data.decode("utf-8")
    code = _STRIP.sub(" ", source)
    mapping = facade_map()
    counts: dict[str, int] = {}
    for owner, member in _FACADE_CALL.findall(code):
        counts[f"{owner}.{member}"] = counts.get(f"{owner}.{member}", 0) + 1
    for name in _GLOBALS:
        found = len(re.findall(r"\b" + re.escape(name) + r"\b", code))
        if found:
            counts[name] = found
    calls, unknown = [], []
    for api, count in sorted(counts.items()):
        if api in ("console.log", "console.warn", "console.error"):
            hooks: tuple[str, ...] = ("log",)
        elif api == "console.repl":
            hooks = ("repl",)
        elif api == "requestFromUser":
            hooks = ("requestFromUser",)
        elif api == "__dtSleep":
            hooks = ("sleep",)
        elif api in mapping:
            hooks = mapping[api]
        elif api.startswith("pipeline.configuration"):
            hooks = ("existingConfiguration",)
        else:
            unknown.append({"api": api, "count": count})
            continue
        state, owner = _state(hooks)
        calls.append({"api": api, "count": count, "hooks": list(hooks), "state": state, "owner": owner})
    dynamic = sorted({label for pattern, label in _DYNAMIC if pattern.search(code)})
    version = api_version(source)
    syntax = syntax_error(source)
    blocked = [call["api"] for call in calls if call["state"] == UNSUPPORTED]
    planned = sorted({call["owner"] for call in calls if call["state"] == PLANNED})
    warnings = []
    if version is None:
        warnings.append("No //@api-X header in the leading comment block; version cannot be confirmed.")
    elif version != "1.0":
        warnings.append(f"Unrecognized API version {version}; only 1.0 is known.")
    if dynamic:
        warnings.append("Dynamic constructs prevent a complete static call list: " + ", ".join(dynamic) + ".")
    return {
        "file": Path(path).name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "api_version": version,
        "syntax_valid": syntax is None,
        "syntax_error": syntax,
        "calls": calls,
        "unknown_apis": unknown,
        "unsupported_calls": blocked,
        "awaiting_issues": planned,
        "dynamic_constructs": dynamic,
        "warnings": warnings,
        "preflight": "incomplete" if (dynamic or unknown or version is None) else "static-calls-listed",
        "note": (
            "Conservative static report: nothing was executed. It lists calls visible in the source and cannot prove "
            "which paths a run will take or that a run will succeed."
        ),
    }

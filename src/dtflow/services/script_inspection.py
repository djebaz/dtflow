"""Static inspection of a script version for the Scripts page: Acorn syntax check plus hook verdicts.

Nothing here runs the script. The verdicts are derived from ``scripting.analysis`` (facade call scan) and the hook table
(``scripting.hooks``), so the page can only say what the engine itself would say.
"""

from __future__ import annotations

from dataclasses import dataclass

from dtflow.scripting.analysis import analyze_bytes, parse_syntax_error
from dtflow.scripting.hooks import HOOKS, IMPLEMENTED, PLANNED, UNSUPPORTED
from dtflow.scripting.syntax import is_parser_failure

# UI vocabulary for a hook row: supported / deferred (planned) / unsupported.
_UI_STATE = {IMPLEMENTED: "supported", PLANNED: "deferred", UNSUPPORTED: "unsupported"}


@dataclass(frozen=True, slots=True)
class HookRow:
    api: str
    state: str  # supported | deferred | unsupported
    detail: str


@dataclass(frozen=True, slots=True)
class Inspection:
    name: str
    sha256: str
    syntax_error: tuple[int, int, str] | None  # 1-based line, column, message
    verdict: str  # ok | warning | error
    support: str  # supported | deferred | unsupported | invalid
    tier: str
    title: str
    summary: str
    hooks: tuple[HookRow, ...]
    warnings: tuple[str, ...]
    api_version: str | None
    report: dict


def _detail(call: dict) -> str:
    names = ", ".join(call["hooks"])
    specs = [HOOKS[name] for name in call["hooks"]]
    if call["state"] == UNSUPPORTED:
        blocking = next(spec for spec in specs if spec.state == UNSUPPORTED)
        return f"{blocking.name}: {blocking.note or 'not offered in DTFlow'}"
    if call["state"] == PLANNED:
        return f"Awaiting {call['owner']} · hooks: {names}"
    note = next((spec.note or spec.maps_to for spec in specs if spec.state == IMPLEMENTED), "")
    return f"{note} · hooks: {names}" if note else f"hooks: {names}"


def inspect_source(data: bytes, name: str) -> Inspection:
    report = analyze_bytes(data, name)
    calls = report["calls"]
    rows = [HookRow(call["api"], _UI_STATE[call["state"]], _detail(call)) for call in calls]
    rows += [
        HookRow(item["api"], "deferred", "Not in the vendored script API; cannot be classified")
        for item in report["unknown_apis"]
    ]
    syntax = parse_syntax_error(report["syntax_error"]) if report["syntax_error"] else None
    awaiting = report["awaiting_issues"]
    warnings = tuple(report["warnings"])
    if syntax is not None:
        support, verdict, tier = "invalid", "error", "Not parsed"
        if is_parser_failure(syntax[2]):
            title, tier = "Analysis incomplete", "Not analyzed"
            summary = syntax[2] + " Run is blocked until parsing completes."
        else:
            title = "Syntax error"
            summary = f"Line {syntax[0]}, column {syntax[1]}: {syntax[2]}. Nothing was executed."
    elif report["unsupported_calls"]:
        support, verdict, tier = "unsupported", "error", "Unsupported"
        title = "Not runnable · unsupported hooks"
        summary = "Cannot start: " + ", ".join(report["unsupported_calls"]) + " is unsupported in DTFlow."
    elif awaiting:
        support, verdict, tier = "deferred", "warning", "Awaiting " + ", ".join(awaiting)
        title = "Hooks not available yet"
        summary = "Calls are recognised but their hooks are not implemented yet: " + ", ".join(awaiting) + "."
    elif report["preflight"] == "incomplete":
        support, verdict, tier = "supported", "warning", "Tier A"
        title = "Static inspection limited"
        summary = "Dynamic calls are checked at run time."
    else:
        support, verdict, tier = "supported", "ok", "Tier A"
        title = "No blocking issues found"
        summary = "Static inspection identified supported calls. Dynamic forms can appear at run time."
    return Inspection(
        name=name,
        sha256=report["sha256"],
        syntax_error=syntax,
        verdict=verdict,
        support=support,
        tier=tier,
        title=title,
        summary=summary,
        hooks=tuple(rows),
        warnings=warnings,
        api_version=report["api_version"],
        report=report,
    )

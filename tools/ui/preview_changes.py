"""Choose UI render representatives for a pull request (no Qt, standard library only).

    python tools/ui/preview_changes.py --base origin/main --head HEAD [--force-all] [--json OUT]

Prints changed paths, mapping reasons and exact representative states. Uncertain impact uses page representatives
or the shared fallback pack with an explicit coverage-incomplete warning. Only force-all selects every state."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "tools" / "ui" / "preview_dependencies.json"
CATALOG = ROOT / "tools" / "ui" / "preview_catalog.json"
COVERAGE = ROOT / "tools" / "ui" / "preview_coverage.json"


def glob_regex(pattern):
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out, index = out + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            out, index = out + ".*", index + 2
        elif pattern[index] == "*":
            out, index = out + "[^/]*", index + 1
        else:
            out, index = out + re.escape(pattern[index]), index + 1
    return re.compile(out + r"\Z")


def load_rules(path=RULES):
    rules = json.loads(path.read_text(encoding="utf-8"))["rules"]
    return [(rule, [glob_regex(p) for p in rule["paths"]]) for rule in rules]


def load_coverage(catalog):
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    if coverage.get("schema_version") != 1:
        raise ValueError("Unsupported preview coverage schema")
    packs = coverage["packs"]
    known = {state["id"] for state in catalog}
    for name, states in packs.items():
        if not states or len(states) != len(set(states)) or set(states) - known:
            raise ValueError(f"Invalid representative pack: {name}")
    if "shared" not in packs:
        raise ValueError("Missing shared fallback pack")
    for component in coverage["components"]:
        if component["pack"] not in packs or not component["paths"] or not component["why"]:
            raise ValueError("Invalid component mapping")
    return coverage


def changed_paths(base, head):
    merge_base = subprocess.run(
        ["git", "merge-base", base, head], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff", "--name-status", "-M", "--no-renames", merge_base, head],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # --no-renames reports a rename as delete + add, so both the old and the new path are checked.
    return merge_base, sorted({line.split("\t", 1)[1] for line in diff.splitlines() if "\t" in line})


def representative_plan(changed, rules, coverage, catalog):
    """Resolve first-match component rules, then page sampling; preserve catalogue order."""
    selected, selected_packs, reasons, warnings = set(), set(), [], []
    components = [(rule, [glob_regex(p) for p in rule["paths"]]) for rule in coverage["components"]]
    packs = coverage["packs"]
    for path in changed:
        rule = next((r for r, patterns in rules if any(p.match(path) for p in patterns)), None)
        # Tooling paths can also appear in component rules (including old names
        # during a migration). They exercise the baseline, not that component's UI.
        if rule and rule.get("effect") == "shared":
            selected.update(packs["shared"])
            selected_packs.add("shared")
            reasons.append(f"{path}: {rule['why']} -> shared")
            warning = "preview tooling changed; one representative per view, not exhaustive coverage"
            if warning not in warnings:
                warnings.append(warning)
            continue
        component = next((r for r, patterns in components if any(p.match(path) for p in patterns)), None)
        if component:
            selected.update(packs[component["pack"]])
            selected_packs.add(component["pack"])
            reasons.append(f"{path}: {component['why']} -> {component['pack']}")
            if component.get("gap"):
                warnings.append(f"{path}: {component['gap']}")
            continue
        if rule and "effect" in rule:
            reasons.append(f"{path}: {rule['why']} ({rule['effect']})")
            continue
        categories = rule.get("categories", []) if rule else []
        names = [f"page.{category}" for category in categories] or ["shared"]
        for name in names:
            selected.update(packs[name])
            selected_packs.add(name)
        warning = f"{path}: no component mapping; sampling {', '.join(names)}"
        reasons.append(warning)
        warnings.append(warning)
    return (
        [state["id"] for state in catalog if state["id"] in selected],
        [name for name in packs if name in selected_packs],
        reasons,
        warnings,
    )


def plan(base, head, force_all=False):
    result = {"mode": "full", "reasons": [], "changed": [], "args": ["-s"], "groups": [], "warnings": []}
    if force_all:
        result["reasons"].append("force-all requested")
        return result
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["states"]
    coverage = load_coverage(catalog)
    try:
        merge_base, changed = changed_paths(base, head)
    except (subprocess.CalledProcessError, OSError) as error:
        changed, merge_base = [], None
        warning = f"could not compute the diff against {base} ({error}); sampling shared pack"
        result["warnings"].append(warning)
        states = coverage["packs"]["shared"]
        selected_packs = ["shared"]
        reasons = [warning]
    else:
        states, selected_packs, reasons, warnings = representative_plan(changed, load_rules(), coverage, catalog)
        result["warnings"].extend(warnings)
    result.update(changed=changed, merge_base=merge_base, reasons=reasons)
    result["coverage_complete"] = not result["warnings"]
    result["packs"] = selected_packs
    if states:
        result.update(
            mode="representatives", states=states, args=[arg for state in states for arg in ("--state", state)]
        )
        result["groups"] = sorted({f"{s['category']}/{s['subcategory']}" for s in catalog if s["id"] in states})
    else:
        # Explicit PART requests must remain small even when no UI path is mapped.
        states = coverage["packs"]["shared"]
        result.update(
            mode="representatives",
            states=states,
            args=[arg for state in states for arg in ("--state", state)],
            packs=["shared"],
            coverage_complete=False,
        )
        warning = "no screenshot-impacting paths; sampling shared representatives, not exhaustive coverage"
        result["warnings"].append(warning)
        result["reasons"].append(warning)
    selected_ids = set(states) if states else {state["id"] for state in catalog if state["level"] == "f"}
    selected_rows = [state for state in catalog if state["id"] in selected_ids]
    result["states"] = [state["id"] for state in selected_rows]
    result["fixtures"] = list(dict.fromkeys(state["fixture"] for state in selected_rows))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--force-all", action="store_true")
    parser.add_argument("--json", help="Write the plan to this file")
    args = parser.parse_args(argv)
    result = plan(args.base, args.head, args.force_all)
    if result["mode"] == "full":
        print("Preview selection: full (-s, explicit exhaustive override)")
    else:
        print(
            f"Preview selection: {result['mode']}; {len(result['states'])} screenshots, "
            f"{len(result['fixtures'])} prerequisite fixture groups"
        )
        if result["packs"]:
            print(f"  Packs: {', '.join(result['packs'])}")
    for line in result["reasons"]:
        print(f"  {line}")
    for warning in result["warnings"]:
        print(f"::warning::Coverage incomplete: {warning}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

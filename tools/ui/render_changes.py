"""Choose which UI render groups a pull request needs (no Qt, standard library only).

    python tools/ui/render_changes.py --base origin/main --head HEAD [--force-all] [--json OUT]

Prints the changed paths, the matching rule for each, and the render arguments to use. Anything that is not
explicitly mapped in render_dependencies.json (shared styles, forms, core code, the renderer itself, dependency
files, missing history, errors) falls back to a full `-s` render with the reason spelled out."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "tools" / "ui" / "render_dependencies.json"
CATALOG = ROOT / "tools" / "ui" / "render_catalog.json"


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


def classify(changed, rules):
    """Return (categories, reasons, full). `full` is True when any path is unmapped."""
    categories, reasons, full, effects = set(), [], False, set()
    for path in changed:
        match = next((rule for rule, patterns in rules if any(p.match(path) for p in patterns)), None)
        if match is None:
            full = True
            reasons.append(f"{path}: not mapped, so everything is rendered")
        elif "categories" in match:
            categories.update(match["categories"])
            reasons.append(f"{path}: {match['why']} -> {', '.join(match['categories'])}")
        else:
            effects.add(match["effect"])
            reasons.append(f"{path}: {match['why']} ({match['effect']})")
    return categories, reasons, full, effects


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


def groups_of(categories):
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["states"]
    return sorted({f"{s['category']}/{s['subcategory']}" for s in catalog if s["category"] in categories})


def plan(base, head, force_all=False):
    result = {"mode": "full", "reasons": [], "changed": [], "args": ["-s"], "groups": []}
    if force_all:
        result["reasons"].append("force-all requested")
        return result
    try:
        merge_base, changed = changed_paths(base, head)
    except (subprocess.CalledProcessError, OSError) as error:
        result["reasons"].append(f"could not compute the diff against {base} ({error}); rendering everything")
        return result
    result["changed"], result["merge_base"] = changed, merge_base
    if not changed:
        result["reasons"].append("no changed paths; rendering the fast level")
        result.update(mode="fast", args=["-f"])
        return result
    categories, reasons, full, effects = classify(changed, load_rules())
    result["reasons"] = reasons
    if full:
        return result
    if categories:
        groups = groups_of(categories)
        result.update(mode="groups", groups=groups, categories=sorted(categories))
        result["args"] = [arg for group in groups for arg in ("-g", group)]
    else:
        # Only documentation or non-render files changed: keep a cheap smoke render so the job still proves startup.
        result.update(mode="fast", args=["-f"])
        result["reasons"].append(f"only {', '.join(sorted(effects))} paths changed; rendering the fast level")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--force-all", action="store_true")
    parser.add_argument("--json", help="Write the plan to this file")
    args = parser.parse_args(argv)
    result = plan(args.base, args.head, args.force_all)
    print(f"Render selection: {result['mode']} ({' '.join(result['args'])})")
    for line in result["reasons"]:
        print(f"  {line}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

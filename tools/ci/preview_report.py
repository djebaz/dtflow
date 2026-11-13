"""Build shared Preview summaries and one sticky PR comment from actual output."""

from __future__ import annotations

import html
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from preview_publish import BRANCH, OUT, ROOT, current, gh

MARKER = "<!-- dtflow-ui-preview -->"
LEGACY_MARKERS = ("<!-- dtflow-ui-renders -->", "<!-- dtflow-lora-designer-renders -->")
THUMBNAIL_LIMIT = 48


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def thumbnail_sections(pngs, by_id, base):
    """Share the comment budget across categories, not just the first filenames."""
    groups = {}
    for png in pngs:
        relative = png.relative_to(OUT)
        category = (
            "workflow file" if relative.parts[0] == "workflow" else by_id.get(png.stem, {}).get("category", "other")
        )
        groups.setdefault(category, []).append(png)
    groups = dict(sorted(groups.items()))
    chosen = {category: [] for category in groups}
    shown = 0
    while shown < min(THUMBNAIL_LIMIT, len(pngs)):
        for category, paths in groups.items():
            index = len(chosen[category])
            if index < len(paths) and shown < THUMBNAIL_LIMIT:
                chosen[category].append(paths[index])
                shown += 1
    lines = []
    for category, paths in groups.items():
        title = "LoRA" if category == "lora" else category.title()
        # No `open` attribute: every category starts collapsed. GitHub strips inline CSS;
        # a semantic heading supplies the requested larger, clickable title instead.
        lines += [f"<details><summary><h3>{html.escape(title)} · {len(paths)} previews</h3></summary>", ""]
        visible = chosen[category]
        if len(visible) != len(paths):
            lines += [
                f"Showing {len(visible)} of {len(paths)} previews in this category; browse/download all images below.",
                "",
            ]
        lines.append("<table>")
        for start in range(0, len(visible), 3):
            lines.append("<tr>")
            for png in visible[start : start + 3]:
                name = png.relative_to(OUT).as_posix()
                url = html.escape(f"{base}/{quote(name)}", quote=True)
                label = html.escape(name, quote=True)
                lines.append(
                    f'<td width="33%" valign="top"><a href="{url}"><img src="{url}" width="100%" alt="{label}"></a><br><sub>{label}</sub></td>'
                )
            lines.append("</tr>")
        lines += ["</table>", "", "</details>", ""]
    return lines, shown


def main():
    plan = read_json(OUT / "request.json", {})
    selection = plan.get("selection", {})
    outcomes = read_json(OUT / "outcomes.json", {})
    pngs = sorted(OUT.rglob("*.png"))
    sha, pr = os.environ["HEAD_SHA"], os.environ.get("PR_NUMBER")
    run_url = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    passed = (
        os.environ.get("CAPTURE_OUTCOME") == "success"
        and outcomes
        and all(item["exit_code"] == 0 for item in outcomes.values())
    )
    catalog = read_json(ROOT / "tools/ui/preview_catalog.json", {"states": []})["states"]
    by_id = {row["id"]: row for row in catalog}
    planned = selection.get("states", [])
    actual_states = {path.stem for path in (OUT / "states").rglob("*.png")}
    missing = [state for state in planned if state not in actual_states]
    passed = passed and bool(pngs) and not missing
    counts = Counter(by_id[state]["category"] for state in actual_states if state in by_id)
    manifests = [read_json(path, {}) for path in (OUT / "states").glob("preview-manifest-*.json")]
    combined = next((data for data in manifests if "workers" in data), {})
    waits = combined.get("wait_timeouts", [])
    warnings = selection.get("warnings", [])
    lines = [
        MARKER,
        f"## Preview · {'Passed' if passed else 'Failed / incomplete'}",
        "",
        f"- Source: {'PR #' + pr if pr else 'dispatch ref'} · `{sha}` · [Action run]({run_url}).",
        f"- Mode: **{plan.get('mode', 'unknown')}**"
        + (f" · category **{plan['category']}**" if plan.get("category") else "")
        + ".",
        f"- PNGs saved: **{len(pngs)}** · planned registered states: **{len(planned)}** · missing: **{len(missing)}**.",
    ]
    if counts:
        lines.append(
            "- Saved categories: " + ", ".join(f"{name}: {count}" for name, count in sorted(counts.items())) + "."
        )
    if plan.get("workflow"):
        lines.append(f"- Workflow JSON: `{plan['workflow']}` (strict offline import; no execution).")
    for name, item in outcomes.items():
        lines.append(f"- {name}: exit **{item['exit_code']}**; deadline {item['timeout_seconds']}s.")
    if not outcomes:
        lines.append("- Capture did not produce an outcome report; inspect setup/capture logs.")
    if missing:
        lines += ["", "**Missing planned states:** " + ", ".join(f"`{name}`" for name in missing[:20])]
    if warnings:
        lines += ["", "**Coverage incomplete: representative sampling, not exhaustive coverage.**"]
        lines += [f"- {warning}" for warning in warnings[:8]]
    if waits:
        lines += [
            "",
            f"**Fixture waits timed out ({len(waits)}); captures show the state reached, not accepted behavior.**",
        ]
    if combined.get("workers"):
        lines += ["", "<details><summary>Capture workers</summary>", ""]
        lines += [
            f"- {name}: {row['elapsed_seconds']:.1f}s, exit {row['exit_code']}"
            for name, row in combined["workers"].items()
        ]
        lines += ["", "</details>"]
    artifact = os.environ.get("ARTIFACT_URL")
    lines += [
        "",
        f"[Download PNGs and diagnostics]({artifact})"
        if artifact
        else f"Artifact unavailable; inspect the [run]({run_url}).",
    ]
    lines += [
        "Requested artifact retention: two days. The manual Artifact Sweeper can delete artifacts older than 30 minutes.",
        "",
    ]
    if pngs and os.environ.get("PUBLISHED") == "true" and pr:
        base = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/raw/{BRANCH}/pr{pr}/{sha}"
        sections, shown = thumbnail_sections(pngs, by_id, base)
        lines += sections
        tree = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/tree/{BRANCH}/pr{pr}/{sha}"
        count = f"all {shown}" if shown == len(pngs) else f"{shown} of {len(pngs)}"
        lines.append(f"Showing {count} previews. [Browse image files]({tree}); the artifact also contains every image.")
    elif pngs:
        lines += ["Inline previews unavailable/not requested; all PNGs are in the artifact."]
    reasons = selection.get("reasons", [])
    if reasons:
        lines += ["", "<details><summary>Selection reasons</summary>", ""]
        lines += [f"- {reason}" for reason in reasons[:40]]
        lines += [
            "",
            "Full selection and exact state/fixture lists are in request.json in the artifact.",
            "",
            "</details>",
        ]
    lines += ["", "_Offline layout evidence only; not native Windows/DPI, packaged or provider acceptance._", ""]
    note = "\n".join(lines)
    (OUT / "summary.md").write_text(note, encoding="utf-8", newline="\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8", newline="\n") as summary:
        summary.write(note)
    if not pr or not current():
        print("No PR or request superseded/draft; sticky comment not updated.")
        return 0
    pages = json.loads(
        gh("api", f"repos/{os.environ['GITHUB_REPOSITORY']}/issues/{pr}/comments", "--paginate", "--slurp")
    )
    comments = [comment for page in pages for comment in page if comment["user"]["type"] == "Bot"]
    matching = [comment for comment in comments if MARKER in comment["body"]]
    if not matching:
        matching = [comment for comment in comments if any(marker in comment["body"] for marker in LEGACY_MARKERS)]
    if matching:
        endpoint = f"repos/{os.environ['GITHUB_REPOSITORY']}/issues/comments/{matching[-1]['id']}"
        method = "PATCH"
    else:
        endpoint = f"repos/{os.environ['GITHUB_REPOSITORY']}/issues/{pr}/comments"
        method = "POST"
    payload = OUT / "comment.json"
    payload.write_text(json.dumps({"body": note}), encoding="utf-8", newline="\n")
    gh("api", "--method", method, endpoint, "--input", str(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

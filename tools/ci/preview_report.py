"""Build shared Preview summaries and one sticky PR comment from actual output."""

from __future__ import annotations

import html
import json
import os
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
        # Inline emphasis keeps the toggle compact; headings add excessive vertical margins.
        # No `open` attribute: every category starts collapsed.
        lines += [f"<details><summary><strong>{html.escape(title)}</strong> [{len(paths)}]</summary>", ""]
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
    manifests = [read_json(path, {}) for path in (OUT / "states").glob("preview-manifest-*.json")]
    combined = next((data for data in manifests if "workers" in data), {})
    waits = combined.get("wait_timeouts", [])
    warnings = selection.get("warnings", [])
    artifact = os.environ.get("ARTIFACT_URL")
    repo_url = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
    mode = plan.get("mode", "unknown") + (f" / {plan['category']}" if plan.get("category") else "")
    source = f"PR #{pr}" if pr else "dispatch ref"
    links = [f"[Run]({run_url})"]
    if artifact:
        links.append(f"[Download]({artifact})")
    published = pngs and os.environ.get("PUBLISHED") == "true" and pr
    if published:
        tree = f"{repo_url}/tree/{BRANCH}/pr{pr}/{sha}"
        links.append(f"[Browse images]({tree})")
    state_count = f" · **{len(planned) - len(missing)}/{len(planned)} states**" if planned else ""
    lines = [
        MARKER,
        f"## Preview · {'Passed' if passed else 'Failed / incomplete'}",
        "",
        f"**{mode}** · [{source} @ {sha[:7]}]({repo_url}/commit/{sha}) · **{len(pngs)} images**"
        + state_count
        + " · "
        + " · ".join(links),
        "",
    ]
    if warnings:
        lines += ["⚠ Representative sample, not exhaustive; mapping details in Diagnostics.", ""]
    if waits:
        lines += [f"⚠ {len(waits)} fixture waits timed out; captured states may be incomplete.", ""]
    failures = [f"{name}: exit {item['exit_code']}" for name, item in outcomes.items() if item["exit_code"] != 0]
    problems = ([f"{len(missing)} missing states"] if missing else []) + failures
    if not outcomes:
        problems.append("capture outcome unavailable")
    if not artifact:
        problems.append("artifact unavailable")
    if problems:
        lines += ["**Attention:** " + "; ".join(problems) + ". See Diagnostics / Run.", ""]
    if published:
        base = f"{repo_url}/raw/{BRANCH}/pr{pr}/{sha}"
        sections, shown = thumbnail_sections(pngs, by_id, base)
        if shown < len(pngs):
            lines += [f"Showing {shown} of {len(pngs)} images; Download / Browse images includes every file.", ""]
        lines += sections
    elif pngs:
        lines += ["Inline images unavailable/not requested; use Download / Run.", ""]
    lines += [
        "<details><summary>Diagnostics</summary>",
        "",
        "| Detail | Value |",
        "|---|---|",
        f"| Commit | `{sha}` |",
        f"| Capture | {len(pngs)} images; {len(planned)} planned states; {len(missing)} missing |",
        "| Artifact retention | Requested: 2 days; manual Sweeper can delete after 30 minutes |",
    ]
    if plan.get("workflow"):
        lines.append(f"| Workflow file | `{plan['workflow']}` (strict offline import; no execution) |")
    for name, item in outcomes.items():
        lines.append(f"| {name} | Exit {item['exit_code']}; deadline {item['timeout_seconds']}s |")
    for name, row in combined.get("workers", {}).items():
        lines.append(f"| Worker `{name}` | {row['elapsed_seconds']:.1f}s; exit {row['exit_code']} |")
    if missing:
        lines += ["", "Missing: " + ", ".join(f"`{name}`" for name in missing[:20])]
    if waits:
        lines += ["", "Timed-out fixture waits: " + ", ".join(f"`{name}`" for name in waits)]
    # Keep path-by-path information out of the main review flow. Deduplicate warnings
    # already present as reasons; omit no-impact paths, whose full record stays in the artifact.
    details = list(
        dict.fromkeys(
            [
                *warnings,
                *(reason for reason in selection.get("reasons", []) if not reason.endswith(("(docs)", "(none)"))),
            ]
        )
    )
    if details:
        lines += ["", "Selection notes:", "```text", *details[:40], "```"]
    lines += [
        "",
        "Full selection, exact states, prerequisites and logs: Download → request.json / manifests.",
        "",
        "</details>",
        "",
        "_Offline layout only—not Windows/DPI, packaged or provider acceptance._",
        "",
    ]
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

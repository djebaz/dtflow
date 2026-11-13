"""Publish latest-per-PR PNGs without keeping screenshot history in source branches.

Retains the existing ci-render-previews branch for old links. A lease-protected,
parentless commit preserves the other PR directories and bounds branch history.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tmp/ui-previews"
BRANCH = "ci-render-previews"


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout.strip()


def current():
    """Never replace latest previews with an obsolete head or an automatic draft run."""
    pr = os.environ.get("PR_NUMBER")
    if not pr:
        return True
    data = json.loads(gh("api", f"repos/{os.environ['GITHUB_REPOSITORY']}/pulls/{pr}"))
    automatic = os.environ["GITHUB_EVENT_NAME"] == "pull_request"
    return (
        data["head"]["sha"] == os.environ["HEAD_SHA"]
        and (data["head"].get("repo") or {}).get("full_name") == os.environ["GITHUB_REPOSITORY"]
        and (not automatic or (not data["draft"] and data["state"] == "open"))
    )


def main():
    pr, sha = os.environ["PR_NUMBER"], os.environ["HEAD_SHA"]
    if not re.fullmatch(r"[1-9][0-9]*", pr) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid PR/commit identity")
    pngs = sorted(OUT.rglob("*.png"))
    if not pngs or not current():
        print("No PNGs or request superseded/draft; inline publication skipped.")
        return 0
    gh("auth", "setup-git")
    repo = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}.git"
    for attempt in range(3):
        if not current():
            return 0
        with tempfile.TemporaryDirectory(prefix="ui-preview-publish-") as directory:
            work = Path(directory)

            def git(*args, check=True, cwd=work):
                return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)

            git("init", "-q")
            git("remote", "add", "origin", repo)
            # Distinguish a genuinely absent branch from a fetch/auth/network failure.
            refs = git("ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}").stdout.strip()
            old = ""
            if refs:
                git("fetch", "-q", "--depth", "1", "origin", f"refs/heads/{BRANCH}")
                old = git("rev-parse", "FETCH_HEAD").stdout.strip()
                git("checkout", "-q", "-b", BRANCH, "FETCH_HEAD")
            else:
                git("checkout", "-q", "--orphan", BRANCH)
            destination = work / f"pr{pr}"
            if destination.is_symlink() or destination.resolve().parent != work.resolve():
                raise ValueError("Refusing redirected PR preview directory")
            if destination.exists():
                shutil.rmtree(destination)
            for png in pngs:
                target = destination / sha / png.relative_to(OUT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(png, target)
            git("add", "-A")
            tree = git("write-tree").stdout.strip()
            commit = git(
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit-tree",
                tree,
                "-m",
                "UI previews (latest per PR)",
            ).stdout.strip()
            if not current():
                return 0
            pushed = git(
                "push",
                "-q",
                f"--force-with-lease=refs/heads/{BRANCH}:{old}",
                "origin",
                f"{commit}:refs/heads/{BRANCH}",
                check=False,
            )
            if pushed.returncode == 0:
                with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as output:
                    output.write("published=true\n")
                return 0
        time.sleep((attempt + 1) * 3)
    raise RuntimeError("Could not publish previews after three lease attempts; artifact remains the fallback")


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".ps1",
    ".bat",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".qss",
    ".svg",
    ".csv",
    ".ipynb",
}

EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "build",
    "dist",
    "tmp",
    "tmp_files",
    "__pycache__",
}

WINDOWS_PATH_RE = re.compile(r"(?i)\b[A-Z]:[\\/](?:Users|Documents and Settings|home|tmp|Temp|Program Files)[\\/][^\s\"'<>|]+")
UNIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home|var|tmp|opt|mnt|srv)/[^\s\"'<>|]+")


def run_git(args):
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def should_scan(path: Path) -> bool:
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return False
    parts = set(path.parts)
    return not any(part in parts for part in EXCLUDED_PARTS)


def changed_files(base_ref: str, head_ref: str) -> list[Path]:
    output = run_git(["diff", "--name-only", "--diff-filter=ACMR", base_ref, head_ref])
    return [Path(line.strip()) for line in output.splitlines() if line.strip()]


def tracked_files() -> list[Path]:
    output = run_git(["ls-files"])
    return [Path(line.strip()) for line in output.splitlines() if line.strip()]


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    except OSError:
        return []
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for regex in (WINDOWS_PATH_RE, UNIX_PATH_RE):
            for match in regex.finditer(line):
                findings.append(f"{path}:{lineno}: {match.group(0)}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail on absolute local path leaks in tracked text files.")
    parser.add_argument("--mode", choices=("changed", "all"), default="changed")
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--head-ref", default="HEAD")
    args = parser.parse_args()

    repo_root = Path.cwd()
    if args.mode == "all":
        files = tracked_files()
    else:
        base_ref = args.base_ref.strip()
        if not base_ref:
            merge_base = run_git(["merge-base", "origin/main", args.head_ref]).strip()
            if not merge_base:
                raise RuntimeError("unable to resolve merge-base against origin/main")
            base_ref = merge_base
        files = changed_files(base_ref, args.head_ref)

    findings: list[str] = []
    for rel_path in files:
        full_path = repo_root / rel_path
        if not should_scan(rel_path):
            continue
        findings.extend(scan_file(full_path))

    if findings:
        print("Absolute local path leak(s) detected:")
        for item in findings:
            print(item)
        return 1

    print("No absolute local path leaks detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

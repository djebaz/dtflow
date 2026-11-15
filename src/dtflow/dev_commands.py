"""Small source-checkout shortcuts shared by local development commands."""

import argparse
import os
from pathlib import Path
import subprocess
import sys


def _root() -> Path:
    root = Path(__file__).resolve().parents[2]
    if not (root / "pyproject.toml").is_file() or not (root / "tools/ci/fast_tests.txt").is_file():
        raise SystemExit("Run these developer shortcuts from an editable DTFlow source checkout.")
    return root


def check() -> int:
    parser = argparse.ArgumentParser(description="Check Ruff lint and formatting.")
    parser.add_argument("-f", "--fix", action="store_true", help="Apply lint fixes and formatting.")
    args = parser.parse_args()
    root = _root()
    commands = (
        ["check", ".", *(["--fix"] if args.fix else [])],
        ["format", ".", *([] if args.fix else ["--check"])],
    )
    result = 0
    for command in commands:
        code = subprocess.run([sys.executable, "-m", "ruff", *command], cwd=root).returncode
        if code and not result:
            result = code
    return result


def test() -> int:
    parser = argparse.ArgumentParser(description="Run the offline pytest suite (Fast by default).")
    parser.add_argument("-f", "--full", action="store_true", help="Run the full suite instead of Fast.")
    args = parser.parse_args()
    root = _root()
    command = [sys.executable, "-m", "pytest", "-n", "auto"]
    if not args.full:
        targets = [
            line.strip()
            for line in (root / "tools/ci/fast_tests.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not targets:
            parser.error("Fast test selection is empty.")
        command.extend(["--durations=0", "--durations-min=0", *targets])
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run(command, cwd=root, env=env).returncode

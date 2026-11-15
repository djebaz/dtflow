# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = ["numpy==2.3.5", "kmeans1d==0.5.1"]
# ///
"""Minimal source-only launcher for the ordinary experimental LoRA command."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dtflow.lora_cli import add_commands, run_command  # noqa: E402


def main():
    parser = argparse.ArgumentParser(prog="dtflow-q8p")
    add_commands(parser.add_subparsers(required=True))
    return run_command(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

"""Extract the upstream Draw Things SharedScript JavaScript from SharedScript.swift.

Developer tool: it never runs at application runtime. Usage:

    python tools/scripting/extract_shared_script.py SharedScript.swift [--check]

Without --check it writes the vendored resource; with --check it only compares and exits non-zero on drift.
Pinned upstream: drawthingsai/draw-things-community e2c7fa474ce3bddfe34ad20bdde75f425344800e,
Libraries/Scripting/Sources/SharedScript.swift.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

PINNED_COMMIT = "e2c7fa474ce3bddfe34ad20bdde75f425344800e"
PINNED_SWIFT_SHA256 = "1d04fa29395b274f4b89657b3b8c0bf3514c3e14fcdf7de47c093e5c5c1cfad3"
RESOURCE = Path(__file__).resolve().parents[2] / "src/dtflow/scripting/resources/shared_script.js"
START = '  public static let SharedScript = """\n'
END = '    """\n}'
HEADER = f"""// Vendored from Draw Things community scripting (GPL-3.0).
// Source: https://github.com/drawthingsai/draw-things-community/blob/{PINNED_COMMIT}/Libraries/Scripting/Sources/SharedScript.swift
// Upstream SharedScript.swift SHA-256: {PINNED_SWIFT_SHA256}
// Extracted verbatim from the Swift multi-line string (4-space indent removed). Do not edit: re-run
// tools/scripting/extract_shared_script.py against the pinned upstream file. DTFlow overlays live in
// dtflow/scripting/engine.py, never in this file.
"""


def extract(swift: str) -> str:
    start = swift.index(START) + len(START)
    end = swift.index(END, start)
    body = swift[start:end]
    if "\\" in body:
        raise SystemExit("Swift escape sequences found; extend the extractor before vendoring.")
    lines = []
    for line in body.split("\n"):
        if line.strip() and not line.startswith("    "):
            raise SystemExit(f"Unexpected indentation: {line!r}")
        lines.append(line[4:] if line.startswith("    ") else line)
    return HEADER + "\n".join(lines).rstrip("\n") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("swift", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = args.swift.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if digest != PINNED_SWIFT_SHA256:
        print(f"Upstream file hash {digest} does not match the pinned {PINNED_SWIFT_SHA256}.", file=sys.stderr)
        return 1
    text = extract(source.decode("utf-8"))
    if args.check:
        same = RESOURCE.read_text(encoding="utf-8") == text
        print("vendored JS matches pinned upstream" if same else "vendored JS differs from pinned upstream")
        return 0 if same else 1
    RESOURCE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {RESOURCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

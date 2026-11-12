"""Extract the plain-literal weight tables of Draw Things' ``ModelMapping.swift`` to JSON.

Dev tool, run when the pinned Draw Things revision changes. The converter
(``src/dtflow/services/lora_convert``) ships the result as ``data/lora_mappings.json`` so nothing needs Swift at run time.

Usage:
    python extract_swift_mappings.py /path/to/draw-things-community/Libraries/SwiftDiffusion/Sources/Mappings/ModelMapping.swift OUT.json REVISION
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Swift table name -> JSON key
TABLES = {
    "UNet": "sd1_unet",
    "UNetXLBase": "sdxl_unet",
    "UNetXLBaseFixed": "sdxl_unet_fixed",
}
_ENTRY = re.compile(r'"([^"]+)":\s*\[([^\]]*)\]')
_NAME = re.compile(r'"([^"]+)"')


def extract(text: str, table: str) -> dict[str, list[str]]:
    start = re.search(rf"public static let {table}: ModelWeightMapping = \[\n", text)
    if start is None:
        raise SystemExit(f"table {table} not found")
    end = text.index("\n  ]\n", start.end())
    body = text[start.end() : end]
    out: dict[str, list[str]] = {}
    for key, names in _ENTRY.findall(body):
        if "ModelWeightElement" in names or "(" in names:
            raise SystemExit(f"{table}: unexpected element for {key}: {names!r}")
        out[key] = _NAME.findall(names)
    return out


_PAIR = re.compile(r'\("([^"]+)",\s*"([^"]+)"\)')


def extract_pairs(text: str, table: str) -> list[list[str]]:
    start = re.search(rf"public static let {table}: \[\(String, String\)\] = \[\n", text)
    if start is None:
        raise SystemExit(f"pairs {table} not found")
    end = text.index("\n  ]\n", start.end())
    return [list(pair) for pair in _PAIR.findall(text[start.end() : end])]


def main() -> int:
    source, destination, revision = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    text = source.read_text(encoding="utf-8")
    data: dict[str, object] = {"revision": revision}
    for swift_name, json_name in TABLES.items():
        data[json_name] = extract(text, swift_name)
        print(json_name, len(data[json_name]))
    data["sd1_unet_partials"] = extract_pairs(text, "UNetPartials")
    data["resnets_partials"] = extract_pairs(text, "ResNetsPartials")
    print("partials", len(data["sd1_unet_partials"]), len(data["resnets_partials"]))
    destination.write_text(json.dumps(data, indent=0, sort_keys=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

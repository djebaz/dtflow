"""Build a portable offline HTML gallery pairing Qt and design-system PNG captures."""

import argparse
import base64
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).with_name("comparison_gallery.html")


def scan(root):
    if not root.is_dir():
        raise ValueError(f"Capture directory not found: {root}")
    # Ignore auxiliary subset/debug runs when a full capture tree is supplied.
    folders = [root / name for name in ("screens", "states") if (root / name).is_dir()] or [root]
    return {path.relative_to(root).as_posix(): path for folder in folders for path in sorted(folder.rglob("*.png"))}


def image_record(path):
    data = path.read_bytes()
    if data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" or len(data) < 24:
        raise ValueError(f"Invalid PNG header: {path}")
    width, height = struct.unpack(">II", data[16:24])
    return {"width": width, "height": height, "src": "data:image/png;base64," + base64.b64encode(data).decode()}


def build_data(qt, design):
    left, right = scan(qt), scan(design)
    contract_path = qt / "states/contract.json"
    web_manifest = design / "states/manifest.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8")) if contract_path.exists() else None
    web_contract = (
        json.loads(web_manifest.read_text(encoding="utf-8")).get("state_contract") if web_manifest.exists() else None
    )
    pairs = []
    for name in sorted(left.keys() | right.keys()):
        group = (
            "states"
            if "states" in Path(name).parts or Path(name).name.startswith(tuple(str(n) for n in range(30, 40)))
            else "screens"
        )
        pairs.append(
            {
                "name": name,
                "group": group,
                "qt": image_record(left[name]) if name in left else None,
                "design": image_record(right[name]) if name in right else None,
            }
        )
        definition = (contract or {}).get("sheets", {}).get(Path(name).stem)
        if definition and contract == web_contract:
            width, height = definition["frame"]
            pairs[-1]["regions"] = [
                {
                    "label": row["label"] + " / " + state,
                    "x": 150 + c * (width * 2 + 12),
                    "y": 58 + r * (height * 2 + 42),
                    "width": width * 2,
                    "height": height * 2,
                }
                for r, row in enumerate(definition["rows"])
                for c, state in enumerate(row["states"])
            ]
    if not pairs:
        raise ValueError("No PNG captures found in either directory")
    evidence = []
    for label, root in [("Qt", qt), ("Design system", design)]:
        for folder in [root, root / "screens", root / "states"]:
            path = folder / "manifest.json"
            if path.exists():
                report = json.loads(path.read_text(encoding="utf-8"))
                evidence.append(
                    {
                        "side": label,
                        "file": str(path.resolve()),
                        "browser": report.get("browser"),
                        "bundle_sha256": report.get("bundle_sha256"),
                        "limits": report.get("limits", []),
                        "unsupported": report.get("unsupported", []),
                        "failures": report.get("failures", []),
                        "errors": report.get("errors", []),
                    }
                )
    return {
        "qt_source": str(qt.resolve()),
        "design_source": str(design.resolve()),
        "pairs": pairs,
        "evidence": evidence,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-q",
        "--qt",
        type=Path,
        default=ROOT / "tmp/qt-renders",
        help="Qt tree containing screens/ and states/, or a single capture folder",
    )
    parser.add_argument(
        "-d", "--design", type=Path, default=ROOT / "tmp/ds-renders", help="Design-system capture tree or single folder"
    )
    parser.add_argument(
        "-o", "--out", type=Path, default=ROOT / "tmp/render-gallery/index.html", help="Self-contained HTML file"
    )
    args = parser.parse_args()
    try:
        if args.out.suffix.lower() != ".html":
            raise ValueError("--out must be an .html file")
        data = build_data(args.qt, args.design)
        payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
        html = TEMPLATE.read_text(encoding="utf-8").replace("__CAPTURE_DATA__", payload)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(html, encoding="utf-8", newline="\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Gallery failed: {error}\n")
    matched = sum(bool(pair["qt"] and pair["design"]) for pair in data["pairs"])
    print(f"{args.out.resolve()} — {matched} paired, {len(data['pairs']) - matched} unmatched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

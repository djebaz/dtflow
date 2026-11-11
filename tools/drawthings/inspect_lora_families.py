"""Inspect converted Draw Things .ckpt tensor metadata without reading weights.

PowerShell:
    python tools/drawthings/inspect_lora_families.py -d "C:\LoRAs" --json lora-report.json
    python tools/drawthings/inspect_lora_families.py "C:\LoRAs\zimage.ckpt" "C:\LoRAs\krea.ckpt" --json lora-report.json

Directory mode reads all .ckpt files directly inside the directory, without
recursing into subdirectories. Explicit file paths can be used alongside -d.

This standard-library-only probe opens SQLite files read-only and extracts names,
shapes and adapted module patterns. It mirrors #371's confirmed Z-Image/Krea 2
fingerprints and also reports non-authoritative architecture-width *hints*
for other model families. Widths are NOT a family classification: e.g. both
Krea 2 and FLUX.2 use 6144, and 3072 belongs to several architectures.
No tensor weights, absolute paths, prompts or credentials enter the output.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import struct
import sys

MAX_TENSORS = 20_000
SHAPE_TEXT = re.compile(r"^\s*[\[(]\s*\d+(?:\s*,\s*\d+)*\s*[\])]\s*$")
DIT_LORA = re.compile(r"^__dit__\[t-([^\]]+)\]__(?:up|down)__$")
MODULE_LAYER = re.compile(r"^(?:ffn|ada_ln_\d+|[kqvo]|to_(?:gate|k|q|v|out))-(\d+)-")
MARKERS = (
    "ada_ln",
    "ffn_",
    "proj",
    "text_fusion",
    "txtfusion",
    "textfusion",
    "attn",
    "attention",
    "cross_attn",
    "double_stream",
    "single_stream",
    "double_blocks",
    "single_blocks",
    "transformer",
    "diffusion_model",
)

# Architectural *width clues* transcribed from Draw Things LoRAImporter.swift.
# Not exhaustive, not necessarily exposed by an individual LoRA and not unique
# across families. Never use these hints to set receipt.family or DT+ version.
# See https://github.com/drawthingsai/draw-things-community/blob/main/Libraries/ModelOp/Sources/LoRAImporter.swift
TRANSFORMER_WIDTHS = {
    "pixart": (1152,),
    "sd3": (1536, 2432),
    "flux1": (3072, 12288),
    "flux_2": (3072, 4096, 6144),
    "hunyuan_video": (3072,),
    "wan_2_1": (1536, 5120),
    "wan_2_2": (3072, 5120),
    "hidream_i1": (2560,),
    "qwen_image": (3072,),
    "qwen_image_2.1": (4096, 12288),
    "z_image": (3840,),
    "ernie_image": (4096,),
    "ltx_2": (2048, 4096),
    "krea_2": (6144,),
    "moss_image": (3072,),
}
UNET_WIDTHS = {"v1": (320, 640, 1280), "v2": (320, 640, 1280), "sdxl_base_v0.9": (320, 640, 1280)}


def dimensions(value: object) -> tuple[int, ...]:
    """Decode s4nnc integer dimensions, matching the production classifier."""
    if isinstance(value, bytes):
        if not value or len(value) > 64 or len(value) % 4:
            return ()
        values = struct.unpack(f"<{len(value) // 4}i", value)
        return tuple(d for d in values if d > 0)
    if isinstance(value, str) and SHAPE_TEXT.fullmatch(value):
        try:
            parsed = json.loads(value.replace("(", "[").replace(")", "]"))
            if isinstance(parsed, list) and all(type(d) is int and d > 0 for d in parsed):
                return tuple(parsed)
        except ValueError:
            pass
    return ()


def describe_shape(value: object) -> str:
    """Describe the SQLite dimension storage without exposing tensor data."""
    if isinstance(value, bytes):
        return f"blob:{len(value)}"
    if isinstance(value, str):
        return "text"
    return "null" if value is None else type(value).__name__


def sample_shape(value: object) -> str:
    """Show only the dimension metadata, not a weight or payload prefix."""
    if isinstance(value, bytes):
        return value[:32].hex()
    return str(value)[:64]


def fingerprint_checks(
    modules: list[str],
    widths: set[int],
    namespace_widths: dict[str, set[int]],
) -> dict[str, dict[str, bool]]:
    """Show each production family signature's independent structural evidence."""
    unet = namespace_widths.get("__unet__", set())
    text = namespace_widths.get("__text_model__", set())
    te2 = namespace_widths.get("__te2__text_model__", set())
    fixed_unet = namespace_widths.get("__unet_fixed__", set())
    return {
        "z_image": {
            "width_3840": 3840 in widths,
            "ffn_10240": 10240 in widths,
            "ffn_modules": any(re.fullmatch(r"ffn-\d+-ffn_(?:gate|down|up)_proj-\d+-\d+", m) for m in modules),
            "attention_or_modulation_modules": (
                any(re.fullmatch(r"(?:k|q|v|o)-\d+-\d+", m) for m in modules)
                or all(any(re.fullmatch(rf"ada_ln_{index}-\d+-\d+", m) for m in modules) for index in range(4))
            ),
        },
        "krea_2": {
            "width_6144": 6144 in widths,
            "key_value_1536": 1536 in widths,
            "mlp_16384": 16384 in widths,
            "attention_modules": any(re.fullmatch(r"to_(?:gate|k|q|v|out)-\d+-\d+", m) for m in modules),
            "text_fusion": any(m.startswith(("text_fusion_", "textfusion_", "txtfusion_")) for m in modules),
        },
        "moss_image": {
            "width_3072": 3072 in widths,
            "ffn_8192": 8192 in widths,
            "ffn_down_proj": any(re.fullmatch(r"ffn_down_proj-\d+-\d+", m) for m in modules),
            "ffn_up_proj": any(re.fullmatch(r"ffn_up_proj-\d+-\d+", m) for m in modules),
            "ffn_gate_proj": any(re.fullmatch(r"ffn_gate_proj-\d+-\d+", m) for m in modules),
            "x_o": any(re.fullmatch(r"x_o-\d+-\d+", m) for m in modules),
        },
        "flux1": {
            "hidden_3072": 3072 in widths,
            "feed_forward_12288": 12288 in widths,
            "all_57_block_projection_pairs": all(
                sum(module.startswith(f"{name}-") for module in modules) == 2 * (19 + 38)
                for name in ("x_q", "x_k", "x_v", "x_o", "x_linear1", "x_out_proj")
            ),
            "all_19_joint_block_modulation_pairs": all(
                sum(module.startswith(f"x_ada_ln_{index}-") for module in modules) == 2 * 19 for index in (3, 4, 5)
            ),
        },
        "flux2_9b": {
            "hidden_4096": 4096 in widths,
            "feed_forward_12288": 12288 in widths,
            "all_32_block_qkvo_pairs": all(
                sum(module.startswith(f"{name}-") for module in modules) == 2 * (8 + 24)
                for name in ("x_q", "x_k", "x_v", "x_o")
            ),
            "all_24_single_block_ffn_pairs": all(
                sum(module.startswith(f"{name}-") for module in modules) == 2 * 24 for name in ("x_w1", "x_w2", "x_w3")
            ),
            "all_8_joint_block_projections": all(
                sum(module.startswith(f"{name}-") for module in modules) == 2 * 8
                for name in ("x_gate_proj", "x_up_proj", "x_down_proj", "c_q", "c_o")
            ),
        },
        "qwen_image_2.1": {
            "hidden_4096": 4096 in widths,
            "feed_forward_12288": 12288 in widths,
            "five_projection_pairs_per_32_layers": all(
                sum(module.startswith(f"{name}-") for module in modules) == 2 * 32
                for name in ("x_q", "x_k", "x_v", "x_o", "ffn_down_proj")
            ),
        },
        "v1": {
            "text_encoder_768": 768 in text,
            "unet_320_640_1280_and_768": {320, 640, 1280, 768} <= unet,
            "no_sd2_or_sdxl_unet_context": not ({1024, 2048} & unet),
            "no_sd2_or_sdxl_text_width": not ({1024, 1280} & text),
            "no_sdxl_second_text_encoder": "__te2__text_model__" not in namespace_widths,
        },
        "sdxl_base_v0.9": {
            "clip_l_768": 768 in text,
            "clip_g_1280": 1280 in te2,
            "unet_640_1280": {640, 1280} <= unet,
            "fixed_unet_conditioning_2048": 2048 in fixed_unet,
            "no_refiner_unet_widths": not ({384, 1536} & unet),
        },
    }


def inspect(path: Path, sample_count: int) -> dict:
    """Inspect a SQLite checkpoint; preserve ambiguity and non-LoRA cases."""
    if not path.is_file():
        raise ValueError("File not found or not a regular file.")
    report: dict = {"file": path.name, "size_bytes": path.stat().st_size}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0) as db:
        db.execute("PRAGMA query_only=ON")
        report["tables"] = [
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        columns = [{"name": row[1], "type": row[2]} for row in db.execute("PRAGMA table_info(tensors)")]
        report["tensor_columns"] = columns
        available = {column["name"] for column in columns}
        name_col = next((x for x in ("name", "key", "tensor_name") if x in available), None)
        dim_col = next((x for x in ("dim", "shape", "dims", "dimensions") if x in available), None)
        report["name_column"], report["dimension_column"] = name_col, dim_col
        if name_col is None:
            report["error"] = "No supported tensor-name column; classifier returns Unknown."
            return report
        # Only whitelisted schema column names; no data column is selected.
        select = name_col + (", " + dim_col if dim_col else "")
        rows = db.execute(f"SELECT {select} FROM tensors LIMIT ?", (MAX_TENSORS + 1,)).fetchall()

    report["tensor_rows_scanned"] = len(rows)
    if len(rows) > MAX_TENSORS:
        report["error"] = f"More than {MAX_TENSORS} tensors; classifier returns Unknown."
        return report

    modules: list[str] = []
    all_widths: Counter[int] = Counter()
    lora_widths: set[int] = set()
    namespace_widths: dict[str, set[int]] = {}
    namespaces: Counter[str] = Counter()
    module_groups: Counter[str] = Counter()
    shape_encodings: Counter[str] = Counter()
    markers: Counter[str] = Counter()
    layer_indices: set[int] = set()
    samples: list[dict] = []
    informative: list[dict] = []
    lora_count = 0

    for row in rows:
        if not isinstance(row[0], str):
            continue
        name = row[0].lower()
        namespace = name.split("[", 1)[0]
        namespaces[namespace] += 1
        match = DIT_LORA.fullmatch(name)
        if match:
            module = match.group(1)
            modules.append(module)
            module_groups[module.split("-", 1)[0]] += 1
            index_match = MODULE_LAYER.match(module)
            if index_match:
                layer_indices.add(int(index_match.group(1)))
        is_adapter = name.endswith(("__up__", "__down__"))
        if is_adapter:
            lora_count += 1

        raw = row[1] if dim_col else None
        dims = dimensions(raw)
        all_widths.update(dims)
        if is_adapter and name.startswith(namespace + "[t-"):
            namespace_widths.setdefault(namespace, set()).update(dims)
        if match:
            lora_widths.update(dims)
        shape_encodings[describe_shape(raw)] += 1
        for marker in MARKERS:
            if marker in name:
                markers[marker] += 1
        example = {
            "tensor_name": row[0],
            "parsed_dimensions": list(dims),
            "dim_storage": describe_shape(raw),
            "dim_prefix": sample_shape(raw),
        }
        if len(samples) < sample_count:
            samples.append(example)
        elif len(informative) < sample_count and (match or any(m in name for m in MARKERS)):
            informative.append(example)

    checks = fingerprint_checks(modules, lora_widths, namespace_widths)
    matches = [family for family, gates in checks.items() if all(gates.values())]
    detected = matches[0] if len(matches) == 1 else "unknown"
    # These candidates deliberately use SHAPE ONLY. They are useful to choose
    # the next real file to smoke, but must never become receipt evidence.
    width_refs = TRANSFORMER_WIDTHS if modules else UNET_WIDTHS
    width_hints = (
        [
            {"family": family, "matching_widths": sorted(all_widths.keys() & set(expected))}
            for family, expected in width_refs.items()
            if all_widths.keys() & set(expected)
        ]
        if lora_count
        else []
    )

    report.update(
        {
            "tensor_names_read": sum(namespaces.values()),
            "lora_tensor_count": lora_count,
            "dit_lora_tensor_count": len(modules),
            "namespaces": dict(namespaces),
            "adapter_dimensions_by_namespace": {
                namespace: sorted(dims) for namespace, dims in namespace_widths.items()
            },
            "module_groups": module_groups.most_common(30),
            "marker_counts": dict(markers),
            "observed_layer_indices": sorted(layer_indices),
            "dimension_storage": dict(shape_encodings),
            "most_common_dimensions": all_widths.most_common(25),
            "first_tensors": samples,
            "other_tensors": informative,
            "fingerprint_checks": checks,
            "current_detector_result": detected,
            "width_only_hints_not_classification": width_hints,
        }
    )
    if not modules and detected == "unknown":
        report["limitation"] = (
            "No Draw Things __dit__ LoRA up/down tensors. Only the guarded "
            "SD 1.x and SDXL Base signatures are currently supported "
            "without DiT tensors; other architectures remain unknown."
        )
    return report


def print_report(report: dict) -> None:
    """Display a compact human-readable evidence summary."""
    print(f"\n== {report['file']} ({report.get('size_bytes', 0):,} bytes)")
    print("Tables:", report.get("tables", []))
    print("Tensor columns:", report.get("tensor_columns", []))
    if "error" in report:
        print("Error:", report["error"])
        return
    print("Tensors:", report["tensor_rows_scanned"])
    print("LoRA up/down:", report["lora_tensor_count"], "DiT:", report["dit_lora_tensor_count"])
    print("Namespaces:", report["namespaces"])
    print("Adapter dimensions by namespace:", report["adapter_dimensions_by_namespace"])
    print("Module groups:", report["module_groups"])
    print("Observed layer indices:", report["observed_layer_indices"])
    print("Dimension storage:", report["dimension_storage"])
    print("Frequent dimensions:", report["most_common_dimensions"])
    print("Classifier gates:")
    for family, gates in report["fingerprint_checks"].items():
        print(" ", family, ", ".join(f"{k}={v}" for k, v in gates.items()))
    print("Current classifier:", report["current_detector_result"])
    print("Width-only hints (NOT classifications):", report["width_only_hints_not_classification"])
    if report.get("limitation"):
        print("Limitation:", report["limitation"])
    for section in ("first_tensors", "other_tensors"):
        print(section.replace("_", " ").capitalize() + ":")
        for item in report[section]:
            print(" ", item["tensor_name"], "| dimensions=", item["parsed_dimensions"])


def main() -> int:
    """Inspect local converted files and optionally save JSON evidence."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="Local converted .ckpt paths.")
    parser.add_argument(
        "-d",
        "--directory",
        type=Path,
        help="Inspect all .ckpt files in this directory (non-recursive).",
    )
    parser.add_argument("--sample", type=int, default=8, help="Tensor examples per group (0–100).")
    parser.add_argument("--json", type=Path, help="Write one metadata-only JSON report.")
    args = parser.parse_args()
    if not 0 <= args.sample <= 100:
        parser.error("--sample must be between 0 and 100.")

    paths = list(args.files)
    if args.directory is not None:
        directory = args.directory.expanduser()
        if not directory.is_dir():
            parser.error(f"Not a directory: {directory}")
        try:
            paths.extend(
                sorted(
                    (item for item in directory.iterdir() if item.is_file() and item.suffix.lower() == ".ckpt"),
                    key=lambda item: item.name.casefold(),
                )
            )
        except OSError as exc:
            parser.error(f"Cannot scan directory: {exc}")

    if not paths:
        parser.error("Provide .ckpt files or a directory containing .ckpt files with -d.")

    reports = []
    failed = False
    seen: set[Path] = set()
    for path in paths:
        canonical = path.expanduser().resolve()
        if canonical in seen:
            continue
        seen.add(canonical)
        try:
            report = inspect(path.expanduser(), args.sample)
            failed |= "error" in report
        except (OSError, sqlite3.Error, ValueError, struct.error) as exc:
            report = {"file": path.name, "error": f"Inspection failed: {exc.__class__.__name__}"}
            failed = True
        reports.append(report)
        print_report(report)
    if args.json:
        try:
            args.json.write_text(
                json.dumps(reports, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"Could not write JSON report: {exc.__class__.__name__}", file=sys.stderr)
            return 1
        print(f"\nSaved report: {args.json}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

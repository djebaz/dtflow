#!/usr/bin/env python3
"""Run a local SCHP ATR-18 ONNX folder smoke test with class masks and overlays.

Dependencies: numpy, onnxruntime, Pillow (all already declared by DTFlow).
No Transformers, PyTorch, huggingface_hub, OpenCV, or DTFlow imports.

The pinned model is downloaded once from Hugging Face with SHA-256 verification.
The output includes full-resolution indexed masks, binary class masks, overlays,
and original-versus-overlay comparison images with a legend.

Usage (from the DTFlow repository root):
    python3 tools/images/schp_atr18_smoke.py --input-dir "C:\\photos" --output-dir "tmp\\schp-smoke"

The same command works with the repository's locked environment using:
    uv run python3 tools/images/schp_atr18_smoke.py --input-dir "C:\\photos" --output-dir "tmp\\schp-smoke"
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Exact file revision and Git LFS SHA-256 recorded in the upstream model commit.
MODEL_REVISION = "1ddc548f48cc1435a505e565ff5f56dcfe6017e3"
MODEL_NAME = "schp-atr-18-int8-static.onnx"
MODEL_URL = (
    f"https://huggingface.co/pirocheto/schp-atr-18/resolve/{MODEL_REVISION}/"
    f"onnx/{MODEL_NAME}"
)
MODEL_SHA256 = "4420d8db8c1f266967c89485786b01209f6d405f320fc0f87e8ced49392cefb5"
MODEL_BYTES = 69_141_996
SIDE = 512
LABELS = (
    "Background", "Hat", "Hair", "Sunglasses", "Upper-clothes", "Skirt",
    "Pants", "Dress", "Belt", "Left-shoe", "Right-shoe", "Face",
    "Left-leg", "Right-leg", "Left-arm", "Right-arm", "Bag", "Scarf",
)
# Match DTFlow's established labels and RGB per-layer colors where available.
COLORS = (
    (0, 0, 0), (255, 87, 34), (121, 85, 72), (96, 125, 139),
    (66, 135, 245), (171, 71, 188), (52, 168, 83), (233, 30, 99),
    (255, 193, 7), (141, 110, 99), (141, 110, 99), (255, 112, 112),
    (156, 204, 101), (156, 204, 101), (255, 183, 77), (255, 183, 77),
    (0, 150, 136), (0, 188, 212),
)
# IMPORTANT: This model uses RGB pixels, but its published mean/std follow
# the BGR-indexed normalization constants used during SCHP training.
MEAN = (0.406, 0.456, 0.485)
STD = (0.225, 0.224, 0.229)
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})
MAX_IMAGE_PIXELS = 16_000_000


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 without loading the entire model into RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(path: Path, *, allow_download: bool) -> str:
    """Reuse or safely download the pinned ONNX file; never accept a bad digest."""
    if path.is_file():
        actual = sha256_file(path)
        if actual == MODEL_SHA256:
            print(f"Model verified and reused: {path}")
            return actual
        raise RuntimeError(
            f"Existing model has an unexpected SHA-256: {path}\n"
            f"Expected {MODEL_SHA256}\nFound    {actual}\n"
            "Remove or move that file explicitly before retrying."
        )
    if not allow_download:
        raise FileNotFoundError(f"Model is missing and --no-download was selected: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    digest = hashlib.sha256()
    print(f"Downloading model (~{MODEL_BYTES / 1024 / 1024:.1f} MiB): {MODEL_URL}", flush=True)
    try:
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "dtflow-schp-smoke/1.0"})
        with urllib.request.urlopen(request, timeout=90) as response, part.open("wb") as dest:
            total = 0
            shown_at = time.monotonic()
            while chunk := response.read(1024 * 1024):
                dest.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if time.monotonic() - shown_at >= 1:
                    print(f"  Downloaded {total / 1024 / 1024:.1f} MiB", flush=True)
                    shown_at = time.monotonic()
        actual = digest.hexdigest()
        if total != MODEL_BYTES or actual != MODEL_SHA256:
            raise RuntimeError(
                f"Downloaded content failed integrity checks: {total} bytes; SHA-256 {actual}; "
                f"expected {MODEL_BYTES} bytes and SHA-256 {MODEL_SHA256}."
            )
        os.replace(part, path)
    finally:
        # Only remove our incomplete temporary download; preserve existing checkpoints.
        part.unlink(missing_ok=True)
    print(f"Model downloaded and SHA-256 verified: {path}")
    return MODEL_SHA256


def load_session(model_path: Path, threads: int):
    """Create one CPU ONNX Runtime session and validate the model's public contract."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if len(inputs) != 1 or inputs[0].type != "tensor(float)":
        raise RuntimeError(f"Unexpected ONNX inputs: {[(x.name, x.shape, x.type) for x in inputs]}")
    input_shape = inputs[0].shape
    if (
        inputs[0].name != "pixel_values"
        or len(input_shape) != 4
        or input_shape[1:] != [3, SIDE, SIDE]
        or (isinstance(input_shape[0], int) and input_shape[0] != 1)
    ):
        raise RuntimeError(f"Expected pixel_values [1,3,512,512], got {inputs[0].name} {input_shape}")
    if not any(out.name == "logits" for out in outputs):
        raise RuntimeError(f"Model does not expose the expected 'logits' output: {[x.name for x in outputs]}")
    return session


def prepare_image(source):
    """Replicate upstream SCHPImageProcessor resize/normalize without torch."""
    import numpy as np
    from PIL import Image

    resized = source.resize((SIDE, SIDE), resample=Image.Resampling.BILINEAR)
    rgb = np.asarray(resized, dtype=np.float32) / np.float32(255.0)
    rgb = (rgb - np.asarray(MEAN, dtype=np.float32)) / np.asarray(STD, dtype=np.float32)
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None, ...], dtype=np.float32)


def load_rgb(path: Path):
    """Read one EXIF-oriented still image; flatten transparency on white."""
    from PIL import Image, ImageOps

    with Image.open(path) as opened:
        if opened.width * opened.height > MAX_IMAGE_PIXELS:
            raise ValueError(f"Image exceeds the {MAX_IMAGE_PIXELS:,}-pixel smoke-test bound")
        if getattr(opened, "n_frames", 1) != 1:
            raise ValueError("Animated images are not supported")
        rgba = ImageOps.exif_transpose(opened).convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def make_visuals(source, labels, out_dir: Path, stem: str, opacity: float):
    """Save source-aligned class IDs, binary masks, overlay and labelled comparison."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    out_dir.mkdir(parents=True, exist_ok=True)
    # Preserve raw class IDs so results remain inspectable without a color legend.
    indexed = Image.fromarray(labels.astype(np.uint8))
    indexed.save(out_dir / f"{stem}_labels.png")

    color_lut = np.asarray(COLORS, dtype=np.uint8)
    colored = Image.fromarray(color_lut[labels], mode="RGB")
    alpha = Image.fromarray(np.where(labels > 0, round(255 * opacity), 0).astype(np.uint8))
    colored.putalpha(alpha)
    overlay = Image.alpha_composite(source.convert("RGBA"), colored).convert("RGB")
    overlay.save(out_dir / f"{stem}_overlay.png")

    counts = np.bincount(labels.reshape(-1), minlength=len(LABELS))
    detected = [idx for idx in range(1, len(LABELS)) if counts[idx] > 0]
    for idx in detected:
        binary = Image.fromarray((labels == idx).astype(np.uint8) * 255)
        binary.save(out_dir / f"{stem}_mask_{idx:02d}_{LABELS[idx].lower().replace('-', '_')}.png")

    # Left: original; middle: mask overlay; right: legend and pixel coverage.
    w, h = source.size
    legend_width = 250
    compared = Image.new("RGB", (w * 2 + legend_width, max(h, 56 + len(LABELS) * 26)), "white")
    compared.paste(source, (0, 0))
    compared.paste(overlay, (w, 0))
    draw = ImageDraw.Draw(compared)
    font = ImageFont.load_default()
    left = w * 2 + 12
    draw.text((left, 12), "SCHP ATR-18 | class legend", fill=(15, 15, 15), font=font)
    draw.text((left, 32), f"Overlay opacity: {opacity:.0%}", fill=(75, 75, 75), font=font)
    for idx, name in enumerate(LABELS):
        y = 58 + idx * 26
        if idx == 0:
            draw.rectangle((left, y, left + 14, y + 14), outline=(110, 110, 110))
        else:
            draw.rectangle((left, y, left + 14, y + 14), fill=COLORS[idx])
        pct = 100.0 * int(counts[idx]) / labels.size
        draw.text((left + 22, y + 1), f"{idx:02d} {name}: {pct:.1f}%", fill=(20, 20, 20), font=font)
    compared.save(out_dir / f"{stem}_compare.png")
    return {LABELS[idx]: round(100.0 * int(counts[idx]) / labels.size, 3) for idx in range(len(LABELS))}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the smoke-test CLI with simple paths and predictable output options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", type=Path, required=True, help="Folder containing images (non-recursive).")
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/schp-atr18-smoke"), help="Output folder.")
    parser.add_argument("--model", type=Path, default=Path("tmp/models") / MODEL_NAME, help="Cached INT8 ONNX file.")
    parser.add_argument("--no-download", action="store_true", help="Require an existing, hash-verified ONNX file.")
    parser.add_argument("--opacity", type=float, default=0.50, help="Overlay opacity between 0.0 and 1.0 (default 0.50).")
    parser.add_argument("--threads", type=int, default=4, help="ONNX Runtime CPU inference threads (default 4).")
    args = parser.parse_args(argv)
    if not 0.0 <= args.opacity <= 1.0:
        parser.error("--opacity must be between 0 and 1")
    if args.threads < 1:
        parser.error("--threads must be at least 1")
    if not args.input_dir.is_dir():
        parser.error(f"--input-dir is not an existing folder: {args.input_dir}")
    return args


def main(argv: list[str] | None = None) -> int:
    """Download the model once, process all supported stills, and print a summary."""
    import numpy as np
    from PIL import Image

    args = parse_args(argv)
    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        print(f"No supported images found in {args.input_dir}", file=sys.stderr)
        return 2

    model_hash = ensure_model(args.model, allow_download=not args.no_download)
    t_load = time.perf_counter()
    session = load_session(args.model, args.threads)
    print(f"CPU ONNX session ready in {time.perf_counter() - t_load:.2f}s; {len(files)} image(s) queued")

    output_root = args.output_dir.resolve()
    summary_rows = []
    fail_count = 0
    for n, path in enumerate(files, start=1):
        stamp = time.perf_counter()
        # Numbered prefix avoids collisions from files sharing names with different extensions.
        stem = f"{n:04d}_{path.stem}"
        try:
            source = load_rgb(path)
            tensor = prepare_image(source)
            inf_start = time.perf_counter()
            logits = session.run(["logits"], {"pixel_values": tensor})[0]
            inference_seconds = time.perf_counter() - inf_start
            if logits.shape != (1, len(LABELS), SIDE, SIDE):
                raise RuntimeError(f"Unexpected logits shape: {logits.shape}")
            small_labels = np.argmax(logits[0], axis=0).astype(np.uint8)
            # Keep class IDs intact on the original pixel grid with nearest-neighbor.
            label_image = Image.fromarray(small_labels).resize(source.size, Image.Resampling.NEAREST)
            full_labels = np.asarray(label_image, dtype=np.uint8)
            class_coverages = make_visuals(source, full_labels, output_root / stem, stem, args.opacity)
            elapsed = time.perf_counter() - stamp
            record = {
                "image_name": path.name,
                "image_sha256": sha256_file(path),
                "width": source.width,
                "height": source.height,
                "model_url": MODEL_URL,
                "model_sha256": model_hash,
                "class_labels": list(LABELS),
                "preprocessing": {"resize": [SIDE, SIDE], "resampling": "bilinear", "mean": MEAN, "std": STD},
                "inference_seconds": round(inference_seconds, 3),
                "total_seconds": round(elapsed, 3),
                "class_coverages_percent": class_coverages,
            }
            (output_root / stem / f"{stem}_report.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
            )
            summary_rows.append({"image": path.name, "status": "ok", "inference_s": f"{inference_seconds:.3f}", "total_s": f"{elapsed:.3f}"})
            print(f"[{n}/{len(files)}] {path.name}: inference {inference_seconds:.2f}s, total {elapsed:.2f}s -> {output_root / stem}", flush=True)
        except Exception as exc:
            fail_count += 1
            summary_rows.append({"image": path.name, "status": "error", "inference_s": "", "total_s": ""})
            print(f"[{n}/{len(files)}] ERROR {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=("image", "status", "inference_s", "total_s"))
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"Finished: {len(files) - fail_count}/{len(files)} images; results in {output_root}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

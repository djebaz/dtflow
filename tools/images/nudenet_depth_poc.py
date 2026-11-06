#!/usr/bin/env python3
"""CPU NudeNet target-region depth/normal contour comparison; heuristic masks, not ground truth."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import cv2 as cv
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from edge_nudenet_poc import NUDENET_SPECS, PERSON_SPECS, fit_box_zones, nudenet_640_session
from pascal_nudenet_poc import verified_checkpoint
from schp_atr18_smoke import (
    DEPTH_BYTES,
    DEPTH_SHA256,
    DEPTH_URL,
    IMAGE_SUFFIXES,
    depth_session,
    detect_body_parts,
    load_rgb,
    person_crop_box,
    select_people,
)

TARGET = "MALE_GENITALIA_EXPOSED"  # NN_ is a UI prefix, not part of the model's label.
COLOR = (255, 80, 180)


def depth_map(image, session):
    """Letterbox to the pinned export's 518 square input, then remove padding."""
    side = 518
    scale = side / max(image.size)
    width, height = (max(1, min(side, round(v * scale))) for v in image.size)
    left, top = (side - width) // 2, (side - height) // 2
    small = image.resize((width, height), Image.Resampling.BICUBIC)
    # Replicated borders avoid introducing a high-contrast black frame.
    padded = np.pad(np.asarray(small), ((top, side - height - top), (left, side - width - left), (0, 0)), mode="edge")
    rgb = padded.astype(np.float32) / np.float32(255)
    rgb = (rgb - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    from dtflow.services import person_selection as selection

    raw = selection._run(session, np.ascontiguousarray(rgb.transpose(2, 0, 1)[None]))[0].squeeze()
    if raw.ndim != 2 or not np.isfinite(raw).all():
        raise RuntimeError(f"Unexpected depth output: {raw.shape}")
    square = cv.resize(raw, (side, side), interpolation=cv.INTER_CUBIC)
    result = cv.resize(square[top : top + height, left : left + width], image.size, interpolation=cv.INTER_CUBIC)
    return result.astype(np.float32), {"input": [side, side], "resized": [width, height], "padding_xy": [left, top]}


def response(values, inside):
    limit = max(float(np.percentile(values[inside], 98)), 1e-6)
    return np.clip(values / limit, 0, 1).astype(np.float32)


def geometry(depth, inside):
    """Float depth, amplified approximate normals, and independent boundary cues."""
    low, high = np.percentile(depth[inside], (2, 98))
    relative = ((depth - low) / max(float(high - low), 1e-6)).astype(np.float32)
    normal_fields, changes = [], []
    for sigma in (1.0, 3.0):
        smooth = cv.GaussianBlur(relative, (0, 0), sigma)
        dy, dx = np.gradient(smooth)
        strength = max(float(np.percentile(np.hypot(dx, dy)[inside], 95)), 1e-6)
        normals = np.stack((-8 * dx / strength, -8 * dy / strength, np.ones_like(dx)), axis=-1)
        normals /= np.maximum(np.linalg.norm(normals, axis=-1, keepdims=True), 1e-6)
        # Differentiate float vectors, not the quantized RGB preview.
        change = np.zeros_like(relative)
        change[:, 1:] = np.linalg.norm(np.diff(normals, axis=1), axis=-1)
        change[1:] = np.maximum(change[1:], np.linalg.norm(np.diff(normals, axis=0), axis=-1))
        normal_fields.append(normals)
        changes.append(response(change, inside))
    smooth = cv.GaussianBlur(relative, (0, 0), 1.0)
    dy, dx = np.gradient(smooth)
    gradient = response(np.hypot(dx, dy), inside)
    curvature = []
    for sigma in (0.5, 1.0, 2.0):
        layer = cv.GaussianBlur(relative, (0, 0), sigma)
        gy, gx = np.gradient(layer)
        curvature.append(response(np.abs(np.gradient(gx, axis=1) + np.gradient(gy, axis=0)), inside))
    bend = np.maximum.reduce(curvature)
    return (
        relative,
        normal_fields[0],
        {
            "depth_gradient": gradient,
            "normal_fine": changes[0],
            "normal_broad": changes[1],
            "curvature": bend,
            "combined": np.maximum(changes[0], 0.65 * bend),
        },
    )


def boxed(pixels, detections):
    result = Image.fromarray(np.asarray(pixels, dtype=np.uint8))
    draw = ImageDraw.Draw(result)
    font = ImageFont.load_default(size=16)
    for detection in detections:
        x0, y0, x1, y1 = detection["box_xyxy"]
        draw.rectangle((x0, y0, x1, y1), outline=COLOR, width=2)
        draw.text(
            (x0 + 3, y0 + 3), f"{detection['score']:.2f}", font=font, fill="white", stroke_width=2, stroke_fill="black"
        )
    return result


def diagnostic(rgb, values, inside, detections, *, vector=False):
    if vector:
        color = np.clip((values + 1) * 127.5, 0, 255).astype(np.uint8)
    else:
        color = cv.cvtColor(
            cv.applyColorMap((np.clip(values, 0, 1) * 255).astype(np.uint8), cv.COLORMAP_TURBO), cv.COLOR_BGR2RGB
        )
    result = rgb.copy()
    result[inside] = (0.3 * rgb[inside] + 0.7 * color[inside]).astype(np.uint8)
    return boxed(result, detections)


def contour_preview(rgb, layers, inside, detections):
    result = rgb.copy()
    for _, mask in layers:
        alpha = cv.resize(mask.astype(np.float32), (rgb.shape[1], rgb.shape[0]), interpolation=cv.INTER_LINEAR)
        alpha = cv.GaussianBlur(alpha, (0, 0), 0.7) * inside
        result = (result * (1 - 0.4 * alpha[..., None]) + np.array(COLOR) * (0.4 * alpha[..., None])).astype(np.uint8)
        contours, _ = cv.findContours((alpha >= 0.5).astype(np.uint8), cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        cv.drawContours(result, contours, -1, (255, 255, 255), 1, cv.LINE_AA)
    return boxed(result, detections)


def tile(image, caption, view, size):
    result = Image.new("RGB", (size, size + 52), (22, 22, 26))
    photo = ImageOps.contain(image.crop(view), (size, size), Image.Resampling.LANCZOS)
    result.paste(photo, ((size - photo.width) // 2, 52 + (size - photo.height) // 2))
    ImageDraw.Draw(result).text((10, 8), caption, font=ImageFont.load_default(size=17), fill="white")
    return result


def save_panels(panels, crop_size, detections, output, prefix, args):
    if detections:
        x0 = min(d["box_xyxy"][0] for d in detections)
        y0 = min(d["box_xyxy"][1] for d in detections)
        x1 = max(d["box_xyxy"][2] for d in detections)
        y1 = max(d["box_xyxy"][3] for d in detections)
        margin = 0.6 * max(x1 - x0, y1 - y0)
        view = (
            max(0, math.floor(x0 - margin)),
            max(0, math.floor(y0 - margin)),
            min(crop_size[0], math.ceil(x1 + margin)),
            min(crop_size[1], math.ceil(y1 + margin)),
        )
    else:
        view = (0, 0, *crop_size)
    # Every panel uses exactly the same zoom; no per-panel auto-cropping.
    tiles = [tile(image, f"{i:02d} {caption}", view, args.tile) for i, (_, caption, image) in enumerate(panels, 1)]
    if args.compare:
        columns = min(4, len(tiles))
        sheet = Image.new(
            "RGB", (columns * args.tile, math.ceil(len(tiles) / columns) * (args.tile + 52)), (22, 22, 26)
        )
        for i, panel in enumerate(tiles):
            sheet.paste(panel, ((i % columns) * args.tile, (i // columns) * (args.tile + 52)))
        sheet.save(output / f"{prefix}_compare.jpg", quality=93)
    else:
        for (name, _, _), panel in zip(panels, tiles, strict=True):
            panel.save(output / f"{prefix}_{name}.jpg", quality=93)
    return view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input-dir", type=Path, required=True)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("tmp/nudenet-depth-poc"))
    parser.add_argument("-ns", "--nudenet-size", choices=("320n", "640m"), default="640m")
    parser.add_argument("-ps", "--person-size", choices=("n", "s", "m"), default="n")
    parser.add_argument("-s", "--confidence", type=float, default=0.25)
    parser.add_argument("-q", "--person-threshold", type=float, default=0.4)
    parser.add_argument(
        "-r", "--resolution", type=int, default=512, help="Geometry/growth grid long side, not model input"
    )
    parser.add_argument("-t", "--threads", type=int, default=4)
    parser.add_argument("-e", "--edge-weight", type=float, default=12)
    parser.add_argument("-fp", "--fit-percentile", type=float, default=35)
    parser.add_argument("-u", "--smooth", type=float, default=4)
    parser.add_argument(
        "-x",
        "--expand",
        type=float,
        default=0,
        help="Expand fitted zones by 0..32 growth-grid pixels, within person mask; try 2",
    )
    parser.add_argument(
        "-cp",
        "--compare",
        action="store_true",
        help="Save one labeled comparison JPG per image instead of separate overlays",
    )
    parser.add_argument("-ts", "--tile", type=int, default=640, help="Panel size; comparison has up to four columns")
    parser.add_argument(
        "-w", "--overwrite", action="store_true", help="Replace only this POC's recognized flat outputs"
    )
    parser.add_argument("-d", "--no-download", action="store_true")
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error("input-dir must exist")
    if not 0 < args.confidence <= 1 or not 0 < args.person_threshold < 1:
        parser.error("confidence must be in (0,1], person-threshold in (0,1)")
    if not 64 <= args.resolution <= 1024 or not 256 <= args.tile <= 1024 or args.threads < 1:
        parser.error("resolution 64..1024, tile 256..1024 and threads >= 1 required")
    if (
        not all(math.isfinite(v) for v in (args.edge_weight, args.fit_percentile, args.smooth, args.expand))
        or args.edge_weight < 0
        or not 0 <= args.fit_percentile <= 100
        or not 0 <= args.smooth <= 12
        or not 0 <= args.expand <= 32
    ):
        parser.error("edge-weight >= 0, fit-percentile 0..100, smooth 0..12 and expand 0..32 must be finite")
    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        parser.error("No supported still images")
    output = args.output_dir.resolve()
    source_dir = args.input_dir.resolve()
    if (
        output == source_dir
        or output in source_dir.parents
        or args.output_dir.is_symlink()
        or args.output_dir.is_junction()
    ):
        parser.error("output-dir must not redirect or contain input-dir")
    previous = list(output.iterdir()) if output.exists() else []
    pattern = re.compile(
        r"ndp_\d{4,}_.+_(?:compare|reference|depth|normals|depth_gradient|normal_fine|normal_broad|curvature|fit_\w+|report)\.(?:jpg|json)$"
    )
    if previous and (
        not args.overwrite
        or any(p.is_symlink() or p.is_junction() or not p.is_file() or not pattern.fullmatch(p.name) for p in previous)
    ):
        parser.error("Output is not empty; -w replaces recognized nudenet_depth_poc outputs only")
    from dtflow.services import person_selection as selection

    person_path = Path("tmp/models") / f"yolo26{args.person_size}-seg-ultralytics-v8.4.0.onnx"
    parts_path = Path("tmp/models") / f"nudenet-{args.nudenet_size}.onnx"
    depth_path = Path("tmp/models/depth-anything-v2-small.onnx")
    for path, spec in (
        (person_path, PERSON_SPECS[args.person_size]),
        (parts_path, NUDENET_SPECS[args.nudenet_size]),
        (depth_path, (DEPTH_URL, DEPTH_BYTES, "sha256", DEPTH_SHA256)),
    ):
        verified_checkpoint(path, spec, not args.no_download)
    person = selection._verified_session(selection.describe_model(person_path), selection._segmentation_session)
    parts = selection._verified_session(
        selection.describe_model(parts_path),
        nudenet_640_session if args.nudenet_size == "640m" else selection._faces_session,
    )
    depth = depth_session(depth_path, allow_download=False, threads=args.threads)
    if any(session.get_providers() != ["CPUExecutionProvider"] for session in (person, parts, depth)):
        raise RuntimeError("All models must use ONNX Runtime CPU only")
    cv.setNumThreads(args.threads)
    output.mkdir(parents=True, exist_ok=True)
    for path in previous:
        if path.is_symlink() or path.is_junction() or not path.is_file() or path.resolve().parent != output:
            raise RuntimeError(f"Output changed before cleanup: {path.name}")
        path.unlink()
    print(f"CPU models loaded; {len(files)} images; NudeNet {args.nudenet_size}; target {TARGET}", flush=True)
    failures = 0
    for index, path in enumerate(files, 1):
        prefix = f"ndp_{index:04d}_{path.stem[:70]}"
        try:
            started = time.perf_counter()
            source = load_rgb(path)
            people = select_people(
                selection._instances(person, source, 0.25, mask_threshold=args.person_threshold),
                source.size,
                0,
                1,
                False,
            )
            report = {
                "source": path.name,
                "target": TARGET,
                "settings": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                "checkpoints": {
                    "person": PERSON_SPECS[args.person_size][3],
                    "nudenet": NUDENET_SPECS[args.nudenet_size][3],
                    "depth": DEPTH_SHA256,
                },
                "note": "Approximate depth-derived geometry and heuristic box-fit candidates; no pose or ground-truth anatomy.",
            }
            detections, crop = [], source
            if people:
                _, mask, person_box = people[0]
                crop_box = tuple(int(v) for v in person_crop_box(person_box, source.size))
                x0, y0, x1, y1 = crop_box
                crop = source.crop(crop_box)
                inside = mask[y0:y1, x0:x1] > 0
                _, found, _ = detect_body_parts(
                    parts, crop, args.confidence, input_side=640 if args.nudenet_size == "640m" else 320
                )
                detections = [d for d in found if d["label"] == TARGET]
                report["person_crop_xyxy"] = crop_box
            report["detections_crop_coordinates"] = detections
            rgb = np.asarray(crop)
            panels = [("reference", "NN boxes / scores", boxed(rgb, detections))]
            if detections:
                tick = time.perf_counter()
                z, fit = depth_map(crop, depth)
                report["depth_seconds"] = time.perf_counter() - tick
                report["depth_preprocessing"] = fit
                scale = min(1, args.resolution / max(crop.size))
                size = tuple(max(2, round(v * scale)) for v in crop.size)
                valid = cv.resize(inside.astype(np.uint8), size, interpolation=cv.INTER_NEAREST) > 0
                if not valid.any():
                    raise RuntimeError("Person mask is empty at geometry resolution")
                relative, normals, fields = geometry(cv.resize(z, size, interpolation=cv.INTER_AREA), valid)

                def full(values, target_size=crop.size):
                    return cv.resize(values, target_size, interpolation=cv.INTER_LINEAR)

                panels.extend(
                    [
                        (
                            "depth",
                            "Relative depth (display clipped)",
                            diagnostic(rgb, full(relative), inside, detections),
                        ),
                        (
                            "normals",
                            "Approx. normals / gain 8",
                            diagnostic(rgb, full(normals), inside, detections, vector=True),
                        ),
                    ]
                )
                for name in ("depth_gradient", "normal_fine", "normal_broad", "curvature"):
                    panels.append(
                        (
                            name,
                            name.replace("_", " ") + " / cool=weak, warm=strong",
                            diagnostic(rgb, full(fields[name]), inside, detections),
                        )
                    )
                report["candidates"] = {}
                for name, field in fields.items():
                    layers, records, summary = fit_box_zones(
                        cv,
                        full(field),
                        inside,
                        detections,
                        args.resolution,
                        args.edge_weight,
                        args.smooth,
                        args.expand,
                        percentile=args.fit_percentile,
                    )
                    panels.append(
                        (
                            f"fit_{name}",
                            f"Fit: {name.replace('_', ' ')} / expand {args.expand:g}",
                            contour_preview(rgb, layers, inside, detections),
                        )
                    )
                    report["candidates"][name] = {"zones": records, "summary": summary}
                report["status"] = "compared"
            else:
                report["status"] = "no_target" if people else "no_person"
                panels[0] = ("reference", report["status"] + " / depth skipped", panels[0][2])
            report["comparison_view_crop_xyxy"] = save_panels(panels, crop.size, detections, output, prefix, args)
            report["seconds"] = time.perf_counter() - started
            (output / f"{prefix}_report.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
            print(f"{path.name}: {len(detections)} targets, {report['status']}; {report['seconds']:.2f}s", flush=True)
        except Exception as exc:
            failures += 1
            print(f"{path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

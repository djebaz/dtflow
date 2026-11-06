#!/usr/bin/env python3
"""NudeNet boxes -> EfficientSAM-Ti INT8 masks, using ONNX Runtime CPU only.

One main person, all direct input-folder images, flat labeled outputs. No depth,
contour growth, or inferred anatomical ground truth. Missing checkpoints download.
"""

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
from PIL import Image, ImageDraw

from edge_nudenet_poc import NUDENET_SPECS, PERSON_SPECS, nudenet_640_session
from nudenet_depth_poc import TARGET, boxed, contour_preview, tile
from pascal_nudenet_poc import verified_checkpoint
from schp_atr18_smoke import IMAGE_SUFFIXES, detect_body_parts, load_rgb, person_crop_box, select_people

SAM_FILENAME = "image_segmentation_efficientsam_ti_2025april_int8.onnx"
SAM_SPEC = (
    f"https://huggingface.co/opencv/image_segmentation_efficientsam/resolve/1e791988b34b2098736003057da0f3e6bc826259/{SAM_FILENAME}",
    20_479_928,
    "sha256",
    "5ecc8d59a2802c32246e68553e1cf8ce74cf74ba707b84f206eb9181ff774b4e",
)


def sam_session(path, threads):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    expected = {
        "batched_images": [1, 3, 1024, 1024],
        "batched_point_coords": [1, 1, 6, 2],
        "batched_point_labels": [1, 1, 6],
    }
    inputs = session.get_inputs()
    if len(inputs) != 3 or any(
        item.name not in expected or item.shape != expected[item.name] or item.type != "tensor(float)"
        for item in inputs
    ):
        raise RuntimeError("Expected the pinned April 2025 EfficientSAM-Ti INT8 three-input export")
    outputs = {item.name: item for item in session.get_outputs()}
    if (
        set(outputs) != {"output_masks", "iou_predictions"}
        or outputs["output_masks"].shape != [1, 1, 3, 1024, 1024]
        or outputs["iou_predictions"].shape != [1, 1, 3]
        or any(item.type != "tensor(float)" for item in outputs.values())
    ):
        raise RuntimeError("Unexpected EfficientSAM mask/IoU output contract")
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise RuntimeError("EfficientSAM must run on ONNX Runtime CPU only")
    return session


def context_crop(image, detection, factor):
    x0, y0, x1, y1 = detection["box_xyxy"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    radius = factor * max(x1 - x0, y1 - y0) / 2
    box = (
        max(0, math.floor(cx - radius)),
        max(0, math.floor(cy - radius)),
        min(image.width, math.ceil(cx + radius)),
        min(image.height, math.ceil(cy + radius)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise RuntimeError("Empty target context crop")
    local = dict(detection, box_xyxy=[x0 - box[0], y0 - box[1], x1 - box[0], y1 - box[1]])
    return image.crop(box), local, box


def expanded_prompt(detection, size, factor):
    x0, y0, x1, y1 = detection["box_xyxy"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) * factor / 2, (y1 - y0) * factor / 2
    requested = [cx - rx, cy - ry, cx + rx, cy + ry]
    clipped = [max(0, requested[0]), max(0, requested[1]), min(size[0], requested[2]), min(size[1], requested[3])]
    return dict(detection, box_xyxy=clipped), requested != clipped


def show_prompt(image, prompt):
    """Pink remains the original NudeNet box; cyan is the actual SAM box prompt."""
    ImageDraw.Draw(image).rectangle(tuple(prompt["box_xyxy"]), outline=(0, 235, 255), width=2)
    return image


def segment(session, image, detection):
    """RGB /255, square letterbox, box-corner labels 2/3 and unused labels -1."""
    side = 1024
    scale = side / max(image.size)
    width, height = (max(1, min(side, round(v * scale))) for v in image.size)
    left, top = (side - width) // 2, (side - height) // 2
    small = image.resize((width, height), Image.Resampling.BILINEAR)
    rgb = np.pad(np.asarray(small), ((top, side - height - top), (left, side - width - left), (0, 0)), mode="edge")
    tensor = np.ascontiguousarray(rgb.astype(np.float32).transpose(2, 0, 1)[None] / np.float32(255))
    x0, y0, x1, y1 = detection["box_xyxy"]
    coordinates = np.zeros((1, 1, 6, 2), dtype=np.float32)
    coordinates[0, 0, :2] = [
        [x0 * width / image.width + left, y0 * height / image.height + top],
        [x1 * width / image.width + left, y1 * height / image.height + top],
    ]
    labels = np.full((1, 1, 6), -1, dtype=np.float32)
    labels[0, 0, :2] = [2, 3]
    started = time.perf_counter()
    logits, scores = session.run(
        ["output_masks", "iou_predictions"],
        {"batched_images": tensor, "batched_point_coords": coordinates, "batched_point_labels": labels},
    )
    seconds = time.perf_counter() - started
    if (
        logits.shape != (1, 1, 3, side, side)
        or scores.shape != (1, 1, 3)
        or not np.isfinite(logits).all()
        or not np.isfinite(scores).all()
    ):
        raise RuntimeError("Invalid/nonfinite EfficientSAM outputs")
    masks = []
    for index in np.argsort(-scores[0, 0], kind="stable"):
        local = logits[0, 0, index, top : top + height, left : left + width]
        restored = cv.resize(local, image.size, interpolation=cv.INTER_LINEAR)
        masks.append((int(index), float(scores[0, 0, index]), restored >= 0))
    return (
        masks,
        seconds,
        {
            "input": [side, side],
            "resized": [width, height],
            "padding_xy": [left, top],
            "box_points": coordinates[0, 0, :2].tolist(),
            "point_labels": labels[0, 0].tolist(),
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input-dir", type=Path, required=True)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("tmp/nudenet-sam-poc"))
    parser.add_argument("-m", "--model", type=Path, default=Path("tmp/models") / SAM_FILENAME)
    parser.add_argument("-ns", "--nudenet-size", choices=("320n", "640m"), default="640m")
    parser.add_argument("-ps", "--person-size", choices=("n", "s", "m"), default="n")
    parser.add_argument("-s", "--confidence", type=float, default=0.25)
    parser.add_argument("-q", "--person-threshold", type=float, default=0.3)
    parser.add_argument(
        "-k", "--context", type=float, default=2.5, help="Local crop side relative to target box long side, 1..6"
    )
    parser.add_argument(
        "-b",
        "--box-scale",
        type=float,
        default=1,
        help="SAM prompt width/height factor, 1..4; original NN box stays visible",
    )
    parser.add_argument(
        "-sw",
        "--box-sweep",
        action="store_true",
        help="Whole folder: compare 1.0/1.3/1.6x prompts on identical crops; 3 SAM calls per target",
    )
    parser.add_argument("-t", "--threads", type=int, default=4)
    parser.add_argument("-ts", "--tile", type=int, default=640)
    parser.add_argument(
        "-j",
        "--clip-person",
        action="store_true",
        help="Optionally clip SAM masks to YOLO; off by default to expose SAM's actual boundary",
    )
    parser.add_argument(
        "-cp", "--compare", action="store_true", help="Show all 3 SAM candidates; with -sw, one prompt-size column each"
    )
    parser.add_argument(
        "-mr",
        "--mask-rank",
        type=int,
        choices=(1, 2, 3),
        default=3,
        help="Select candidate by descending predicted-IoU rank (default 3); -cp shows all and marks this selection",
    )
    parser.add_argument("-f", "--images-only", action="store_true", help="Skip JSON reports")
    parser.add_argument(
        "-w", "--overwrite", action="store_true", help="Replace only this POC's recognized flat outputs"
    )
    parser.add_argument("-d", "--no-download", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.box_scale) or not 1 <= args.box_scale <= 4:
        parser.error("box-scale must be finite and in [1,4]")
    if args.box_sweep and args.box_scale != 1:
        parser.error("-sw uses fixed 1.0/1.3/1.6 scales; omit -b")
    scales = (1.0, 1.3, 1.6) if args.box_sweep else (args.box_scale,)
    context_factor = max(args.context, max(scales) * 1.2)
    if not args.input_dir.is_dir():
        parser.error("input-dir must exist")
    if not 0 < args.confidence <= 1 or not 0 < args.person_threshold < 1:
        parser.error("confidence in (0,1], person-threshold in (0,1) required")
    if (
        not math.isfinite(args.context)
        or not 1 <= args.context <= 6
        or args.threads < 1
        or not 256 <= args.tile <= 1024
    ):
        parser.error("context 1..6, threads >= 1, tile 256..1024 required")
    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        parser.error("No supported still images")
    output, input_dir = args.output_dir.resolve(), args.input_dir.resolve()
    if (
        output == input_dir
        or output in input_dir.parents
        or args.output_dir.is_symlink()
        or args.output_dir.is_junction()
    ):
        parser.error("output-dir must not redirect or contain input-dir")
    previous = list(output.iterdir()) if output.exists() else []
    pattern = re.compile(r"nsp_\d{4,}_.+_(?:d\d{2,}_(?:overlay|compare)|reference|report)\.(?:jpg|json)$")
    if previous and (
        not args.overwrite
        or any(p.is_symlink() or p.is_junction() or not p.is_file() or not pattern.fullmatch(p.name) for p in previous)
    ):
        parser.error("Output is not empty; -w replaces recognized nudenet_sam_poc outputs only")
    from dtflow.services import person_selection as selection

    person_path = Path("tmp/models") / f"yolo26{args.person_size}-seg-ultralytics-v8.4.0.onnx"
    parts_path = Path("tmp/models") / f"nudenet-{args.nudenet_size}.onnx"
    for path, spec in (
        (args.model, SAM_SPEC),
        (person_path, PERSON_SPECS[args.person_size]),
        (parts_path, NUDENET_SPECS[args.nudenet_size]),
    ):
        verified_checkpoint(path, spec, not args.no_download)
    person = selection._verified_session(selection.describe_model(person_path), selection._segmentation_session)
    parts = selection._verified_session(
        selection.describe_model(parts_path),
        nudenet_640_session if args.nudenet_size == "640m" else selection._faces_session,
    )
    sam = sam_session(args.model, args.threads)
    if any(s.get_providers() != ["CPUExecutionProvider"] for s in (person, parts, sam)):
        raise RuntimeError("All sessions must use ONNX Runtime CPU only")
    cv.setNumThreads(args.threads)
    output.mkdir(parents=True, exist_ok=True)
    for entry in previous:
        if entry.is_symlink() or entry.is_junction() or not entry.is_file() or entry.resolve().parent != output:
            raise RuntimeError(f"Output changed before cleanup: {entry.name}")
        entry.unlink()
    print(f"CPU models loaded; EfficientSAM-Ti INT8; {len(files)} images; {TARGET}", flush=True)
    print(
        f"SAM box scales: {', '.join(f'{v:g}x' for v in scales)}; {len(scales)} calls per target; fixed context {context_factor:g}x",
        flush=True,
    )
    failures = 0
    for image_index, path in enumerate(files, 1):
        prefix = f"nsp_{image_index:04d}_{path.stem[:70]}"
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
                    "nudenet": {
                        "algorithm": NUDENET_SPECS[args.nudenet_size][2],
                        "digest": NUDENET_SPECS[args.nudenet_size][3],
                    },
                    "sam_sha256": SAM_SPEC[3],
                },
                "backend": "ONNX Runtime CPU",
                "mask_threshold": 0,
                "score_meaning": "Model-predicted IoU used for ranking, not measured accuracy",
                "targets": [],
            }
            detections, crop = [], source
            if people:
                _, person_mask, person_box = people[0]
                box = tuple(int(v) for v in person_crop_box(person_box, source.size))
                crop = source.crop(box)
                inside = person_mask[box[1] : box[3], box[0] : box[2]] > 0
                _, found, _ = detect_body_parts(
                    parts, crop, args.confidence, input_side=640 if args.nudenet_size == "640m" else 320
                )
                detections = [d for d in found if d["label"] == TARGET]
                report["person_crop_source_xyxy"] = box
            for target_index, detection in enumerate(detections, 1):
                roi, local_detection, roi_box = context_crop(crop, detection, context_factor)
                columns = []
                target_report = {
                    "detection_person_crop_coordinates": detection,
                    "local_crop_person_coordinates": roi_box,
                    "context_factor": context_factor,
                    "sam_seconds": 0.0,
                    "prompt_variants": [],
                }
                valid = (
                    inside[roi_box[1] : roi_box[3], roi_box[0] : roi_box[2]]
                    if args.clip_person
                    else np.ones((roi.height, roi.width), dtype=bool)
                )
                for factor in scales:
                    prompt, clipped = expanded_prompt(local_detection, roi.size, factor)
                    candidates, sam_seconds, preprocessing = segment(sam, roi, prompt)
                    variant = {
                        "box_scale": factor,
                        "prompt_local_xyxy": prompt["box_xyxy"],
                        "prompt_clipped": clipped,
                        "sam_seconds": sam_seconds,
                        "preprocessing": preprocessing,
                        "selected_rank": args.mask_rank,
                        "candidates": [],
                    }
                    target_report["sam_seconds"] += sam_seconds
                    panels = []
                    if args.compare:
                        reference = show_prompt(boxed(np.asarray(roi), [local_detection]), prompt)
                        panels.append(tile(reference, f"{factor:g}x: NN pink / SAM cyan", (0, 0, *roi.size), args.tile))
                    for rank, (mask_index, score, raw_mask) in enumerate(candidates, 1):
                        mask = raw_mask & valid
                        variant["candidates"].append(
                            {
                                "rank": rank,
                                "selected": rank == args.mask_rank,
                                "model_mask_index": mask_index,
                                "predicted_iou": score,
                                "raw_pixels": int(raw_mask.sum()),
                                "pixels": int(mask.sum()),
                            }
                        )
                        if args.compare or rank == args.mask_rank:
                            preview = show_prompt(
                                contour_preview(np.asarray(roi), [(7, mask)], valid, [local_detection]), prompt
                            )
                            caption = f"{factor:g}x / {rank}: predicted IoU {score:.3f}" + (
                                " / selected" if rank == args.mask_rank else ""
                            )
                            panels.append(tile(preview, caption, (0, 0, *roi.size), args.tile))
                    columns.append(panels)
                    target_report["prompt_variants"].append(variant)
                if len(scales) == 1:
                    # Retain the original single-prompt JSON fields for existing report readers.
                    target_report.update(preprocessing=variant["preprocessing"], candidates=variant["candidates"])
                if args.box_sweep:
                    result = Image.new(
                        "RGB", (args.tile * len(columns), (args.tile + 52) * len(columns[0])), (22, 22, 26)
                    )
                    for column, panels in enumerate(columns):
                        for row, panel in enumerate(panels):
                            result.paste(panel, (column * args.tile, row * (args.tile + 52)))
                elif args.compare:
                    result = Image.new("RGB", (args.tile * 2, (args.tile + 52) * 2), (22, 22, 26))
                    for i, panel in enumerate(columns[0]):
                        result.paste(panel, ((i % 2) * args.tile, (i // 2) * (args.tile + 52)))
                else:
                    result = columns[0][0]
                filename = (
                    f"{prefix}_d{target_index:02d}_{'compare' if args.compare or args.box_sweep else 'overlay'}.jpg"
                )
                result.save(output / filename, quality=93)
                target_report["output"] = filename
                report["targets"].append(target_report)
            report["status"] = "segmented" if detections else "no_target" if people else "no_person"
            if not detections:
                tile(crop, report["status"] + " / SAM skipped", (0, 0, *crop.size), args.tile).save(
                    output / f"{prefix}_reference.jpg", quality=93
                )
            report["seconds"] = time.perf_counter() - started
            if not args.images_only:
                (output / f"{prefix}_report.json").write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
                )
            print(
                f"{path.name}: {len(detections)} targets, {report['status']}; SAM {sum(t['sam_seconds'] for t in report['targets']):.2f}s, total {report['seconds']:.2f}s",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            print(f"{path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

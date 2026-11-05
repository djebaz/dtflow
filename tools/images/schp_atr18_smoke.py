#!/usr/bin/env python3
"""Compare SCHP ATR-18 on DTFlow person crops with shared NudeNet face refinement.

Run from the repository root with its uv-managed environment:
    uv run python tools/images/schp_atr18_smoke.py --help

Missing SCHP, YOLO26 person and NudeNet face ONNX files download automatically.
Use --pose-model without a path to fetch the default pose checkpoint too.
Existing local files are reused; --no-download disables every download. No provider calls.
Raw and refined outputs are grouped by type and aligned to the oriented source.
Optionally add -k / --pose-model for conservative arm/leg label correction;
-q controls joint confidence and -w controls limb-zone width. Pose-corrected
outputs have a _pose suffix; raw/refined masks and SCHP confidence stay intact.
All NudeNet classes add _body outputs with independent anatomical masks.
Image contrast and SCHP score changes guide independent body-region boundaries.
--body-confidence breaks equal-area region ties, never excludes low-score pixels.
--boundary controls the strength of the combined edge barrier (default 0.12).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Exact file revision and Git LFS SHA-256 recorded in the upstream model commit.
MODEL_REVISION = "1ddc548f48cc1435a505e565ff5f56dcfe6017e3"
MODEL_NAME = "schp-atr-18-int8-static.onnx"
MODEL_URL = f"https://huggingface.co/pirocheto/schp-atr-18/resolve/{MODEL_REVISION}/onnx/{MODEL_NAME}"
MODEL_SHA256 = "4420d8db8c1f266967c89485786b01209f6d405f320fc0f87e8ced49392cefb5"
MODEL_BYTES = 69_141_996
SIDE = 512
LABELS = (
    "Background",
    "Hat",
    "Hair",
    "Sunglasses",
    "Upper-clothes",
    "Skirt",
    "Pants",
    "Dress",
    "Belt",
    "Left-shoe",
    "Right-shoe",
    "Face",
    "Left-leg",
    "Right-leg",
    "Left-arm",
    "Right-arm",
    "Bag",
    "Scarf",
)
# High-contrast qualitative palette. Left/right pairs intentionally differ.
# Visual-only colors: class IDs are untouched by changes to this palette.
COLORS = (
    (0, 0, 0),  # 00 Background
    (255, 67, 54),  # 01 Hat - scarlet
    (0, 188, 172),  # 02 Hair - turquoise
    (255, 214, 0),  # 03 Sunglasses - yellow
    (46, 94, 255),  # 04 Upper-clothes - blue
    (226, 45, 171),  # 05 Skirt - magenta
    (28, 168, 70),  # 06 Pants - green
    (255, 127, 24),  # 07 Dress - orange
    (129, 68, 213),  # 08 Belt - violet
    (0, 128, 204),  # 09 Left-shoe - azure
    (255, 91, 145),  # 10 Right-shoe - pink
    (186, 223, 41),  # 11 Face - lime
    (63, 204, 242),  # 12 Left-leg - cyan
    (219, 50, 27),  # 13 Right-leg - red
    (153, 132, 255),  # 14 Left-arm - lavender
    (69, 214, 54),  # 15 Right-arm - bright green
    (139, 80, 30),  # 16 Bag - brown
    (255, 173, 0),  # 17 Scarf - amber
)
# IMPORTANT: This model uses RGB pixels, but its published mean/std follow
# the BGR-indexed normalization constants used during SCHP training.
MEAN = (0.406, 0.456, 0.485)
STD = (0.225, 0.224, 0.229)
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})
MAX_IMAGE_PIXELS = 16_000_000
NUDENET_CONFIDENCE = 0.10
RED_CORE_CONFIDENCE = 0.90
DISPLAY_LABELS = (*LABELS, "Skin")  # synthetic class 18, never an SCHP prediction
DISPLAY_COLORS = (*COLORS, (255, 0, 255))  # vivid magenta stays visible over natural skin

# Published upstream asset digests and sizes; no export toolchain is required.
# NudeNet uses the Git blob identity from its pinned source commit.
CHECKPOINTS = {
    "person": (
        "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-seg.onnx",
        11_218_582,
        "sha256",
        "711e0ef837c677ae4006f65fe503a62a6b0529979f4828ed50b674a7482b7d2e",
    ),
    "faces": (
        "https://raw.githubusercontent.com/notAI-tech/NudeNet/"
        "21ccea100712f0844a4f6bb66c9f3972b2c32f85/nudenet/320n.onnx",
        12_150_158,
        "git-sha1",
        "68eedc3934ac160fce68e2bd4336cfe17eef140b",
    ),
    "pose": (
        "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.onnx",
        12_125_856,
        "sha256",
        "93fc5e1d6b7690f33b4e1d60d6e9aec1cea14bdbc361bfae11778969be662078",
    ),
}


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 without loading the entire model into RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_checkpoint(path: Path, spec, *, allow_download: bool):
    """Reuse a local checkpoint or download and verify the pinned role default."""
    if path.is_file():
        print(f"Reusing local checkpoint: {path}", flush=True)
        return
    if not allow_download:
        raise FileNotFoundError(f"Checkpoint missing with --no-download: {path}")
    url, expected_bytes, algorithm, expected_digest = spec
    digest = hashlib.sha256() if algorithm == "sha256" else hashlib.sha1()
    if algorithm == "git-sha1":
        digest.update(f"blob {expected_bytes}\0".encode("ascii"))
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {path.name} ({expected_bytes / 1024 / 1024:.1f} MiB): {url}", flush=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
    part = Path(temporary)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "dtflow-schp-smoke/1.0"})
        total = 0
        shown_at = time.monotonic()
        with os.fdopen(fd, "wb") as dest:
            with urllib.request.urlopen(request, timeout=90) as response:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > expected_bytes:
                        raise RuntimeError(f"Download exceeds expected checkpoint size: {path.name}")
                    dest.write(chunk)
                    digest.update(chunk)
                    if time.monotonic() - shown_at >= 1:
                        print(f"  {path.name}: {total / 1024 / 1024:.1f} MiB", flush=True)
                        shown_at = time.monotonic()
        if total != expected_bytes or digest.hexdigest() != expected_digest:
            raise RuntimeError(
                f"Checkpoint download failed integrity checks: {path.name}; "
                f"{total} bytes, {algorithm} {digest.hexdigest()}"
            )
        # Do not replace a file another run supplied while this download was active.
        if not path.exists():
            os.replace(part, path)
        print(f"Checkpoint ready: {path}", flush=True)
    finally:
        part.unlink(missing_ok=True)


def ensure_model(path: Path, *, allow_download: bool) -> str:
    """Retain SCHP's pinned SHA-256 check for both cached and downloaded files."""
    ensure_checkpoint(path, (MODEL_URL, MODEL_BYTES, "sha256", MODEL_SHA256), allow_download=allow_download)
    actual = sha256_file(path)
    if actual != MODEL_SHA256:
        raise RuntimeError(
            f"Existing SCHP model has an unexpected SHA-256: {path}\n"
            f"Expected {MODEL_SHA256}\nFound    {actual}\n"
            "Move or remove that file explicitly before retrying."
        )
    return actual


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
    """Fit a person crop into centered white 512-square padding without stretching."""
    import numpy as np
    from PIL import Image

    scale = SIDE / max(source.size)
    width = max(1, min(SIDE, round(source.width * scale)))
    height = max(1, min(SIDE, round(source.height * scale)))
    left, top = (SIDE - width) // 2, (SIDE - height) // 2
    canvas = Image.new("RGB", (SIDE, SIDE), "white")
    canvas.paste(source.resize((width, height), Image.Resampling.BILINEAR), (left, top))
    rgb = np.asarray(canvas, dtype=np.float32) / np.float32(255.0)
    rgb = (rgb - np.asarray(MEAN, dtype=np.float32)) / np.asarray(STD, dtype=np.float32)
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None, ...], dtype=np.float32)
    return tensor, (left, top, left + width, top + height)


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


def classify_logits(logits, fit, target_size):
    """Unpad and bilinearly restore logits before argmax, as in DTFlow SegFormer.

    Accumulate stable softmax one class at a time to avoid an 18-channel
    source-resolution allocation. Scores remain uncalibrated.
    """
    import numpy as np
    from PIL import Image

    if logits.shape != (1, len(LABELS), SIDE, SIDE):
        raise RuntimeError(f"Unexpected logits shape: {logits.shape}")
    if not np.isfinite(logits).all():
        raise RuntimeError("Model returned non-finite class logits.")
    left, top, right, bottom = fit
    width, height = target_size
    labels = np.zeros((height, width), dtype=np.uint8)
    maximum = np.full((height, width), -np.inf, dtype=np.float32)
    denominator = np.zeros((height, width), dtype=np.float32)
    for idx, plane in enumerate(logits[0]):
        cropped = plane[top:bottom, left:right].astype(np.float32)
        score = np.asarray(
            Image.fromarray(cropped).resize(target_size, Image.Resampling.BILINEAR),
            dtype=np.float32,
        )
        updated = np.maximum(maximum, score)
        denominator = denominator * np.exp(maximum - updated) + np.exp(score - updated)
        labels[score > maximum] = idx
        maximum = updated
    return labels, (1.0 / denominator).astype(np.float32)


def person_crop_box(box, image_size):
    """Mirror SegFormer's source-pixel crop and 8% margin, including edge clipping."""
    width, height = image_size
    x0, y0, x1, y1 = box
    mx, my = int((x1 - x0) * 0.08), int((y1 - y0) * 0.08)
    return max(0, x0 - mx), max(0, y0 - my), min(width, x1 + mx), min(height, y1 + my)


def refine_face(labels, inside, box, face_boxes):
    """Adapt raw SCHP IDs to the shared offset encoding; keep no-detection behavior."""
    import numpy as np

    from dtflow.services.person_selection import _cap_face

    # The shared helper expects class + 1 inside a person, 0 outside.
    encoded = np.where(inside, labels + 1, 0).astype(np.uint8)
    if face_boxes:
        encoded = np.frombuffer(_cap_face(encoded.tobytes(), box, face_boxes), dtype=np.uint8).reshape(labels.shape)
    return np.where(encoded > 0, encoded.astype(np.int16) - 1, 0).astype(np.uint8)


def segment_person(session, source, mask, box, face_boxes, min_confidence):
    """Run one SCHP crop, clip to the shared person silhouette and refine face pixels."""
    import numpy as np

    crop_box = person_crop_box(box, source.size)
    crop = source.crop(crop_box)
    tensor, fit = prepare_image(crop)
    stamp = time.perf_counter()
    logits = session.run(["logits"], {"pixel_values": tensor})[0]
    inference_seconds = time.perf_counter() - stamp
    crop_labels, crop_confidence = classify_logits(logits, fit, crop.size)
    x0, y0, x1, y1 = box
    cx0, cy0, _, _ = crop_box
    region = np.s_[y0 - cy0 : y1 - cy0, x0 - cx0 : x1 - cx0]
    inside = mask[y0:y1, x0:x1] > 0
    labels = np.where(inside, crop_labels[region], 0).astype(np.uint8)
    confidence = np.where(inside, crop_confidence[region], 0).astype(np.float32)
    filtered = labels.copy()
    suppressed = (filtered != 0) & (confidence < min_confidence)
    filtered[suppressed] = 0
    refined = refine_face(filtered, inside, box, face_boxes)

    shape = (source.height, source.width)
    raw_full = np.zeros(shape, dtype=np.uint8)
    refined_full = np.zeros(shape, dtype=np.uint8)
    confidence_full = np.zeros(shape, dtype=np.float32)
    raw_full[y0:y1, x0:x1] = labels
    refined_full[y0:y1, x0:x1] = refined
    confidence_full[y0:y1, x0:x1] = confidence
    details = {
        "person_box": list(box),
        "crop_box": list(crop_box),
        "schp_content_box": list(fit),
        "schp_input_size": [SIDE, SIDE],
        "padding_color": [255, 255, 255],
        "logit_restoration": "remove padding, bilinear to source crop, then argmax",
        "person_pixels": int(inside.sum()),
        "suppressed_foreground_pixels": int(suppressed.sum()),
        "face_refinement": "applied" if face_boxes else "unavailable_no_face_detected",
        "face_pixels_before_refinement": int((filtered == 11).sum()),
        "face_pixels_after_refinement": int((refined == 11).sum()),
        "face_pixels_reclassified_as_skin": int(((filtered == 11) & (refined == 18)).sum()),
        "mean_top_class_confidence_inside_person": float(confidence[inside].mean()),
        "inference_seconds": inference_seconds,
    }
    return raw_full, refined_full, confidence_full, details


def detect_body_parts(session, source, threshold):
    """Decode every NudeNet class from one inference; NMS only within each class."""
    import ast
    import numpy as np

    from dtflow.services import person_selection as selection
    from person_parts_mask_poc import decode_parts, preprocess_parts

    names = {int(k): str(v) for k, v in ast.literal_eval(session.get_modelmeta().custom_metadata_map["names"]).items()}
    tensor, geometry = preprocess_parts(source)
    output = selection._run(session, tensor)[0]
    detections = []
    for class_id in sorted(names):
        found, _ = decode_parts(
            output,
            geometry,
            source.size,
            threshold,
            selection.FACES_IOU,
            names,
            exclude_ids=[idx for idx in names if idx != class_id],
        )
        detections.extend(found)
    detections.sort(key=lambda detection: -detection["score"])
    faces = [d for d in detections if d["label"].startswith("FACE_")]
    boxes = np.asarray([d["box_xyxy"] for d in faces], dtype=np.float32).reshape(-1, 4)
    scores = np.asarray([d["score"] for d in faces], dtype=np.float32)
    face_boxes = [tuple(float(v) for v in boxes[i]) for i in selection._nms(boxes, scores, selection.FACES_IOU)]
    return names, detections, face_boxes


def body_palette(names):
    """Keep existing SCHP colors and assign vivid colors to the added anatomical classes."""
    import colorsys

    colors = list(DISPLAY_COLORS)
    for idx in range(len(names)):
        rgb = colorsys.hsv_to_rgb((0.08 + idx * 0.61803398875) % 1.0, 0.85, 1.0)
        colors.append(tuple(round(channel * 255) for channel in rgb))
    return tuple(colors)


def body_boundary_strength(source, confidence, person_mask):
    """Combine local RGB contrast and score differences, independently of display colors.

    A light RGB blur reduces pixel texture. Each neighboring pair contributes
    65% normalized RGB distance and 35% absolute SCHP score difference to both
    endpoints. Edges outside the person do not influence its interior.
    """
    import numpy as np
    from PIL import ImageFilter

    rgb = np.asarray(source.filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32) / 255.0
    inside = person_mask > 0
    strength = np.zeros(inside.shape, dtype=np.float32)
    for a, b in ((np.s_[:, :-1], np.s_[:, 1:]), (np.s_[:-1, :], np.s_[1:, :])):
        contrast = np.sqrt(np.mean((rgb[a] - rgb[b]) ** 2, axis=-1))
        difference = 0.65 * contrast + 0.35 * np.abs(confidence[a] - confidence[b])
        difference *= inside[a] & inside[b]
        np.maximum(strength[a], difference, out=strength[a])
        np.maximum(strength[b], difference, out=strength[b])
    return strength


def largest_body_region(candidate, confidence, seed_floor):
    """Keep the largest component; high-score seeds only break equal-area ties."""
    import numpy as np
    from person_parts_mask_poc import label_components

    components, count = label_components(candidate)
    if not count:
        return np.zeros_like(candidate), False
    sizes = np.bincount(components.ravel(), minlength=count + 1)
    sizes[0] = 0
    largest = np.flatnonzero(sizes == sizes.max())
    seeded = np.unique(components[candidate & (confidence >= seed_floor)])
    preferred = largest[np.isin(largest, seeded)]
    chosen = int(preferred[0] if preferred.size else largest[0])
    return components == chosen, bool(chosen in seeded)


def refine_body_parts(
    source,
    labels,
    person_mask,
    confidence,
    detections,
    names,
    boundary_confidence,
    out_dir,
    stem,
    *,
    rounding=0.0,
    save_diagnostics=True,
    boundary_threshold=0.12,
    edge_method="existing",
    canny_low=60,
    canny_high=140,
):
    """Select each box independently using image/score edges; retain overlapping masks.

    The single-label map is only a compatibility projection. Independent cropped
    masks are authoritative and drive the body overlay, including in body-only mode.
    """
    import math

    from person_parts_mask_poc import smooth_mask
    import numpy as np
    from PIL import Image, ImageDraw

    ordered = sorted(names)
    class_ids = {key: len(DISPLAY_LABELS) + idx for idx, key in enumerate(ordered)}
    class_names = (*DISPLAY_LABELS, *(f"NN_{names[key]}" for key in ordered))
    colors = body_palette(names)
    combined = np.zeros(labels.shape, dtype=np.uint8)
    claimed = np.zeros(labels.shape, dtype=bool)
    superpixels = None
    if edge_method == "slic":
        try:
            from skimage.segmentation import slic, find_boundaries
        except ImportError as exc:
            raise RuntimeError("SLIC requires scikit-image: uv pip install scikit-image") from exc
        ys, xs = np.nonzero(person_mask)
        if len(xs):
            px0, px1 = max(0, int(xs.min()) - 4), min(source.width, int(xs.max()) + 5)
            py0, py1 = max(0, int(ys.min()) - 4), min(source.height, int(ys.max()) + 5)
            crop = np.asarray(source.crop((px0, py0, px1, py1)))
            area = int((person_mask[py0:py1, px0:px1] > 0).sum())
            segments = slic(crop, n_segments=max(16, round(area / 400)),
                            compactness=10, sigma=1, start_label=1,
                            channel_axis=-1, enforce_connectivity=True)
            superpixels = np.zeros(labels.shape, dtype=np.int32)
            superpixels[py0:py1, px0:px1] = segments
            strength = np.zeros(labels.shape, dtype=np.float32)
            strength[py0:py1, px0:px1] = find_boundaries(segments, mode="thick").astype(np.float32)
        else:
            strength = np.zeros(labels.shape, dtype=np.float32)
    elif edge_method == "canny":
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("Canny requires OpenCV: uv pip install opencv-python-headless") from exc
        gray = cv2.cvtColor(np.asarray(source), cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, canny_low, canny_high, L2gradient=True)
        strength = (edges > 0).astype(np.float32)
    else:
        strength = body_boundary_strength(source, confidence, person_mask)
    boundary = (person_mask > 0) & (strength >= boundary_threshold)
    layers = []
    unions = {}
    reports = []
    boxes_view = source.copy() if save_diagnostics else None
    draw = ImageDraw.Draw(boxes_view) if save_diagnostics else None
    for detection_index, detection in enumerate(detections, start=1):
        bx0, by0, bx1, by1 = detection["box_xyxy"]
        x0, y0 = max(0, math.floor(bx0)), max(0, math.floor(by0))
        x1, y1 = min(source.width, math.ceil(bx1)), min(source.height, math.ceil(by1))
        if x1 <= x0 or y1 <= y0:
            continue
        region = np.s_[y0:y1, x0:x1]
        candidate = person_mask[region] > 0
        name = detection["label"]
        # Neither SCHP classes nor another body mask can exclude this region.
        if superpixels is not None:
            # SLIC regions are closed. Keep whole superpixels whose centers fall
            # inside the detection box, independently for each body detection.
            ids = superpixels[region]
            valid = ids[candidate & (ids > 0)]
            if valid.size:
                selected_ids = np.unique(valid)
                supported = candidate & np.isin(ids, selected_ids)
            else:
                supported = np.zeros_like(candidate)
            seeded = bool(np.any(supported & (confidence[region] >= boundary_confidence)))
        else:
            allowed = candidate & ~boundary[region]
            supported, seeded = largest_body_region(allowed, confidence[region], boundary_confidence)
        allowed = candidate & ~boundary[region] if superpixels is None else candidate
        selected_pixels = int(supported.sum())
        if rounding > 0 and supported.any() and edge_method != "slic":
            # Stay inside the chosen component: never fill a silhouette gap or
            # expand into another region across an image/score barrier.
            supported = smooth_mask(supported, blur_sigma=rounding) & supported
            supported, _ = largest_body_region(supported, confidence[region], boundary_confidence)
        class_id = class_ids[detection["class_id"]]
        layers.append({"class_id": class_id, "box": (x0, y0, x1, y1), "mask": supported})
        mask_name = f"{stem}_body_detection_{detection_index:03d}_{name.lower()}.png"
        if save_diagnostics:
            if class_id not in unions:
                unions[class_id] = np.zeros(labels.shape, dtype=bool)
            unions[class_id][region] |= supported
            # One source-aligned mask per detection, even for the same class.
            individual = Image.new("L", source.size)
            individual.paste(Image.fromarray(supported.astype(np.uint8) * 255), (x0, y0))
            individual.save(out_dir / "masks" / mask_name)
        take = supported & ~claimed[region]
        combined[region][take] = class_id
        claimed[region][take] = True
        reports.append(
            {
                "label": name,
                "score": detection["score"],
                "box_xyxy": detection["box_xyxy"],
                "person_supported_pixels": int(candidate.sum()),
                "interior_seed_pixels": int((allowed & (confidence[region] >= boundary_confidence)).sum()),
                "seed_selection": ("slic_superpixels_with_high_score_seed" if seeded else "slic_superpixels_no_high_score_seed")
                if superpixels is not None
                else ("largest_component_with_high_score_seed" if seeded else "largest_component_no_high_score_seed"),
                "boundary_supported_pixels": int(allowed.sum()),
                "selected_segment_pixels_before_rounding": selected_pixels,
                "rounded_segment_pixels": int(supported.sum()),
                "boundary_pixels_excluded": int((candidate & boundary[region]).sum()),
                "other_segment_pixels_excluded": int((allowed & ~supported).sum()),
                "mask_file": f"masks/{mask_name}" if save_diagnostics else None,
            }
        )
        if supported.any() and draw is not None:
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=colors[class_id], width=2)
            draw.text(
                (x0 + 3, y0 + 3),
                f"{name} {detection['score']:.2f}",
                fill=colors[class_id],
                stroke_width=1,
                stroke_fill="black",
            )
    if save_diagnostics:
        for class_id, mask in unions.items():
            Image.fromarray(mask.astype(np.uint8) * 255).save(
                out_dir / "masks" / f"{stem}_body_part_{class_names[class_id].lower()}.png"
            )
        Image.fromarray(boundary.astype(np.uint8) * 255).save(out_dir / "masks" / f"{stem}_body_boundary.png")
        boxes_view.save(out_dir / "persons" / f"{stem}_body_boxes.png")
    return (
        combined,
        class_names,
        colors,
        layers,
        strength,
        {
            "status": "applied" if claimed.any() else "no_supported_body_parts",
            "interior_seed_score": boundary_confidence,
            "boundary_strength_threshold": boundary_threshold,
            "edge_method": edge_method,
            "canny_thresholds": [canny_low, canny_high] if edge_method == "canny" else None,
            "boundary_rgb_weight": 0.65 if edge_method == "existing" else None,
            "boundary_score_weight": 0.35,
            "boundary_rgb_blur_source_pixels": 1.0,
            "rounding_radius_source_pixels": rounding,
            "rounding_preserves_selected_region_and_silhouette": True,
            "assigned_pixels": int(claimed.sum()),
            "confidence_definition": "SCHP maximum class score; variations guide edges, high scores guide seeds",
            "independent_masks_preserve_overlaps": True,
            "segment_selection": "largest edge-enclosed component; high-score seeds only break equal-area ties; independent of SCHP class",
            "combined_overlap_rule": "single-label compatibility projection only: highest NudeNet detection score",
            "overlay_overlap_rule": "average class colors at overlaps, draw every independent contour",
            "detections": reports,
        },
    )


def match_pose_people(people, poses):
    """Match whole-image skeletons to person instances once, using box IoU."""
    candidates = []
    for person_index, (_, _, box) in enumerate(people):
        x0, y0, x1, y1 = box
        for pose_index, pose in enumerate(poses):
            px0, py0, px1, py1 = pose["box_source_xyxy"]
            intersection = max(0, min(x1, px1) - max(x0, px0)) * max(0, min(y1, py1) - max(y0, py0))
            union = (x1 - x0) * (y1 - y0) + max(0, px1 - px0) * max(0, py1 - py0) - intersection
            iou = intersection / union if union > 0 else 0
            if iou >= 0.25:
                candidates.append((iou, person_index, pose_index))
    matched = {}
    used = set()
    for iou, person_index, pose_index in sorted(candidates, reverse=True):
        if person_index not in matched and pose_index not in used:
            matched[person_index] = (poses[pose_index], iou)
            used.add(pose_index)
    return matched


def correct_pose_limbs(labels, person_mask, box, matched, kpt_confidence, width):
    """Correct only arm/leg family conflicts inside unambiguous confident pose zones."""
    import numpy as np

    from person_parts_mask_poc import limb_zone

    corrected = labels.copy()
    changed = np.zeros(labels.shape, dtype=bool)
    if matched is None:
        return corrected, changed, {"status": "unavailable_no_matching_pose", "changed_pixels": 0}
    pose, match_iou = matched
    x0, y0, x1, y1 = box
    inside = person_mask[y0:y1, x0:x1] > 0
    points = pose["keypoints"]
    # COCO subject-side joints. Hands, feet, face, torso and clothes are untouched.
    segments = {
        12: ((11, 13), (13, 15)),
        13: ((12, 14), (14, 16)),
        14: ((5, 7), (7, 9)),
        15: ((6, 8), (8, 10)),
    }
    zones = {}
    used_segments = []
    for class_id, bones in segments.items():
        zone = np.zeros(inside.shape, dtype=bool)
        for start, end in bones:
            ax, ay, ac = points[start]
            bx, by, bc = points[end]
            if min(ac, bc) < kpt_confidence:
                continue
            part = limb_zone(inside, (ax - x0, ay - y0), (bx - x0, by - y0), "segment", width)
            if part.any():
                zone |= part
                used_segments.append([start, end])
        zones[class_id] = zone
    count = sum(zone.astype(np.uint8) for zone in zones.values())
    original = labels[y0:y1, x0:x1]
    target = corrected[y0:y1, x0:x1]
    for class_id, zone in zones.items():
        # Do not guess through crossings, or rewrite left/right within a family.
        wrong_family = np.isin(original, (14, 15) if class_id in (12, 13) else (12, 13))
        replace = zone & (count == 1) & wrong_family
        target[replace] = class_id
    changed[y0:y1, x0:x1] = target != original
    details = {
        "status": "applied" if used_segments else "unavailable_no_confident_limb_zones",
        "match_box_iou": match_iou,
        "keypoints_xyc": points,
        "used_joint_pairs": used_segments,
        "keypoint_confidence": kpt_confidence,
        "zone_half_width_fraction_of_bone": width,
        "overlapping_zone_pixels_left_unchanged": int((count > 1).sum()),
        "arm_to_leg_pixels": int((np.isin(original, (14, 15)) & np.isin(target, (12, 13))).sum()),
        "leg_to_arm_pixels": int((np.isin(original, (12, 13)) & np.isin(target, (14, 15))).sum()),
        "changed_pixels": int(changed.sum()),
    }
    return corrected, changed, details


def save_pose_overlay(source, matched, kpt_confidence, out_dir: Path, stem: str):
    """Show the matched skeleton and joint scores for visual inspection."""
    from PIL import ImageDraw

    overlay = source.copy()
    draw = ImageDraw.Draw(overlay)
    if matched is None:
        draw.text((8, 8), "No matching pose: arm/leg correction skipped", fill=(255, 80, 80))
    else:
        pose, _ = matched
        points = pose["keypoints"]
        for start, end, color in (
            (5, 7, (153, 132, 255)),
            (7, 9, (153, 132, 255)),
            (6, 8, (69, 214, 54)),
            (8, 10, (69, 214, 54)),
            (11, 13, (63, 204, 242)),
            (13, 15, (63, 204, 242)),
            (12, 14, (219, 50, 27)),
            (14, 16, (219, 50, 27)),
        ):
            ax, ay, ac = points[start]
            bx, by, bc = points[end]
            if min(ac, bc) >= kpt_confidence:
                draw.line((ax, ay, bx, by), fill=color, width=3)
        for idx, (x, y, score) in enumerate(points):
            if score >= kpt_confidence:
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="white", outline="black")
                draw.text((x + 5, y), f"{idx}: {score:.2f}", fill="white", stroke_width=1, stroke_fill="black")
    overlay.save(out_dir / "persons" / f"{stem}_pose_keypoints.png")


def create_output_dir(requested: Path) -> Path:
    """Preserve earlier runs by choosing a numbered sibling if the folder exists."""
    root = requested.resolve()
    candidate = root
    suffix = 2
    while True:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = root.with_name(f"{root.name}_{suffix}")
            suffix += 1


def confidence_colors(confidence, inside):
    """Map 0 / 0.5 / red-core floor to blue / orange / red; saturate above it."""
    import numpy as np

    score = np.clip(confidence, 0, 1)
    blue = np.asarray((0, 80, 255), dtype=np.float32)
    orange = np.asarray((255, 165, 0), dtype=np.float32)
    red = np.asarray((255, 0, 0), dtype=np.float32)
    first = blue + (orange - blue) * (score[..., None] * 2)
    red_fraction = np.clip((score[..., None] - 0.5) / (RED_CORE_CONFIDENCE - 0.5), 0, 1)
    second = orange + (red - orange) * red_fraction
    colors = np.where((score <= 0.5)[..., None], first, second)
    return np.where(inside[..., None], np.rint(colors), 0).astype(np.uint8)


def save_confidence(confidence, inside, out_dir: Path, stem: str):
    """Save a source-aligned RGB heatmap plus a separate fixed-range legend."""
    import numpy as np
    from PIL import Image, ImageDraw

    heatmap = Image.fromarray(confidence_colors(confidence, inside))
    heatmap.save(out_dir / "confidence" / f"{stem}_confidence.png")
    legend = Image.new("RGB", (360, 64), "white")
    ramp = np.linspace(0, 1, 320, dtype=np.float32)[None, :]
    bar = np.repeat(confidence_colors(ramp, np.ones_like(ramp, dtype=bool)), 18, axis=0)
    legend.paste(Image.fromarray(bar), (20, 8))
    draw = ImageDraw.Draw(legend)
    draw.text((20, 30), "0.0 low", fill="black")
    draw.text((153, 30), "0.5", fill="black")
    draw.text((284, 30), "1.0 high", fill="black")
    draw.text((20, 48), "Uncalibrated model score", fill="black")
    legend.save(out_dir / "confidence" / f"{stem}_scale.png")
    return heatmap, legend


def save_score_overlay(source, confidence, person_mask, detections, out_dir, stem, opacity, body_floor, *, flat=False):
    """Show SCHP scores within the exact YOLO silhouette, with detector boxes."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    inside = person_mask > 0
    heat = Image.fromarray(confidence_colors(confidence, inside)).convert("RGBA")
    heat.putalpha(Image.fromarray(np.where(inside, round(255 * opacity), 0).astype(np.uint8)))
    overlay = Image.alpha_composite(source.convert("RGBA"), heat).convert("RGB")
    if flat:
        overlay = Image.fromarray(confidence_colors(confidence, inside))
    # Outline the actual segmentation mask, including holes, rather than its box.
    silhouette = Image.fromarray(inside.astype(np.uint8) * 255)
    eroded = np.asarray(silhouette.filter(ImageFilter.MinFilter(3))) > 0
    edge = inside & ~eroded
    overlay.paste("white", mask=Image.fromarray(edge.astype(np.uint8) * 255))
    draw = ImageDraw.Draw(overlay)
    for detection in detections:
        x0, y0, x1, y1 = detection["box_xyxy"]
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(source.width, int(x1)), min(source.height, int(y1))
        if x1 <= x0 or y1 <= y0 or not inside[y0:y1, x0:x1].any():
            continue
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline="cyan", width=1)
        draw.text(
            (x0 + 2, y0 + 2),
            f"{detection['label']} {detection['score']:.2f}",
            fill="cyan",
            stroke_width=1,
            stroke_fill="black",
        )
    width, height = source.size
    result = Image.new("RGB", (width + 380, max(height, 250)), "white")
    result.paste(overlay, (0, 0))
    draw = ImageDraw.Draw(result)
    left = width + 14
    draw.text((left, 16), "SCHP confidence | YOLO person mask", fill="black")
    draw.text((left, 42), "White outline: person silhouette", fill="black")
    draw.text((left, 66), "Cyan boxes: NudeNet detections", fill="black")
    draw.text((left, 90), "Unblended scores" if flat else f"Score overlay opacity: {opacity:.0%}", fill="black")
    ramp = np.linspace(0, 1, 320, dtype=np.float32)[None, :]
    bar = np.repeat(confidence_colors(ramp, np.ones_like(ramp, dtype=bool)), 18, axis=0)
    result.paste(Image.fromarray(bar), (left, 122))
    draw.text((left, 146), "0.0 blue     0.5 orange     >=0.9 red", fill="black")
    draw.text((left, 172), f"Preferred interior seed score: {body_floor:.2f}", fill="black")
    draw.text((left, 198), "Uncalibrated maximum SCHP class score", fill="black")
    draw.text((left, 222), "Outside silhouette: black" if flat else "Outside silhouette: original image", fill="black")
    suffix = "score_map" if flat else "score_overlay"
    result.save(out_dir / "overlays" / f"{stem}_{suffix}.png")


def save_person_overlay(source, person_mask, out_dir, stem):
    """Inspect the unchanged decoded YOLO silhouette, including every background gap."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    inside = person_mask > 0
    binary = Image.fromarray(inside.astype(np.uint8) * 255)
    tint = Image.new("RGBA", source.size, (0, 220, 190))
    tint.putalpha(binary.point(lambda value: 90 if value else 0))
    overlay = Image.alpha_composite(source.convert("RGBA"), tint).convert("RGB")
    edge = inside & ~(np.asarray(binary.filter(ImageFilter.MinFilter(3))) > 0)
    overlay.paste("white", mask=Image.fromarray(edge.astype(np.uint8) * 255))
    result = Image.new("RGB", (max(source.width * 2, 640), source.height + 56), "white")
    result.paste(overlay, (0, 0))
    result.paste(binary.convert("RGB"), (source.width, 0))
    draw = ImageDraw.Draw(result)
    draw.text((8, source.height + 8), "Raw YOLO silhouette | white = person, black = background", fill="black")
    draw.text(
        (8, source.height + 30), "No smoothing or hole filling; model-missed gaps remain unresolved", fill="black"
    )
    result.save(out_dir / "overlays" / f"{stem}_person_overlay.png")


def save_body_overlay(source, layers, class_names, colors, out_dir, stem, opacity):
    """Render every independent region; overlap never suppresses a region or legend entry."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    counts = np.zeros((source.height, source.width), dtype=np.uint16)
    summed = np.zeros((source.height, source.width, 3), dtype=np.float32)
    class_pixels = {}
    for layer in layers:
        x0, y0, x1, y1 = layer["box"]
        mask, class_id = layer["mask"], layer["class_id"]
        counts[y0:y1, x0:x1] += mask
        summed[y0:y1, x0:x1] += mask[..., None] * np.asarray(colors[class_id], dtype=np.float32)
        if mask.any():
            class_pixels[class_id] = class_pixels.get(class_id, 0) + int(mask.sum())
    present = counts > 0
    rgb = np.asarray(source, dtype=np.float32).copy()
    rgb[present] = (1 - opacity) * rgb[present] + opacity * summed[present] / counts[present, None]
    overlay = Image.fromarray(np.rint(rgb).astype(np.uint8))
    # Draw all contours after fills, even when a lower-score region is wholly
    # contained in another one. Pad so box-aligned edges are also outlined.
    for layer in layers:
        mask = layer["mask"]
        binary = Image.fromarray(np.pad(mask, 1).astype(np.uint8) * 255)
        eroded = np.asarray(binary.filter(ImageFilter.MinFilter(3)))[1:-1, 1:-1] > 0
        contour = Image.fromarray((mask & ~eroded).astype(np.uint8) * 255)
        overlay.paste(colors[layer["class_id"]], layer["box"][:2], contour)
    detected = sorted(class_pixels)
    result = Image.new("RGB", (source.width + 420, max(source.height, 130 + len(detected) * 28)), "white")
    result.paste(overlay, (0, 0))
    draw = ImageDraw.Draw(result)
    left = source.width + 14
    draw.text((left, 16), "Independent NudeNet body regions", fill="black")
    draw.text((left, 40), "Overlaps: mixed fill, separate contours", fill="black")
    draw.text((left, 64), "Area counts include overlapping detections", fill="black")
    draw.text((left, 88), f"Pixels shared by regions: {int((counts > 1).sum())}", fill="black")
    for row, class_id in enumerate(detected):
        y = 120 + row * 28
        draw.rounded_rectangle((left, y, left + 16, y + 16), radius=4, fill=colors[class_id])
        name = class_names[class_id].removeprefix("NN_").replace("_", " ").title()
        draw.text((left + 26, y), f"{name}: {class_pixels[class_id]} px", fill="black")
    result.save(out_dir / "overlays" / f"{stem}_body_overlay.png")
    return {
        "coverage_percent_of_source_counting_overlaps": {
            class_names[k]: round(100 * v / counts.size, 3) for k, v in class_pixels.items()
        },
        "overlapping_pixels": int((counts > 1).sum()),
        "maximum_regions_per_pixel": int(counts.max()),
    }


def save_boundary_overlay(source, strength, person_mask, threshold, out_dir, stem):
    """Expose the active edge barriers used for region selection."""
    import numpy as np
    from PIL import Image, ImageDraw

    edges = (person_mask > 0) & (strength >= threshold)
    overlay = source.copy()
    overlay.paste("cyan", mask=Image.fromarray(edges.astype(np.uint8) * 255))
    result = Image.new("RGB", (source.width + 380, max(source.height, 160)), "white")
    result.paste(overlay, (0, 0))
    draw = ImageDraw.Draw(result)
    left = source.width + 14
    for row, text in enumerate(
        (
            "Cyan: computed region barriers",
            "Edge map: active boundary algorithm",
            f"Edge strength threshold: {threshold:.3f}",
            "Canny uses binary edge strength (0 or 1)",
            "Heuristic edges; shadows may also appear",
        )
    ):
        draw.text((left, 16 + row * 26), text, fill="black")
    result.save(out_dir / "overlays" / f"{stem}_boundary_overlay.png")
    # Also save the actual edge map without source-image colors or legend.
    Image.fromarray(edges.astype(np.uint8) * 255).save(
        out_dir / "overlays" / f"{stem}_edge_map.png"
    )


def make_visuals(
    source,
    labels,
    out_dir: Path,
    stem: str,
    opacity: float,
    title: str,
    confidence_view,
    class_names=None,
    class_colors=None,
    overlay_only=False,
):
    """Write flat per-type outputs, with one shared stem and matching mask/legend colors."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    class_names = DISPLAY_LABELS if class_names is None else class_names
    class_colors = DISPLAY_COLORS if class_colors is None else class_colors
    # Class labels are in the source grid after optional confidence suppression.
    if not overlay_only:
        Image.fromarray(labels.astype(np.uint8)).save(out_dir / "labels" / f"{stem}_labels.png")

    color_lut = np.asarray(class_colors, dtype=np.uint8)
    colored = Image.fromarray(color_lut[labels])
    alpha = Image.fromarray(np.where(labels > 0, round(255 * opacity), 0).astype(np.uint8))
    colored.putalpha(alpha)
    overlay = Image.alpha_composite(source.convert("RGBA"), colored).convert("RGB")

    # A thin outline separates adjacent colors and improves visual inspection.
    edges = np.zeros(labels.shape, dtype=bool)
    edges[:, 1:] |= (labels[:, 1:] != labels[:, :-1]) & (labels[:, 1:] != 0)
    edges[1:, :] |= (labels[1:, :] != labels[:-1, :]) & (labels[1:, :] != 0)
    if edges.any():
        overlay.paste((255, 255, 255), mask=Image.fromarray((edges * 220).astype(np.uint8)))
    counts = np.bincount(labels.reshape(-1), minlength=len(class_names))
    if overlay_only:
        detected = [idx for idx in range(1, len(class_names)) if counts[idx] > 0]
        width, height = overlay.size
        result = Image.new("RGB", (width + 380, max(height, 80 + len(detected) * 28)), "white")
        result.paste(overlay, (0, 0))
        draw = ImageDraw.Draw(result)
        draw.text((width + 14, 16), "Class legend", fill=(20, 20, 20))
        draw.text((width + 14, 38), title, fill=(75, 75, 75))
        for row, idx in enumerate(detected):
            y = 72 + row * 28
            draw.rounded_rectangle((width + 14, y, width + 30, y + 16), radius=4, fill=class_colors[idx])
            name = class_names[idx].removeprefix("NN_").replace("_", " ").replace("-", " ").title()
            pct = 100.0 * int(counts[idx]) / labels.size
            draw.text((width + 40, y + 1), f"{name}: {pct:.1f}%", fill=(20, 20, 20))
        result.save(out_dir / "overlays" / f"{stem}_overlay.png")
        return {class_names[idx]: round(100.0 * int(counts[idx]) / labels.size, 3) for idx in range(len(class_names))}
    overlay.save(out_dir / "overlays" / f"{stem}_overlay.png")

    detected = [idx for idx in range(1, len(class_names)) if counts[idx] > 0]
    for idx in detected:
        binary = Image.fromarray((labels == idx).astype(np.uint8) * 255)
        binary.save(out_dir / "masks" / f"{stem}_mask_{idx:02d}_{class_names[idx].lower().replace('-', '_')}.png")

    w, h = source.size
    legend_width = 380
    compared = Image.new("RGB", (w * 3 + legend_width, max(h, 84 + len(class_names) * 26 + 72)), "white")
    compared.paste(source, (0, 0))
    compared.paste(overlay, (w, 0))
    heatmap, scale = confidence_view
    compared.paste(heatmap, (w * 2, 0))
    compared.paste(scale, (w * 3, 84 + len(class_names) * 26))
    draw = ImageDraw.Draw(compared)
    font = ImageFont.load_default()
    left = w * 3 + 12
    draw.text((left, 12), "SCHP ATR-18 | class legend", fill=(15, 15, 15), font=font)
    draw.text((left, 52), title, fill=(75, 75, 75), font=font)
    draw.text((left, 32), f"Overlay opacity: {opacity:.0%}", fill=(75, 75, 75), font=font)
    for idx, name in enumerate(class_names):
        y = 84 + idx * 26
        if idx == 0:
            draw.rectangle((left, y, left + 14, y + 14), outline=(110, 110, 110))
        else:
            draw.rectangle((left, y, left + 14, y + 14), fill=class_colors[idx])
        pct = 100.0 * int(counts[idx]) / labels.size
        draw.text((left + 22, y + 1), f"{idx:02d} {name}: {pct:.1f}%", fill=(20, 20, 20), font=font)
    compared.save(out_dir / "comparisons" / f"{stem}_compare.png")
    return {class_names[idx]: round(100.0 * int(counts[idx]) / labels.size, 3) for idx in range(len(class_names))}


def repair_person_silhouette(mask, box):
    """Close narrow gaps in one YOLO instance without combining separate instances."""
    import numpy as np
    from PIL import Image, ImageFilter

    x0, y0, x1, y1 = box
    result = np.zeros_like(mask, dtype=np.uint8)
    crop = np.where(mask[y0:y1, x0:x1] > 0, 255, 0).astype(np.uint8)
    if not crop.size or not crop.any():
        return result
    # A small per-instance morphological closing reconnects narrow cracks and
    # fills tiny holes. It never uses another detection's silhouette.
    image = Image.fromarray(crop)
    closed = image.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    result[y0:y1, x0:x1] = np.maximum(crop, np.asarray(closed, dtype=np.uint8))
    return result


def select_people(people, image_size, min_area, max_people, repair):
    """Filter by source-image silhouette pixels, then retain the largest instances."""
    import numpy as np

    width, height = image_size
    selected = []
    for score, mask, box in people:
        cleaned = repair_person_silhouette(mask, box) if repair else np.where(mask > 0, 255, 0).astype(np.uint8)
        area = int(np.count_nonzero(cleaned))
        fraction = area / (width * height)
        if fraction >= min_area:
            selected.append((area, score, cleaned, box))
    selected.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(score, mask, box) for _, score, mask, box in selected[:max_people]]


DEPTH_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/4472b7362082ad9968fee890ca0f1e5aca36b93d/onnx/model.onnx"
DEPTH_SHA256 = "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c"
DEPTH_BYTES = 99_060_839


def depth_session(path, *, allow_download, threads):
    """Load a pinned ONNX depth checkpoint without PyTorch or Transformers."""
    import onnxruntime as ort

    ensure_checkpoint(path, (DEPTH_URL, DEPTH_BYTES, "sha256", DEPTH_SHA256),
                      allow_download=allow_download)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


def save_depth_geometry(source, person_mask, out_dir, stem, session):
    """Export relative depth, approximate normals, and depth curvature."""
    import numpy as np
    from PIL import Image

    side = 518
    image = source.resize((side, side), Image.Resampling.BICUBIC)
    rgb = np.asarray(image, dtype=np.float32) / 255.0
    rgb = (rgb - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    tensor = np.transpose(rgb, (2, 0, 1))[None].astype(np.float32)
    raw = np.asarray(session.run(None, {session.get_inputs()[0].name: tensor})[0]).squeeze()
    if raw.ndim != 2:
        raise RuntimeError(f"Unexpected depth output shape: {raw.shape}")
    depth_image = Image.fromarray(raw.astype(np.float32), mode="F")
    depth = np.asarray(depth_image.resize(source.size, Image.Resampling.BICUBIC), dtype=np.float32)
    inside = person_mask > 0
    if not inside.any():
        return
    low, high = np.percentile(depth[inside], (2, 98))
    depth = np.clip((depth - low) / max(float(high - low), 1e-6), 0, 1)
    dy, dx = np.gradient(depth)
    normals = np.stack((-dx, -dy, np.ones_like(depth)), axis=-1)
    normals /= np.maximum(np.linalg.norm(normals, axis=-1, keepdims=True), 1e-6)
    curvature = np.abs(np.gradient(dx, axis=1) + np.gradient(dy, axis=0))
    curvature /= max(float(np.percentile(curvature[inside], 98)), 1e-6)
    for name, values in (
        ("depth", depth),
        ("normals", (normals + 1) * 0.5),
        ("curvature", np.clip(curvature, 0, 1)),
    ):
        pixels = (values * 255).astype(np.uint8)
        pixels[~inside] = 0
        Image.fromarray(pixels).save(out_dir / "overlays" / f"{stem}_{name}.png")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the smoke-test CLI with simple paths and predictable output options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--input-dir", type=Path, required=True, help="Folder containing images (non-recursive).")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("tmp/schp-atr18-smoke"),
        help="Output folder; existing folders are preserved using a numbered sibling.",
    )
    parser.add_argument(
        "-m", "--model", type=Path, default=Path("tmp/models") / MODEL_NAME, help="Cached INT8 ONNX file."
    )
    parser.add_argument("-n", "--no-download", action="store_true", help="Disable downloads for every checkpoint.")
    parser.add_argument(
        "-a", "--opacity", type=float, default=0.50, help="Overlay opacity between 0.0 and 1.0 (default 0.50)."
    )
    parser.add_argument("-t", "--threads", type=int, default=4, help="ONNX Runtime CPU inference threads (default 4).")
    parser.add_argument(
        "-c",
        "--min-confidence",
        type=float,
        default=0.0,
        help="Minimum pixel score for refined outputs only; raw outputs remain untouched.",
    )
    parser.add_argument(
        "-p",
        "--person-model",
        type=Path,
        default=Path("tmp/models/yolo26n-seg.onnx"),
        help="Person ONNX path; download the default checkpoint if missing.",
    )
    parser.add_argument(
        "-f",
        "--faces-model",
        type=Path,
        default=Path("tmp/models/320n.onnx"),
        help="Face ONNX path; download NudeNet 320n if missing.",
    )
    parser.add_argument(
        "-d", "--confidence", type=float, default=0.25, help="Person detection threshold (default 0.25)."
    )
    parser.add_argument("-j", "--max-people", type=int, default=2, help="Maximum retained people per image (default 2).")
    parser.add_argument("-u", "--min-person-area", type=float, default=0.10,
                        help="Minimum person mask fraction of full image (default 0.10).")
    parser.add_argument("-z", "--repair-person-mask", action=argparse.BooleanOptionalAction, default=True,
                        help="Close narrow per-person mask gaps (default enabled).")
    parser.add_argument(
        "-k",
        "--pose-model",
        type=Path,
        nargs="?",
        const=Path("tmp/models/yolo26n-pose.onnx"),
        help="Enable pose correction; optional ONNX path, default tmp/models/yolo26n-pose.onnx; download if missing.",
    )
    parser.add_argument(
        "-q", "--pose-kpt-confidence", type=float, default=0.3, help="Minimum joint score (default 0.3)."
    )
    parser.add_argument(
        "-w", "--pose-width", type=float, default=0.20, help="Limb-zone half-width / bone length (default 0.20)."
    )
    parser.add_argument(
        "-b",
        "--body-confidence",
        type=float,
        default=RED_CORE_CONFIDENCE,
        help="SCHP seed score for equal-area region ties (default 0.90); never excludes lower-score pixels.",
    )
    parser.add_argument("--edge-method", choices=("existing", "canny", "slic"), default="existing",
                        help="Boundary algorithm; SLIC requires scikit-image, Canny requires OpenCV (default existing).")
    parser.add_argument("--depth-geometry", action="store_true", help="Export Depth Anything V2 Small geometry diagnostics.")
    parser.add_argument("--canny-low", type=int, default=60, help="Canny lower hysteresis threshold (default 60).")
    parser.add_argument("--canny-high", type=int, default=140, help="Canny upper hysteresis threshold (default 140).")
    parser.add_argument(
        "-g",
        "--boundary",
        type=float,
        default=0.12,
        help="Image/score edge barrier strength (default 0.12); higher values keep fewer barriers.",
    )
    parser.add_argument(
        "-e",
        "--body-only",
        action="store_true",
        help="Save body, clothing, raw silhouette, score and boundary overlays with legends, plus reports; skip binary masks and comparisons.",
    )
    parser.add_argument(
        "-r",
        "--rounding",
        type=float,
        default=None,
        help="Round body-region contours by this source-pixel radius; default 3 in body-only mode, otherwise 0.",
    )
    args = parser.parse_args(argv)
    if args.rounding is None:
        args.rounding = 3.0 if args.body_only else 0.0
    if not 0.0 <= args.rounding <= 32.0:
        parser.error("--rounding must be between 0 and 32 source pixels")
    if not 0.0 <= args.body_confidence <= 1.0:
        parser.error("--body-confidence must be between 0 and 1")
    if not 0 <= args.canny_low < args.canny_high <= 255:
        parser.error("--canny-low and --canny-high must satisfy 0 <= low < high <= 255")
    if not 0.0 < args.boundary <= 1.0:
        parser.error("--boundary must be greater than 0 and at most 1")
    if not 0.0 <= args.pose_kpt_confidence <= 1.0:
        parser.error("--pose-kpt-confidence must be between 0 and 1")
    if not 0.0 < args.pose_width <= 1.0:
        parser.error("--pose-width must be greater than 0 and at most 1")
    if not 0.0 <= args.confidence <= 1.0:
        parser.error("--confidence must be between 0 and 1")
    if not 0.0 <= args.opacity <= 1.0:
        parser.error("--opacity must be between 0 and 1")
    if args.max_people < 1:
        parser.error("--max-people must be at least 1")
    if not 0.0 <= args.min_person_area <= 1.0:
        parser.error("--min-person-area must be between 0 and 1")
    if args.threads < 1:
        parser.error("--threads must be at least 1")
    if not 0.0 <= args.min_confidence <= 1.0:
        parser.error("--min-confidence must be between 0 and 1")
    if not args.input_dir.is_dir():
        parser.error(f"--input-dir is not an existing folder: {args.input_dir}")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run shared person/face detection and one padded SCHP inference per person."""
    args = parse_args(argv)

    import numpy as np
    from PIL import Image

    from dtflow.services import person_selection as selection

    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        print(f"No supported images found in {args.input_dir}", file=sys.stderr)
        return 2

    depth_estimator = (
        depth_session(Path("tmp/models/depth-anything-v2-small.onnx"),
                      allow_download=not args.no_download, threads=args.threads)
        if args.depth_geometry else None
    )

    # Reuse the production decoder, model checks, detection thresholds and NMS.
    # Existing user files retain their model contracts; missing files get role defaults.
    for role, path in (("person", args.person_model), ("faces", args.faces_model), ("pose", args.pose_model)):
        if path is not None:
            ensure_checkpoint(path, CHECKPOINTS[role], allow_download=not args.no_download)
    person_ref = selection.describe_model(args.person_model)
    faces_ref = selection.describe_model(args.faces_model)
    person_session = selection._verified_session(person_ref, selection._segmentation_session)
    faces_session = selection._verified_session(faces_ref, selection._faces_session)
    pose = pose_ref = pose_layout = None
    if args.pose_model is not None:
        from person_parts_mask_poc import pose_session

        def build_pose(data):
            return pose_session(data, args.threads)

        pose_ref = selection.describe_model(args.pose_model)
        pose, _, pose_layout = selection._verified_session(pose_ref, build_pose)
    model_hash = ensure_model(args.model, allow_download=not args.no_download)
    session = load_session(args.model, args.threads)
    output_root = create_output_dir(args.output_dir)
    print(f"CPU sessions ready; {len(files)} image(s) queued; results: {output_root}", flush=True)
    kinds = (
        ("overlays", "manifests")
        if args.body_only
        else ("labels", "confidence", "overlays", "masks", "comparisons", "manifests", "crops", "persons")
    )
    for kind in kinds:
        (output_root / kind).mkdir()
    summary_rows = []
    fail_count = 0
    for n, path in enumerate(files, start=1):
        stamp = time.perf_counter()
        stem = f"{n:04d}_{path.stem}"
        try:
            source = load_rgb(path)
            detect_start = time.perf_counter()
            detected_people = selection._instances(person_session, source, args.confidence)
            people = select_people(detected_people, source.size, args.min_person_area, args.max_people, args.repair_person_mask)
            nudenet_names, body_detections, face_boxes = {}, [], []
            if people:
                nudenet_names, body_detections, face_boxes = detect_body_parts(
                    faces_session, source, NUDENET_CONFIDENCE
                )
            pose_matches = {}
            pose_diagnostics = None
            if pose is not None and people:
                from person_parts_mask_poc import decode_pose
                from yolo_person_zone_mask_poc import letterbox

                pose_tensor, pose_geometry = letterbox(source)
                pose_output = pose.run([pose.get_outputs()[0].name], {pose.get_inputs()[0].name: pose_tensor})[0]
                pose_people, pose_diagnostics = decode_pose(
                    pose_output, pose_layout, pose_geometry, args.confidence, args.pose_kpt_confidence, 0.45
                )
                pose_matches = match_pose_people(people, pose_people)
                del pose_output, pose_tensor
            detection_seconds = time.perf_counter() - detect_start
            person_reports = []
            for index, (score, mask, box) in enumerate(people, start=1):
                person_stem = f"{stem}_p{index:02d}"
                raw, refined, confidence, details = segment_person(
                    session, source, mask, box, face_boxes, args.min_confidence
                )
                x0, y0, x1, y1 = box
                person_mask = np.zeros((source.height, source.width), dtype=np.uint8)
                person_mask[y0:y1, x0:x1] = np.where(mask[y0:y1, x0:x1] > 0, 255, 0).astype(np.uint8)
                save_score_overlay(
                    source,
                    confidence,
                    person_mask,
                    body_detections,
                    output_root,
                    person_stem,
                    args.opacity,
                    args.body_confidence,
                )
                save_score_overlay(
                    source,
                    confidence,
                    person_mask,
                    body_detections,
                    output_root,
                    person_stem,
                    args.opacity,
                    args.body_confidence,
                    flat=True,
                )
                save_person_overlay(source, person_mask, output_root, person_stem)
                if depth_estimator is not None:
                    save_depth_geometry(source, person_mask, output_root, person_stem, depth_estimator)
                details["person_cutoff"] = "YOLO instance segmentation silhouette, not detection box"
                details["person_postprocessing"] = (
                    "none: preserve decoded background gaps; missed gaps are not reconstructed"
                )
                details["score_overlay"] = f"{person_stem}_score_overlay.png"
                details["score_map"] = f"{person_stem}_score_map.png"
                confidence_view = None
                raw_coverage = refined_coverage = None
                if not args.body_only:
                    confidence_view = save_confidence(confidence, person_mask > 0, output_root, person_stem)
                    raw_coverage = make_visuals(
                        source,
                        raw,
                        output_root,
                        f"{person_stem}_raw",
                        args.opacity,
                        "Raw: person-clipped",
                        confidence_view,
                    )
                    refined_title = "Refined: face boxes" if face_boxes else "Unrefined: no face detected"
                    refined_coverage = make_visuals(
                        source,
                        refined,
                        output_root,
                        f"{person_stem}_refined",
                        args.opacity,
                        refined_title,
                        confidence_view,
                    )
                if pose is not None:
                    matched = pose_matches.get(index - 1)
                    corrected, changed, pose_details = correct_pose_limbs(
                        refined, person_mask, box, matched, args.pose_kpt_confidence, args.pose_width
                    )
                    pose_title = (
                        f"Pose: {pose_details['changed_pixels']} corrected pixels"
                        if pose_details["status"] == "applied"
                        else "Pose unavailable: unchanged"
                    )
                    if not args.body_only:
                        details["pose_class_coverages_percent_of_source"] = make_visuals(
                            source,
                            corrected,
                            output_root,
                            f"{person_stem}_pose",
                            args.opacity,
                            pose_title,
                            confidence_view,
                        )
                        Image.fromarray(changed.astype(np.uint8) * 255).save(
                            output_root / "masks" / f"{person_stem}_pose_changed.png"
                        )
                        save_pose_overlay(source, matched, args.pose_kpt_confidence, output_root, person_stem)
                    details["pose_correction"] = pose_details
                clothing = np.where(np.isin(raw, (1, 3, 4, 5, 6, 7, 8, 9, 10, 16, 17)), raw, 0).astype(np.uint8)
                details["clothing_class_coverages_percent_of_source"] = make_visuals(
                    source,
                    clothing,
                    output_root,
                    f"{person_stem}_clothes",
                    args.opacity,
                    "SCHP clothing and accessories",
                    confidence_view,
                    overlay_only=args.body_only,
                )
                body, body_names, body_colors, body_layers, boundary_strength, body_details = refine_body_parts(
                    source,
                    raw,
                    person_mask,
                    confidence,
                    body_detections,
                    nudenet_names,
                    args.body_confidence,
                    output_root,
                    person_stem,
                    rounding=args.rounding,
                    save_diagnostics=not args.body_only,
                    boundary_threshold=args.boundary,
                    edge_method=args.edge_method,
                    canny_low=args.canny_low,
                    canny_high=args.canny_high,
                )
                if not args.body_only:
                    make_visuals(
                        source,
                        body,
                        output_root,
                        f"{person_stem}_body",
                        args.opacity,
                        "Single-label projection; independent masks are authoritative",
                        confidence_view,
                        class_names=body_names,
                        class_colors=body_colors,
                    )
                details["body_overlay"] = save_body_overlay(
                    source, body_layers, body_names, body_colors, output_root, person_stem, args.opacity
                )
                save_boundary_overlay(source, boundary_strength, person_mask, args.boundary, output_root, person_stem)
                details["body_refinement"] = body_details
                if not args.body_only:
                    Image.fromarray(person_mask).save(output_root / "persons" / f"{person_stem}_person.png")
                    crop = source.crop(tuple(details["crop_box"]))
                    crop.save(output_root / "crops" / f"{person_stem}_crop.png")
                    # Save the exact fitted RGB image (before normalization) for geometry inspection.
                    left, top, right, bottom = details["schp_content_box"]
                    fitted = Image.new("RGB", (SIDE, SIDE), "white")
                    fitted.paste(crop.resize((right - left, bottom - top), Image.Resampling.BILINEAR), (left, top))
                    fitted.save(output_root / "crops" / f"{person_stem}_input.png")
                details.update(
                    {
                        "index": index,
                        "output_stem": person_stem,
                        "person_score": score,
                        "raw_class_coverages_percent_of_source": raw_coverage,
                        "refined_class_coverages_percent_of_source": refined_coverage,
                    }
                )
                person_reports.append(details)
            elapsed = time.perf_counter() - stamp
            record = {
                "image_name": path.name,
                "image_sha256": sha256_file(path),
                "width": source.width,
                "height": source.height,
                "model_url": MODEL_URL,
                "model_sha256": model_hash,
                "person_model_sha256": person_ref.sha256,
                "faces_model_sha256": faces_ref.sha256,
                "pose_model_sha256": pose_ref.sha256 if pose_ref is not None else None,
                "pose_detection": pose_diagnostics,
                "pose_minimum_match_iou": 0.25,
                "class_labels": list(DISPLAY_LABELS),
                "synthetic_skin_class_id": 18,
                "body_class_labels": list(body_names) if person_reports else list(DISPLAY_LABELS),
                "nudenet_class_names": nudenet_names,
                "body_interior_seed_score": args.body_confidence,
                "body_boundary_strength_threshold": args.boundary,
                "edge_method": args.edge_method,
                "canny_thresholds": [args.canny_low, args.canny_high] if args.edge_method == "canny" else None,
                "body_only": args.body_only,
                "rounding_radius_source_pixels": args.rounding,
                "nudenet_detection_threshold": NUDENET_CONFIDENCE,
                "raw_definition": "unfiltered SCHP classification, restored to source grid and clipped to person",
                "min_confidence_refined_only": args.min_confidence,
                "confidence_definition": "uncalibrated maximum softmax after source-crop logit restoration; zero outside person",
                "confidence_palette": {
                    "0.0": [0, 80, 255],
                    "0.5": [255, 165, 0],
                    str(RED_CORE_CONFIDENCE): [255, 0, 0],
                    "1.0": [255, 0, 0],
                    "outside_person": [0, 0, 0],
                },
                "person_detection_threshold": args.confidence,
                "max_people": args.max_people,
                "min_person_area": args.min_person_area,
                "repair_person_mask": args.repair_person_mask,
                "detected_people_before_filter": len(detected_people),
                "face_detection_threshold": NUDENET_CONFIDENCE,
                "face_nms_iou": selection.FACES_IOU,
                "face_boxes": [list(box) for box in face_boxes],
                "face_box_expansion": {"sideways": 0.5, "above": 1.0, "below": selection.FACE_BELOW},
                "preprocessing": {
                    "person_crop_margin_fraction": 0.08,
                    "resize": "aspect-preserving, centered white padding to 512x512",
                    "resampling": "bilinear",
                    "mean": MEAN,
                    "std": STD,
                },
                "detection_seconds": detection_seconds,
                "total_seconds": elapsed,
                "status": "ok" if people else "no_person_detected",
                "people": person_reports,
            }
            (output_root / "manifests" / f"{stem}_report.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
            )
            summary_rows.append(
                {"image": path.name, "status": record["status"], "people": len(people), "total_s": f"{elapsed:.3f}"}
            )
            print(f"[{n}/{len(files)}] {path.name}: {len(people)} person(s), {elapsed:.2f}s", flush=True)
        except Exception as exc:
            fail_count += 1
            summary_rows.append({"image": path.name, "status": "error", "people": "", "total_s": ""})
            (output_root / "manifests" / f"{stem}_report.json").write_text(
                json.dumps(
                    {"image_name": path.name, "status": "error", "error": f"{type(exc).__name__}: {exc}"}, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"[{n}/{len(files)}] ERROR {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    with (output_root / "manifests" / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=("image", "status", "people", "total_s"))
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"Finished: {len(files) - fail_count}/{len(files)} images; results in {output_root}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
        print(f"Fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

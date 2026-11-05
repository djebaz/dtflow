#!/usr/bin/env python3
"""CPU edge or SCHP LIP-20 score boundaries with NudeNet-seeded zones.

Automatically downloads missing pinned ONNX checkpoints. Writes flat edge views,
a zone preview with legend, an RGBA person cutout and a JSON report. Only the
largest person is processed. Zone names are detector suggestions, not anatomy.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pascal_nudenet_poc import UNKNOWN, save_preview, verified_checkpoint
from schp_atr18_smoke import CHECKPOINTS, IMAGE_SUFFIXES, detect_body_parts, load_rgb, person_crop_box, select_people

PERSON_SPECS = {
    "n": CHECKPOINTS["person"],
    "s": (
        "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s-seg.onnx",
        41_912_273,
        "sha256",
        "d205b2c489e7cf0cdb183bb23e56dc8a32a79602e8c5b1f5ecb01af0dc6822c3",
    ),
    "m": (
        "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26m-seg.onnx",
        94_615_730,
        "sha256",
        "38a6e1140fba87287d5943170975e111872a4a1c489d6209646455ac01081dac",
    ),
}

REVISION = "01a752eb4006688376e576dc24941c54608f6dd0"
EDGE_SPEC = (
    f"https://huggingface.co/opencv/edge_detection_dexined/resolve/{REVISION}/edge_detection_dexined_2024sep.onnx",
    47_235_563,
    "sha256",
    "a50d01dc8481549c7dedb9eb3e0123b810a016520df75e4669a504609982cdd0",
)
PIDINET_REVISION = "6b3f899da74e697a7d2c2e7cba10cfce057d6e55"
PIDINET_FILES = {
    "pidinet": (
        "pidinet_table5.onnx",
        144_170,
        "bca69bf456d073689303e2a56974baa2a6a1753b77425d8cac4986352093ce32",
        2_881_392,
        "e7c11c8f06033295d34c0e4e09f672728ff63544ee5a2103e211623a779c5c11",
    ),
    "pidinet-tiny": (
        "table5_pidinet_tiny.onnx",
        86_530,
        "05b1f8df5627f0a2f5a69c5adfddb353c3ad6fcc4feece089f8125f99b90244e",
        307_120,
        "fd11bdd6fa5358ba15212a3c8887ce9aed48bdbeb5cc2032f5612e7447062c9a",
    ),
}
LIP_REVISION = "7c0028fe34dcd8bd534ffa291a9086cdc856e4a9"
LIP_SPEC = (
    f"https://huggingface.co/pirocheto/schp-lip-20/resolve/{LIP_REVISION}/onnx/schp-lip-20-int8-static.onnx",
    68_314_352,
    "sha256",
    "871c9e209dc67d6be61c73dcc79cb49e638fcf5bbc35a19b4c7429eba0bfc046",
)
# Fixed by semantic class, never detection order, score rank or image contents.
# Covered/exposed classes deliberately get separate hues; unknown is not painted.
ZONE_COLORS = {
    "FEMALE_GENITALIA_COVERED": (145, 70, 255),
    "FACE_FEMALE": (0, 110, 255),
    "BUTTOCKS_EXPOSED": (255, 140, 0),
    "FEMALE_BREAST_EXPOSED": (255, 50, 50),
    "FEMALE_GENITALIA_EXPOSED": (255, 0, 180),
    "MALE_BREAST_EXPOSED": (255, 225, 0),
    "ANUS_EXPOSED": (160, 230, 0),
    "FEET_EXPOSED": (0, 220, 255),
    "BELLY_COVERED": (150, 95, 35),
    "FEET_COVERED": (0, 125, 135),
    "FACE_MALE": (160, 160, 255),
    "BELLY_EXPOSED": (0, 195, 80),
    "MALE_GENITALIA_EXPOSED": (255, 145, 200),
    "ANUS_COVERED": (135, 0, 40),
    "FEMALE_BREAST_COVERED": (0, 50, 135),
    "BUTTOCKS_COVERED": (255, 205, 145),
}


def zone_palette(reports):
    """Same class has the same RGB in every image and in both overlay modes."""
    palette = {0: (0, 0, 0), UNKNOWN: (128, 128, 128)}
    for record in reports:
        # White explicitly marks a future/unrecognized class rather than randomizing it.
        shade = ZONE_COLORS.get(record["label"].upper(), (240, 240, 240))
        palette[record["zone_id"]] = shade
        record["color_rgb"] = list(shade)
    return palette


def edge_checkpoints(detector, model):
    """External weights retain their pinned basename, as referenced by the graph."""
    if detector == "dexined":
        return [(model, EDGE_SPEC)]
    if detector == "lip-20":
        return [(model, LIP_SPEC)]
    filename, size, digest, data_size, data_digest = PIDINET_FILES[detector]
    base = f"https://huggingface.co/bdck/PiDiNet_ONNX/resolve/{PIDINET_REVISION}"
    return [
        (model, (f"{base}/{filename}", size, "sha256", digest)),
        (model.parent / f"{filename}.data", (f"{base}/{filename}.data", data_size, "sha256", data_digest)),
    ]


def load_pidinet(path, threads, detector="pidinet"):
    """CPU-only ORT session; no PyTorch, export or GPU dependency at runtime."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise RuntimeError(f"{detector} must use ONNX Runtime CPU only")
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].type != "tensor(float)" or len(inputs[0].shape) != 4:
        raise RuntimeError(f"{detector} requires one float NCHW input")
    batch, channels, height, width = inputs[0].shape
    if (isinstance(batch, int) and batch != 1) or (isinstance(channels, int) and channels != 3):
        raise RuntimeError(f"Unsupported {detector} input: {inputs[0].shape}")
    default_size = 473 if detector == "lip-20" else 512
    size = (width if isinstance(width, int) else default_size, height if isinstance(height, int) else default_size)
    if min(size) < 1 or max(size) > 2048:
        raise RuntimeError(f"Unsupported {detector} spatial dimensions: {size}")
    if detector == "lip-20" and size != (473, 473):
        raise RuntimeError(f"Expected LIP-20 473-square input, got {size}")
    outputs = session.get_outputs()
    expected_output = "logits" if detector == "lip-20" else "fused"
    if not any(item.name == expected_output for item in outputs):
        raise RuntimeError(f"{detector} does not expose its expected {expected_output} output")
    return session, inputs[0].name, size


def pidinet_edges(cv, model, crop):
    """Publisher's RGB/ImageNet normalization; fused output already has sigmoid."""
    session, input_name, size = model
    rgb = np.asarray(crop.resize(size, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    rgb = (rgb - np.asarray([0.485, 0.456, 0.406], dtype=np.float32)) / np.asarray(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32)
    output = np.asarray(session.run(["fused"], {input_name: tensor})[0])
    if output.ndim != 4 or output.shape[:2] != (1, 1) or not np.isfinite(output).all():
        raise RuntimeError(f"Invalid PiDiNet fused output: {output.shape}")
    if output.min() < -1e-5 or output.max() > 1.00001:
        raise RuntimeError("Expected PiDiNet probabilities, not logits")
    return cv.resize(np.clip(output[0, 0], 0, 1), crop.size, interpolation=cv.INTER_LINEAR)


def lip_score_boundaries(cv, model, crop, inside, resolution, sensitivity):
    """Use spatial changes in winning softmax scores, never class IDs as barriers.

    Restore logits before softmax on the bounded growth grid. Neighbor differences
    use only pairs inside the person, excluding artificial silhouette boundaries.
    """
    session, input_name, size = model
    rgb = np.asarray(crop.resize(size, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    rgb = (rgb - np.asarray([0.406, 0.456, 0.485], dtype=np.float32)) / np.asarray(
        [0.225, 0.224, 0.229], dtype=np.float32
    )
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32)
    logits = np.asarray(session.run(["logits"], {input_name: tensor})[0])
    if logits.shape != (1, 20, 473, 473) or not np.isfinite(logits).all():
        raise RuntimeError(f"Invalid LIP-20 logits: {logits.shape}")
    scale = min(1.0, resolution / max(crop.size))
    work_size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    maximum = np.full((work_size[1], work_size[0]), -np.inf, dtype=np.float32)
    denominator = np.zeros_like(maximum)
    for plane in logits[0]:
        score = cv.resize(plane, work_size, interpolation=cv.INTER_LINEAR)
        updated = np.maximum(maximum, score)
        denominator = denominator * np.exp(maximum - updated) + np.exp(score - updated)
        maximum = updated
    confidence = 1.0 / denominator
    valid = cv.resize(inside.astype(np.uint8), work_size, interpolation=cv.INTER_NEAREST) > 0
    strength = np.zeros_like(confidence)
    for a, b in ((np.s_[:, :-1], np.s_[:, 1:]), (np.s_[:-1, :], np.s_[1:, :])):
        change = np.abs(confidence[a] - confidence[b]) * (valid[a] & valid[b])
        np.maximum(strength[a], change, out=strength[a])
        np.maximum(strength[b], change, out=strength[b])
    # Normalize the derived cost, not the confidence preview; record the exact scale.
    normalizer = max(float(np.percentile(strength[valid], 98)), 1e-3) if valid.any() else 1.0
    barriers = np.clip(sensitivity * strength / normalizer, 0, 1)
    barriers[~valid] = 0
    return (
        cv.resize(barriers, crop.size, interpolation=cv.INTER_LINEAR),
        cv.resize(confidence, crop.size, interpolation=cv.INTER_LINEAR),
        {
            "signal": "absolute neighboring winning-softmax-score differences",
            "normalizer_p98": normalizer,
            "working_size": list(work_size),
            "scores_calibrated": False,
            "semantic_class_constraints": False,
        },
    )


def detect_edges(cv, net, crop):
    """Follow the pinned OpenCV Zoo artifact's BGR preprocessing and fused output."""
    bgr = np.asarray(crop, dtype=np.uint8)[:, :, ::-1].copy()
    blob = cv.dnn.blobFromImage(bgr, 1.0, (512, 512), (103.5, 116.2, 123.6), swapRB=False, crop=False)
    net.setInput(blob)
    output = np.asarray(net.forward())
    if output.ndim not in (3, 4, 5) or not np.isfinite(output).all():
        raise RuntimeError(f"Invalid DexiNed output: {output.shape}")
    # Zoo iterates the first axis and selects its final (fused) prediction.
    fused = output[-1].squeeze()
    if fused.ndim != 2:
        raise RuntimeError(f"Expected fused 2D edge logits, got {fused.shape}")
    edges = 1.0 / (1.0 + np.exp(-np.clip(fused.astype(np.float32), -80, 80)))
    # Preserve absolute strengths; per-image min/max normalization distorts growth costs.
    return cv.resize(edges, crop.size, interpolation=cv.INTER_LINEAR)


def finish_zones(cv, zones, valid, fixed, smooth, expand):
    """Simultaneous rounding, then bounded competing expansion through the silhouette.

    This is geometric finishing, not another score pass: expansion may cross a
    local score barrier within its small radius, but cannot overwrite another zone.
    Units are pixels on the existing region-growth grid, not source-image pixels.
    """
    result = zones.copy()
    if smooth > 0:
        best = np.full(zones.shape, 0.5, dtype=np.float32)
        result.fill(0)
        # Every mask comes from the same original map; no sequential morphology bias.
        for zone in np.unique(zones):
            if zone == 0:
                continue
            support = cv.GaussianBlur((zones == zone).astype(np.float32), (0, 0), smooth, borderType=cv.BORDER_CONSTANT)
            better = valid & ((support > best) | ((support == best) & (zones == zone)))
            result[better], best[better] = zone, support[better]
        result[fixed] = zones[fixed]
    if expand > 0:
        height, width = valid.shape
        empty = valid & (result == 0)
        adjacent = np.zeros_like(valid)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            ya, yb = max(0, dy), min(height, height + dy)
            xa, xb = max(0, dx), min(width, width + dx)
            adjacent[ya:yb, xa:xb] |= empty[ya - dy : yb - dy, xa - dx : xb - dx]
        distance = np.full(valid.shape, np.inf, dtype=np.float64)
        distance[result > 0] = 0
        queue = [
            (0.0, int(result[y, x]), int(y), int(x)) for y, x in zip(*np.nonzero(adjacent & (result > 0)), strict=True)
        ]
        heapq.heapify(queue)
        while queue:
            cost, zone, y, x = heapq.heappop(queue)
            if cost != distance[y, x] or result[y, x] != zone:
                continue
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                ny, nx = y + dy, x + dx
                if not (0 <= ny < height and 0 <= nx < width) or not empty[ny, nx]:
                    continue
                if dy and dx and (not valid[y, nx] or not valid[ny, x]):
                    continue
                candidate = cost + (math.sqrt(2) if dy and dx else 1.0)
                if candidate <= expand and candidate < distance[ny, nx]:
                    distance[ny, nx], result[ny, nx] = candidate, zone
                    heapq.heappush(queue, (candidate, zone, ny, nx))
    result[~valid] = 0
    return result


def grow_zones(cv, edges, inside, detections, resolution, edge_weight, growth, smooth, expand, balance=0):
    """Compete from small detector seeds using edge-weighted path lengths only.

    No semantic classes or rectangular growth barriers. A per-seed path budget,
    proportional to detection size, prevents unlimited flooding across weak edges.
    """
    height, width = inside.shape
    scale = min(1.0, resolution / max(height, width))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    field = cv.resize(edges, size, interpolation=cv.INTER_AREA)
    valid = cv.resize(inside.astype(np.uint8), size, interpolation=cv.INTER_NEAREST) > 0
    h, w = valid.shape
    sx, sy = w / width, h / height
    zones = np.zeros((h, w), dtype=np.uint16)
    distance = np.full((h, w), np.inf, dtype=np.float64)
    fixed = np.zeros_like(valid)
    budgets, reports, queue = {}, [], []
    centers = {}
    yy, xx = np.ogrid[:h, :w]
    for index, detection in enumerate(detections, 7):
        x0, y0, x1, y1 = detection["box_xyxy"]
        cx, cy = (x0 + x1) * sx / 2, (y0 + y1) * sy / 2
        rx, ry = max(1.0, (x1 - x0) * sx * 0.2), max(1.0, (y1 - y0) * sy * 0.2)
        center_distance = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        core = valid & ~fixed & (center_distance <= 1)
        record = dict(detection, zone_id=index, status="rejected_no_person_core")
        reports.append(record)
        if not core.any():
            continue
        # Balanced mode anchors to the nearest valid geometric-center pixel;
        # a weak edge must not shift the seed to one side of the detector box.
        ranking = np.where(
            core, (xx - cx) ** 2 + (yy - cy) ** 2 if balance > 0 else center_distance + 2 * field, np.inf
        )
        y, x = np.unravel_index(int(np.argmin(ranking)), ranking.shape)
        zones[y, x], distance[y, x], fixed[y, x] = index, 0.0, True
        radius = max(1.0, math.hypot((x1 - x0) * sx, (y1 - y0) * sy) / 2)
        budgets[index] = max(4.0, growth * radius)
        centers[index] = (cx, cy, radius * radius)
        record.update(status="seeded", seed_xy=[round(float(x / sx), 2), round(float(y / sy), 2)])
        heapq.heappush(queue, (0.0, index, int(y), int(x)))
    while queue:
        cost, zone, y, x = heapq.heappop(queue)
        if cost != distance[y, x] or zones[y, x] != zone:
            continue
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            ny, nx = y + dy, x + dx
            if not (0 <= ny < h and 0 <= nx < w) or not valid[ny, nx] or fixed[ny, nx]:
                continue
            strength = max(float(field[y, x]), float(field[ny, nx]))
            diagonal = bool(dy and dx)
            if diagonal:
                # Do not cut a silhouette corner or tunnel between edge-barrier pixels.
                if not valid[y, nx] or not valid[ny, x] or fixed[y, nx] or fixed[ny, x]:
                    continue
                strength = max(strength, float(field[y, nx]), float(field[ny, x]))
            step = math.sqrt(2) if diagonal else 1.0
            spatial_cost = 0.0
            if balance > 0:
                cx, cy, radius_squared = centers[zone]
                # Isotropic soft prior, not a box clip or forced mirroring. It
                # discourages long off-center excursions while preserving edges.
                radial_squared = ((nx - cx) ** 2 + (ny - cy) ** 2 + (x - cx) ** 2 + (y - cy) ** 2) / 2
                spatial_cost = balance * radial_squared / radius_squared
            candidate = cost + step * (1.0 + edge_weight * strength * strength + spatial_cost)
            if candidate <= budgets[zone] and candidate < distance[ny, nx]:
                distance[ny, nx], zones[ny, nx] = candidate, zone
                heapq.heappush(queue, (candidate, zone, ny, nx))
    zones = finish_zones(cv, zones, valid, fixed, smooth, expand)
    result = cv.resize(zones, (width, height), interpolation=cv.INTER_NEAREST)
    result[~inside] = 0
    result[inside & (result == 0)] = UNKNOWN
    for record in reports:
        record["pixels"] = int(np.count_nonzero(result == record["zone_id"]))
        if record["status"] == "seeded":
            record["status"] = "assigned" if record["pixels"] else "empty_after_restoration"
    return result, reports


def grow_independent_zones(cv, edges, inside, detections, resolution, edge_weight, growth, smooth, expand, balance=0):
    """Reuse single-zone growth independently; retain masks on the bounded grid.

    No other zone's seed, boundary or ownership can block a zone in this mode.
    Full-size masks are used one at a time for counts, never retained as a stack.
    """
    height, width = inside.shape
    scale = min(1.0, resolution / max(height, width))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    field = cv.resize(edges, size, interpolation=cv.INTER_AREA)
    valid = cv.resize(inside.astype(np.uint8), size, interpolation=cv.INTER_NEAREST) > 0
    sx, sy = size[0] / width, size[1] / height
    layers, reports = [], []
    coverage = np.zeros(valid.shape, dtype=np.uint16)
    for zone_id, detection in enumerate(detections, 7):
        x0, y0, x1, y1 = detection["box_xyxy"]
        scaled = dict(detection, box_xyxy=[x0 * sx, y0 * sy, x1 * sx, y1 * sy])
        single, details = grow_zones(
            cv, field, valid, [scaled], resolution, edge_weight, growth, smooth, expand, balance
        )
        mask = single == 7
        record = dict(details[0], box_xyxy=list(detection["box_xyxy"]), zone_id=zone_id)
        if "seed_xy" in record:
            record["seed_xy"] = [round(record["seed_xy"][0] / sx, 2), round(record["seed_xy"][1] / sy, 2)]
        restored = cv.resize(mask.astype(np.uint8), (width, height), interpolation=cv.INTER_NEAREST) > 0
        record["pixels"] = int(np.count_nonzero(restored & inside))
        if record["pixels"]:
            layers.append((zone_id, mask))
            coverage += mask.astype(np.uint16)
        elif record["status"] == "assigned":
            record["status"] = "empty_after_restoration"
        reports.append(record)
    restored_coverage = cv.resize(coverage, (width, height), interpolation=cv.INTER_NEAREST)
    summary = {
        "unknown_pixels": int(np.count_nonzero(inside & (restored_coverage == 0))),
        "overlap_pixels": int(np.count_nonzero(inside & (restored_coverage > 1))),
    }
    return layers, reports, summary


def contract_zones(cv, edges, inside, detections, resolution, edge_weight, smooth, expand, box_scale, roundness):
    """Experimental rounded-box shrink-wrap, with one coherent radial contour.

    Search from the enlarged box perimeter toward its central 35% core. Edge
    attraction competes with an outer-boundary preference and angular smoothness;
    this is not independent per-pixel inward flooding or anatomical parsing.
    """
    height, width = inside.shape
    scale = min(1.0, resolution / max(height, width))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    field = cv.resize(edges, size, interpolation=cv.INTER_AREA)
    valid = cv.resize(inside.astype(np.uint8), size, interpolation=cv.INTER_NEAREST) > 0
    sx, sy = size[0] / width, size[1] / height
    yy, xx = np.ogrid[: size[1], : size[0]]
    angles, samples = 128, 64
    theta = np.arange(angles, dtype=np.float32) * (2 * np.pi / angles)
    directions = np.stack((np.cos(theta), np.sin(theta)), axis=1)
    fractions = np.linspace(1.0, 0.35, samples, dtype=np.float32)
    # Three circuits permit angular continuity across the seam; retain the middle.
    transition = (12.0 + 4.0 * smooth) * (fractions[:, None] - fractions[None, :]) ** 2
    layers, reports = [], []
    coverage = np.zeros(valid.shape, dtype=np.uint16)
    for zone_id, detection in enumerate(detections, 7):
        x0, y0, x1, y1 = detection["box_xyxy"]
        cx, cy = (x0 + x1) * sx / 2, (y0 + y1) * sy / 2
        half = np.array(
            [max(1.0, (x1 - x0) * sx * box_scale / 2), max(1.0, (y1 - y0) * sy * box_scale / 2)], dtype=np.float32
        )
        record = dict(detection, zone_id=zone_id, status="rejected_no_person_core", start="edge")
        reports.append(record)
        core = valid & (((xx - cx) / max(half[0] * 0.35, 1)) ** 2 + ((yy - cy) / max(half[1] * 0.35, 1)) ** 2 <= 1)
        if not core.any():
            continue
        # Radial intersection with a signed-distance rounded rectangle.
        radius = float(2 * min(half) * roundness)
        lower = np.zeros(angles, dtype=np.float32)
        upper = np.full(angles, float(np.linalg.norm(half)), dtype=np.float32)
        for _ in range(20):
            middle = (lower + upper) / 2
            q = np.abs(directions * middle[:, None]) - (half - radius)
            sdf = np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(np.maximum(q[:, 0], q[:, 1]), 0) - radius
            lower = np.where(sdf <= 0, middle, lower)
            upper = np.where(sdf > 0, middle, upper)
        radii = lower[:, None] * fractions[None, :]
        map_x = (cx + directions[:, 0, None] * radii).astype(np.float32)
        map_y = (cy + directions[:, 1, None] * radii).astype(np.float32)
        responses = cv.remap(field, map_x, map_y, cv.INTER_LINEAR, borderMode=cv.BORDER_CONSTANT)
        supported = cv.remap(valid.astype(np.uint8), map_x, map_y, cv.INTER_NEAREST, borderMode=cv.BORDER_CONSTANT) > 0
        # Prefer an enclosing outer edge over equally strong eyes/mouth edges.
        unary = 2.0 * (1 - fractions)[None, :] - edge_weight * responses
        unary += np.where(supported, 0.0, 4.0)
        cost = np.zeros(samples, dtype=np.float32)
        parents = np.empty((angles * 3, samples), dtype=np.int16)
        for step in range(angles * 3):
            choices = cost[:, None] + transition
            previous = choices.argmin(axis=0)
            parents[step] = previous
            cost = unary[step % angles] + choices[previous, np.arange(samples)]
            cost -= cost.min()
        path = np.empty(angles * 3, dtype=np.int16)
        state = int(cost.argmin())
        for step in range(angles * 3 - 1, -1, -1):
            path[step] = state
            state = int(parents[step, state])
        selected = fractions[path[angles : 2 * angles]]
        # Smooth the cyclic radial profile, not independent contour fragments.
        for _ in range(3):
            selected = (np.roll(selected, 1) + 2 * selected + np.roll(selected, -1)) / 4
        final_radii = lower * selected
        points = np.rint(np.array([cx, cy]) + directions * final_radii[:, None]).astype(np.int32)
        mask = np.zeros(valid.shape, dtype=np.uint8)
        cv.fillPoly(mask, [points], 1)
        mask = (mask > 0) & valid
        fixed = np.zeros_like(valid)
        center_rank = np.where(core, (xx - cx) ** 2 + (yy - cy) ** 2, np.inf)
        seed_y, seed_x = np.unravel_index(int(center_rank.argmin()), valid.shape)
        fixed[seed_y, seed_x] = True
        mask[seed_y, seed_x] = True
        finished = finish_zones(cv, mask.astype(np.uint16) * zone_id, valid, fixed, smooth, expand) == zone_id
        restored = cv.resize(finished.astype(np.uint8), (width, height), interpolation=cv.INTER_NEAREST) > 0
        pixels = int(np.count_nonzero(restored & inside))
        selected_response = cv.remap(
            field,
            (cx + directions[:, 0] * final_radii).astype(np.float32)[None],
            (cy + directions[:, 1] * final_radii).astype(np.float32)[None],
            cv.INTER_LINEAR,
            borderMode=cv.BORDER_CONSTANT,
        )
        record.update(
            status="assigned" if pixels else "empty_after_restoration",
            pixels=pixels,
            box_scale=box_scale,
            roundness=roundness,
            mean_contour_response=float(selected_response.mean()),
            mean_retained_radius=float(selected.mean()),
        )
        if pixels:
            layers.append((zone_id, finished))
            coverage += finished.astype(np.uint16)
    for record in reports:
        record.setdefault("pixels", 0)
    restored = cv.resize(coverage, (width, height), interpolation=cv.INTER_NEAREST)
    return (
        layers,
        reports,
        {
            "unknown_pixels": int(np.count_nonzero(inside & (restored == 0))),
            "overlap_pixels": int(np.count_nonzero(inside & (restored > 1))),
        },
    )


def fit_box_zones(cv, edges, inside, detections, resolution, edge_weight, smooth, expand):
    """Auto-budget each zone against a rounded width/height-normalized box.

    The target box is a soft size prior, not a filled rectangle. Its boundary
    calibrates the edge-weighted budget; a 35% normalized outer search margin
    bounds excursions. Finishing still operates separately on the inferred mask.
    """
    height, width = inside.shape
    scale = min(1.0, resolution / max(height, width))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    field = cv.resize(edges, size, interpolation=cv.INTER_AREA)
    valid = cv.resize(inside.astype(np.uint8), size, interpolation=cv.INTER_NEAREST) > 0
    h, w = valid.shape
    sx, sy = w / width, h / height
    yy, xx = np.ogrid[:h, :w]
    layers, reports = [], []
    coverage = np.zeros(valid.shape, dtype=np.uint16)
    neighbors = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    for zone_id, detection in enumerate(detections, 7):
        x0, y0, x1, y1 = detection["box_xyxy"]
        cx, cy = (x0 + x1) * sx / 2, (y0 + y1) * sy / 2
        rx, ry = max(1.0, (x1 - x0) * sx / 2), max(1.0, (y1 - y0) * sy / 2)
        nx, ny = (xx - cx) / rx, (yy - cy) / ry
        qx, qy = np.abs(nx) - 0.7, np.abs(ny) - 0.7
        envelope = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - 0.3
        domain = valid & (envelope <= 0.35)
        core = domain & (nx * nx + ny * ny <= 0.3**2)
        record = dict(detection, zone_id=zone_id, status="rejected_no_person_core", start="box", pixels=0)
        reports.append(record)
        if not core.any():
            continue
        seed_y, seed_x = np.unravel_index(int(np.where(core, nx * nx + ny * ny, np.inf).argmin()), valid.shape)
        distance = np.full(valid.shape, np.inf, dtype=np.float64)
        distance[seed_y, seed_x] = 0
        queue = [(0.0, int(seed_y), int(seed_x))]
        while queue:
            cost, y, x = heapq.heappop(queue)
            if cost != distance[y, x]:
                continue
            for dy, dx in neighbors:
                py, px = y + dy, x + dx
                if not (0 <= py < h and 0 <= px < w) or not domain[py, px]:
                    continue
                strength = max(float(field[y, x]), float(field[py, px]))
                if dy and dx:
                    if not domain[y, px] or not domain[py, x]:
                        continue
                    strength = max(strength, float(field[y, px]), float(field[py, x]))
                step = math.hypot(dx / rx, dy / ry)
                outside = max(0.0, float(envelope[py, px]))
                candidate = cost + step * (1.0 + edge_weight * strength * strength + 32 * outside * outside)
                if candidate < distance[py, px]:
                    distance[py, px] = candidate
                    heapq.heappush(queue, (candidate, py, px))
        reached = np.isfinite(distance)
        # The band is at least one grid pixel wide, including small detections.
        band = min(0.25, max(0.06, 1.0 / min(rx, ry)))
        boundary = domain & reached & (np.abs(envelope) <= band)
        if np.count_nonzero(boundary) < 4:
            record["status"] = "rejected_insufficient_reachable_box_boundary"
            continue
        budget = float(np.percentile(distance[boundary], 50))
        mask = reached & (distance <= budget) & domain
        fixed = np.zeros_like(valid)
        fixed[seed_y, seed_x] = True
        labels = finish_zones(cv, mask.astype(np.uint16) * zone_id, valid, fixed, smooth, expand)
        mask = labels == zone_id
        restored = cv.resize(mask.astype(np.uint8), (width, height), interpolation=cv.INTER_NEAREST) > 0
        pixels = int(np.count_nonzero(restored & inside))
        record.update(
            status="assigned" if pixels else "empty_after_restoration",
            pixels=pixels,
            seed_xy=[round(float(seed_x / sx), 2), round(float(seed_y / sy), 2)],
            auto_budget=budget,
            boundary_samples=int(np.count_nonzero(boundary)),
            normalized_search_margin=0.35,
            fit_metric="separate box half-width / half-height",
        )
        if pixels:
            layers.append((zone_id, mask))
            coverage += mask.astype(np.uint16)
    restored = cv.resize(coverage, (width, height), interpolation=cv.INTER_NEAREST)
    return (
        layers,
        reports,
        {
            "unknown_pixels": int(np.count_nonzero(inside & (restored == 0))),
            "overlap_pixels": int(np.count_nonzero(inside & (restored > 1))),
        },
    )


def exclusive_contours(cv, layers, reports, inside):
    """Resolve overlapping inward contours by normalized distance to box centers."""
    height, width = inside.shape
    shape = layers[0][1].shape if layers else (1, 1)
    labels = np.zeros(shape, dtype=np.uint16)
    best = np.full(shape, np.inf, dtype=np.float32)
    yy, xx = np.ogrid[: shape[0], : shape[1]]
    by_id = {record["zone_id"]: record for record in reports}
    for zone_id, mask in layers:
        x0, y0, x1, y1 = by_id[zone_id]["box_xyxy"]
        cx, cy = (x0 + x1) * shape[1] / width / 2, (y0 + y1) * shape[0] / height / 2
        rx, ry = max(1, (x1 - x0) * shape[1] / width / 2), max(1, (y1 - y0) * shape[0] / height / 2)
        distance = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        take = mask & (distance < best)
        labels[take], best[take] = zone_id, distance[take]
    result = cv.resize(labels, (width, height), interpolation=cv.INTER_NEAREST)
    result[~inside] = 0
    result[inside & (result == 0)] = UNKNOWN
    for record in reports:
        record["pixels"] = int(np.count_nonzero(result == record["zone_id"]))
        if record["status"] == "assigned" and not record["pixels"]:
            record["status"] = "empty_after_exclusive_resolution"
    return result


def save_overlap_preview(cv, source, box, inside, layers, reports, names, destination, finishing, palette, blur=0):
    """Average fills without losing mask ownership; outline every independent zone."""
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    grid_shape = layers[0][1].shape if layers else (1, 1)
    coverage = np.zeros(grid_shape, dtype=np.float32)
    colors = np.zeros((*grid_shape, 3), dtype=np.float32)
    shades = {}
    for zone_id, mask in layers:
        shade = palette[zone_id]
        shades[zone_id] = shade
        colors[mask] += shade
        coverage[mask] += 1
    # Render soft coverage, not enlarged nearest-neighbor label stair-steps.
    # These temporary display buffers never feed back into masks or area counts.
    colors = cv.GaussianBlur(colors, (0, 0), 0.45, borderType=cv.BORDER_REPLICATE)
    coverage = cv.GaussianBlur(coverage, (0, 0), 0.45, borderType=cv.BORDER_REPLICATE)
    restored_colors = cv.resize(colors, (width, height), interpolation=cv.INTER_LINEAR)
    restored_coverage = cv.resize(coverage, (width, height), interpolation=cv.INTER_LINEAR)
    if blur > 0:
        # Feather only the premultiplied zone layer, never the photograph.
        restored_colors = cv.GaussianBlur(restored_colors, (0, 0), blur, borderType=cv.BORDER_REPLICATE)
        restored_coverage = cv.GaussianBlur(restored_coverage, (0, 0), blur, borderType=cv.BORDER_REPLICATE)
    restored_colors /= np.maximum(restored_coverage, 1e-6)[..., None]
    zone_alpha = 0.55 * np.clip(restored_coverage, 0, 1)[..., None]
    rgb = np.asarray(source).copy()
    region = rgb[y0:y1, x0:x1]
    region[inside] = (region[inside] * (1 - zone_alpha[inside]) + restored_colors[inside] * zone_alpha[inside]).astype(
        np.uint8
    )
    # Trace the small masks before enlargement, using subpixel AA strokes instead
    # of tracing the stair-steps of an already nearest-neighbor-upscaled mask.
    coordinate_scale = np.array([width / grid_shape[1], height / grid_shape[0]], dtype=np.float32)
    stroke = np.zeros_like(region) if blur > 0 else region
    stroke_alpha = np.zeros((height, width), dtype=np.uint8) if blur > 0 else None
    for zone_id, mask in layers:
        contours, _ = cv.findContours(mask.astype(np.uint8), cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        display_contours = []
        for contour in contours:
            simplified = cv.approxPolyDP(contour, 0.35, True).astype(np.float32)
            points = (simplified + 0.5) * coordinate_scale - 0.5
            display_contours.append(np.rint(points * 256).astype(np.int32))
        cv.polylines(stroke, display_contours, True, shades[zone_id], 2, lineType=cv.LINE_AA, shift=8)
        if stroke_alpha is not None:
            cv.polylines(stroke_alpha, display_contours, True, 255, 2, lineType=cv.LINE_AA, shift=8)
    if blur > 0:
        blurred_stroke = cv.GaussianBlur(stroke, (0, 0), blur, borderType=cv.BORDER_CONSTANT).astype(np.float32)
        opacity = cv.GaussianBlur(stroke_alpha, (0, 0), blur, borderType=cv.BORDER_CONSTANT).astype(np.float32) / 255.0
        region[:] = np.clip(region * (1 - opacity[..., None]) + blurred_stroke, 0, 255).astype(np.uint8)
    # Stroke thickness can protrude beyond the mask; restore all non-person pixels.
    original = np.asarray(source.crop(box))
    region[~inside] = original[~inside]
    font = ImageFont.load_default(size=18)
    person_pixels = max(1, int(inside.sum()))
    rows = [
        (
            f"{names[r['zone_id']].replace('_', ' ').title()} (zone {r['zone_id']}) - "
            f"{100 * r['pixels'] / person_pixels:.1f}%",
            shades[r["zone_id"]],
        )
        for r in reports
        if r["pixels"]
    ]
    notes = [
        "Mixed fills = overlapping zones",
        "Outlines = individual zones",
        "Coverage totals may exceed 100%",
        "Unassigned areas: original image",
        "NudeNet names are suggestions",
    ]
    if finishing:
        notes.append("Each zone rounded / expanded")
    if blur > 0:
        notes.append(f"Display feather: {blur:g} source px")
    panel_width = max(380, max(font.getbbox(text)[2] for text in [*(text for text, _ in rows), *notes]) + 80)
    preview = Image.new(
        "RGB", (source.width + panel_width, max(source.height, 100 + 36 * (len(rows) + len(notes)))), (24, 27, 34)
    )
    preview.paste(Image.fromarray(rgb), (0, 0))
    draw = ImageDraw.Draw(preview)
    left, top = source.width + 20, 20
    draw.text((left, top), "Overlapping edge-guided zones", fill="white", font=font)
    top += 44
    for text, shade in rows:
        draw.rectangle((left, top, left + 24, top + 24), fill=shade, outline="white")
        draw.text((left + 40, top), text, fill="white", font=font)
        top += 36
    top += 16
    for text in notes:
        draw.text((left, top), text, fill=(175, 180, 190), font=font)
        top += 36
    if destination is not None:
        preview.save(destination, quality=95, subsampling=0)
    return preview


def save_score_preview(cv, source, box, inside, scores, destination):
    """Show unnormalized model scores with a fixed 0..1 scale, not accuracy claims."""
    x0, y0, x1, y1 = box
    color = cv.applyColorMap(np.rint(np.clip(scores, 0, 1) * 255).astype(np.uint8), cv.COLORMAP_TURBO)
    color = color[:, :, ::-1]
    rgb = np.asarray(source).copy()
    region = rgb[y0:y1, x0:x1]
    region[inside] = (region[inside] * 0.35 + color[inside] * 0.65).astype(np.uint8)
    preview = Image.new("RGB", (source.width + 380, max(source.height, 320)), (24, 27, 34))
    preview.paste(Image.fromarray(rgb), (0, 0))
    draw, font = ImageDraw.Draw(preview), ImageFont.load_default(size=18)
    draw.text((source.width + 20, 20), "LIP-20 winning class score", fill="white", font=font)
    draw.text((source.width + 20, 54), "Uncalibrated; not an accuracy map", fill="white", font=font)
    for index, value in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
        bgr = cv.applyColorMap(np.array([[round(value * 255)]], dtype=np.uint8), cv.COLORMAP_TURBO)[0, 0]
        y = 100 + index * 36
        draw.rectangle((source.width + 20, y, source.width + 44, y + 24), fill=tuple(int(v) for v in bgr[::-1]))
        draw.text((source.width + 60, y), f"{value:.2f}", fill="white", font=font)
    preview.save(destination, quality=95, subsampling=0)


def render_zone_preview(source, box, inside, zones, reports, cv, overlap_layers, finishing, blur, destination=None):
    """Shared zone renderer; comparison panels stay in memory, with no intermediate files."""
    names = {0: "background", UNKNOWN: "unknown"}
    names.update({r["zone_id"]: r["label"].lower() for r in reports})
    palette = zone_palette(reports)
    if overlap_layers is not None:
        preview = save_overlap_preview(
            cv,
            source,
            box,
            inside,
            overlap_layers,
            reports,
            names,
            destination,
            finishing,
            palette,
            blur=blur,
        )
    else:
        preview = save_preview(
            source,
            box,
            inside,
            zones,
            names,
            destination,
            "Edge-guided zones",
            (
                "NudeNet names are suggestions",
                "Rounded / expanded after growth" if finishing else "Boundaries follow edge costs",
                "Unassigned areas: original image",
            ),
            palette=palette,
            antialias=True,
            contour_blur=blur,
            hide_unknown=True,
        )
    return preview, {str(key): name for key, name in names.items()}


def save_outputs(
    source,
    box,
    inside,
    edges,
    zones,
    reports,
    output,
    prefix,
    detector,
    finishing=False,
    overlap_layers=None,
    cv=None,
    overlay_only=False,
    blur=0,
):
    x0, y0, x1, y1 = box
    _, names = render_zone_preview(
        source,
        box,
        inside,
        zones,
        reports,
        cv,
        overlap_layers,
        finishing,
        blur,
        output / f"{prefix}_zones_overlay.jpg",
    )
    if overlay_only:
        return names
    alpha = np.zeros((source.height, source.width), dtype=np.uint8)
    alpha[y0:y1, x0:x1] = inside.astype(np.uint8) * 255
    cutout = source.convert("RGBA")
    cutout.putalpha(Image.fromarray(alpha))
    cutout.save(output / f"{prefix}_person_cutout.png")
    full_edges = np.zeros((source.height, source.width), dtype=np.float32)
    full_edges[y0:y1, x0:x1] = np.where(inside, edges, 0)
    Image.fromarray(np.rint(full_edges * 255).astype(np.uint8)).save(output / f"{prefix}_edges.png")
    rgb = np.asarray(source, dtype=np.float32).copy()
    opacity = (full_edges * 0.85)[..., None]
    rgb = (rgb * (1 - opacity) + np.array([255, 64, 32]) * opacity).astype(np.uint8)
    legend = (
        "LIP-20 score changes" if detector == "lip-20" else f"{detector} edges",
        "Red: stronger score variation" if detector == "lip-20" else "Red: stronger edge response",
        "Not an anatomical prediction",
    )
    font = ImageFont.load_default(size=18)
    panel_width = max(330, max(font.getbbox(text)[2] for text in legend) + 40)
    preview = Image.new("RGB", (source.width + panel_width, max(source.height, 140)), (24, 27, 34))
    preview.paste(Image.fromarray(rgb), (0, 0))
    draw = ImageDraw.Draw(preview)
    for index, text in enumerate(legend):
        draw.text((source.width + 20, 20 + index * 34), text, fill="white", font=font)
    preview.save(output / f"{prefix}_edges_overlay.jpg", quality=95, subsampling=0)
    return names


def output_directory(args, files, parser):
    """Preflight nonrecursive cleanup, including recognized previous Pascal outputs."""
    if args.output_dir.is_symlink() or args.output_dir.is_junction():
        parser.error("output-dir must not be a link/junction")
    output = args.output_dir.resolve()
    if args.input_dir.resolve().is_relative_to(output):
        parser.error("output-dir must not contain input-dir")
    output.mkdir(parents=True, exist_ok=True)
    previous = list(output.iterdir())
    if previous and not args.overwrite:
        parser.error("output-dir is not empty; use -w / --overwrite to remove previous POC outputs")
    generated = re.compile(
        r"\d{4,}_.+_(?:compare\.jpg|scores_overlay\.jpg|edges\.png|edges_overlay\.jpg|pascal_overlay\.jpg|zones_overlay\.jpg|"
        r"person_cutout\.png|report\.json|person\.png|confidence\.png|"
        r"(?:pascal|zones)_labels\.png|(?:pascal|zones)_\d+_[a-z_]+\.png)"
    )
    protected = {p.resolve() for p in (*files, args.model, args.person_model, args.nudenet_model)}
    protected.update(path.resolve() for path, _ in edge_checkpoints(args.detector, args.model))
    for entry in previous:
        if (
            entry.is_symlink()
            or entry.is_junction()
            or not entry.is_file()
            or entry.resolve().parent != output
            or entry.resolve() in protected
            or not generated.fullmatch(entry.name)
        ):
            parser.error(f"Refusing cleanup of unrelated/redirected entry: {entry.name}")
    return output, previous


def compute_zones(cv, edges, inside, detections, args):
    """One zoning path shared by normal processing and parameter sweeps."""
    layers = None
    if args.start in ("edge", "box"):
        if args.start == "box":
            layers, details, summary = fit_box_zones(
                cv,
                edges,
                inside,
                detections,
                args.resolution,
                args.edge_weight,
                args.smooth,
                args.expand,
            )
        else:
            layers, details, summary = contract_zones(
                cv,
                edges,
                inside,
                detections,
                args.resolution,
                args.edge_weight,
                args.smooth,
                args.expand,
                args.box_scale,
                args.roundness,
            )
        zones = None
        if not args.overlap:
            zones = exclusive_contours(cv, layers, details, inside)
            layers = None
            summary = {"unknown_pixels": int(np.count_nonzero(zones == UNKNOWN)), "overlap_pixels": 0}
    elif args.overlap:
        layers, details, summary = grow_independent_zones(
            cv,
            edges,
            inside,
            detections,
            args.resolution,
            args.edge_weight,
            args.growth,
            args.smooth,
            args.expand,
            args.balance,
        )
        zones = None
    else:
        zones, details = grow_zones(
            cv,
            edges,
            inside,
            detections,
            args.resolution,
            args.edge_weight,
            args.growth,
            args.smooth,
            args.expand,
            args.balance,
        )
        summary = {"unknown_pixels": int(np.count_nonzero(zones == UNKNOWN)), "overlap_pixels": 0}
    return zones, layers, details, summary


def sweep_cases(args):
    """Broad one-factor comparison or a small grid around owner-selected winners."""
    if args.sweep == "growth":
        cases = []
        for balance in (1.0, 2.0):
            for index in range(10):
                trial = argparse.Namespace(**vars(args))
                trial.start = "center"
                trial.growth = round(1.0 + 0.8 * index / 9, 6)
                trial.balance = balance
                cases.append((trial, "growth_balance"))
        return cases
    if args.sweep == "refined":
        cases = []
        for smooth in (8.0, 10.0, 12.0):
            for blur in (3.0, 3.75, 4.5):
                trial = argparse.Namespace(**vars(args))
                trial.start, trial.box_scale, trial.expand = "edge", 1.6, 0.0
                trial.smooth, trial.blur = smooth, blur
                cases.append((trial, "refined"))
        return cases
    alternatives = {
        "smooth": (args.smooth / 2, min(12, args.smooth * 1.5)) if args.smooth else (1, 3),
        "expand": (max(0, args.expand - 2), min(32, args.expand + 2)) if args.expand else (2, 5),
        "blur": (args.blur / 2, min(32, args.blur * 1.5)) if args.blur else (2, 6),
        "balance": (args.balance / 2, min(4, args.balance * 1.5)) if args.balance else (0.5, 1.5),
        "growth": (args.growth * 0.75, args.growth * 1.25),
        "box_scale": (max(1, args.box_scale - 0.2), min(3, args.box_scale + 0.2)),
    }
    seen, cases = set(), []
    for start in (args.start, "center" if args.start == "edge" else "edge"):
        active = (
            ("smooth", "expand", "blur", "balance", "growth")
            if start == "center"
            else ("smooth", "expand", "blur", "box_scale")
        )
        changes = [("baseline", None)] + [(name, value) for name in active for value in alternatives[name]]
        for name, value in changes:
            if value is not None and not math.isfinite(value):
                continue
            trial = argparse.Namespace(**vars(args))
            trial.start = start
            if value is not None:
                setattr(trial, name, round(value, 4))
                if name == "growth" and trial.growth <= 0:
                    continue
            identity = (start, trial.smooth, trial.expand, trial.blur, trial.balance, trial.growth, trial.box_scale)
            if identity not in seen:
                seen.add(identity)
                cases.append((trial, name))
    return cases


def save_sweep(cv, args, source, box, inside, edges, scores, detections, base_report, output, index, analysis_seconds):
    """Reuse the first image's inference; write unique flat files for each setting."""
    cases = sweep_cases(args)
    descriptions = {
        "broad": "one-setting variants across both start modes",
        "refined": "refined u=8/10/12 x c=3/3.75/4.5; edge start, k=1.6, x=0",
        "growth": "10 g values from 1.0 through 1.8, each with z=1 and z=2; center start",
    }
    description = descriptions[args.sweep]
    print(f"Sweep: first sorted input only, {len(cases)} variants; {description}", flush=True)
    failures = 0
    flags = (("u", "smooth"), ("x", "expand"), ("c", "blur"), ("z", "balance"), ("g", "growth"), ("k", "box_scale"))
    settings_names = {"blur": "display_blur_source_pixels"}
    for number, (trial, varied) in enumerate(cases, 1):
        tag = "_".join(f"{flag}{getattr(trial, name):.9g}".replace(".", "p") for flag, name in flags)
        # Keep names bounded even when the source has a very long prompt filename.
        prefix = f"{index:04d}_sweep{number:02d}_{trial.start}_{varied}_{tag}"
        try:
            tick = time.perf_counter()
            zones, layers, details, summary = compute_zones(cv, edges, inside, detections, trial)
            growth_seconds = time.perf_counter() - tick
            names = save_outputs(
                source,
                box,
                inside,
                edges,
                zones,
                details,
                output,
                prefix,
                trial.detector,
                finishing=bool(trial.smooth or trial.expand),
                overlap_layers=layers,
                cv=cv,
                overlay_only=trial.overlay_only,
                blur=trial.blur,
            )
            if scores is not None and not trial.overlay_only:
                save_score_preview(cv, source, box, inside, scores, output / f"{prefix}_scores_overlay.jpg")
            report = dict(base_report, settings=dict(base_report["settings"]))
            for _, name in flags:
                report["settings"][settings_names.get(name, name)] = getattr(trial, name)
            report["settings"].update(start=trial.start, center_only_settings_active=trial.start == "center")
            report.update(
                crop_box=list(box),
                label_names=names,
                detections=details,
                **summary,
                coordinates="crop-relative, EXIF-oriented pixels",
                growth_seconds=growth_seconds,
                sweep={
                    "index": number,
                    "varied": varied,
                    "preset": args.sweep,
                    "strategy": {
                        "broad": "one_setting_at_a_time",
                        "refined": "focused_smooth_blur_grid",
                        "growth": "growth_balance_grid",
                    }[args.sweep],
                    "source_selection": "first_sorted_supported_image",
                    "inference_reused": True,
                },
                shared_analysis_seconds=analysis_seconds,
                total_seconds=time.perf_counter() - tick,
                total_seconds_scope="this variant's zoning and output only; model analysis shared across variants",
                limitation="Zones are heuristic; changes are a parameter comparison, not accuracy validation.",
            )
            if not trial.overlay_only:
                (output / f"{prefix}_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Sweep {number}/{len(cases)}: {trial.start}, {varied}; {tag}", flush=True)
        except Exception as exc:
            failures += 1
            print(f"{prefix}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return failures


def save_comparison(cv, args, source, box, inside, edges, detections, base_report, output, prefix, analysis_seconds):
    """Compare the two owner-selected recipes, sharing one image's model analysis."""
    presets = (
        (
            "7 / Outside-in",
            {
                "start": "edge",
                "smooth": 12.0,
                "expand": 0.0,
                "blur": 3.0,
                "box_scale": 1.6,
                "growth": 1.4,
                "balance": 1.0,
                "roundness": 0.3,
                "overlap": True,
            },
            "-l edge -k 1.6 -u 12 -x 0 -c 3 -a",
        ),
        (
            "20 / Center-out",
            {
                "start": "center",
                "smooth": 8.0,
                "expand": 0.0,
                "blur": 3.0,
                "box_scale": 1.6,
                "growth": 1.75,
                "balance": 1.0,
                "roundness": 0.3,
                "overlap": True,
            },
            "-l center -g 1.75 -z 1 -u 8 -x 0 -c 3 -a",
        ),
    )
    tick = time.perf_counter()
    panels, results = [], []
    font = ImageFont.load_default(size=18)
    for name, overrides, caption in presets:
        trial = argparse.Namespace(**vars(args))
        for key, value in overrides.items():
            setattr(trial, key, value)
        if box is None:
            image = source.copy()
            result = {"name": name, "settings": overrides, "status": "no_person", "detections": []}
            status = "No person detected; zoning not run"
        else:
            started = time.perf_counter()
            zones, layers, details, summary = compute_zones(cv, edges, inside, detections, trial)
            growth_seconds = time.perf_counter() - started
            image, names = render_zone_preview(
                source,
                box,
                inside,
                zones,
                details,
                cv,
                layers,
                bool(trial.smooth or trial.expand),
                trial.blur,
            )
            result = {
                "name": name,
                "settings": overrides,
                "label_names": names,
                "detections": details,
                "growth_seconds": growth_seconds,
                **summary,
            }
            status = f"{sum(d['pixels'] > 0 for d in details)} zones | zoning {growth_seconds:.2f}s"
        panel_width = max(image.width, font.getbbox(caption)[2] + 40)
        panel = Image.new("RGB", (panel_width, image.height + 104), (24, 27, 34))
        panel.paste(image, (0, 104))
        draw = ImageDraw.Draw(panel)
        for row, text in enumerate((name, caption, status)):
            draw.text((20, 12 + row * 30), text, fill="white", font=font)
        panels.append(panel)
        results.append(result)
    combined = Image.new("RGB", (panels[0].width + 16 + panels[1].width, max(p.height for p in panels)), (10, 12, 16))
    combined.paste(panels[0], (0, 0))
    combined.paste(panels[1], (panels[0].width + 16, 0))
    combined.save(output / f"{prefix}_compare.jpg", quality=95, subsampling=0)
    if not args.overlay_only:
        report = dict(base_report)
        # Preset-owned zoning values live with each preset, not in shared settings.
        owned = {
            "smooth",
            "expand",
            "display_blur_source_pixels",
            "box_scale",
            "growth",
            "balance",
            "roundness",
            "overlap",
            "start",
            "center_only_settings_active",
        }
        report["settings"] = {key: value for key, value in base_report["settings"].items() if key not in owned}
        report.update(
            comparison={"layout": "left: refined 7; right: broad 20", "inference_shared": True, "presets": results},
            crop_box=list(box) if box is not None else None,
            shared_analysis_seconds=analysis_seconds,
            total_seconds=analysis_seconds + time.perf_counter() - tick,
            total_seconds_scope="shared image analysis plus both zoning/render passes; model startup excluded",
        )
        (output / f"{prefix}_compare_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{base_report['source']}: comparison saved -> {prefix}_compare.jpg", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input-dir", type=Path, required=True)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("tmp/edge-nudenet-poc"))
    parser.add_argument("-b", "--detector", choices=("dexined", "pidinet", "pidinet-tiny", "lip-20"), default="dexined")
    parser.add_argument("-m", "--model", type=Path, help="Checkpoint path; default follows the selected detector")
    parser.add_argument(
        "-ps",
        "--person-size",
        choices=("n", "s", "m"),
        default="n",
        help="YOLO26 person segmenter: nano, small or medium; default n; missing checkpoint downloads",
    )
    parser.add_argument(
        "-p", "--person-model", type=Path, help="Override path for the exact pinned --person-size checkpoint"
    )
    parser.add_argument("-n", "--nudenet-model", type=Path, default=Path("tmp/models/nudenet-320n.onnx"))
    parser.add_argument(
        "-t", "--threads", type=int, default=4, help="Edge inference threads; person/parts use at most 4"
    )
    parser.add_argument(
        "-r", "--resolution", type=int, default=384, help="Region-growth dimension, not edge-model input"
    )
    parser.add_argument("-s", "--parts-confidence", type=float, default=0.25)
    parser.add_argument(
        "-l",
        "--start",
        choices=("center", "edge", "box"),
        default="center",
        help="center: budgeted growth (default); edge: inward shrink-wrap; box: experimental per-box auto-fit budget",
    )
    parser.add_argument(
        "-k",
        "--box-scale",
        type=float,
        default=1.25,
        help="Outside-in initial box width/height multiplier, 1..3 (default 1.25)",
    )
    parser.add_argument(
        "-y",
        "--roundness",
        type=float,
        default=0.3,
        help="Outside-in initial corner radius / shorter box side, 0..0.5 (default 0.3)",
    )
    parser.add_argument(
        "-q",
        "--person-mask-threshold",
        type=float,
        default=0.5,
        help="YOLO mask probability threshold in (0,1); lower is looser (try 0.35; default 0.5)",
    )
    parser.add_argument(
        "-j",
        "--repair-person",
        action="store_true",
        help="Close narrow silhouette gaps within the selected instance; never merge detections",
    )
    parser.add_argument(
        "-f", "--overlay-only", action="store_true", help="Write only *_zones_overlay.jpg; no other images or JSON"
    )
    parser.add_argument(
        "-c",
        "--blur",
        type=float,
        default=0,
        help="Additional display-only Gaussian feather of zone borders/outlines, source-pixel sigma 0..32 (try 2)",
    )
    parser.add_argument(
        "-a",
        "--overlap",
        action="store_true",
        help="Grow and finish every NudeNet zone independently, allowing overlapping masks",
    )
    parser.add_argument(
        "-v",
        "--sensitivity",
        type=float,
        default=1.0,
        help="LIP-20 score-boundary gain: >1 strengthens faint changes, <1 weakens them (default 1)",
    )
    parser.add_argument("-e", "--edge-weight", type=float, default=12, help="Higher values resist crossing edges more")
    parser.add_argument(
        "-u",
        "--smooth",
        type=float,
        default=0,
        help="Zone rounding Gaussian sigma in growth-grid pixels, 0..12; 0 disables (try 1.5)",
    )
    parser.add_argument(
        "-x",
        "--expand",
        type=float,
        default=0,
        help="Competing geometric expansion into unknown pixels, growth-grid radius 0..32 (try 3)",
    )
    parser.add_argument(
        "-g", "--growth", type=float, default=2.5, help="Center-start path budget relative to detection half-diagonal"
    )
    parser.add_argument(
        "-z",
        "--balance",
        type=float,
        default=0,
        help="Center-start seed/radial growth penalty, 0..4; 0 keeps legacy growth (try 1)",
    )
    parser.add_argument("-d", "--no-download", action="store_true")
    parser.add_argument(
        "-cp",
        "--compare",
        action="store_true",
        help="Whole directory: selected presets 7 and 20 side by side in one JPG per image; -f omits combined JSON",
    )
    parser.add_argument(
        "-sw",
        "--sweep",
        nargs="?",
        const="broad",
        choices=("broad", "refined", "growth"),
        help="First image only: broad varies six settings; refined compares 9 winners-nearby combinations; growth runs 10 g values x z=1/2",
    )
    parser.add_argument("-w", "--overwrite", action="store_true", help="Delete recognized previous POC outputs only")
    args = parser.parse_args(argv)
    if args.compare and args.sweep:
        parser.error("--compare processes the whole directory and cannot be combined with --sweep")
    if args.start == "box" and (args.compare or args.sweep):
        parser.error("--start box is standalone; existing --compare/--sweep presets target center/edge modes")
    if args.person_model is None:
        args.person_model = Path("tmp/models") / f"yolo26{args.person_size}-seg-ultralytics-v8.4.0.onnx"
    if args.model is None:
        if args.detector == "dexined":
            filename = "edge_detection_dexined_2024sep.onnx"
        elif args.detector == "lip-20":
            filename = "schp-lip-20-int8-static.onnx"
        else:
            filename = PIDINET_FILES[args.detector][0]
        args.model = Path("tmp/models") / filename
    if not args.input_dir.is_dir():
        parser.error("input-dir must be an existing directory")
    if not math.isfinite(args.person_mask_threshold) or not 0 < args.person_mask_threshold < 1:
        parser.error("person-mask-threshold must be finite and in (0,1)")
    if not math.isfinite(args.sensitivity) or args.sensitivity <= 0:
        parser.error("sensitivity must be finite and > 0")
    if args.detector != "lip-20" and args.sensitivity != 1.0:
        parser.error("--sensitivity applies only to --detector lip-20")
    if not math.isfinite(args.smooth) or not 0 <= args.smooth <= 12:
        parser.error("smooth must be finite and in [0,12]")
    if not math.isfinite(args.expand) or not 0 <= args.expand <= 32:
        parser.error("expand must be finite and in [0,32]")
    if not math.isfinite(args.blur) or not 0 <= args.blur <= 32:
        parser.error("blur must be finite and in [0,32]")
    if not math.isfinite(args.balance) or not 0 <= args.balance <= 4:
        parser.error("balance must be finite and in [0,4]")
    if not math.isfinite(args.box_scale) or not 1 <= args.box_scale <= 3:
        parser.error("box-scale must be finite and in [1,3]")
    if not math.isfinite(args.roundness) or not 0 <= args.roundness <= 0.5:
        parser.error("roundness must be finite and in [0,0.5]")
    if args.threads < 1 or not 64 <= args.resolution <= 1024 or not 0 < args.parts_confidence <= 1:
        parser.error("threads >= 1, resolution in [64,1024], parts-confidence in (0,1] required")
    if (
        not math.isfinite(args.edge_weight)
        or args.edge_weight < 0
        or not math.isfinite(args.growth)
        or args.growth <= 0
    ):
        parser.error("edge-weight must be finite and >= 0; growth must be finite and > 0")
    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        parser.error("No supported images found")
    if args.sweep:
        files = files[:1]
    import cv2 as cv
    from dtflow.services import person_selection as selection

    output, previous = output_directory(args, files, parser)
    edge_files = edge_checkpoints(args.detector, args.model)
    for path, spec in (
        *edge_files,
        (args.person_model, PERSON_SPECS[args.person_size]),
        (args.nudenet_model, CHECKPOINTS["faces"]),
    ):
        verified_checkpoint(path, spec, not args.no_download)
    cv.setNumThreads(args.threads)
    if args.detector == "dexined":
        net = cv.dnn.readNetFromONNX(str(args.model))
        net.setPreferableBackend(cv.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv.dnn.DNN_TARGET_CPU)
    else:
        net = load_pidinet(args.model, args.threads, args.detector)
    people_session = selection._verified_session(
        selection.describe_model(args.person_model), selection._segmentation_session
    )
    parts_session = selection._verified_session(selection.describe_model(args.nudenet_model), selection._faces_session)
    if any(s.get_providers() != ["CPUExecutionProvider"] for s in (people_session, parts_session)):
        raise RuntimeError("Detectors must use ONNX Runtime CPU only")
    for entry in previous:
        if entry.is_symlink() or entry.is_junction() or not entry.is_file() or entry.resolve().parent != output:
            raise RuntimeError(f"Output entry changed before cleanup: {entry.name}")
        entry.unlink()
    print(f"CPU models loaded; removed {len(previous)} prior outputs; {len(files)} images", flush=True)
    if args.start == "box":
        print("Box-fit: width/height-normalized per-zone auto budgets; -g, -z, -k and -y are inactive", flush=True)
    failures = 0
    for index, path in enumerate(files, 1):
        prefix = f"{index:04d}_{path.stem}"
        try:
            started = time.perf_counter()
            source = load_rgb(path)
            people = select_people(
                selection._instances(people_session, source, 0.25, mask_threshold=args.person_mask_threshold),
                source.size,
                0,
                1,
                args.repair_person,
            )
            report = {
                "source": path.name,
                "algorithm": f"{args.detector}-nudenet-edge-growth-v1",
                "people": len(people),
                "edge_backend": "OpenCV DNN CPU" if args.detector == "dexined" else "ONNX Runtime CPU",
                "detector_backend": "ONNX Runtime CPU",
                "person_segmenter": f"yolo26{args.person_size}-seg",
                "person_checkpoint_sha256": PERSON_SPECS[args.person_size][3],
                "edge_detector": args.detector,
                "edge_revision": (
                    REVISION
                    if args.detector == "dexined"
                    else LIP_REVISION
                    if args.detector == "lip-20"
                    else PIDINET_REVISION
                ),
                "edge_sha256": edge_files[0][1][3],
                "edge_checkpoints": {path.name: spec[3] for path, spec in edge_files},
                "settings": {
                    "edge_weight": args.edge_weight,
                    "growth": args.growth,
                    "resolution": args.resolution,
                    "parts_confidence": args.parts_confidence,
                    "person_mask_threshold": args.person_mask_threshold,
                    "person_size": args.person_size,
                    "repair_person": args.repair_person,
                    "sensitivity": args.sensitivity,
                    "smooth": args.smooth,
                    "expand": args.expand,
                    "overlap": args.overlap,
                    "overlay_only": args.overlay_only,
                    "display_blur_source_pixels": args.blur,
                    "balance": args.balance,
                    "start": args.start,
                    "box_scale": args.box_scale,
                    "roundness": args.roundness,
                    "center_only_settings_active": args.start == "center",
                    "box_fit_auto_budget": args.start == "box",
                    "finishing_units": "region-growth grid pixels",
                },
            }
            if args.compare and not people:
                save_comparison(
                    cv,
                    args,
                    source,
                    None,
                    None,
                    None,
                    [],
                    report,
                    output,
                    prefix,
                    time.perf_counter() - started,
                )
                continue
            if people:
                _, mask, person_box = people[0]
                box = tuple(int(v) for v in person_crop_box(person_box, source.size))
                x0, y0, x1, y1 = box
                crop, inside = source.crop(box), mask[y0:y1, x0:x1] > 0
                tick = time.perf_counter()
                scores = None
                if args.detector == "dexined":
                    edges = detect_edges(cv, net, crop)
                elif args.detector == "lip-20":
                    edges, scores, score_details = lip_score_boundaries(
                        cv, net, crop, inside, args.resolution, args.sensitivity
                    )
                    report["score_boundaries"] = score_details
                else:
                    edges = pidinet_edges(cv, net, crop)
                report["edge_seconds"] = time.perf_counter() - tick
                _, detections, _ = detect_body_parts(parts_session, crop, args.parts_confidence)
                detections = [d for d in detections if not d["label"].startswith("ARMPITS_")]
                if args.compare:
                    save_comparison(
                        cv,
                        args,
                        source,
                        box,
                        inside,
                        edges,
                        detections,
                        report,
                        output,
                        prefix,
                        time.perf_counter() - started,
                    )
                    continue
                if args.sweep:
                    print(f"Sweep input: {path.name}", flush=True)
                    failures += save_sweep(
                        cv,
                        args,
                        source,
                        box,
                        inside,
                        edges,
                        scores,
                        detections,
                        report,
                        output,
                        index,
                        time.perf_counter() - started,
                    )
                    continue
                tick = time.perf_counter()
                zones, overlap_layers, details, zone_summary = compute_zones(cv, edges, inside, detections, args)
                report["growth_seconds"] = time.perf_counter() - tick
                names = save_outputs(
                    source,
                    box,
                    inside,
                    edges,
                    zones,
                    details,
                    output,
                    prefix,
                    args.detector,
                    finishing=bool(args.smooth or args.expand),
                    overlap_layers=overlap_layers,
                    cv=cv,
                    overlay_only=args.overlay_only,
                    blur=args.blur,
                )
                if scores is not None and not args.overlay_only:
                    save_score_preview(cv, source, box, inside, scores, output / f"{prefix}_scores_overlay.jpg")
                report.update(
                    crop_box=list(box),
                    label_names=names,
                    detections=details,
                    coordinates="crop-relative, EXIF-oriented pixels",
                    **zone_summary,
                    limitation="Edges include texture/shadows; zones are heuristic, not anatomical segmentation.",
                )
                print(
                    f"{path.name}: 1 person, {sum(d['pixels'] > 0 for d in details)} zones; "
                    f"edges {report['edge_seconds']:.2f}s, growth {report['growth_seconds']:.2f}s",
                    flush=True,
                )
            else:
                report["status"] = "no_person"
                print(f"{path.name}: no person", flush=True)
            report["total_seconds"] = time.perf_counter() - started
            if not args.overlay_only:
                (output / f"{prefix}_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            failures += 1
            print(f"{path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

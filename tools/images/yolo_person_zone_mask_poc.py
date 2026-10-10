"""CPU-only YOLO26 end-to-end person segmentation + manual rectangle PoC."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import platform
import time
from pathlib import Path

# Bound NumPy's mask reconstruction as well as ORT; no GPU imports/probes.
for _key in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[_key] = "1"

from person_zone_mask_poc import load_image, output_path, thread_count, validate_box

LOG = logging.getLogger("yolo_person_zone_mask_poc")
SIDE = 640


def confidence(value: str) -> float:
    number = float(value)
    if not 0 < number <= 1:
        raise argparse.ArgumentTypeError("Confidence must be >0 and <=1")
    return number


def session_for(data: bytes, threads: int):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(data, sess_options=options, providers=["CPUExecutionProvider"])
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise ValueError("CPU-only provider contract failed")
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or inputs[0].shape != [1, 3, 640, 640] or inputs[0].type != "tensor(float)":
        raise ValueError("Requires float32 input [1,3,640,640]")
    if len(outputs) != 2 or outputs[0].shape != [1, 300, 38] or outputs[1].shape != [1, 32, 160, 160]:
        raise ValueError("Requires YOLO26 end2end outputs [1,300,38] and [1,32,160,160]")
    if any(output.type != "tensor(float)" for output in outputs):
        raise ValueError("Requires float32 outputs")
    meta = session.get_modelmeta().custom_metadata_map
    if meta.get("task") != "segment" or meta.get("end2end", "").lower() != "true":
        raise ValueError("Requires segment/end2end metadata; raw YOLO exports need a different decoder")
    names = ast.literal_eval(meta.get("names", "{}"))
    if not isinstance(names, dict) or names.get(0) != "person":
        raise ValueError("Requires COCO person class 0")
    return session, meta, names


def letterbox(image):
    import numpy as np
    from PIL import Image

    width, height = image.size
    gain = min(SIDE / width, SIDE / height)
    resized = (max(1, round(width * gain)), max(1, round(height * gain)))
    left = round((SIDE - resized[0]) / 2 - 0.1)
    top = round((SIDE - resized[1]) / 2 - 0.1)
    canvas = Image.new("RGB", (SIDE, SIDE), (114, 114, 114))
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (left, top))
    tensor = np.ascontiguousarray(np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0)
    # "gain" is the single Ultralytics-style ratio used to unmap boxes; gain_xy is the effective
    # per-axis ratio after rounding (differs by <1 px for odd sizes) and is kept for the mask crop.
    return tensor, {"resized_wh": list(resized), "padding_left_top": [left, top], "gain": gain,
                    "gain_xy": [resized[0] / width, resized[1] / height]}


def decode(detections, prototypes, geometry, size, threshold, names):
    import numpy as np
    from PIL import Image

    if detections.shape != (1, 300, 38) or prototypes.shape != (1, 32, 160, 160):
        raise ValueError("Unexpected output tensor shapes")
    if not np.isfinite(detections).all() or not np.isfinite(prototypes).all():
        raise ValueError("Non-finite model outputs")
    rows = detections[0]
    accepted = rows[rows[:, 4] >= threshold]
    if np.any((accepted[:, 4] < 0) | (accepted[:, 4] > 1)):
        raise ValueError("Scores outside [0,1]")
    if np.any(accepted[:, 5] != np.round(accepted[:, 5])):
        raise ValueError("Expected end2end integer class IDs")
    classes, counts = np.unique(accepted[:, 5].astype(int), return_counts=True)
    histogram = [{"class_id": int(c), "name": names.get(int(c), "unknown"), "detections": int(n)}
                 for c, n in zip(classes, counts)]
    people = accepted[accepted[:, 5] == 0]
    people = people[np.argsort(-people[:, 4])]
    # Decode sequentially and cap to 20 instances to bound mask work/memory.
    dropped = max(0, len(people) - 20)
    people = people[:20]
    union = np.zeros((SIDE, SIDE), dtype=bool)
    yy, xx = np.ogrid[:SIDE, :SIDE]
    records = []
    left, top = geometry["padding_left_top"]
    gain_x = gain_y = geometry["gain"]
    width, height = size
    proto = prototypes[0].reshape(32, -1)
    for row in people:
        x0, y0, x1, y1 = np.clip(row[:4], 0, SIDE)
        if x1 <= x0 or y1 <= y0:
            continue
        logits = (row[6:] @ proto).reshape(160, 160)
        if not np.isfinite(logits).all():
            raise ValueError("Non-finite reconstructed mask")
        # Float logits resized first, threshold at zero (=sigmoid >.5), crop to detection box.
        full = np.asarray(Image.fromarray(logits).resize((SIDE, SIDE), Image.Resampling.BILINEAR)) > 0
        full &= (xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1)
        union |= full
        box = [float(np.clip((x0-left)/gain_x, 0, width)), float(np.clip((y0-top)/gain_y, 0, height)),
               float(np.clip((x1-left)/gain_x, 0, width)), float(np.clip((y1-top)/gain_y, 0, height))]
        records.append({"score": float(row[4]), "box_input_xyxy": [float(v) for v in row[:4]],
                        "box_source_xyxy": box, "mask_pixels_input": int(full.sum())})
    rw, rh = geometry["resized_wh"]
    cropped = (union[top:top+rh, left:left+rw].astype(np.uint8) * 255)
    person = np.asarray(Image.fromarray(cropped).resize(size, Image.Resampling.NEAREST)).copy()
    person_rows = rows[rows[:, 5] == 0]
    top_scores = [float(v) for v in np.sort(person_rows[:, 4])[::-1][:3]]
    return person, {"class_histogram_above_confidence": histogram, "person_instances": records,
                    "person_instances_dropped_at_cap": dropped,
                    "max_person_score": top_scores[0] if top_scores else None,
                    "top3_person_scores_any_confidence": top_scores,
                    "prototype_shape": list(prototypes.shape), "mask_coefficients": int(rows.shape[1] - 6)}


def run(args):
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    began = time.perf_counter()
    timings = {}
    out = output_path(args.out)
    LOG.info("START YOLO26 CPU threads=%d confidence=%.3f", args.threads, args.confidence)
    start = time.perf_counter()
    if args.model.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Model exceeds 64 MiB")
    data = args.model.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.sha256.lower():
        raise ValueError("Model SHA256 mismatch")
    timings["model_read_checksum"] = (time.perf_counter()-start)*1000
    start = time.perf_counter()
    image, raw_size = load_image(args.source)
    validate_box(args.box, image.size)
    timings["image_decode"] = (time.perf_counter()-start)*1000
    start = time.perf_counter()
    session, metadata, names = session_for(data, args.threads)
    del data
    timings["session_load"] = (time.perf_counter()-start)*1000
    LOG.info("SESSION provider=%s source_wh=%s box=%s", session.get_providers(), image.size, args.box)
    start = time.perf_counter()
    tensor, geometry = letterbox(image)
    timings["preprocessing"] = (time.perf_counter()-start)*1000
    LOG.info("LETTERBOX %s; starting one inference", geometry)
    start = time.perf_counter()
    detections, prototypes = session.run(None, {session.get_inputs()[0].name: tensor})
    timings["inference_cold_single_run"] = (time.perf_counter()-start)*1000
    LOG.info("INFERENCE %.1f ms outputs=%s,%s", timings["inference_cold_single_run"], detections.shape, prototypes.shape)
    start = time.perf_counter()
    person, diagnostics = decode(detections, prototypes, geometry, image.size, args.confidence, names)
    del detections, prototypes, tensor
    zone = np.zeros_like(person)
    x0, y0, x1, y1 = args.box
    zone[y0:y1, x0:x1] = 255
    edit = np.bitwise_and(person, zone)
    overlay = Image.composite(Image.blend(image, Image.new("RGB", image.size, (255, 64, 64)), .45),
                              image, Image.fromarray(edit))
    timings["postprocessing"] = (time.perf_counter()-start)*1000
    warnings = []
    if not np.any(person):
        warnings.append("Empty person mask; inspect detections/scores/export")
    elif not np.any(edit):
        warnings.append("Person mask does not intersect manual box")
    if diagnostics["person_instances_dropped_at_cap"]:
        warnings.append("Person masks capped at 20 highest-score instances")
    if "'half': True" in metadata.get("args", ""):
        warnings.append("Export args say half=True: graph may run FP16 internally on CPU; consider a half=False export")
    person_px, edit_px = int(np.count_nonzero(person)), int(np.count_nonzero(edit))
    edit_fraction = edit_px / person_px if person_px else 0.0
    if person_px and 0 < edit_fraction < 0.25:
        warnings.append(f"Manual box covers only {edit_fraction:.1%} of the person mask; check box coordinates")
    LOG.info("DETECTIONS %s", diagnostics)
    LOG.info("MASKS person_pixels=%d edit_pixels=%d", np.count_nonzero(person), np.count_nonzero(edit))
    for warning in warnings:
        LOG.warning(warning)
    report = {
        "schema_version": 1, "algorithm": "yolo26-end2end-person-zone-poc-v1",
        "model": {"path": str(args.model.resolve()), "sha256": digest, "metadata": metadata},
        "model_io": {"inputs": [{"name": i.name, "shape": i.shape, "type": i.type} for i in session.get_inputs()],
                     "outputs": [{"name": o.name, "shape": o.shape, "type": o.type} for o in session.get_outputs()]},
        "source": {"path": str(args.source.resolve()), "raw_size_wh": list(raw_size), "oriented_size_wh": list(image.size)},
        "runtime": {"providers": session.get_providers(), "onnxruntime": ort.__version__, "numpy": np.__version__,
                    "platform": platform.platform(), "processor": platform.processor(), "inference_workers": 1,
                    "intra_op_threads": args.threads, "inter_op_threads": 1, "sequential": True, "spinning": False,
                    "numpy_blas_threads": 1},
        "preprocessing": {"orientation": "EXIF transpose", "alpha": "white composite", "color": "RGB",
                          "resize": "Pillow bilinear letterbox 640 square, pad114", "scale": "/255, no mean/std",
                          "input": "float32 NCHW", **geometry},
        "decoding": {"layout": "xyxy,score,class,32 mask coefficients; end2end, no additional NMS",
                     "person_class": 0, "confidence": args.confidence, "mask_threshold": "logits >0",
                     "mapping": "Pillow bilinear prototype logits to 640; threshold/crop detection; unpad; nearest to source",
                     "union": "up to 20 person instances; manual rectangle AND union; no feather"},
        "coordinates": {"box_xyxy": args.box, "space": "EXIF-oriented source pixels",
                        "bounds": "left/top inclusive, right/bottom exclusive"},
        "masks": {"person_pixels": person_px, "zone_pixels": int(np.count_nonzero(zone)),
                  "edit_pixels": edit_px, "edit_fraction_of_person": edit_fraction,
                  "polarity": "255 selected; 0 unselected"},
        "diagnostics": diagnostics, "warnings": warnings, "timings_ms": timings,
        "limitations": ["Pillow interpolation is not verified identical to Ultralytics", "One cold run, not a benchmark",
                        "No inpainting compatibility claim", "Report paths and overlay may be sensitive",
                        "Model is AGPL-3.0 (Ultralytics); check licensing before shipping in an app"],
    }
    out.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    for name, mask in (("person_mask.png", person), ("zone_mask.png", zone), ("edit_mask.png", edit)):
        with (out/name).open("xb") as handle:
            Image.fromarray(mask).save(handle, format="PNG")
    with (out/"overlay.jpg").open("xb") as handle:
        overlay.save(handle, format="JPEG", quality=90)
    timings["image_output_write"] = (time.perf_counter()-start)*1000
    timings["total_before_report_write"] = (time.perf_counter()-began)*1000
    with (out/"report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    LOG.info("DONE %s timings_ms=%s", out, timings)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--source", type=Path, required=True)
    parser.add_argument("-m", "--model", type=Path, required=True)
    parser.add_argument("-c", "--sha256", required=True)
    parser.add_argument("-b", "--box", nargs=4, type=int, required=True, metavar=("X0", "Y0", "X1", "Y1"))
    parser.add_argument("-f", "--confidence", type=confidence, default=.25)
    parser.add_argument("-t", "--threads", type=thread_count, default=1)
    parser.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run(args)
    except Exception:
        LOG.exception("PoC failed; no retry/fallback; any partial outputs are preserved")
        if args.out.exists():
            LOG.error("Partial output left at %s; remove it before retrying", args.out)
        return 1  # argparse owns exit code 2 (usage errors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
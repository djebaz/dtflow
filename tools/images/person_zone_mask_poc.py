"""CPU-only, non-generative LR-ASPP person/rectangle mask experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import re
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = "torchvision-lraspp-mobilenet-v3-large-voc21-320-v1"
SIDE = 320
PROFILES = {
    "torchvision": {
        "contract": CONTRACT,
        "classes": 21,
        "person": 15,
        "weights": "LRASPP_MobileNet_V3_Large_Weights.COCO_WITH_VOC_LABELS_V1",
        "metadata_required": True,
        "resize_note": "experimental; upstream short edge is 520",
        "export_note": "Caller-supplied TorchVision export",
    },
    "fastseg": {
        "contract": "fastseg-mobilev3small-lraspp-f64-cityscapes19-320-v1",
        "classes": 19,
        "person": 11,
        "weights": "mobilev3small-lraspp-f64-114fc23b.pt",
        "metadata_required": False,
        "resize_note": "experimental fixed-size export; upstream predict normalizes without resizing",
        "export_note": "Owner-supplied 320 export: AvgPool2d kernel/stride patched to 40x40; optional dynamic INT8",
    },
}
MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16_000_000
LOGGER = logging.getLogger("person_zone_mask_poc")


def thread_count(value: str) -> int:
    count = int(value)
    maximum = min(4, os.cpu_count() or 1)
    if not 1 <= count <= maximum:
        raise argparse.ArgumentTypeError(f"Threads must be between 1 and {maximum}")
    return count


def validate_box(box: list[int], size: tuple[int, int]) -> None:
    x0, y0, x1, y1 = box
    width, height = size
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"Box must satisfy 0 <= x0 < x1 <= {width}, 0 <= y0 < y1 <= {height}")


def output_path(path: Path) -> Path:
    scratch = ROOT / "tmp"
    if scratch.resolve() != scratch.absolute():
        raise ValueError("Repository tmp must not be redirected")
    resolved = path.resolve()
    if not resolved.is_relative_to(scratch) or resolved == scratch:
        raise ValueError("Output must be a new directory below repository tmp/")
    if resolved != path.absolute() or resolved.exists():
        raise ValueError("Output must not exist or traverse redirected directories")
    return resolved


def load_image(path: Path):
    from PIL import Image, ImageOps

    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Source exceeds 64 MiB")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Source exceeds 16 million pixels")
            frames = getattr(source, "n_frames", 1)
            if frames != 1 and source.format != "MPO":
                raise ValueError("Animated/multiframe images are unsupported; MPO uses primary frame 0")
            source.seek(0)
            LOGGER.info("IMAGE format=%s frames=%d selected_frame=0", source.format, frames)
            raw_size = source.size
            oriented = ImageOps.exif_transpose(source).convert("RGBA")
            rgb = Image.new("RGBA", oriented.size, "white")
            rgb.alpha_composite(oriented)
            return rgb.convert("RGB"), raw_size


def preprocess(image):
    import numpy as np
    from PIL import Image

    pixels = np.asarray(image.resize((SIDE, SIDE), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    return np.ascontiguousarray(((pixels - mean) / std).transpose(2, 0, 1)[None])


def masks_from_logits(logits, size: tuple[int, int], box: list[int], profile: str = "torchvision"):
    import numpy as np
    from PIL import Image

    validate_box(box, size)
    spec = PROFILES[profile]
    if logits.shape != (1, spec["classes"], SIDE, SIDE) or logits.dtype != np.float32 or not np.isfinite(logits).all():
        raise ValueError(f"Expected finite float32 logits [1,{spec['classes']},320,320]")
    # Argmax in model coordinates, then nearest-neighbour binary resize: no feather spill.
    small = (logits[0].argmax(axis=0) == spec["person"]).astype(np.uint8) * 255
    person = np.asarray(Image.fromarray(small).resize(size, Image.Resampling.NEAREST)).copy()
    zone = np.zeros_like(person)
    x0, y0, x1, y1 = box
    zone[y0:y1, x0:x1] = 255
    return person, zone, np.bitwise_and(person, zone)



def prediction_diagnostics(tensor, logits, profile: str) -> dict:
    """Summaries only: no raw image/tensor data or extra inference."""
    import numpy as np

    def stats(values):
        return {
            "min": float(values.min()),
            "max": float(values.max()),
            "mean": float(values.mean(dtype=np.float64)),
            "std": float(values.std(dtype=np.float64)),
        }

    spec = PROFILES[profile]
    labels = logits[0].argmax(axis=0)
    counts = np.bincount(labels.ravel(), minlength=spec["classes"])
    pixels = int(labels.size)
    person_logits = logits[0, spec["person"]]
    winning_logits = logits[0].max(axis=0)
    margin = person_logits.astype(np.float64) - winning_logits.astype(np.float64)
    histogram = [
        {"class_id": index, "model_pixels": int(count), "fraction": float(count / pixels)}
        for index, count in enumerate(counts)
    ]
    dominant = int(counts.argmax())
    return {
        "input_shape": list(tensor.shape),
        "input_dtype": str(tensor.dtype),
        "normalized_rgb_channels": {name: stats(tensor[0, index]) for index, name in enumerate(("R", "G", "B"))},
        "output_shape": list(logits.shape),
        "output_dtype": str(logits.dtype),
        "output_finite": True,  # masks_from_logits validated this before diagnostics.
        "class_histogram_model_grid": histogram,
        "dominant_class_id": dominant,
        "dominant_fraction": float(counts[dominant] / pixels),
        "person_class_id": spec["person"],
        "person_model_pixels": int(counts[spec["person"]]),
        "class_logits": [{"class_id": index, **stats(channel)} for index, channel in enumerate(logits[0])],
        "person_minus_winning_logit": stats(margin),
        "logit_note": "Raw scores, not calibrated confidence; person-minus-winner is <=0, ties may not win argmax",
    }


def create_session(model_bytes: bytes, threads: int, profile: str = "torchvision"):
    import onnxruntime as ort

    spec = PROFILES[profile]
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise ValueError("Session did not select CPUExecutionProvider exclusively")
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or len(outputs) != 1:
        raise ValueError("Contract requires exactly one input and one output")
    if inputs[0].type != "tensor(float)" or inputs[0].shape != [1, 3, SIDE, SIDE]:
        raise ValueError("Contract requires float32 input [1,3,320,320]")
    if outputs[0].type != "tensor(float)" or outputs[0].shape != [1, spec["classes"], SIDE, SIDE]:
        raise ValueError(f"Contract requires float32 output [1,{spec['classes']},320,320]")
    declared = session.get_modelmeta().custom_metadata_map.get("mask_poc_contract")
    if declared != spec["contract"] and (spec["metadata_required"] or declared is not None):
        if spec["metadata_required"]:
            raise ValueError(f"Required model metadata mask_poc_contract must equal {spec['contract']}")
        raise ValueError(
            f"Optional mask_poc_contract metadata is present but conflicts with selected profile: "
            f"expected {spec['contract']}, got {declared!r}"
        )
    return session


def run(args: argparse.Namespace) -> Path:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    spec = PROFILES[args.profile]
    LOGGER.info("START profile=%s person_class=%d threads=%d", args.profile, spec["person"], args.threads)
    total_start = time.perf_counter()
    destination = output_path(args.out)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", args.sha256):
        raise ValueError("--sha256 must be the expected model's 64-digit SHA-256")
    start = time.perf_counter()
    if args.model.stat().st_size > MAX_BYTES:
        raise ValueError("Model exceeds 64 MiB; use a self-contained LR-ASPP ONNX export")
    model_bytes = args.model.read_bytes()
    checksum = hashlib.sha256(model_bytes).hexdigest()
    if checksum != args.sha256.lower():
        raise ValueError("Model SHA-256 mismatch")
    model_read_ms = (time.perf_counter() - start) * 1000
    LOGGER.info("MODEL checksum verified sha256=%s bytes=%d read_ms=%.1f", checksum, len(model_bytes), model_read_ms)
    start = time.perf_counter()
    image, raw_size = load_image(args.source)
    validate_box(args.box, image.size)
    image_load_ms = (time.perf_counter() - start) * 1000
    LOGGER.info("IMAGE raw_wh=%s oriented_wh=%s box_xyxy=%s decode_ms=%.1f", raw_size, image.size, args.box, image_load_ms)
    start = time.perf_counter()
    session = create_session(model_bytes, args.threads, args.profile)
    session_load_ms = (time.perf_counter() - start) * 1000
    LOGGER.info("SESSION providers=%s load_ms=%.1f", session.get_providers(), session_load_ms)
    LOGGER.info("TENSORS input=%s output=%s", session.get_inputs()[0].shape, session.get_outputs()[0].shape)
    del model_bytes
    start = time.perf_counter()
    tensor = preprocess(image)
    preprocess_ms = (time.perf_counter() - start) * 1000
    LOGGER.info("INFERENCE starting one CPU call; preprocess_ms=%.1f", preprocess_ms)
    start = time.perf_counter()
    logits = session.run([session.get_outputs()[0].name], {session.get_inputs()[0].name: tensor})[0]
    inference_ms = (time.perf_counter() - start) * 1000
    LOGGER.info("INFERENCE completed cold_run_ms=%.1f", inference_ms)
    start = time.perf_counter()
    person, zone, edit = masks_from_logits(logits, image.size, args.box, args.profile)
    mask_processing_ms = (time.perf_counter() - start) * 1000
    diagnostic_start = time.perf_counter()
    diagnostics = prediction_diagnostics(tensor, logits, args.profile)
    diagnostics_ms = (time.perf_counter() - diagnostic_start) * 1000
    LOGGER.info("PREDICTIONS model-grid class_id:pixels=%s",
                ", ".join(f"{row['class_id']}:{row['model_pixels']}" for row in diagnostics["class_histogram_model_grid"]))
    LOGGER.info("PERSON class=%d model_pixels=%d source_pixels=%d edit_pixels=%d",
                spec["person"], diagnostics["person_model_pixels"], int(np.count_nonzero(person)), int(np.count_nonzero(edit)))
    LOGGER.info("LOGITS person=%s person_minus_winner=%s",
                diagnostics["class_logits"][spec["person"]], diagnostics["person_minus_winning_logit"])
    diagnostic_warnings = []
    if not np.any(person):
        diagnostic_warnings.append("Empty person mask before box intersection: review class scores, preprocessing and export")
    elif not np.any(edit):
        diagnostic_warnings.append("Person detected, but the manual box does not intersect the person mask")
    if diagnostics["dominant_fraction"] == 1.0:
        diagnostic_warnings.append(f"Every model pixel predicts class {diagnostics['dominant_class_id']}")
    for message in diagnostic_warnings:
        LOGGER.warning(message)
    del logits, tensor
    start = time.perf_counter()
    highlight = Image.new("RGB", image.size, (255, 64, 64))
    tinted = Image.blend(image, highlight, 0.45)
    overlay = Image.composite(tinted, image, Image.fromarray(edit))
    postprocess_ms = mask_processing_ms + (time.perf_counter() - start) * 1000
    report = {
        "schema_version": 3,
        "diagnostics": diagnostics,
        "warnings": diagnostic_warnings,
        "model": {
            "contract": spec["contract"],
            "profile": args.profile,
            "class_count": spec["classes"],
            "person_class": spec["person"],
            "export_note": spec["export_note"],
            "path": str(args.model.resolve()),
            "sha256": checksum,
            "weights": spec["weights"],
            "provenance": "caller-supplied export; metadata/checksum are not architecture or accuracy proof",
        },
        "runtime": {
            "onnxruntime": ort.__version__,
            "numpy": np.__version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "providers": session.get_providers(),
            "inference_workers": 1,
            "intra_op_threads": args.threads,
            "inter_op_threads": 1,
            "execution": "sequential",
            "spinning": False,
        },
        "source": {
            "path": str(args.source.resolve()),
            "raw_size_wh": list(raw_size),
            "oriented_size_wh": list(image.size),
            "selected_frame": 0,
            "multiframe_policy": "MPO primary frame 0 only; other multiframe inputs rejected",
        },
        "preprocessing": {
            "orientation": "EXIF transpose",
            "alpha": "white composite",
            "color": "RGB",
            "resize": f"Pillow bilinear stretch to 320x320 ({spec['resize_note']})",
            "input": "float32 NCHW [1,3,320,320]",
            "scale": "divide by 255",
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
        },
        "coordinates": {
            "box_xyxy": args.box,
            "space": "EXIF-oriented source pixels",
            "origin": "top-left",
            "bounds": "left/top inclusive, right/bottom exclusive; no clipping",
            "mask_mapping": f"argmax model logits, person class {spec['person']}, nearest resize to oriented source",
        },
        "masks": {
            "polarity": "255 selected; 0 unselected",
            "operation": "person AND rectangle",
            "person_pixels": int(np.count_nonzero(person)),
            "zone_pixels": int(np.count_nonzero(zone)),
            "edit_pixels": int(np.count_nonzero(edit)),
            "feather": False,
        },
        "timings_ms": {
            "model_read_checksum": model_read_ms,
            "image_decode": image_load_ms,
            "session_load": session_load_ms,
            "preprocessing": preprocess_ms,
            "inference_cold_single_run": inference_ms,
            "postprocessing": postprocess_ms,
            "diagnostics": diagnostics_ms,
        },
        "limitations": [
            "Semantic union of people, not instance selection",
            "No inpainting compatibility claim",
            "One cold run, not a throughput benchmark",
            "Paths and overlay may be sensitive",
            "320-square stretch and argmax-before-nearest resize may distort shapes and produce blocky edges",
            "Pillow resize is not verified numerically identical to the upstream tensor pipeline",
        ] + ([
            "Cityscapes street-scene training: portrait/indoor segmentation quality is not established",
            "FastSeg selects person class 11 only; rider class 12 is deliberately excluded",
            "Owner pooling/export adaptation and INT8 parity are not established",
        ] if args.profile == "fastseg" else []),
    }
    # New directory only; exclusive files ensure even partial runs are never overwritten.
    destination.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    for name, mask in (("person_mask.png", person), ("zone_mask.png", zone), ("edit_mask.png", edit)):
        with (destination / name).open("xb") as handle:
            Image.fromarray(mask).save(handle, format="PNG")
    with (destination / "overlay.jpg").open("xb") as handle:
        overlay.save(handle, format="JPEG", quality=90)
    report["timings_ms"]["image_output_write"] = (time.perf_counter() - start) * 1000
    report["timings_ms"]["total_before_report_write"] = (time.perf_counter() - total_start) * 1000
    with (destination / "report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    LOGGER.info("DONE wrote five outputs; timings_ms=%s", report["timings_ms"])
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--source", type=Path, required=True, help="One local image (read only)")
    parser.add_argument(
        "-p",
        "--profile",
        choices=PROFILES,
        default="torchvision",
        help="Explicit model contract (default torchvision; supplied MobileV3Small exports use fastseg)",
    )
    parser.add_argument("-m", "--model", type=Path, required=True, help="Local self-contained LR-ASPP ONNX export")
    parser.add_argument("-c", "--sha256", required=True, help="Expected SHA-256 of the trusted export")
    parser.add_argument("-b", "--box", nargs=4, type=int, required=True, metavar=("X0", "Y0", "X1", "Y1"))
    parser.add_argument(
        "-t", "--threads", type=thread_count, default=1, help="Intra-op threads (default 1; cap 4/CPU count)"
    )
    parser.add_argument("-o", "--out", type=Path, required=True, help="New directory below repository tmp/")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        destination = run(args)
    except Exception as exc:
        # ORT has its own exception types; report failure, never retry with another provider.
        LOGGER.exception("Mask PoC failed: %s: %s", type(exc).__name__, exc)
        LOGGER.error("Requested output directory: %s (may contain partial output; nothing removed)", args.out.absolute())
        return 1
    print(f"Saved masks and report to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

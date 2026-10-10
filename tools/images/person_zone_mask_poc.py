"""CPU-only, non-generative LR-ASPP person/rectangle mask experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
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
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("Only single-frame images are supported")
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
        raise ValueError(f"Model must declare mask_poc_contract={spec['contract']}; see README")
    return session


def run(args: argparse.Namespace) -> Path:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    spec = PROFILES[args.profile]
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
    start = time.perf_counter()
    image, raw_size = load_image(args.source)
    validate_box(args.box, image.size)
    image_load_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    session = create_session(model_bytes, args.threads, args.profile)
    session_load_ms = (time.perf_counter() - start) * 1000
    del model_bytes
    start = time.perf_counter()
    tensor = preprocess(image)
    preprocess_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    logits = session.run([session.get_outputs()[0].name], {session.get_inputs()[0].name: tensor})[0]
    inference_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    person, zone, edit = masks_from_logits(logits, image.size, args.box, args.profile)
    del logits, tensor
    highlight = Image.new("RGB", image.size, (255, 64, 64))
    tinted = Image.blend(image, highlight, 0.45)
    overlay = Image.composite(tinted, image, Image.fromarray(edit))
    postprocess_ms = (time.perf_counter() - start) * 1000
    report = {
        "schema_version": 2,
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
        },
        "limitations": [
            "Semantic union of people, not instance selection",
            "No inpainting compatibility claim",
            "One cold run, not a throughput benchmark",
            "Paths and overlay may be sensitive",
        ],
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
    try:
        destination = run(args)
    except Exception as exc:
        # ORT has its own exception types; report failure, never retry with another provider.
        print(f"Mask PoC failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Saved masks and report to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

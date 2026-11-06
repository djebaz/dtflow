# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = ["ultralytics==8.4.203", "onnx==1.19.1"]
# ///
"""Export a trusted local OBB checkpoint to FP32 ONNX in an isolated uv environment.

Raw head matches ordinary training validation; end2end selects the one-to-one
head. Export traces the model on CPU. No dataset inference or benchmarking.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("-r", "--resolution", type=int, default=640)
    parser.add_argument("-t", "--threads", type=int, default=2)
    parser.add_argument("-hd", "--head", choices=("raw", "end2end"), default="raw")
    parser.add_argument(
        "-y",
        "--trust",
        action="store_true",
        help="Explicitly trust the .pt source; loading PyTorch pickles can execute code",
    )
    parser.add_argument(
        "-w", "--overwrite", action="store_true", help="Replace only the explicitly named ONNX and export report"
    )
    args = parser.parse_args()
    if not args.trust:
        parser.error("Only export checkpoints you trust; pass -y / --trust to acknowledge pickle loading")
    source, output = args.input.resolve(), args.output.resolve()
    report_path = output.with_suffix(".export.json")
    if source.suffix.lower() != ".pt" or not source.is_file() or output.suffix.lower() != ".onnx":
        parser.error("An existing .pt input and an .onnx output are required")
    if args.input.is_symlink() or args.output.is_symlink() or report_path.is_symlink():
        parser.error("Input/output files must not be symlinks")
    if (output.exists() or report_path.exists()) and not args.overwrite:
        parser.error("Output/report already exists; choose a new output or explicitly pass -w")
    if not 64 <= args.resolution <= 2048 or args.resolution % 32 or args.threads < 1:
        parser.error("resolution must be a multiple of 32 in [64,2048]; threads >= 1")
    if version("ultralytics") != "8.4.203":
        raise RuntimeError("Use Ultralytics 8.4.203 for this export recipe")
    # Disable Ultralytics' ad-hoc dependency installer; uv/the Colab owner supplies dependencies.
    os.environ["YOLO_AUTOINSTALL"] = "false"
    import onnx
    import torch
    from ultralytics import YOLO

    torch.set_num_threads(args.threads)
    output.parent.mkdir(parents=True, exist_ok=True)
    source_hash = digest(source)
    with tempfile.TemporaryDirectory(prefix="obb-export-", dir=output.parent) as temporary:
        work = Path(temporary).resolve()
        if work.parent != output.parent:
            raise RuntimeError("Unexpected export workspace location")
        local = work / source.name
        shutil.copyfile(source, local)
        if digest(local) != source_hash:
            raise RuntimeError("Source checkpoint changed while being copied")
        model = YOLO(str(local))
        if model.task != "obb":
            raise RuntimeError(f"Expected OBB checkpoint, got {model.task}")
        exported = Path(
            model.export(
                format="onnx",
                imgsz=args.resolution,
                device="cpu",
                batch=1,
                dynamic=False,
                quantize=32,
                nms=False if args.head == "end2end" else None,
                simplify=False,
                opset=17,
            )
        ).resolve()
        if exported.parent != work:
            raise RuntimeError("Export escaped its temporary workspace")
        onnx.checker.check_model(str(exported))
        graph = onnx.load(str(exported), load_external_data=False)
        if any(tensor.data_location == onnx.TensorProto.EXTERNAL for tensor in graph.graph.initializer):
            raise RuntimeError("Expected a self-contained ONNX file, not external weights")
        metadata = {item.key: item.value for item in graph.metadata_props}
        is_end2end = metadata.get("end2end", "false").lower() == "true"
        if metadata.get("task") != "obb" or is_end2end != (args.head == "end2end"):
            raise RuntimeError("Exported task/head does not match the requested recipe")
        shapes = {
            item.name: [
                d.dim_value if d.HasField("dim_value") else d.dim_param for d in item.type.tensor_type.shape.dim
            ]
            for item in (*graph.graph.input, *graph.graph.output)
        }
        if (
            len(graph.graph.input) != 1
            or shapes[graph.graph.input[0].name] != [1, 3, args.resolution, args.resolution]
            or len(graph.graph.output) != 1
            or any(
                item.type.tensor_type.elem_type != onnx.TensorProto.FLOAT
                for item in (*graph.graph.input, *graph.graph.output)
            )
        ):
            raise RuntimeError("Expected one static FP32 image input and one FP32 OBB output")
        output_shape = shapes[graph.graph.output[0].name]
        if (
            len(output_shape) != 3
            or output_shape[0] != 1
            or (output_shape[-1] != 7 if is_end2end else output_shape[1] != 5 + len(model.names))
        ):
            raise RuntimeError(f"Unexpected OBB output layout: {output_shape}")
        report = {
            "source": source.name,
            "source_sha256": source_hash,
            "output": output.name,
            "output_sha256": digest(exported),
            "precision": "FP32",
            "head": args.head,
            "resolution": args.resolution,
            "ultralytics": version("ultralytics"),
            "torch": torch.__version__,
            "onnx": onnx.__version__,
            "tensor_shapes": shapes,
            "metadata": metadata,
            "acceptance": "Export and structural checks only; no ONNX inference, parity, quality or speed acceptance",
        }
        if not args.overwrite and (output.exists() or report_path.exists()):
            raise RuntimeError("Output appeared while export was running; existing file left unchanged")
        os.replace(exported, output)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Exported {output}\nTensor shapes: {shapes}\nReport: {report_path}", flush=True)


if __name__ == "__main__":
    main()

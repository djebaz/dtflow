"""CLI presentation for the explicit cloud pose experiment; ordinary session owns execution."""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from dtflow.core.model_profiles import model_key
from dtflow.core.models import TimeoutPolicy, ValidationError
from dtflow.core.pose import (
    CONTROL_MODES,
    CONTROLS,
    OFFICIAL_PROBE_CONTROLS,
    XINSIR_PROBE_INPUTS,
    SD15_POSE_PROBE_CONTROL,
    SD1_PROBE_CONTROLS,
    SDXL_CANNY_PROBE_CONTROL,
    SDXL_POSE_PROBE_CONTROL,
    SDXL_POSE_PROBE_MODELS,
    PoseControl,
)


def add_commands(commands, add_request_options):
    pose = commands.add_parser("pose", help="Body pose on DT+ Cloud, including unverified SDXL and SD 1.5 probes.")
    actions = pose.add_subparsers(dest="pose_action", required=True)
    catalogue = actions.add_parser("catalogue", help="Show pinned detector/licence and control identifiers offline.")
    catalogue.set_defaults(state=Path(".dtflow"))
    for name in ("install", "extract", "generate", "retry"):
        child = actions.add_parser(
            name,
            help={
                "install": "Explicitly download and verify the AGPL-3.0 YOLO26n-pose ONNX detector (no inference).",
                "extract": "Offline extraction only: one body (exactly one found, or chosen with --person); omit low-confidence joints, no detailed face/fingers.",
                "generate": "Explicit DT+ submission; sends pose map off-machine and may consume credits.",
                "retry": "Explicit NEW cloud submission using retained settings/map; not remote recovery (may use credits).",
            }[name],
        )
        child.add_argument("-d", "--state", type=Path, default=Path(".dtflow"))
        if name == "extract":
            child.add_argument("-i", "--image", type=Path, required=True)
            child.add_argument(
                "-o", "--out", type=Path, required=True, help="New PNG; matching .pose.json evidence is saved."
            )
            child.add_argument("-pn", "--person", type=int, help="Person number when the photo shows several people.")
        if name == "install":
            child.add_argument(
                "-a",
                "--accept",
                action="store_true",
                required=True,
                help="Explicitly accept the displayed publisher licence and download the pinned ONNX.",
            )
        if name in ("generate", "retry"):
            child.add_argument("-b", "--backend", choices=("cloud",), required=True)
            child.add_argument("-o", "--out", type=Path)
            child.add_argument("-t", "--timeout", type=float, default=600)
        if name == "retry":
            child.add_argument("-c", "--config", type=Path, required=True, help="Saved pose generation metadata JSON.")
        if name == "generate":
            child.add_argument("-c", "--config", type=Path, help="Ordinary txt2img configuration (not pose metadata).")
            add_request_options(child)
            child.set_defaults(endpoint=None, prefix=False)
            source = child.add_mutually_exclusive_group(required=True)
            source.add_argument(
                "-im", "--image", type=Path, help="Prepared pose, Canny, grayscale depth or palette-coloured semantic segmentation map."
            )
            source.add_argument(
                "-ph",
                "--photo",
                type=Path,
                help="Source photo: local pose, Canny, depth or YOLO semantic-map extraction.",
            )
            child.add_argument("-pn", "--person", type=int, help="Person number when the photo shows several people.")
            child.add_argument(
                "-co",
                "--control",
                choices=(*CONTROLS, SDXL_POSE_PROBE_CONTROL, *OFFICIAL_PROBE_CONTROLS),
                required=True,
                help="Exact control checkpoint; non-FLUX controls are CLI-only probes.",
            )
            child.add_argument(
                "-ct", "--control-type", choices=XINSIR_PROBE_INPUTS,
                help="Explicit Union mode; official controls infer their native input type.",
            )
            child.add_argument(
                "-sm", "--seg-method", choices=("yolo", "clothes"), default="yolo",
                help="Segmentation photo preprocessing: YOLO silhouettes or YOLO + configured SegFormer clothes.",
            )
            child.add_argument(
                "-dm", "--depth-model", type=Path,
                help="Local pinned Depth Anything V2 Small ONNX for depth photo extraction.",
            )
            child.add_argument(
                "-cm",
                "--control-mode",
                choices=CONTROL_MODES,
                default="Balanced",
                help="Draw Things control force: Balanced, Prompt (prompt leads) or Control (pose leads).",
            )
            child.add_argument("-st", "--start", type=float, default=0.0, help="Control start, 0..1 of the steps.")
            child.add_argument("-en", "--end", type=float, help="Control end, 0..1; defaults to 0.65 for fast-model Pose, otherwise 1.")
            child.add_argument(
                "-cs",
                "--strength",
                type=float,
                help="Control weight (0,2]; defaults to 0.9 for fast-model Pose, 0.77 for official SD 1.x Pose, otherwise required.",
            )


def prepare_canny_probe(path: Path, width: int, height: int, *, photo: bool):
    """Prepare a target-sized RGB edge map; OpenCV is optional and only needed for a source photo."""
    from PIL import Image
    from dtflow.services import image_preparation as pixels

    content = pixels.load_source_file(path)
    image = pixels._verified(content)
    fit = pixels._fit(image.size, (width, height))
    fitted = image.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.LANCZOS)
    evidence = {"origin": "canny_photo" if photo else "prepared_canny_map", "source_sha256": content.sha256}
    if photo:
        try:
            import cv2
        except ImportError as exc:
            raise ValidationError(
                "Canny photo extraction needs OpenCV; run with uv run --with opencv-python-headless==4.11.0.86."
            ) from exc
        import numpy as np

        # Replicate the source boundary before extracting edges; black photo padding
        # would introduce a synthetic rectangular contour.
        gray = cv2.cvtColor(np.asarray(fitted.convert("RGB")), cv2.COLOR_RGB2GRAY)
        gray = cv2.copyMakeBorder(
            gray, fit.top, height - fit.top - fit.fitted_height,
            fit.left, width - fit.left - fit.fitted_width, cv2.BORDER_REPLICATE,
        )
        edges = cv2.Canny(gray, 100, 200)
        canvas = Image.fromarray(edges).convert("RGB")
        evidence.update({"canny_low": "100", "canny_high": "200", "opencv_version": cv2.__version__})
    else:
        canvas = Image.new("RGB", (width, height), "black")
        canvas.paste(fitted.convert("RGB"), (fit.left, fit.top))
    if canvas.getbbox() is None:
        raise ValidationError("Canny map is empty; no guidance was submitted.")
    map_content = pixels._content(canvas)
    evidence["map_sha256"] = map_content.sha256
    evidence["preparation"] = (
        "aspect_fit_edge_replicated_photo_before_canny" if photo else "aspect_fit_black_padding_no_crop"
    )
    return pixels.prepare_image_bytes(map_content.encoded, width, height), tuple(sorted(evidence.items()))


def prepare_spatial_probe(path: Path, width: int, height: int, *, input_type: str, photo: bool, depth_model: Path | None, state: Path, seg_method: str = "yolo"):
    """Fit depth or semantic maps; preserve class colours with nearest-neighbour resampling."""
    import hashlib

    import numpy as np
    from PIL import Image

    from dtflow.services import image_preparation as pixels

    content = pixels.load_source_file(path)
    image = pixels._verified(content)
    fit = pixels._fit(image.size, (width, height))
    evidence = {"origin": f"prepared_{input_type}_map", "source_sha256": content.sha256}
    if input_type == "depth":
        if photo:
            import onnxruntime as ort

            model = depth_model or Path("tmp/models/depth-anything-v2-small.onnx")
            expected = "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c"
            if not model.is_file():
                raise ValidationError("Depth Anything V2 Small ONNX is missing; provide --depth-model or a prepared depth map.")
            with model.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != expected:
                raise ValidationError("Depth model differs from the pinned Depth Anything V2 Small checkpoint.")
            session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
            rgb = np.asarray(image.resize((518, 518), Image.Resampling.BICUBIC), dtype=np.float32) / 255
            rgb = (rgb - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
                [0.229, 0.224, 0.225], dtype=np.float32
            )
            tensor = np.transpose(rgb, (2, 0, 1))[None].astype(np.float32)
            depth = np.asarray(session.run(None, {session.get_inputs()[0].name: tensor})[0]).squeeze()
            if depth.ndim != 2 or not np.isfinite(depth).all() or depth.max() <= depth.min():
                raise ValidationError("Depth model did not produce a finite nonconstant depth map.")
            image = Image.fromarray(np.rint((depth - depth.min()) / (depth.max() - depth.min()) * 255).astype(np.uint8))
            evidence.update({"origin": "depth_photo", "detector": "Depth Anything V2 Small ONNX", "detector_sha256": digest})
        else:
            image = image.convert("L")
        fitted = image.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.BILINEAR).convert("RGB")
    else:
        if photo:
            from dtflow.persistence.person_models import load_person_models
            from dtflow.services.person_selection import control_segmentation

            models = load_person_models(state)
            model = models.segmentation
            if model is None:
                raise ValidationError("Choose or install the YOLO segmentation model in Inpaint first, or use --image.")
            clothes = models.clothes if seg_method == "clothes" else None
            if seg_method == "clothes" and clothes is None:
                raise ValidationError("Choose the existing SegFormer clothes model in Inpaint first.")
            image, count = control_segmentation(content, model, clothes)
            if clothes is not None:
                evidence.update({"clothes_sha256": clothes.sha256, "clothes_model": "Configured SegFormer human parsing"})
            evidence.update({
                "origin": "seg_photo",
                "detector": "Configured YOLO segmentation",
                "detector_sha256": model.sha256,
                "instances": str(count),
                "palette": "ADE20K sparse COCO overlap; unknown black",
                "seg_method": seg_method,
            })
        fitted = image.convert("RGB").resize((fit.fitted_width, fit.fitted_height), Image.Resampling.NEAREST)
    if input_type == "depth":
        # Continue the depth field outside the fitted photo without a false depth discontinuity.
        canvas = Image.fromarray(np.pad(
            np.asarray(fitted),
            ((fit.top, height - fit.top - fit.fitted_height),
             (fit.left, width - fit.left - fit.fitted_width), (0, 0)),
            mode="edge",
        ))
        preparation = "aspect_fit_edge_replicated_depth_no_crop"
    else:
        canvas = Image.new("RGB", (width, height), "black")
        canvas.paste(fitted, (fit.left, fit.top))
        preparation = "aspect_fit_black_padding_no_crop"
    map_content = pixels._content(canvas)
    evidence.update({"map_sha256": map_content.sha256, "preparation": preparation})
    return pixels.prepare_image_bytes(map_content.encoded, width, height), tuple(sorted(evidence.items()))


async def run(args):
    from dtflow.cli import resolve_generation
    from dtflow.core.request_records import request_from_record
    from dtflow.services.body_pose import DETECTOR, extract_body, install_detector, prepare_pose, save_extraction
    from dtflow.services.config import read_json_file
    from dtflow.services.session import (
        _require_pose_family,
        generate_pose_once,
        pose_request,
        retry_pose_once,
        selection_for,
    )

    def progress(event):
        if event.kind in ("preparing", "progress", "terminal"):
            print(f"[cloud] {event.kind}: {event.stage}", file=sys.stderr)

    if args.pose_action == "catalogue":
        print(
            json.dumps(
                {
                    "controls": CONTROLS,
                    "sdxl_probe": {
                        "control": SDXL_POSE_PROBE_CONTROL,
                        "models": SDXL_POSE_PROBE_MODELS,
                        "cloud_application": "unverified; CLI-only feasibility probe",
                    },
                    "sdxl_canny_probe": {"control": SDXL_CANNY_PROBE_CONTROL, "models": OFFICIAL_PROBE_CONTROLS[SDXL_CANNY_PROBE_CONTROL][1]},
                    "sd15_pose_probe": {
                        "control": SD15_POSE_PROBE_CONTROL,
                        "family": "sd_1",
                        "models": "catalogue text-to-image checkpoints",
                        "cloud_application": "unverified in DTFlow; CLI-only feasibility probe",
                    },
                    "official_probes": OFFICIAL_PROBE_CONTROLS,
                    "sd1_family_controls": SD1_PROBE_CONTROLS,
                    "xinsir_inputs": XINSIR_PROBE_INPUTS,
                    "detector": asdict(DETECTOR),
                    "cloud_application": "unverified; requires Echo-listed control and owner smoke",
                    "selection": "one body (exactly one found, or --person); incomplete torso refused; missing joints omitted",
                    "fit": "aspect preserving, centered black padding; no crop",
                },
                indent=2,
            )
        )
        return 0
    if args.pose_action == "install":
        report = await asyncio.to_thread(
            install_detector, args.state, lambda stage, done, total: print(f"{stage}: {done}/{total}", file=sys.stderr)
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.pose_action == "extract":
        content, evidence = await asyncio.to_thread(extract_body, args.image, args.state, args.person)
        print(json.dumps(await asyncio.to_thread(save_extraction, args.out, content, evidence), indent=2))
        return 0
    policy = TimeoutPolicy(total=args.timeout)
    if args.pose_action == "retry":
        record = read_json_file(args.config, "Pose generation metadata")
        if record.get("backend_id") != "drawthings-cloud":
            raise ValidationError("Retry requires DT+ pose metadata; no backend substitution.")
        request = request_from_record(record["request"])
        if request.pose_control is None:
            raise ValidationError("Retry requires saved pose control settings.")
    else:
        native = OFFICIAL_PROBE_CONTROLS.get(args.control)
        input_type = args.control_type or (native[0] if native else "pose")
        request = resolve_generation(args)
        key = model_key(request.model_id)
        fast_pose = input_type == "pose" and (
            (key == "sd_xl_turbo" and args.control == SDXL_POSE_PROBE_CONTROL)
            or (key in ("flux_1_schnell", "pixelwave_flux_1_schnell_04")
                and args.control in CONTROLS and "_2.0_" in args.control)
        )
        sd15_pose = input_type == "pose" and (
            args.control == SD15_POSE_PROBE_CONTROL
        )
        default_strength = 0.9 if fast_pose else (0.77 if sd15_pose else None)
        if args.strength is None and default_strength is None:
            raise ValidationError("--strength is required outside the fast-model and official SD 1.x Pose presets.")
        control = PoseControl(
            args.control,
            args.strength if args.strength is not None else default_strength,
            input_type=input_type,
            control_mode=args.control_mode,
            guidance_start=args.start,
            guidance_end=args.end if args.end is not None else (0.65 if fast_pose else 1.0),
        )
        # Validate the checkpoint family before reading a photograph or running ONNX.
        _require_pose_family(args.state, request.model_id, control_checkpoint=control.checkpoint)
        if args.seg_method != "yolo" and (control.input_type != "seg" or args.photo is None):
            raise ValidationError("--seg-method clothes requires a source photo and Segmentation control.")
        if args.depth_model is not None and (control.input_type != "depth" or args.photo is None):
            raise ValidationError("--depth-model is only used with a source photo and Depth control.")
        if control.input_type != "pose" and args.person is not None:
            raise ValidationError("Person selection is only used for pose extraction.")
        if control.input_type in ("depth", "seg"):
            prepared, evidence = await asyncio.to_thread(
                prepare_spatial_probe,
                args.photo or args.image,
                request.width_px,
                request.height_px,
                input_type=control.input_type,
                photo=args.photo is not None,
                depth_model=args.depth_model,
                state=args.state,
                seg_method=args.seg_method,
            )
        elif control.input_type == "canny":
            prepared, evidence = await asyncio.to_thread(
                prepare_canny_probe,
                args.photo or args.image,
                request.width_px,
                request.height_px,
                photo=args.photo is not None,
            )
        else:
            prepared, evidence = await asyncio.to_thread(
                prepare_pose,
                args.photo or args.image,
                request.width_px,
                request.height_px,
                photo=args.photo is not None,
                state=args.state,
                person=args.person,
            )
        request = pose_request(request, control, prepared, evidence)
    selection = selection_for("cloud", None, args.state)
    options = {
        "output": args.out,
        "timeouts": policy,
        "on_event": progress,
        "asset_ids": getattr(args, "asset", ()) or (),
    }
    if args.pose_action == "retry":
        # No implicit replay: a new explicit submission, same retained input and seed, new request identity.
        outcome = await retry_pose_once(selection, args.state, request, **options)
    else:
        outcome = await generate_pose_once(selection, args.state, request, prepared, **options)
    print(
        json.dumps(
            {
                "attempt_id": outcome.attempt_id,
                "backend": outcome.backend_id,
                "outputs": outcome.output_paths,
                "metadata": outcome.metadata_path,
                "warnings": outcome.warnings,
                f"{request.pose_control.input_type}_adherence": "requires owner observation",
            },
            indent=2,
        )
    )
    return 0

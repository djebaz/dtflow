"""CLI presentation for the explicit cloud pose experiment; ordinary session owns execution."""

import asyncio
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from dtflow.core.image_inputs import ImageInputSnapshot
from dtflow.core.models import TimeoutPolicy, ValidationError
from dtflow.core.pose import CONTROLS, PoseControl


def add_commands(commands, add_request_options):
    pose = commands.add_parser("pose", help="Experimental FLUX.1 Dev body pose; cloud application is unverified.")
    actions = pose.add_subparsers(dest="pose_action", required=True)
    catalogue = actions.add_parser("catalogue", help="Show pinned detector/licence and control identifiers offline.")
    catalogue.set_defaults(state=Path(".dtflow"))
    for name in ("install", "extract", "generate", "retry"):
        child = actions.add_parser(name, help={
            "install": "Explicitly download and verify the AGPL-3.0 YOLO26n-pose ONNX detector (no inference).",
            "extract": "Offline extraction only: one body (exactly one found, or chosen with --person); omit low-confidence joints, no detailed face/fingers.",
            "generate": "Explicit DT+ submission; sends pose map off-machine and may consume credits.",
            "retry": "Explicit NEW cloud submission using retained settings/map; not remote recovery (may use credits).",
        }[name])
        child.add_argument("-d", "--state", type=Path, default=Path(".dtflow"))
        if name == "extract":
            child.add_argument("-i", "--image", type=Path, required=True)
            child.add_argument("-o", "--out", type=Path, required=True, help="New PNG; matching .pose.json evidence is saved.")
            child.add_argument("-pn", "--person", type=int, help="Person number when the photo shows several people.")
        if name == "install":
            child.add_argument("-a", "--accept", action="store_true", required=True,
                               help="Explicitly accept the displayed publisher licence and download the pinned ONNX.")
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
            source.add_argument("-im", "--image", type=Path, help="Prepared OpenPose RGB body map, black background.")
            source.add_argument("-ph", "--photo", type=Path, help="Explicit local ONNX extraction before submission.")
            child.add_argument("-pn", "--person", type=int, help="Person number when the photo shows several people.")
            child.add_argument("-co", "--control", choices=CONTROLS, required=True)
            child.add_argument("-cs", "--strength", type=float, required=True,
                               help="Control weight (0,2], NOT img2img denoising or text guidance; full step range, Balanced.")


async def run(args):
    from dtflow.cli import resolve_generation
    from dtflow.core.request_records import request_from_record
    from dtflow.services.body_pose import DETECTOR, extract_body, install_detector, prepare_pose, save_extraction
    from dtflow.services.config import read_json_file
    from dtflow.services.image_inputs import restore_image_inputs
    from dtflow.services.session import bind_prepared, open_session, run_generation, selection_for

    def progress(event):
        if event.kind in ("preparing", "progress", "terminal"):
            print(f"[cloud] {event.kind}: {event.stage}", file=sys.stderr)

    if args.pose_action == "catalogue":
        print(json.dumps({"controls": CONTROLS, "detector": asdict(DETECTOR),
                          "cloud_application": "unverified; requires Echo-listed control and owner smoke",
                          "selection": "one body (exactly one found, or --person); incomplete torso refused; missing joints omitted",
                          "fit": "aspect preserving, centered black padding; no crop"}, indent=2))
        return 0
    if args.pose_action == "install":
        report = await asyncio.to_thread(install_detector, args.state,
                                        lambda stage, done, total: print(f"{stage}: {done}/{total}", file=sys.stderr))
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
        prepared = await asyncio.to_thread(restore_image_inputs, args.state, request.image_inputs)
        # No implicit replay: this command is a new explicit submission, same immutable input and seed.
        request = replace(request, request_id=uuid4().hex)
    else:
        control = PoseControl(args.control, args.strength)
        request = resolve_generation(args)
        # Validate exact checkpoint before reading a photograph or running ONNX.
        from dtflow.core.pose import MODELS

        if request.model_id not in MODELS:
            raise ValidationError("Pose is restricted to exact FLUX.1 Dev f16/q5p/q8p checkpoints.")
        prepared, evidence = await asyncio.to_thread(
            prepare_pose, args.photo or args.image, request.width_px, request.height_px,
            photo=args.photo is not None, state=args.state, person=args.person,
        )
        request = replace(request, schema_version=3, pose_control=replace(control, evidence=evidence),
                          image_inputs=ImageInputSnapshot.from_prepared(uuid4().hex, prepared, preserve_outside=False))
    request = await bind_prepared(args.state, "cloud", request, getattr(args, "asset", ()) or ())
    selection = selection_for("cloud", None, args.state)
    async with open_session(selection, args.state, output=args.out, timeouts=policy, pose_inputs=True) as service:
        outcome = await run_generation(service, request, prepared_inputs=prepared, on_event=progress)
    print(json.dumps({"attempt_id": outcome.attempt_id, "backend": outcome.backend_id,
                      "outputs": outcome.output_paths, "metadata": outcome.metadata_path,
                      "warnings": outcome.warnings, "pose_adherence": "requires owner observation"}, indent=2))
    return 0

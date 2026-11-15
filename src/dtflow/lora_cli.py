"""Presentation for the local-only experimental LoRA storage commands."""

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

from dtflow.diagnostics import asset_ref_data, event

log = logging.getLogger(__name__)


def _revision(value):
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        raise argparse.ArgumentTypeError("Revision must be a 7..40 character hexadecimal Git commit.")
    return value.lower()


def add_commands(commands):
    group = commands.add_parser("lora", help="Local-only experimental LoRA CKPT inspection and q8p encoding.")
    actions = group.add_subparsers(dest="lora_command", required=True)
    for action in ("inspect", "compare", "quantize"):
        child = actions.add_parser(
            action,
            help={
                "inspect": "Validate raw F16/q8p/ezm7 CKPT payloads; never uploads.",
                "compare": "Compare decoded tensor values against another CKPT; never generates.",
                "quantize": "EXPERIMENTAL: encode a raw F16 CKPT into a separate q8p/ezm7 file.",
            }[action],
        )
        child.add_argument("input", type=Path, help="Closed standalone Draw Things LoRA .ckpt file.")
        child.add_argument("-r", "--revision", type=_revision, help="Record the checkout commit in the JSON report.")
        if action == "compare":
            child.add_argument("reference", type=Path, help="Matching tensor inventory, e.g. an iOS q8p reference.")
        if action == "quantize":
            child.add_argument("-o", "--out", type=Path, required=True, help="New .ckpt path in an existing directory.")
            child.add_argument(
                "-e",
                "--experimental",
                action="store_true",
                required=True,
                help="Explicitly acknowledge the unverified experimental writer.",
            )


def run_command(args):
    # Lazy: normal GUI and other CLI commands do not import optional clustering code.
    from dtflow.services.lora_q8p_codec import QuantizationError
    from dtflow.services.lora_quantization import compare_files, inspect_file, quantize_file

    action = args.lora_command
    event(log, "lora_quantize.started", data={"action": action})
    previous = 0.0

    def progress(done, total):
        nonlocal previous
        now = time.monotonic()
        if now - previous >= 1 or done == total:
            print(f"Quantizing: {done}/{total} blocks", file=sys.stderr, flush=True)
            previous = now

    try:
        if action == "inspect":
            result = inspect_file(args.input)
        elif action == "compare":
            result = compare_files(args.input, args.reference)
        else:
            result = quantize_file(args.input, args.out, progress)
        result["recorded_revision"] = args.revision or "unknown"
        source = result["input"]
        event(
            log,
            "lora_quantize.completed",
            data={
                "action": action,
                **asset_ref_data(source_sha256=source["sha256"], sha256=result.get("output", {}).get("sha256")),
                "tensors": source["tensors"],
                "input_bytes": source["file_bytes"],
                **(
                    {"output_bytes": result["output"]["file_bytes"], "seconds": result["seconds"]}
                    if action == "quantize"
                    else {}
                ),
            },
        )
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        event(log, "lora_quantize.interrupted", level=logging.WARNING, data={"action": action})
        print(
            "Local experiment interrupted. Check the requested destination before retrying; no provider work started.",
            file=sys.stderr,
        )
        return 130
    except (QuantizationError, OSError, sqlite3.Error, ValueError, MemoryError) as exc:
        code = (
            exc.code
            if isinstance(exc, QuantizationError)
            else ("resource_limit" if isinstance(exc, MemoryError) else "local_or_container_error")
        )
        event(log, "lora_quantize.failed", level=logging.WARNING, data={"action": action, "code": code})
        message = str(exc) if isinstance(exc, QuantizationError) else "Could not process the local CKPT or output."
        print(json.dumps({"error": code, "message": message, "remote_outcome": "not_started"}), file=sys.stderr)
        return 2 if isinstance(exc, QuantizationError) else 5

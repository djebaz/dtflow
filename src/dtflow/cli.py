"""Headless command line: parsing and presentation only; orchestration lives in the shared services."""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, replace
from pathlib import Path

from dtflow import startup
from dtflow.build import display_identity, identity
from dtflow.composition import BACKENDS, image_endpoint_label
from dtflow.core.gallery import MAX_GALLERY_LIMIT, SORTS, GalleryQuery
from dtflow.core.image_operations import ImageOperationPermit
from dtflow.core.model_profiles import exact_model_description_digest
from dtflow.core.models import (
    BackendError,
    ErrorCategory,
    LoRASelection,
    RemoteOutcome,
    ResolvedGenerationRequest,
    Sampler,
    TimeoutPolicy,
    ValidationError,
)
from dtflow.diagnostics import event as diagnostic_event
from dtflow.gallery_limits import add_arguments as add_gallery_limit_arguments
from dtflow.gallery_limits import resolve as resolve_gallery_limits
from dtflow.logging_setup import LoggingSetupError, configure_logging
from dtflow.persistence.assets import AssetRegistry
from dtflow.persistence.queues import post_control, read_queues
from dtflow.persistence.settings import load_cloud_concurrency, save_cloud_concurrency
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.services.artifacts import atomic_write
from dtflow.services.config import export_config, load_config, resolve_request
from dtflow.services.gallery import inspect_output, query_outputs
from dtflow.services.planning import Plan, describe_plan, expand_plan, load_plan, with_rng_seed
from dtflow.services.wildcards import bind_wildcards
from dtflow.services.queue_session import create_queue, dispatch_queue, relaunch_queue
from dtflow.services.session import (
    ASSET_NOTE,
    asset_view,
    bind_prepared,
    generate_image_files_once,
    open_session,
    prepare_asset,
    reconcile_asset,
    remember_inventory,
    run_generation,
    selection_for,
)

EXIT_OK = 0
EXIT_INVALID = 2  # invalid input or missing credentials: nothing was dispatched
EXIT_FAILED = 3  # backend rejected or failed with a known outcome
EXIT_UNKNOWN = 4  # remote outcome unknown; never replayed automatically
EXIT_LOCAL = 5  # local state, output, logging-independent I/O or deadline failure
EXIT_INTERRUPTED = 130
KINDS = ("model", "lora", "control", "other", "unknown")

log = logging.getLogger("dtflow.cli")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="DTFlow headless CLI. Results are JSON on stdout; progress and diagnostics go to stderr."
    )
    command.add_argument("-v", "--version", action="version", version=display_identity())
    command.add_argument("-x", "--dev", action="store_true", help="Add sanitized diagnostic detail to the log file.")
    command.add_argument("-q", "--logs", type=Path, help="Log directory (default: repository logs/ or per-user data).")
    add_gallery_limit_arguments(command)
    commands = command.add_subparsers(dest="command", required=True)
    gui = commands.add_parser("gui", help=COMMAND_HELP["gui"])
    gui.add_argument(
        "-nc", "--no-cache", action="store_true", help="Bypass disposable cache reads and writes for this run."
    )
    add_gallery_limit_arguments(gui)
    gui.add_argument(
        "-format",
        "--format",
        choices=("jpg", "webp"),
        default="jpg",
        help="Developer thumbnail-cache format: jpg (quality 85, default) or webp (quality 80).",
    )
    testing = commands.add_parser("test", help="Run repository tests through pytest.")
    testing.add_argument("-all", "--all", action="store_true", help="Run offline tests, optionally with live groups.")
    testing.add_argument("-live", "--live", action="store_true", help="Run non-generative live connectivity checks.")
    testing.add_argument(
        "-gen", "--generate", action="store_true", help="Run live generation smoke tests (may use credits)."
    )
    testing.add_argument(
        "-local", "--local", action="store_true", help="Limit live checks to the local Draw Things server."
    )
    testing.add_argument("-cloud", "--cloud", action="store_true", help="Limit live checks to DT+ cloud.")
    for name in ("generate", "check", "models", "attempts", "settings"):
        child = commands.add_parser(name, help=COMMAND_HELP[name])
        child.add_argument(
            "-d",
            "--state",
            type=Path,
            default=Path(".dtflow"),
            help="Local execution state directory (default: .dtflow).",
        )
        if name in ("generate", "check", "models"):
            child.add_argument(
                "-b",
                "--backend",
                choices=BACKENDS,
                required=True,
                help="Explicit backend: fake (offline fixture), local (Draw Things gRPC) or cloud (DT+). No fallback.",
            )
            child.add_argument(
                "-e",
                "--endpoint",
                help="Local Draw Things HOST:PORT. The shared secret comes from DRAWTHINGS_SHARED_SECRET.",
            )
            child.add_argument(
                "-nc",
                "--no-cache",
                action="store_true",
                help="Bypass disposable cache reads and writes for this run.",
            )
        if name == "models":
            child.add_argument("-r", "--refresh", action="store_true", help="Refresh the inventory.")
            child.add_argument("-k", "--kind", choices=KINDS, help="Only list entries of this kind.")
        if name == "attempts":
            child.add_argument("-a", "--attempt", help="Inspect a single local attempt; never resubmit it.")
        if name == "settings":
            child.add_argument(
                "-j",
                "--jobs",
                type=int,
                help="Persist the cloud concurrency maximum (1-10, default 4; not entitlement).",
            )
        if name == "generate":
            child.add_argument("-c", "--config", type=Path, help="Versioned generation config or prior metadata.json.")
            child.add_argument("-u", "--export", type=Path, help="Write the effective credential-free config here.")
            add_request_options(child)
            child.add_argument(
                "-r",
                "--prefix",
                action="store_true",
                help="Prepend selected LoRAs' server trigger prefixes to the prompt.",
            )
            child.add_argument("-o", "--out", type=Path, help="Final output root (default: state directory/outputs).")
            add_scenario_option(child)
            child.add_argument("-t", "--timeout", type=float, default=600.0, help="Total backend deadline in seconds.")
    add_edit_command(commands)
    add_queue_commands(commands)
    add_asset_commands(commands)
    add_gallery_commands(commands)
    return command


def add_gallery_commands(commands) -> None:
    """Read-only saved outputs through the same bounded query service as Images."""
    gallery = commands.add_parser("gallery", help="Browse committed saved outputs offline; never generates.")
    actions = gallery.add_subparsers(dest="gallery_command", required=True)
    for name in ("list", "show"):
        child = actions.add_parser(
            name, help="Query saved outputs." if name == "list" else "Inspect one output's safe metadata."
        )
        child.add_argument("-d", "--state", type=Path, default=Path(".dtflow"))
        if name == "show":
            child.add_argument("identity", help="Stable output identity from gallery list.")
            continue
        child.add_argument("-p", "--prompt", default="", help="Case-insensitive literal positive-prompt substring.")
        child.add_argument("-n", "--negative", default="", help="Case-insensitive literal negative-prompt substring.")
        child.add_argument("-m", "--model", default="", help="Exact recorded model identifier.")
        child.add_argument("-l", "--lora", default="", help="Exact recorded LoRA identifier.")
        child.add_argument("-s", "--since", help="Inclusive UTC date or offset-aware ISO timestamp.")
        child.add_argument("-u", "--until", help="Exclusive UTC date or offset-aware ISO timestamp.")
        child.add_argument("-r", "--ratio", default="", help="square, portrait, landscape or exact W:H.")
        child.add_argument("-w", "--width", type=int, help="Exact width in pixels.")
        child.add_argument("-y", "--height", type=int, help="Exact height in pixels.")
        child.add_argument("-o", "--sort", choices=SORTS, default="newest")
        child.add_argument("-k", "--limit", type=int, default=100, help=f"Page size in 1..{MAX_GALLERY_LIMIT}.")
        child.add_argument("-f", "--offset", type=int, default=0)


async def run_gallery(args: argparse.Namespace) -> int:
    if args.gallery_command == "show":
        detail = await inspect_output(args.state, args.identity)
        print(
            json.dumps(
                {"output": asdict(detail.item), "metadata": json.loads(detail.metadata_json), "issue": detail.issue},
                indent=2,
            )
        )
    else:
        query = GalleryQuery(**{name: getattr(args, name) for name in GalleryQuery.__dataclass_fields__})
        page = await query_outputs(args.state, query)
        print(json.dumps(asdict(page), indent=2))
    return EXIT_OK


def add_edit_command(commands) -> None:
    child = commands.add_parser(
        "edit", help="Single-shot owner probe for img2img or mask-file inpaint; not acceptance."
    )
    child.add_argument("-b", "--backend", choices=("local", "cloud"), required=True)
    child.add_argument("-e", "--endpoint", help="Local Draw Things HOST:PORT.")
    child.add_argument(
        "-c",
        "--bridge",
        action="store_true",
        help="Explicit Local-to-DT+ bridge: allow an unlisted checkpoint (local backend only; may consume credits).",
    )
    child.add_argument("-d", "--state", type=Path, default=Path(".dtflow"))
    add_request_options(child, image_probe=True)
    child.set_defaults(config=None, lora=None)
    child.add_argument("-src", "--source", type=Path, required=True, help="Source image; never modified.")
    child.add_argument("-mk", "--mask", type=Path, help="Same-size mask; white edits, black preserves.")
    child.add_argument("-st", "--strength", type=float, default=0.75, help="Strength in 0..1 (default: 0.75).")
    child.add_argument(
        "-gr", "--grow", type=int, default=0, help="Signed source-pixel grow/shrink (default: 0; bounded)."
    )
    child.add_argument("-f", "--feather", type=float, default=0.0, help="Inward feather in source pixels (default: 0).")
    child.add_argument(
        "-k", "--keep", choices=("on", "off"), default="on", help="Keep prepared pixels outside mask (default: on)."
    )
    child.add_argument("-o", "--out", type=Path, help="Final output root (default: state directory/outputs).")
    child.add_argument("-t", "--timeout", type=float, default=600.0, help="Total backend deadline in seconds.")
    child.add_argument(
        "-u", "--evidence", required=True, help="User-chosen path-free evidence ID for this owner probe."
    )
    child.add_argument(
        "-r",
        "--probe",
        action="store_true",
        help="Explicit one-run consent: sends images off-machine; cloud may consume credits.",
    )


def add_request_options(child: argparse.ArgumentParser, *, image_probe: bool = False) -> None:
    """Base request parameters; the bounded image probe excludes LoRA/asset options."""
    child.add_argument("-p", "--prompt")
    child.add_argument("-n", "--negative")
    child.add_argument("-m", "--model", help="Exact model identifier (required for local/cloud).")
    child.add_argument("-s", "--seed", type=int)
    child.add_argument("-w", "--width", type=int)
    child.add_argument("-y", "--height", type=int)
    child.add_argument("-i", "--steps", type=int)
    child.add_argument("-g", "--guidance", type=float)
    child.add_argument("-a", "--sampler", choices=tuple(x.value for x in Sampler))
    if image_probe:
        return
    child.add_argument(
        "-l", "--lora", action="append", metavar="ID:WEIGHT", help="Exact LoRA identifier and explicit weight."
    )
    child.add_argument(
        "-z",
        "--asset",
        action="append",
        metavar="ASSET_ID",
        help="Bind a confirmed local cloud asset receipt (cloud only) to the --lora of the same identifier. "
        "Never uploads; the reference is revalidated before dispatch.",
    )


def add_asset_commands(commands) -> None:
    assets = commands.add_parser("assets", help=COMMAND_HELP["assets"])
    actions = assets.add_subparsers(dest="asset_command", required=True)

    def action(name: str, help_text: str, *, ident: bool = False) -> argparse.ArgumentParser:
        child = actions.add_parser(name, help=help_text, description=help_text)
        child.add_argument(
            "-d",
            "--state",
            type=Path,
            default=Path(".dtflow"),
            help="Local execution state directory (default: .dtflow).",
        )
        if ident:
            child.add_argument("asset", help="Local asset identifier from `assets prepare` or `assets list`.")
        return child

    prepare = action(
        "prepare",
        "Prepare one converted LoRA on DT+. May UPLOAD file bytes; the only command that does. Cloud only.",
    )
    prepare.add_argument("-f", "--file", type=Path, required=True, help="Already-converted Draw Things LoRA file.")
    prepare.add_argument("-n", "--name", help="Identifier to use instead of the file name.")
    action("list", "List locally known receipts and unresolved operations (local registry; no network).")
    action("show", "Show one local record (no network).", ident=True)
    action(
        "reconcile",
        "Metadata-only server check of one local record under the current credential. Never uploads.",
        ident=True,
    )


def add_scenario_option(child: argparse.ArgumentParser) -> None:
    child.add_argument(
        "-f",
        "--scenario",
        choices=("success", "reject", "lost", "empty", "terminal_error"),
        default="success",
        help="Explicit fake fault scenario (fake backend only); never contacts a server.",
    )


def add_queue_commands(commands) -> None:
    queue = commands.add_parser("queue", help=COMMAND_HELP["queue"])
    canonical = ("add", "list", "show", "start", "pause", "resume", "cancel", "cap", "relaunch")
    actions = queue.add_subparsers(dest="queue_command", required=True, metavar="{" + ",".join(canonical) + "}")

    def action(name: str, help_text: str, *, ident: bool = True) -> argparse.ArgumentParser:
        child = actions.add_parser(name, help=help_text)
        child.add_argument(
            "-d",
            "--state",
            type=Path,
            default=Path(".dtflow"),
            help="Local execution state directory (default: .dtflow).",
        )
        if ident:
            child.add_argument("queue", help="Queue identifier from `queue add` or `queue list`.")
        return child

    add = action("add", "Persist a paused queue of resolved items; contacts no backend.", ident=False)
    add.add_argument("-b", "--backend", choices=BACKENDS, required=True, help="Explicit backend for the whole queue.")
    add.add_argument("-e", "--endpoint", help="Local Draw Things HOST:PORT.")
    add.add_argument("-c", "--config", type=Path, help="Versioned generation config or prior metadata.json.")
    add_request_options(add)
    add.add_argument(
        "-k",
        "--count",
        type=int,
        help="Number of independent items; item N uses seed + N - 1 (default 1, at most 1000).",
    )
    add.add_argument(
        "-r",
        "--plan",
        type=Path,
        help="Versioned queue plan (seed, pair or cross batch); replaces --count. See the README for the file format.",
    )
    add.add_argument(
        "--rng-seed",
        type=int,
        help="Seed for a plan's random dimensions and prompt sample (default: the plan's own, else a fresh one, printed).",
    )
    add.add_argument(
        "--wildcards-dir",
        type=Path,
        help="Folder of wildcard lists NAME.txt for __NAME__ in a prompt variation (default: STATE/wildcards).",
    )
    add.add_argument(
        "-u",
        "--dry",
        action="store_true",
        help="Print the exact item table and count for --plan or --count, then stop; creates nothing.",
    )
    add.add_argument(
        "-j", "--jobs", type=int, help="Queue concurrency maximum, 1-10 (default: the cloud setting, else 4)."
    )
    add_scenario_option(add)
    action("list", "List queues with item state counts.", ident=False)
    action("show", "Show one queue's items in queue order, with attempts, outputs and recorded events.")
    start = action(
        "start", "Dispatch a queue in this process (the single dispatch owner) until it finishes or is cancelled."
    )
    start.add_argument("-o", "--out", type=Path, help="Final output root (default: state directory/outputs).")
    start.add_argument("-t", "--timeout", type=float, default=600.0, help="Total backend deadline per item in seconds.")
    action("pause", "Stop new dispatch; active attempts finish. Applied by the dispatcher or the next start.")
    action("resume", "Allow dispatch again.")
    action(
        "cancel", "Cancel pending items and interrupt local waiting on active ones (remote cancel is not confirmed)."
    )
    # Hidden compatibility alias: unknown outcomes no longer block, so there is nothing left to override.
    action("override", "Deprecated no-op.")
    actions._choices_actions = [a for a in actions._choices_actions if a.dest in canonical]
    cap = action("cap", "Change this queue's concurrency maximum; active work is never cancelled.")
    cap.add_argument(
        "-j", "--jobs", type=int, required=True, help="New maximum, 1-10 (the backend's own limit still applies)."
    )
    relaunch = action(
        "relaunch",
        "Persist a new paused queue from this queue's recorded items; the original and its attempts are untouched.",
    )
    relaunch.add_argument(
        "-n", "--new-seeds", action="store_true", help="Draw new seeds; items that shared a seed share a new one."
    )
    relaunch.add_argument(
        "-f", "--failed", action="store_true", help="Only items that failed, were cancelled or have no result."
    )
    relaunch.add_argument(
        "-i",
        "--item",
        type=int,
        action="append",
        metavar="N",
        help="Only item N (queue position; repeatable). Retry an item this way.",
    )
    relaunch.add_argument("-j", "--jobs", type=int, help="Concurrency maximum, 1-10 (default: the original queue's).")


COMMAND_HELP = {
    "gui": "Launch the Qt shell (connection selection, status and API-key management).",
    "generate": "Generate one text-to-image result and save it with reproducibility metadata.",
    "check": "Bounded non-generative connectivity/authentication check.",
    "models": "List backend inventory with provenance (models, LoRAs, controls, unknown entries).",
    "attempts": "Inspect durable attempts; unknown outcomes are never replayed.",
    "settings": "Show or persist shared settings such as the cloud concurrency maximum.",
    "assets": "Cloud LoRA assets: prepare (may upload), list, show, reconcile. Local receipts are not remote inventory.",
    "queue": "Durable queues with rolling dispatch: add, list, show, start, pause, resume, cancel, cap, relaunch.",
}


def parse_loras(values: list[str] | None) -> tuple[LoRASelection, ...] | None:
    if not values:
        return None
    selections = []
    for value in values:
        identifier, separator, weight = value.rpartition(":")
        try:
            selections.append(LoRASelection(identifier, float(weight)))
        except ValueError:
            raise ValidationError(f"LoRA {value!r} must be ID:WEIGHT with an explicit numeric weight.") from None
        if not separator:
            raise ValidationError(f"LoRA {value!r} must be ID:WEIGHT with an explicit numeric weight.")
    return tuple(selections)


def resolve_generation(args: argparse.Namespace) -> ResolvedGenerationRequest:
    """Resolve config plus explicit overrides into one validated request, before any state or backend is opened."""
    config = load_config(args.config) if args.config else None
    overrides = {
        "prompt": args.prompt,
        "negative_prompt": args.negative,
        "model": args.model,
        "seed": args.seed,
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "guidance": args.guidance,
        "sampler": args.sampler,
        "loras": parse_loras(args.lora),
    }
    request = resolve_request(
        config,
        overrides,
        default_model="fake-fixture" if args.backend == "fake" else None,
        config_name=args.config.name if args.config else None,
    )
    return replace(request, resolution_evidence=(*request.resolution_evidence, ("source", "cli")))


async def bind_assets(args: argparse.Namespace, request: ResolvedGenerationRequest) -> ResolvedGenerationRequest:
    """Attach confirmed local receipts to the request (shared session service; never uploads)."""
    return await bind_prepared(args.state, args.backend, request, args.asset)


def write_export(path: Path, request: ResolvedGenerationRequest) -> None:
    atomic_write(Path(path), (json.dumps(export_config(request), indent=2) + "\n").encode("utf-8"))


def filtered(snapshot, kind: str | None) -> dict:
    data = asdict(snapshot)
    data["entries_total"] = len(snapshot.entries)
    if kind:
        data["entries"] = [entry for entry in data["entries"] if entry["kind"] == kind]
        data["kind"] = kind
    return data


QUEUE_POSTED = {"pause", "resume", "cancel"}


def queue_exit_code(queue: dict) -> int:
    summary = queue["summary"]
    if summary["unknown"] or summary["failed"] or summary["save_failed"] or summary["cancelled"] or summary["pending"]:
        return EXIT_FAILED
    return EXIT_OK


def queue_plan(args: argparse.Namespace) -> Plan:
    if args.plan is not None and args.count is not None:
        raise ValidationError("--plan and --count are mutually exclusive.")
    return (
        load_plan(args.plan) if args.plan is not None else Plan("seed", count=1 if args.count is None else args.count)
    )


async def queue_base(args: argparse.Namespace) -> tuple[Plan, ResolvedGenerationRequest]:
    if args.scenario != "success" and args.backend != "fake":
        raise ValidationError("--scenario applies to the fake backend only.")
    plan = queue_plan(args)
    request = await bind_assets(args, resolve_generation(args))
    plan = bind_wildcards(plan, request.prompt, args.wildcards_dir or args.state / "wildcards")
    if plan.draws or args.rng_seed is not None:
        plan = with_rng_seed(plan, args.rng_seed)
    return plan, request


async def run_queue(args: argparse.Namespace) -> int:
    command = args.queue_command
    if command == "list":
        print(json.dumps([{k: v for k, v in q.items() if k != "items"} for q in read_queues(args.state)], indent=2))
        return EXIT_OK
    if command == "show":
        print(json.dumps(read_queues(args.state, args.queue)[0], indent=2))
        return EXIT_OK
    if command == "override":
        read_queues(args.state, args.queue)  # still reject an unknown queue
        print(
            "`queue override` is deprecated and does nothing: unknown outcomes no longer block dispatch.",
            file=sys.stderr,
        )
        return EXIT_OK
    if command in QUEUE_POSTED or command == "cap":
        post_control(args.state, args.queue, command, {"cap": args.jobs} if command == "cap" else None)
        print(json.dumps({"queue": args.queue, "posted": command, "applied_by": "dispatcher poll or next start"}))
        return EXIT_OK
    if command == "add":
        plan, base = await queue_base(args)
        if args.dry:
            print(json.dumps(asdict(describe_plan(base, plan)), indent=2))
            return EXIT_OK
        requests = expand_plan(base, plan)
        backend = {"name": args.backend, "endpoint": args.endpoint, "scenario": args.scenario}
        queue_id, cap = await create_queue(args.state, backend, requests, args.jobs)
        created = {"queue_id": queue_id, "state": "paused", "items": len(requests), "cap": cap}
        if plan.rng_seed is not None:
            created["rng_seed"] = plan.rng_seed
        print(json.dumps(created))
        return EXIT_OK
    if command == "relaunch":
        queue_id, cap = await relaunch_queue(
            args.state,
            args.queue,
            new_seeds=args.new_seeds,
            failed_only=args.failed,
            positions=tuple(args.item) if args.item else None,
            cap=args.jobs,
        )
        items = sum(read_queues(args.state, queue_id)[0]["summary"].values())
        print(json.dumps({"queue_id": queue_id, "state": "paused", "items": items, "cap": cap, "from": args.queue}))
        return EXIT_OK
    # start: this process becomes the single dispatch owner for the state directory.

    def settled(item_id: str, state: str) -> None:
        print(f"[queue] item {item_id[:8]}: {state}", file=sys.stderr)

    final = await dispatch_queue(
        args.state,
        args.queue,
        timeouts=TimeoutPolicy(total=args.timeout),
        out=args.out,
        on_settle=settled,
        auto_play=False,  # the CLI runs exactly the queue it names; up next is a desktop feature
    )
    print(json.dumps({k: v for k, v in final.items() if k != "events"}, indent=2))
    return queue_exit_code(final)


async def run_assets(args: argparse.Namespace) -> int:
    command = args.asset_command
    if command in ("list", "show"):
        registry = AssetRegistry(args.state)
        await registry.open()
        try:
            if command == "list":
                print(
                    json.dumps({"note": ASSET_NOTE, "assets": [asset_view(x) for x in await registry.list()]}, indent=2)
                )
                return EXIT_OK
            record = await registry.get(args.asset)
            if record is None:
                raise ValidationError("No local asset receipt has that identifier.")
            print(json.dumps({"note": ASSET_NOTE, **asset_view(record)}, indent=2))
            return EXIT_OK
        finally:
            await registry.aclose()
    if command == "prepare":
        record = await prepare_asset(args.state, args.file, args.name, provenance="cli")
        print(json.dumps({"note": ASSET_NOTE, **asset_view(record)}, indent=2))
        return EXIT_OK
    record, result = await reconcile_asset(args.state, args.asset)
    print(
        json.dumps(
            {"note": ASSET_NOTE, "reconciliation": result.status, "evidence": result.evidence, **asset_view(record)},
            indent=2,
        )
    )
    return {"confirmed": EXIT_OK, "upload_required": EXIT_FAILED}.get(result.status, EXIT_UNKNOWN)


async def run_edit(args: argparse.Namespace) -> int:
    # This guard precedes selection, file preparation, durable state and authentication.
    if not args.probe:
        raise ValidationError("edit requires explicit --probe consent; no backend was opened.")
    if args.bridge and args.backend != "local":
        raise ValidationError("--bridge applies only to the local backend forwarding to DT+.")
    request = resolve_generation(args)
    if args.bridge:
        request = replace(
            request,
            allow_unlisted_model=True,
            resolution_evidence=(*request.resolution_evidence, ("image_bridge", "local_to_dtplus")),
        )
    selection = selection_for(args.backend, args.endpoint, args.state)
    digest = exact_model_description_digest(request.model_id) if args.backend == "cloud" else None
    permit = ImageOperationPermit(
        backend_id="drawthings-cloud" if args.backend == "cloud" else "drawthings-grpc",
        endpoint=image_endpoint_label(selection),
        model_id=request.model_id,
        operation="inpaint" if args.mask is not None else "img2img",
        evidence_id=args.evidence,
        status="owner_probe",
        model_description_sha256=digest,
    )
    policy = TimeoutPolicy(total=args.timeout)
    print(
        "WARNING: Owner probe sends source and mask off-machine; cloud may consume credits. This is not confirmed compatibility.",
        file=sys.stderr,
        flush=True,
    )

    def progress(event):
        if event.kind in ("preparing", "progress", "terminal"):
            print(f"[{args.backend}] {event.kind}: {event.stage}", file=sys.stderr)

    outcome = await generate_image_files_once(
        selection,
        args.state,
        request,
        args.source,
        permit=permit,
        mask=args.mask,
        grow=args.grow,
        feather=args.feather,
        strength=args.strength,
        preserve_outside=args.keep == "on",
        allow_image_probe=True,
        output=args.out,
        timeouts=policy,
        on_event=progress,
    )
    evidence = dict(outcome.request.resolution_evidence)
    print(
        json.dumps(
            {
                "attempt_id": outcome.attempt_id,
                "backend": outcome.backend_id,
                "outputs": outcome.output_paths,
                "metadata": outcome.metadata_path,
                "permit_status": evidence["image_permit_status"],
                "evidence_id": evidence["image_permit_evidence"],
                "warnings": list(outcome.warnings),
            },
            indent=2,
        )
    )
    return EXIT_OK


async def run(args: argparse.Namespace) -> int:
    if args.command == "edit":
        return await run_edit(args)
    if args.command == "gallery":
        return await run_gallery(args)
    if args.command == "queue":
        return await run_queue(args)
    if args.command == "assets":
        return await run_assets(args)
    # Resolve invalid input before opening state or starting a backend.
    request = None
    policy = TimeoutPolicy()
    if args.command == "settings":
        if args.jobs is not None:
            save_cloud_concurrency(args.state, args.jobs)
        print(json.dumps({"cloud_concurrency": load_cloud_concurrency(args.state), "entitlement_verified": False}))
        return EXIT_OK
    if args.command == "generate":
        if args.scenario != "success" and args.backend != "fake":
            raise ValidationError("--scenario applies to the fake backend only.")
        request = await bind_assets(args, resolve_generation(args))
        policy = TimeoutPolicy(total=args.timeout)
        if args.export:
            write_export(args.export, request)
    store = None
    if args.command == "attempts":
        store = SQLiteAttempts(args.state)
        try:
            await store.open()
            print(json.dumps(await store.inspect(args.attempt), indent=2))
            return EXIT_OK
        finally:
            await store.aclose()
    selection = selection_for(args.backend, args.endpoint, args.state, scenario=getattr(args, "scenario", "success"))
    async with open_session(selection, args.state, output=getattr(args, "out", None), timeouts=policy) as service:
        diagnostic_event(
            log,
            "cli.command",
            level=logging.INFO,
            data={
                "command": args.command,
                "backend": args.backend,
            },
        )
        if args.command == "check":
            print(json.dumps(asdict(await service.check_connection()), indent=2))
        elif args.command == "models":
            snapshot = await service.get_inventory(args.refresh or args.no_cache)
            if not args.no_cache:
                await remember_inventory(selection, args.state, snapshot)
            print(json.dumps(filtered(snapshot, args.kind), indent=2))
        else:
            label = args.backend

            def progress(event):
                if event.kind in ("preparing", "progress", "terminal"):
                    print(f"[{label}] {event.kind}: {event.stage}", file=sys.stderr)

            outcome = await run_generation(service, request, prefix=args.prefix, on_event=progress)
            report = {
                "attempt_id": outcome.attempt_id,
                "backend": outcome.backend_id,
                "outputs": outcome.output_paths,
                "metadata": outcome.metadata_path,
            }
            if outcome.fake:
                report["fake"] = True
            if outcome.diagnostics:
                report["lora_preflight"] = [asdict(item) for item in outcome.diagnostics]
            if outcome.warnings:
                report["warnings"] = list(outcome.warnings)
            print(json.dumps(report, indent=2))
    return EXIT_OK


def start_logging(args: argparse.Namespace):
    """File logging is best effort: a destination failure is reported and never blocks or alters the command."""
    try:
        return configure_logging(
            directory=args.logs, dev=args.dev, build=identity(), backend=getattr(args, "backend", None) or "-"
        )
    except LoggingSetupError as exc:
        print(f"Logging unavailable: {exc} Continuing without a log file.", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    startup.mark("python imports")
    args = parser().parse_args(argv)
    from dtflow.persistence import thumbnail_cache
    from dtflow.persistence.gallery_snapshot import configure

    configure(no_cache=getattr(args, "no_cache", False))
    thumbnail_cache.configure(no_cache=getattr(args, "no_cache", False))
    logging_session = None if args.command == "test" else start_logging(args)
    try:
        if args.command == "gui":
            startup.mark("logging configured")
            try:
                gallery_limits = resolve_gallery_limits(getattr(args, "load", None), getattr(args, "cards", None))
            except ValueError as exc:
                print(f"Invalid request: {exc}", file=sys.stderr)
                return EXIT_INVALID
            return run_gui_command(no_cache=args.no_cache, gallery_limits=gallery_limits, thumbnail_format=args.format)
        if args.command == "test":
            return run_tests(args)
        return asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        diagnostic_event(
            log,
            "cli.interrupted_locally",
            level=logging.WARNING,
            data={},
        )
        print("Local waiting interrupted. Inspect durable attempts; do not replay unknown outcomes.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except ValidationError as exc:
        diagnostic_event(
            log,
            "cli.invalid_request",
            level=logging.ERROR,
            data={},
        )
        print(f"Invalid request: {exc}", file=sys.stderr)
        return EXIT_INVALID
    except BackendError as exc:
        failure = exc.failure
        diagnostic_event(
            log,
            "cli.backend_failure",
            level=logging.ERROR,
            data={
                "code": failure.code,
                "category": failure.category,
            },
            exc_info=args.dev,
        )
        print(
            json.dumps(
                {
                    "error": failure.code,
                    "category": failure.category,
                    "submission_stage": failure.submission_stage,
                    "remote_outcome": failure.remote_outcome,
                    **({"diagnostics": [list(pair) for pair in failure.diagnostics]} if failure.diagnostics else {}),
                }
            ),
            file=sys.stderr,
        )
        if failure.remote_outcome == RemoteOutcome.UNKNOWN:
            return EXIT_UNKNOWN
        if failure.category in (ErrorCategory.VALIDATION, ErrorCategory.CREDENTIALS):
            return EXIT_INVALID
        return EXIT_FAILED
    except (PersistenceError, OSError, TimeoutError) as exc:
        diagnostic_event(
            log,
            "cli.local_failure",
            level=logging.ERROR,
            data={
                "reason": type(exc).__name__,
            },
            exc_info=args.dev,
        )
        print(
            "Local state, output or connectivity operation failed. Inspect attempts before further action.",
            file=sys.stderr,
        )
        return EXIT_LOCAL
    finally:
        if logging_session is not None:
            logging_session.close()


def run_gui_command(
    *, no_cache: bool = False, gallery_limits: tuple[int, int] | None = None, thumbnail_format: str = "jpg"
) -> int:
    """Qt is imported only here, so every other command runs without it."""
    try:
        from dtflow.ui.splash import GuiUnavailable, run_gui_with_splash

        startup.mark("qt imports")
    except ImportError:
        print("The Qt shell requires its dependencies. Run uv sync.", file=sys.stderr)
        return EXIT_INVALID
    try:
        return run_gui_with_splash(no_cache=no_cache, gallery_limits=gallery_limits, thumbnail_format=thumbnail_format)
    except GuiUnavailable:
        print("The Qt shell requires its dependencies. Run uv sync.", file=sys.stderr)
        return EXIT_INVALID


def run_tests(args: argparse.Namespace) -> int:
    """Invoke the repository's pytest suite without creating an application loop or state."""
    try:
        import pytest
    except ImportError:
        print("Repository tests require development dependencies. Run uv sync.", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[2]
    if not (root / "tests/conftest.py").is_file():
        print("The test command requires a source checkout with its tests directory.", file=sys.stderr)
        return 2
    if args.local and args.generate:
        print("Live generation runs on DT+ only; -local cannot be combined with -gen.", file=sys.stderr)
        return 2
    # A scope flag selects live connectivity checks without a separate -live, unless -gen already names the group.
    live = args.live or ((args.local or args.cloud) and not args.generate)
    scope = "local" if args.local and not args.cloud else "cloud" if args.cloud and not args.local else "both"
    offline = args.all or not (live or args.generate)
    groups = []
    if offline:
        groups.append("(not live and not live_generation)")
    if live:
        groups.append("(live and not live_generation)")
    if args.generate:
        groups.append("live_generation")
    mode = "both" if live and args.generate else "generation" if args.generate else "live" if live else "offline"
    return int(
        pytest.main(
            [
                str(root / "tests"),
                "-c",
                str(root / "pyproject.toml"),
                "-m",
                " or ".join(groups),
                "--dtflow-test-mode",
                mode,
                "--dtflow-live-scope",
                scope,
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

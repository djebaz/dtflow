"""Headless command line: parsing and presentation only; orchestration lives in the shared services."""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, replace
from pathlib import Path

from dtflow.build import display_identity, identity
from dtflow.composition import BACKENDS, BackendSelection, create_backend
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
from dtflow.logging_setup import LoggingSetupError, configure_logging
from dtflow.persistence.settings import load_cloud_concurrency, save_cloud_concurrency
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.services.artifacts import atomic_write
from dtflow.services.config import export_config, load_config, resolve_request
from dtflow.services.generation import GenerationService
from dtflow.services.preflight import apply_trigger_prefixes

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
        description="DTFlow headless text-to-image CLI. Results are JSON on stdout; progress and diagnostics go to stderr."
    )
    command.add_argument("-v", "--version", action="version", version=display_identity())
    command.add_argument("-x", "--dev", action="store_true", help="Add sanitized diagnostic detail to the log file.")
    command.add_argument("-q", "--logs", type=Path, help="Log directory (default: repository logs/ or per-user data).")
    commands = command.add_subparsers(dest="command", required=True)
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
                help="Bypass inventory cache reads and writes (the CLI currently keeps none).",
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
                help="Persist the cloud concurrency maximum (1-4, default 4; not entitlement).",
            )
        if name == "generate":
            child.add_argument("-c", "--config", type=Path, help="Versioned generation config or prior metadata.json.")
            child.add_argument("-u", "--export", type=Path, help="Write the effective credential-free config here.")
            child.add_argument("-p", "--prompt")
            child.add_argument("-n", "--negative")
            child.add_argument("-m", "--model", help="Exact model identifier (required for local/cloud).")
            child.add_argument("-s", "--seed", type=int)
            child.add_argument("-w", "--width", type=int)
            child.add_argument("-y", "--height", type=int)
            child.add_argument("-i", "--steps", type=int)
            child.add_argument("-g", "--guidance", type=float)
            child.add_argument("-a", "--sampler", choices=tuple(x.value for x in Sampler))
            child.add_argument(
                "-l", "--lora", action="append", metavar="ID:WEIGHT", help="Exact LoRA identifier and explicit weight."
            )
            child.add_argument(
                "-r",
                "--prefix",
                action="store_true",
                help="Prepend selected LoRAs' server trigger prefixes to the prompt.",
            )
            child.add_argument("-o", "--out", type=Path, help="Final output root (default: state directory/outputs).")
            child.add_argument(
                "-f",
                "--scenario",
                choices=("success", "reject", "lost", "empty", "terminal_error"),
                default="success",
                help="Explicit fake fault scenario (fake backend only); never contacts a server.",
            )
            child.add_argument("-t", "--timeout", type=float, default=600.0, help="Total backend deadline in seconds.")
    return command


COMMAND_HELP = {
    "generate": "Generate one text-to-image result and save it with reproducibility metadata.",
    "check": "Bounded non-generative connectivity/authentication check.",
    "models": "List backend inventory with provenance (models, LoRAs, controls, unknown entries).",
    "attempts": "Inspect durable attempts; unknown outcomes are never replayed.",
    "settings": "Show or persist shared settings such as the cloud concurrency maximum.",
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


def write_export(path: Path, request: ResolvedGenerationRequest) -> None:
    atomic_write(Path(path), (json.dumps(export_config(request), indent=2) + "\n").encode("utf-8"))


def filtered(snapshot, kind: str | None) -> dict:
    data = asdict(snapshot)
    data["entries_total"] = len(snapshot.entries)
    if kind:
        data["entries"] = [entry for entry in data["entries"] if entry["kind"] == kind]
        data["kind"] = kind
    return data


async def run(args: argparse.Namespace) -> int:
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
        request = resolve_generation(args)
        policy = TimeoutPolicy(total=args.timeout)
        if args.export:
            write_export(args.export, request)
    selection = None
    if args.command != "attempts":
        selection = BackendSelection(
            args.backend,
            args.endpoint,
            load_cloud_concurrency(args.state) if args.backend == "cloud" else 4,
            getattr(args, "scenario", "success"),
        )
        backend = create_backend(selection, timeouts=policy)
    else:
        backend = None
    store = SQLiteAttempts(args.state)
    try:
        await store.open()
        if args.command == "attempts":
            print(json.dumps(await store.inspect(args.attempt), indent=2))
            return EXIT_OK
        async with asyncio.timeout(policy.connect):
            await backend.open()
        service = GenerationService(
            backend, store, getattr(args, "out", None) or args.state / "outputs", timeouts=policy
        )
        log.info("command=%s backend=%s", args.command, args.backend)
        if args.command == "check":
            print(json.dumps(asdict(await service.check_connection()), indent=2))
        elif args.command == "models":
            snapshot = await service.get_inventory(args.refresh or args.no_cache)
            print(json.dumps(filtered(snapshot, args.kind), indent=2))
        else:
            # Real adapters accept only inventory-listed models, so the generation path refreshes inventory first.
            snapshot = await service.get_inventory(refresh=True)
            if args.prefix:
                request = apply_trigger_prefixes(request, snapshot)
            diagnostics = await service.preflight(request) if request.loras else ()
            label = args.backend

            def progress(event):
                if event.kind in ("preparing", "progress", "terminal"):
                    print(f"[{label}] {event.kind}: {event.stage}", file=sys.stderr)

            result = await service.generate(request, on_event=progress)
            report = {
                "attempt_id": result.attempt_id,
                "backend": backend.capabilities.backend_id,
                "outputs": result.output_paths,
                "metadata": result.metadata_path,
            }
            if args.backend == "fake":
                report["fake"] = True
            if diagnostics:
                report["lora_preflight"] = [asdict(item) for item in diagnostics]
            if result.backend_result.warnings:
                report["warnings"] = list(result.backend_result.warnings)
            print(json.dumps(report, indent=2))
        return EXIT_OK
    finally:
        try:
            if backend is not None:
                async with asyncio.timeout(policy.cleanup):
                    await backend.aclose()
        finally:
            await store.aclose()


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
    args = parser().parse_args(argv)
    logging_session = None if args.command == "test" else start_logging(args)
    try:
        if args.command == "test":
            return run_tests(args)
        return asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.warning("interrupted locally")
        print("Local waiting interrupted. Inspect durable attempts; do not replay unknown outcomes.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except ValidationError as exc:
        log.error("invalid request")
        print(f"Invalid request: {exc}", file=sys.stderr)
        return EXIT_INVALID
    except BackendError as exc:
        failure = exc.failure
        log.error("backend failure code=%s category=%s", failure.code, failure.category, exc_info=args.dev)
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
        log.error("local failure %s", type(exc).__name__, exc_info=args.dev)
        print(
            "Local state, output or connectivity operation failed. Inspect attempts before further action.",
            file=sys.stderr,
        )
        return EXIT_LOCAL
    finally:
        if logging_session is not None:
            logging_session.close()


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

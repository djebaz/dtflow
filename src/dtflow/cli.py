"""Minimal headless fake slice; real adapters and complete CLI arrive later."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from dtflow.backends.fake import FakeBackend
from dtflow.build import display_identity
from dtflow.core.models import (
    BackendError,
    RemoteOutcome,
    ResolvedGenerationRequest,
    Sampler,
    TimeoutPolicy,
    ValidationError,
)
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.services.generation import GenerationService


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="DTFlow Phase 2A headless offline fixture application.")
    command.add_argument("-v", "--version", action="version", version=display_identity())
    commands = command.add_subparsers(dest="command", required=True)
    for name in ("generate", "check", "models", "attempts"):
        child = commands.add_parser(name)
        child.add_argument(
            "-d",
            "--state",
            type=Path,
            default=Path(".dtflow"),
            help="Local execution state directory (default: .dtflow).",
        )
        if name != "attempts":
            child.add_argument(
                "-b",
                "--backend",
                choices=("fake",),
                required=True,
                help="Explicit backend selection; only the offline fake exists in Phase 2A.",
            )
            child.add_argument(
                "-nc",
                "--no-cache",
                action="store_true",
                help="Bypass inventory cache (the fake has no persistent cache).",
            )
        if name == "models":
            child.add_argument("-r", "--refresh", action="store_true", help="Refresh the fixture inventory.")
        if name == "attempts":
            child.add_argument("-a", "--attempt", help="Inspect a single local attempt; never resubmit it.")
        if name == "generate":
            child.add_argument("-p", "--prompt", required=True)
            child.add_argument("-n", "--negative", default="")
            child.add_argument("-m", "--model", default="fake-fixture")
            child.add_argument("-s", "--seed", type=int, default=0)
            child.add_argument("-w", "--width", type=int, default=128)
            child.add_argument("-y", "--height", type=int, default=128)
            child.add_argument("-i", "--steps", type=int, default=4)
            child.add_argument("-g", "--guidance", type=float, default=1.0)
            child.add_argument("-a", "--sampler", choices=tuple(x.value for x in Sampler), default="euler_a")
            child.add_argument("-o", "--out", type=Path, help="Final output root (default: state directory/outputs).")
            child.add_argument(
                "-f",
                "--scenario",
                choices=("success", "reject", "lost", "empty", "terminal_error"),
                default="success",
                help="Explicit fake fault scenario; never contacts a server.",
            )
            child.add_argument("-t", "--timeout", type=float, default=600.0, help="Total backend deadline in seconds.")
    return command


async def run(args: argparse.Namespace) -> int:
    # Resolve invalid input before opening state or starting a backend.
    request = None
    if args.command == "generate":
        request = ResolvedGenerationRequest(
            uuid4().hex,
            args.prompt,
            args.model,
            args.seed,
            negative_prompt=args.negative,
            width_px=args.width,
            height_px=args.height,
            steps=args.steps,
            guidance=args.guidance,
            sampler_id=Sampler(args.sampler),
            resolution_evidence=(("source", "cli"),),
        )
        policy = TimeoutPolicy(total=args.timeout)
    else:
        policy = TimeoutPolicy()
    store = SQLiteAttempts(args.state)
    backend = None
    try:
        await store.open()
        if args.command == "attempts":
            print(json.dumps(await store.inspect(args.attempt), indent=2))
            return 0
        backend = FakeBackend(scenario=getattr(args, "scenario", "success"))
        async with asyncio.timeout(policy.connect):
            await backend.open()
        service = GenerationService(
            backend, store, getattr(args, "out", None) or args.state / "outputs", timeouts=policy
        )
        if args.command == "check":
            print(json.dumps(asdict(await service.check_connection()), indent=2))
        elif args.command == "models":
            print(json.dumps(asdict(await service.get_inventory(args.refresh or args.no_cache)), indent=2))
        else:

            def progress(event):
                if event.kind in ("preparing", "progress", "terminal"):
                    print(f"[fake] {event.kind}: {event.stage}", file=sys.stderr)

            result = await service.generate(request, on_event=progress)
            print(
                json.dumps(
                    {
                        "attempt_id": result.attempt_id,
                        "outputs": result.output_paths,
                        "metadata": result.metadata_path,
                        "fake": True,
                    },
                    indent=2,
                )
            )
        return 0
    finally:
        try:
            if backend is not None:
                async with asyncio.timeout(policy.cleanup):
                    await backend.aclose()
        finally:
            await store.aclose()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Local waiting interrupted. Inspect durable attempts; do not replay unknown outcomes.", file=sys.stderr)
        return 130
    except ValidationError as exc:
        print(f"Invalid request: {exc}", file=sys.stderr)
        return 2
    except BackendError as exc:
        failure = exc.failure
        print(
            json.dumps(
                {
                    "error": failure.code,
                    "category": failure.category,
                    "submission_stage": failure.submission_stage,
                    "remote_outcome": failure.remote_outcome,
                }
            ),
            file=sys.stderr,
        )
        return 4 if failure.remote_outcome == RemoteOutcome.UNKNOWN else 3
    except (PersistenceError, OSError, TimeoutError):
        print(
            "Local state, output or connectivity operation failed. Inspect attempts before further action.",
            file=sys.stderr,
        )
        return 5


if __name__ == "__main__":
    raise SystemExit(main())

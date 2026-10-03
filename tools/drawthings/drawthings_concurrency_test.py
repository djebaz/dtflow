"""Probe Draw Things gRPC connectivity and two-job execution behavior.

By default the script uses a deliberately lightweight Z-Image Turbo workload
for iPhone testing. Use --full-workload to keep the selected preset's native
generation settings, or --sequential to run the same two jobs one after another.

The generation phase can consume DT+ Cloud Compute quota or credits when the
target server uses DT+.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from drawthings_py import Configs, DrawThings, RequestBuilder


LIGHT_WIDTH = 300
LIGHT_HEIGHT = 300
LIGHT_STEPS = 4

JOB_SPECS = (
    (
        "job_a",
        "Photorealistic studio photograph of a red ceramic mug "
        "on a plain grey table, soft natural light.",
        123456789,
    ),
    (
        "job_b",
        "Photorealistic studio photograph of a blue ceramic mug "
        "on a plain grey table, soft natural light.",
        987654321,
    ),
)


@dataclass
class JobResult:
    name: str
    started: float
    finished: float
    output: Path | None
    error: str | None

    @property
    def duration(self) -> float:
        return self.finished - self.started


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a Draw Things gRPC endpoint and compare concurrent or "
            "sequential two-job generation behavior."
        )
    )
    parser.add_argument(
        "--host",
        required=True,
        help="Draw Things server hostname or LAN IP, for example 192.168.1.42.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7859,
        help="Draw Things gRPC port (default: 7859).",
    )
    parser.add_argument(
        "--preset",
        default="z_image_turbo",
        help=(
            "drawthings-py community config preset "
            "(default: z_image_turbo, which targets Z-Image Turbo Q6P "
            "with drawthings-py 0.4.0)."
        ),
    )
    parser.add_argument(
        "--full-workload",
        action="store_true",
        help=(
            "Keep the selected preset's native generation settings instead of "
            f"the lightweight {LIGHT_WIDTH}x{LIGHT_HEIGHT}, {LIGHT_STEPS}-step "
            "test overrides."
        ),
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help=(
            "Run job A and job B one after another instead of submitting both "
            "generation calls concurrently."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp") / "drawthings_concurrency",
        help=(
            "Directory for generated PNGs and timing reports "
            "(default: tmp/drawthings_concurrency)."
        ),
    )
    parser.add_argument(
        "--connection-only",
        action="store_true",
        help="Only test gRPC/model-browser connectivity; do not generate images.",
    )
    parser.add_argument(
        "--skip-connection-test",
        action="store_true",
        help="Skip the initial non-generation model-browser request.",
    )
    return parser.parse_args()


def build_request(
    *,
    preset: str,
    prompt: str,
    seed: int,
    full_workload: bool,
) -> RequestBuilder:
    config = Configs.from_preset(preset)

    if not full_workload:
        config.set(
            width=LIGHT_WIDTH,
            height=LIGHT_HEIGHT,
            steps=LIGHT_STEPS,
            batch_count=1,
            batch_size=1,
            hires_fix=False,
        )

    request = RequestBuilder(config, prompt)
    request.config["seed"] = seed
    return request


def describe_workload(args: argparse.Namespace) -> list[str]:
    config = Configs.from_preset(args.preset)
    if not args.full_workload:
        config.set(
            width=LIGHT_WIDTH,
            height=LIGHT_HEIGHT,
            steps=LIGHT_STEPS,
            batch_count=1,
            batch_size=1,
            hires_fix=False,
        )

    mode = "sequential" if args.sequential else "concurrent"
    workload = "full preset" if args.full_workload else "lightweight"
    return [
        f"Mode: {mode}",
        f"Workload: {workload}",
        f"Preset: {args.preset}",
        f"Model: {config['model']}",
        f"Requested size: {config['width']}x{config['height']}",
        f"Steps: {config['steps']}",
        "Note: Draw Things may round requested dimensions to a supported multiple.",
    ]


async def test_connection(host: str, port: int) -> None:
    print(f"[connection] Connecting to {host}:{port} ...")
    async with DrawThings.grpc(
        host=host,
        port=port,
        progressbar=False,
        disable_messages=True,
    ) as service:
        # get_models() exercises the server through the model-browser/Echo path
        # without submitting an image-generation job.
        await service.get_models(refresh_cache=True)
    print("[connection] OK: gRPC connection and model-browser request succeeded.")


async def run_job(
    *,
    name: str,
    host: str,
    port: int,
    preset: str,
    prompt: str,
    seed: int,
    full_workload: bool,
    output_dir: Path,
    output_prefix: str,
    barrier: asyncio.Event | None,
) -> JobResult:
    request = build_request(
        preset=preset,
        prompt=prompt,
        seed=seed,
        full_workload=full_workload,
    )

    # Each job owns a separate client/channel. Concurrent mode waits on the
    # shared barrier so both calls enter service.generate() together.
    async with DrawThings.grpc(
        host=host,
        port=port,
        progressbar=False,
        disable_messages=True,
    ) as service:
        if barrier is not None:
            await barrier.wait()

        started = time.perf_counter()
        print(f"[{name}] generate() entered at +{started:.6f}")

        try:
            results = await service.generate(request)
            finished = time.perf_counter()

            if not results:
                raise RuntimeError("Draw Things returned no images.")

            output = output_dir / f"{output_prefix}_{name}.png"
            results[0].to_file(output)

            print(
                f"[{name}] completed in {finished - started:.2f}s "
                f"-> {output}"
            )
            return JobResult(name, started, finished, output, None)

        except Exception as exc:  # Report both jobs even if one fails.
            finished = time.perf_counter()
            error = f"{type(exc).__name__}: {exc}"
            print(
                f"[{name}] FAILED after {finished - started:.2f}s: {error}",
                file=sys.stderr,
            )
            return JobResult(name, started, finished, None, error)


async def run_concurrent(args: argparse.Namespace) -> tuple[list[JobResult], float]:
    barrier = asyncio.Event()

    jobs = [
        asyncio.create_task(
            run_job(
                name=name,
                host=args.host,
                port=args.port,
                preset=args.preset,
                prompt=prompt,
                seed=seed,
                full_workload=args.full_workload,
                output_dir=args.output_dir,
                output_prefix="concurrent",
                barrier=barrier,
            )
        )
        for name, prompt, seed in JOB_SPECS
    ]

    # Give both tasks a chance to establish their independent gRPC connections
    # and reach the shared start barrier.
    await asyncio.sleep(0.25)

    print(
        "[test] Releasing both jobs together. "
        "This may consume DT+ Cloud Compute quota/credits."
    )
    wall_started = time.perf_counter()
    barrier.set()

    results = await asyncio.gather(*jobs)
    wall_finished = time.perf_counter()
    return results, wall_finished - wall_started


async def run_sequential(args: argparse.Namespace) -> tuple[list[JobResult], float]:
    print(
        "[test] Running both jobs sequentially. "
        "This may consume DT+ Cloud Compute quota/credits."
    )
    wall_started = time.perf_counter()

    results = []
    for name, prompt, seed in JOB_SPECS:
        result = await run_job(
            name=name,
            host=args.host,
            port=args.port,
            preset=args.preset,
            prompt=prompt,
            seed=seed,
            full_workload=args.full_workload,
            output_dir=args.output_dir,
            output_prefix="sequential",
            barrier=None,
        )
        results.append(result)

    wall_finished = time.perf_counter()
    return results, wall_finished - wall_started


def analyse_concurrent(
    results: list[JobResult],
    wall_time: float,
    workload_lines: list[str],
) -> str:
    successful = [result for result in results if result.error is None]
    lines = [
        "",
        "=== Concurrency report ===",
        *workload_lines,
        f"Total wall time: {wall_time:.2f}s",
    ]

    for result in results:
        state = "OK" if result.error is None else "FAILED"
        lines.append(
            f"{result.name}: {state}, duration={result.duration:.2f}s"
        )

    if len(successful) != 2:
        lines.append(
            "Conclusion: concurrency cannot be determined because both "
            "generation requests did not complete successfully."
        )
        return "\n".join(lines)

    a, b = successful
    overlap_start = max(a.started, b.started)
    overlap_end = min(a.finished, b.finished)
    overlap = max(0.0, overlap_end - overlap_start)

    lines.append(f"Observed request-window overlap: {overlap:.2f}s")

    if overlap > 0:
        lines.append(
            "Conclusion: both client generation calls were outstanding at the "
            "same time. This proves concurrent in-flight gRPC requests from "
            "the client, but timing alone does not prove that the backend GPU "
            "performed diffusion work in parallel."
        )
    else:
        lines.append(
            "Conclusion: no overlap was observed between the two completed "
            "generation-call windows."
        )

    lines.append(
        "For stronger evidence of backend parallel execution, compare this "
        "run with --sequential using the same preset and workload."
    )
    return "\n".join(lines)


def analyse_sequential(
    results: list[JobResult],
    wall_time: float,
    workload_lines: list[str],
) -> str:
    lines = [
        "",
        "=== Sequential report ===",
        *workload_lines,
        f"Total wall time: {wall_time:.2f}s",
    ]

    for result in results:
        state = "OK" if result.error is None else "FAILED"
        lines.append(
            f"{result.name}: {state}, duration={result.duration:.2f}s"
        )

    successful = [result for result in results if result.error is None]
    if len(successful) == 2:
        measured_generation_time = sum(result.duration for result in successful)
        lines.append(
            "Summed generate() durations: "
            f"{measured_generation_time:.2f}s"
        )
        lines.append(
            "Use this as the sequential baseline for the concurrent run with "
            "the same preset and workload."
        )
    else:
        lines.append(
            "Sequential baseline is incomplete because both jobs did not "
            "complete successfully."
        )

    return "\n".join(lines)


async def async_main(args: argparse.Namespace) -> int:
    if not args.skip_connection_test:
        try:
            await test_connection(args.host, args.port)
        except Exception as exc:
            print(
                f"[connection] FAILED: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 2

    if args.connection_only:
        print("Connection-only test complete; no generation was submitted.")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)

    workload_lines = describe_workload(args)
    print("[config] " + " | ".join(workload_lines[:-1]))
    print(f"[config] {workload_lines[-1]}")

    if args.sequential:
        results, wall_time = await run_sequential(args)
        report = analyse_sequential(results, wall_time, workload_lines)
        report_path = args.output_dir / "sequential_report.txt"
    else:
        results, wall_time = await run_concurrent(args)
        report = analyse_concurrent(results, wall_time, workload_lines)
        report_path = args.output_dir / "concurrency_report.txt"

    print(report)
    report_path.write_text(report + "\n", encoding="utf-8")
    print(f"\nReport saved to {report_path}")

    return 0 if all(result.error is None for result in results) else 1


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

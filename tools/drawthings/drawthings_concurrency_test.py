"""Probe Draw Things gRPC connectivity and multi-job execution behavior.

By default the script uses a deliberately lightweight Z-Image Turbo workload
for iPhone testing. Use --full-workload to keep the selected preset's native
generation settings, --sequential for a serial baseline, and --jobs to vary the
number of controlled generation requests.

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
MAX_JOBS = 8

JOB_COLORS = (
    "red",
    "blue",
    "green",
    "yellow",
    "purple",
    "orange",
    "white",
    "black",
)
JOB_SEEDS = (
    123456789,
    987654321,
    314159265,
    271828182,
    161803398,
    141421356,
    173205080,
    223606797,
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


def parse_job_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("jobs must be an integer") from exc

    if not 1 <= count <= MAX_JOBS:
        raise argparse.ArgumentTypeError(
            f"jobs must be between 1 and {MAX_JOBS}"
        )
    return count


def build_job_specs(count: int) -> list[tuple[str, str, int]]:
    return [
        (
            f"job_{chr(ord('a') + index)}",
            (
                "Photorealistic studio photograph of a "
                f"{JOB_COLORS[index]} ceramic mug on a plain grey table, "
                "soft natural light."
            ),
            JOB_SEEDS[index],
        )
        for index in range(count)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a Draw Things gRPC endpoint and compare concurrent or "
            "sequential multi-job generation behavior."
        )
    )
    parser.add_argument(
        "-H",
        "--host",
        required=True,
        help="Draw Things server hostname or LAN IP, for example 192.168.1.42.",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=7859,
        help="Draw Things gRPC port (default: 7859).",
    )
    parser.add_argument(
        "-P",
        "--preset",
        default="z_image_turbo",
        help=(
            "drawthings-py community config preset "
            "(default: z_image_turbo, which targets Z-Image Turbo Q6P "
            "with drawthings-py 0.4.0)."
        ),
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=parse_job_count,
        default=2,
        metavar="N",
        help=(
            f"Number of controlled generation jobs, 1-{MAX_JOBS} "
            "(default: 2)."
        ),
    )
    parser.add_argument(
        "-f",
        "--full-workload",
        action="store_true",
        help=(
            "Keep the selected preset's native generation settings instead of "
            f"the lightweight {LIGHT_WIDTH}x{LIGHT_HEIGHT}, {LIGHT_STEPS}-step "
            "test overrides."
        ),
    )
    parser.add_argument(
        "-s",
        "--sequential",
        action="store_true",
        help=(
            "Run the configured jobs one after another instead of submitting "
            "all generation calls concurrently."
        ),
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("tmp") / "drawthings_concurrency",
        help=(
            "Directory for generated PNGs and timing reports "
            "(default: tmp/drawthings_concurrency)."
        ),
    )
    parser.add_argument(
        "-c",
        "--connection-only",
        action="store_true",
        help="Only test gRPC/model-browser connectivity; do not generate images.",
    )
    parser.add_argument(
        "-k",
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
        f"Jobs: {args.jobs}",
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
    # shared barrier so all calls enter service.generate() together.
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

        except Exception as exc:  # Report every job even if one fails.
            finished = time.perf_counter()
            error = f"{type(exc).__name__}: {exc}"
            print(
                f"[{name}] FAILED after {finished - started:.2f}s: {error}",
                file=sys.stderr,
            )
            return JobResult(name, started, finished, None, error)


async def run_concurrent(args: argparse.Namespace) -> tuple[list[JobResult], float]:
    barrier = asyncio.Event()
    job_specs = build_job_specs(args.jobs)

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
        for name, prompt, seed in job_specs
    ]

    # Give all tasks a chance to establish their independent gRPC connections
    # and reach the shared start barrier.
    await asyncio.sleep(0.25)

    print(
        f"[test] Releasing {args.jobs} jobs together. "
        "This may consume DT+ Cloud Compute quota/credits."
    )
    wall_started = time.perf_counter()
    barrier.set()

    results = await asyncio.gather(*jobs)
    wall_finished = time.perf_counter()
    return results, wall_finished - wall_started


async def run_sequential(args: argparse.Namespace) -> tuple[list[JobResult], float]:
    job_specs = build_job_specs(args.jobs)
    print(
        f"[test] Running {args.jobs} jobs sequentially. "
        "This may consume DT+ Cloud Compute quota/credits."
    )
    wall_started = time.perf_counter()

    results = []
    for name, prompt, seed in job_specs:
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

    if len(results) < 2:
        lines.append(
            "Conclusion: one job was requested, so concurrency cannot be determined."
        )
        return "\n".join(lines)

    if len(successful) != len(results):
        lines.append(
            "Conclusion: concurrency cannot be determined because not all "
            "generation requests completed successfully."
        )
        return "\n".join(lines)

    overlap_start = max(result.started for result in successful)
    overlap_end = min(result.finished for result in successful)
    all_overlap = max(0.0, overlap_end - overlap_start)

    first_started = min(result.started for result in successful)
    completion_offsets = ", ".join(
        f"{result.name}={result.finished - first_started:.2f}s"
        for result in successful
    )

    lines.append(f"Observed all-request-window overlap: {all_overlap:.2f}s")
    lines.append(f"Completion offsets: {completion_offsets}")

    if all_overlap > 0:
        lines.append(
            f"Conclusion: all {len(successful)} client generation calls were "
            "outstanding at the same time. This proves concurrent in-flight "
            "gRPC requests from the client, but timing alone does not prove "
            "how many backend generations executed in parallel."
        )
    else:
        lines.append(
            "Conclusion: no interval was observed where every completed "
            "generation call was outstanding at the same time."
        )

    lines.append(
        "Compare this run with --sequential using the same --jobs count, "
        "preset, prompts, seeds, and workload to evaluate backend scheduling."
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
    if len(successful) == len(results):
        measured_generation_time = sum(result.duration for result in successful)
        lines.append(
            "Summed generate() durations: "
            f"{measured_generation_time:.2f}s"
        )
        lines.append(
            "Use this as the sequential baseline for a concurrent run with "
            "the same --jobs count, preset, and workload."
        )
    else:
        lines.append(
            "Sequential baseline is incomplete because not all jobs completed "
            "successfully."
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

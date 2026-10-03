"""Probe Draw Things gRPC connectivity and two-request concurrency.

The script deliberately uses two independent gRPC client connections so that
client-side stream serialization does not hide server-side concurrency.

Run the non-generative connectivity check first. The generation phase can
consume DT+ Cloud Compute quota or credits when the target server uses DT+.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from drawthings_py import Configs, DrawThings, RequestBuilder


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
            "Verify a Draw Things gRPC endpoint and test two simultaneous "
            "generation requests."
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
        default="ernie_image_turbo",
        help=(
            "drawthings-py community config preset "
            "(default: ernie_image_turbo)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp") / "drawthings_concurrency",
        help=(
            "Directory for generated PNGs and the timing report "
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
    output_dir: Path,
    barrier: asyncio.Event,
) -> JobResult:
    config = Configs.from_preset(preset)
    request = RequestBuilder(config, prompt)
    request.config["seed"] = seed

    # Each job owns a separate client/channel. Both wait on the same barrier,
    # then enter service.generate() during the same event-loop turn.
    async with DrawThings.grpc(
        host=host,
        port=port,
        progressbar=False,
        disable_messages=True,
    ) as service:
        await barrier.wait()
        started = time.perf_counter()
        print(f"[{name}] generate() entered at +{started:.6f}")

        try:
            results = await service.generate(request)
            finished = time.perf_counter()

            if not results:
                raise RuntimeError("Draw Things returned no images.")

            output = output_dir / f"{name}.png"
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


def analyse(results: list[JobResult], wall_time: float) -> str:
    successful = [r for r in results if r.error is None]
    lines = [
        "",
        "=== Concurrency report ===",
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
        "wall time with a sequential two-job baseline using the same preset, "
        "prompts, seeds, dimensions, and server/cloud mode."
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

    barrier = asyncio.Event()

    jobs = [
        asyncio.create_task(
            run_job(
                name="job_a",
                host=args.host,
                port=args.port,
                preset=args.preset,
                prompt=(
                    "Photorealistic studio photograph of a red ceramic mug "
                    "on a plain grey table, soft natural light."
                ),
                seed=123456789,
                output_dir=args.output_dir,
                barrier=barrier,
            )
        ),
        asyncio.create_task(
            run_job(
                name="job_b",
                host=args.host,
                port=args.port,
                preset=args.preset,
                prompt=(
                    "Photorealistic studio photograph of a blue ceramic mug "
                    "on a plain grey table, soft natural light."
                ),
                seed=987654321,
                output_dir=args.output_dir,
                barrier=barrier,
            )
        ),
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

    report = analyse(results, wall_finished - wall_started)
    print(report)

    report_path = args.output_dir / "concurrency_report.txt"
    report_path.write_text(report + "\n", encoding="utf-8")
    print(f"\nReport saved to {report_path}")

    return 0 if all(r.error is None for r in results) else 1


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

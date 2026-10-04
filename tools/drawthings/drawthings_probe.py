"""Probe Draw Things and direct DT+ Cloud Compute multi-job behavior.

The default target is a Draw Things gRPC server. Use -d/--cloud to bypass the
Draw Things server and send the same controlled workload directly to DT+ Cloud
Compute using the official API-key authentication flow.

Direct-cloud mode reads DRAWTHINGS_API_KEY from the environment. The key and
derived tokens are never printed or persisted. This probe does not opt into
PAYG billing; live generation can still consume DT+ subscription quota/credits.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import os
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from drawthings_py import Configs, DrawThings, RequestBuilder
from drawthings_py.generated.dt_grpc import image_service
from drawthings_py.grpc.grpc_service import GrpcService
from grpclib.client import Channel

LIGHT_WIDTH = 300
LIGHT_HEIGHT = 300
LIGHT_STEPS = 4
MAX_JOBS = 8

DEFAULT_GRPC_PORT = 7859
CLOUD_API_BASE = "https://api.drawthings.ai"
CLOUD_GRPC_HOST = "compute.drawthings.ai"
CLOUD_GRPC_PORT = 443
CLOUD_API_KEY_ENV = "DRAWTHINGS_API_KEY"

LEGACY_ARG_ALIASES = {
    "-H": "-a",
    "-P": "-r",
    "--full-workload": "--full",
    "--sequential": "--serial",
    "--output-dir": "--out",
    "--connection-only": "--conn",
    "--skip-connection-test": "--skip-conn",
}

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


class CloudAuthError(RuntimeError):
    """Raised when direct DT+ Cloud Compute authentication fails."""


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
        raise argparse.ArgumentTypeError(f"jobs must be between 1 and {MAX_JOBS}")
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


def normalize_legacy_args(argv: list[str]) -> list[str]:
    """Accept the previous probe spellings without advertising them in --help."""
    return [LEGACY_ARG_ALIASES.get(arg, arg) for arg in argv]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare controlled multi-job generation through a Draw Things gRPC server or direct DT+ Cloud Compute."
        )
    )
    parser.add_argument(
        "-a",
        "--host",
        help="Draw Things server hostname or LAN IP, for example 192.168.1.42.",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_GRPC_PORT,
        help=f"Draw Things gRPC port (default: {DEFAULT_GRPC_PORT}).",
    )
    parser.add_argument(
        "-d",
        "--cloud",
        action="store_true",
        help=(
            "Use DT+ Cloud Compute directly instead of a Draw Things server. "
            f"Reads the API key from {CLOUD_API_KEY_ENV}."
        ),
    )
    parser.add_argument(
        "-r",
        "--preset",
        default="z_image_turbo",
        help=("drawthings-py config preset (default: z_image_turbo, targeting Z-Image Turbo Q6P in 0.4.0)."),
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=parse_job_count,
        default=2,
        metavar="N",
        help=f"Number of controlled generation jobs, 1-{MAX_JOBS} (default: 2).",
    )
    parser.add_argument(
        "-f",
        "--full",
        action="store_true",
        help=(
            "Keep the selected preset's native generation settings instead of "
            f"the lightweight {LIGHT_WIDTH}x{LIGHT_HEIGHT}, {LIGHT_STEPS}-step "
            "test overrides."
        ),
    )
    parser.add_argument(
        "-s",
        "--serial",
        action="store_true",
        help="Run jobs one after another instead of submitting them concurrently.",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("tmp") / "drawthings_concurrency",
        help=("Directory for generated PNGs and timing reports (default: tmp/drawthings_concurrency)."),
    )
    parser.add_argument(
        "-c",
        "--conn",
        action="store_true",
        help="Only test connectivity; do not submit a generation.",
    )
    parser.add_argument(
        "-k",
        "--skip-conn",
        action="store_true",
        help="Skip the initial non-generation connectivity request.",
    )

    raw_argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(normalize_legacy_args(raw_argv))

    if args.cloud and args.host:
        parser.error("-a/--host cannot be combined with -d/--cloud")
    if not args.cloud and not args.host:
        parser.error("-a/--host is required unless -d/--cloud is used")

    return args


def build_request(
    *,
    preset: str,
    prompt: str,
    seed: int,
    full: bool,
) -> RequestBuilder:
    config = Configs.from_preset(preset)

    if not full:
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
    if not args.full:
        config.set(
            width=LIGHT_WIDTH,
            height=LIGHT_HEIGHT,
            steps=LIGHT_STEPS,
            batch_count=1,
            batch_size=1,
            hires_fix=False,
        )

    mode = "serial" if args.serial else "concurrent"
    workload = "full preset" if args.full else "lightweight"
    target = "direct DT+ cloud" if args.cloud else f"Draw Things gRPC {args.host}:{args.port}"
    return [
        f"Target: {target}",
        f"Mode: {mode}",
        f"Jobs: {args.jobs}",
        f"Workload: {workload}",
        f"Preset: {args.preset}",
        f"Model: {config['model']}",
        f"Requested size: {config['width']}x{config['height']}",
        f"Steps: {config['steps']}",
        "Note: Draw Things may round requested dimensions to a supported multiple.",
    ]


def _request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    operation: str,
) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "dtflow-cloud-probe/0.0",
    }
    if headers:
        request_headers.update(headers)

    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        raise CloudAuthError(f"{operation} failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise CloudAuthError(f"{operation} failed: {exc.reason}") from exc

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudAuthError(f"{operation} returned invalid JSON") from exc

    if not isinstance(decoded, dict):
        raise CloudAuthError(f"{operation} returned an unexpected response")
    return decoded


class DirectCloudAuth:
    """Minimal official API-key authentication flow for the direct-cloud probe."""

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._short_term_token: str | None = None
        self._token_lock = asyncio.Lock()

    @classmethod
    def from_environment(cls) -> "DirectCloudAuth":
        api_key = os.environ.get(CLOUD_API_KEY_ENV, "").strip()
        if not api_key:
            raise CloudAuthError(f"Direct cloud mode requires {CLOUD_API_KEY_ENV} in the environment.")
        return cls(api_key)

    async def prepare(self) -> None:
        await self._short_token()

    async def _short_token(self) -> str:
        if self._short_term_token:
            return self._short_term_token

        async with self._token_lock:
            if self._short_term_token:
                return self._short_term_token
            token = await asyncio.to_thread(self._fetch_short_token_sync)
            self._short_term_token = token
            return token

    def _fetch_short_token_sync(self) -> str:
        response = _request_json(
            f"{CLOUD_API_BASE}/sdk/token",
            method="POST",
            payload={
                "apiKey": self._api_key,
                "appCheckType": "none",
                "appCheckToken": None,
            },
            timeout=30,
            operation="DT+ short-term token request",
        )
        token = response.get("shortTermToken")
        if not isinstance(token, str) or not token:
            raise CloudAuthError("DT+ short-term token response did not contain a token")
        return token

    async def generation_metadata(
        self,
        request_message: image_service.ImageGenerationRequest,
    ) -> dict[str, str]:
        short_term_token = await self._short_token()
        grpc_token = await asyncio.to_thread(
            self._authenticate_generation_sync,
            short_term_token,
            request_message,
        )
        return {"authorization": f"bearer {grpc_token}"}

    def _authenticate_generation_sync(
        self,
        short_term_token: str,
        request_message: image_service.ImageGenerationRequest,
    ) -> str:
        # Match MediaGenerationKit's authentication blob: serialize the request
        # before large content tensors are attached. The controlled txt2img
        # workload has no content tensors, but clearing them keeps the contract
        # correct for future probe extensions.
        auth_message = copy.deepcopy(request_message)
        auth_message.contents = []
        encoded_blob = base64.b64encode(bytes(auth_message)).decode("ascii")

        response = _request_json(
            f"{CLOUD_API_BASE}/authenticate",
            method="POST",
            payload={
                "blob": encoded_blob,
                "fromBridge": True,
                "attestationSupported": False,
                "assertionPayload": None,
                "originalTransactionId": None,
                "isSandbox": False,
                # Keep this probe subscription-only. MediaGenerationKit uses
                # these fields to opt into PAYG when enabled; we deliberately
                # leave them unset so a probe cannot silently opt into PAYG.
                "consumableType": None,
                "amount": None,
            },
            headers={"Authorization": short_term_token},
            timeout=30,
            operation="DT+ generation authentication",
        )
        token = response.get("gRPCToken")
        if not isinstance(token, str) or not token:
            raise CloudAuthError("DT+ generation authentication returned no gRPC token")
        return token


class _AuthenticatedCloudStub:
    """Inject the per-generation DT+ bearer token into the generated gRPC stub."""

    def __init__(
        self,
        stub: image_service.ImageGenerationServiceStub,
        auth: DirectCloudAuth,
    ):
        self._stub = stub
        self._auth = auth

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stub, name)

    async def generate_image(
        self,
        request: image_service.ImageGenerationRequest,
        *,
        timeout: float | None = None,
        deadline: Any = None,
        metadata: Any = None,
    ) -> AsyncIterator[image_service.ImageGenerationResponse]:
        auth_metadata = await self._auth.generation_metadata(request)

        if metadata is None:
            merged_metadata: Any = auth_metadata
        elif isinstance(metadata, dict):
            merged_metadata = dict(metadata)
            merged_metadata.update(auth_metadata)
        else:
            merged_metadata = list(metadata)
            merged_metadata.extend(auth_metadata.items())

        async for response in self._stub.generate_image(
            request,
            timeout=timeout,
            deadline=deadline,
            metadata=merged_metadata,
        ):
            yield response


class DirectCloudService(GrpcService):
    """drawthings-py gRPC service pointed directly at DT+ Cloud Compute."""

    def __init__(self, auth: DirectCloudAuth):
        super().__init__(
            host=CLOUD_GRPC_HOST,
            port=CLOUD_GRPC_PORT,
            progressbar=False,
            disable_messages=True,
        )
        self._cloud_auth = auth

    async def connect(self) -> None:
        if self._channel is not None:
            return

        tls_context = ssl.create_default_context()
        tls_context.set_alpn_protocols(["h2"])

        try:
            self._channel = Channel(
                CLOUD_GRPC_HOST,
                CLOUD_GRPC_PORT,
                ssl=tls_context,
            )
            raw_stub = image_service.ImageGenerationServiceStub(self._channel)
            self._service = _AuthenticatedCloudStub(raw_stub, self._cloud_auth)
            await self._fetch_models()
        except Exception:
            if self._channel is not None:
                self._channel.close()
            self._channel = None
            self._service = None
            raise


def create_service(
    args: argparse.Namespace,
    cloud_auth: DirectCloudAuth | None,
) -> GrpcService:
    if args.cloud:
        if cloud_auth is None:
            raise RuntimeError("Direct cloud authentication is not initialized")
        return DirectCloudService(cloud_auth)

    return DrawThings.grpc(
        host=args.host,
        port=args.port,
        progressbar=False,
        disable_messages=True,
    )


async def test_connection(
    args: argparse.Namespace,
    cloud_auth: DirectCloudAuth | None,
) -> None:
    if args.cloud:
        print(f"[connection] Connecting directly to DT+ at {CLOUD_GRPC_HOST}:{CLOUD_GRPC_PORT} ...")
    else:
        print(f"[connection] Connecting to {args.host}:{args.port} ...")

    async with create_service(args, cloud_auth) as service:
        # Echo/model browsing is non-generative and matches the handshake used
        # by Draw Things remote/cloud clients.
        await service.get_models(refresh_cache=True)

    if args.cloud:
        print("[connection] OK: DT+ token setup and cloud gRPC handshake succeeded.")
    else:
        print("[connection] OK: gRPC connection and model-browser request succeeded.")


def generation_warning(args: argparse.Namespace) -> str:
    if args.cloud:
        return (
            "This submits directly to DT+ Cloud Compute and may consume "
            "subscription quota/credits. PAYG opt-in is disabled."
        )
    return (
        "This submits through the Draw Things server and may use local compute or DT+ quota if Bridge Mode is enabled."
    )


async def run_job(
    *,
    name: str,
    args: argparse.Namespace,
    cloud_auth: DirectCloudAuth | None,
    prompt: str,
    seed: int,
    output_prefix: str,
    barrier: asyncio.Event | None,
    ready: asyncio.Future[None] | None = None,
) -> JobResult:
    request = build_request(
        preset=args.preset,
        prompt=prompt,
        seed=seed,
        full=args.full,
    )

    # Every job owns an independent gRPC channel. In concurrent mode all jobs
    # wait on the shared barrier before entering generate().
    async with create_service(args, cloud_auth) as service:
        if ready is not None:
            ready.set_result(None)
        if barrier is not None:
            await barrier.wait()

        started = time.perf_counter()
        print(f"[{name}] generate() entered at +{started:.6f}")

        try:
            results = await service.generate(request)
            finished = time.perf_counter()

            if not results:
                raise RuntimeError("Generation returned no images.")

            output = args.out / f"{output_prefix}_{name}.png"
            results[0].to_file(output)

            print(f"[{name}] completed in {finished - started:.2f}s -> {output}")
            return JobResult(name, started, finished, output, None)

        except Exception as exc:  # Report every job even if one fails.
            finished = time.perf_counter()
            error = f"{type(exc).__name__}: {exc}"
            print(
                f"[{name}] FAILED after {finished - started:.2f}s: {error}",
                file=sys.stderr,
            )
            return JobResult(name, started, finished, None, error)


async def _wait_for_ready(task: asyncio.Task[JobResult], ready: asyncio.Future[None]) -> None:
    """Wait for a connected job, propagating any failure before readiness."""
    done, _ = await asyncio.wait((task, ready), return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        await task
        raise RuntimeError("A probe job ended before the concurrent release.")
    await ready


async def run_concurrent(
    args: argparse.Namespace,
    cloud_auth: DirectCloudAuth | None,
) -> tuple[list[JobResult], float]:
    barrier = asyncio.Event()
    job_specs = build_job_specs(args.jobs)
    prefix = "cloud_concurrent" if args.cloud else "concurrent"
    ready = [asyncio.get_running_loop().create_future() for _ in job_specs]
    jobs = [
        asyncio.create_task(
            run_job(
                name=name,
                args=args,
                cloud_auth=cloud_auth,
                prompt=prompt,
                seed=seed,
                output_prefix=prefix,
                barrier=barrier,
                ready=signal,
            )
        )
        for (name, prompt, seed), signal in zip(job_specs, ready, strict=True)
    ]

    try:
        # Release only after every independent connection is actually ready.
        # A failed connection aborts the group before any generation submission.
        await asyncio.gather(*(_wait_for_ready(task, signal) for task, signal in zip(jobs, ready, strict=True)))
        print(f"[test] Releasing {args.jobs} jobs together. {generation_warning(args)}")
        wall_started = time.perf_counter()
        barrier.set()
        results = await asyncio.gather(*jobs)
        return results, time.perf_counter() - wall_started
    finally:
        for signal in ready:
            if not signal.done():
                signal.cancel()
        for task in jobs:
            if not task.done():
                task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


async def run_serial(
    args: argparse.Namespace,
    cloud_auth: DirectCloudAuth | None,
) -> tuple[list[JobResult], float]:
    job_specs = build_job_specs(args.jobs)
    prefix = "cloud_serial" if args.cloud else "serial"

    print(f"[test] Running {args.jobs} jobs serially. {generation_warning(args)}")
    wall_started = time.perf_counter()

    results = []
    for name, prompt, seed in job_specs:
        result = await run_job(
            name=name,
            args=args,
            cloud_auth=cloud_auth,
            prompt=prompt,
            seed=seed,
            output_prefix=prefix,
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
        lines.append(f"{result.name}: {state}, duration={result.duration:.2f}s")

    if len(results) < 2:
        lines.append("Conclusion: one job was requested, so concurrency cannot be determined.")
        return "\n".join(lines)

    if len(successful) != len(results):
        lines.append(
            "Conclusion: concurrency cannot be determined because not all generation requests completed successfully."
        )
        return "\n".join(lines)

    overlap_start = max(result.started for result in successful)
    overlap_end = min(result.finished for result in successful)
    all_overlap = max(0.0, overlap_end - overlap_start)

    first_started = min(result.started for result in successful)
    completion_offsets = ", ".join(f"{result.name}={result.finished - first_started:.2f}s" for result in successful)

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
        "Compare this run with --serial using the same --jobs count, "
        "preset, prompts, seeds, and workload to evaluate backend scheduling."
    )
    return "\n".join(lines)


def analyse_serial(
    results: list[JobResult],
    wall_time: float,
    workload_lines: list[str],
) -> str:
    lines = [
        "",
        "=== Serial report ===",
        *workload_lines,
        f"Total wall time: {wall_time:.2f}s",
    ]

    for result in results:
        state = "OK" if result.error is None else "FAILED"
        lines.append(f"{result.name}: {state}, duration={result.duration:.2f}s")

    successful = [result for result in results if result.error is None]
    if len(successful) == len(results):
        measured_generation_time = sum(result.duration for result in successful)
        lines.append(f"Summed generate() durations: {measured_generation_time:.2f}s")
        lines.append(
            "Use this as the serial baseline for a concurrent run with the same --jobs count, preset, and workload."
        )
    else:
        lines.append("Serial baseline is incomplete because not all jobs completed successfully.")

    return "\n".join(lines)


async def async_main(args: argparse.Namespace) -> int:
    cloud_auth: DirectCloudAuth | None = None
    if args.cloud:
        try:
            cloud_auth = DirectCloudAuth.from_environment()
            await cloud_auth.prepare()
            print(f"[cloud] API-key authentication prepared from {CLOUD_API_KEY_ENV}; credentials are not logged.")
        except Exception as exc:
            print(f"[cloud] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    if not args.skip_conn:
        try:
            await test_connection(args, cloud_auth)
        except Exception as exc:
            print(
                f"[connection] FAILED: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 2

    if args.conn:
        print("Connection-only test complete; no generation was submitted.")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)

    workload_lines = describe_workload(args)
    print("[config] " + " | ".join(workload_lines[:-1]))
    print(f"[config] {workload_lines[-1]}")

    if args.serial:
        results, wall_time = await run_serial(args, cloud_auth)
        report = analyse_serial(results, wall_time, workload_lines)
        report_name = "cloud_serial_report.txt" if args.cloud else "serial_report.txt"
    else:
        results, wall_time = await run_concurrent(args, cloud_auth)
        report = analyse_concurrent(results, wall_time, workload_lines)
        report_name = "cloud_concurrency_report.txt" if args.cloud else "concurrency_report.txt"

    report_path = args.out / report_name
    print(report)
    report_path.write_text(report + "\n", encoding="utf-8")
    print(f"\nReport saved to {report_path}")

    return 0 if all(result.error is None for result in results) else 1


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

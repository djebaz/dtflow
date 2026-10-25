"""Generation behavior of the actual Draw Things adapter against the scripted TLS/gRPC server."""

import asyncio
import struct
from dataclasses import replace

import pytest
from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration
from grpclib.const import Status
from support.dt_server import (
    Drop,
    EchoSpec,
    Fail,
    Hang,
    Image,
    Preview,
    Signpost,
    pixel_tensor,
    scripted_server,
)
from test_local_grpc_connection import MODELS, opened

from dtflow.backends.drawthings import codec
from dtflow.core.models import (
    BackendError,
    Designation,
    ErrorCategory,
    RemoteOutcome,
    ResolvedGenerationRequest,
    ResourceLimits,
    Sampler,
    SubmissionStage,
    TimeoutPolicy,
)
from dtflow.core.png import validate_png

ECHO = EchoSpec(files=("a_q8p.ckpt",), models=MODELS[:1])
SIZE = 128
GOOD = pixel_tensor(SIZE, SIZE)


def make_request(**changes) -> ResolvedGenerationRequest:
    base = ResolvedGenerationRequest("r1", "a prompt", "a_q8p.ckpt", 0, "no", SIZE, SIZE, 4, 3.5, Sampler.EULER_A)
    return replace(base, **changes)


async def run(steps, *, request=None, timeouts=None, limits=None, echo=ECHO, check=None, on_event=None):
    """Run one generation against a scripted server; return (outcome, events, server observations)."""
    events = []
    async with scripted_server(steps, echo) as server:
        adapter = await opened(server.host, server.port, timeouts=timeouts or TimeoutPolicy(connect=1.0, total=5.0))
        if limits:
            adapter.limits = limits
        try:
            await adapter.get_inventory(refresh=True)
            try:
                outcome = await adapter.generate(request or make_request(), on_event=on_event or events.append)
            except BackendError as exc:
                outcome = exc.failure
            if check:
                await check(adapter, server)
            return outcome, events, server.seen
        finally:
            await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("seed,sampler", [(0, Sampler.EULER_A), (4294967295, Sampler.DPMPP_2M_KARRAS)])
async def test_success_stream_sends_the_resolved_request_and_returns_a_png(seed, sampler):
    steps = (Signpost("text_encoded"), Signpost("sampling", 2), Preview(pixel_tensor(16, 16, 4)), Image(GOOD))
    result, events, seen = await run(steps, request=make_request(seed=seed, sampler_id=sampler))
    kinds = [e.kind for e in events]
    assert kinds[0] == "preparing" and kinds.index("submission_possible") < kinds.index("progress")
    assert set(kinds[: kinds.index("submission_possible")]) == {"preparing"}
    assert events[-1].kind == "terminal" and [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert any(e.kind == "artifact_received" for e in events)
    (artifact,) = result.artifacts
    assert artifact.designation == Designation.FINAL and artifact.media_type == "image/png"
    validate_png(artifact.encoded, SIZE, SIZE, decoded_limit=ResourceLimits().tensor_bytes)
    assert result.completion.terminal_status == "OK" and result.completion.required_outputs_valid
    configuration = GenerationConfiguration.GetRootAs(seen.generate_requests[0].configuration, 0)
    assert (
        configuration.Seed() == seed and (configuration.StartWidth(), configuration.StartHeight()) == (SIZE // 64,) * 2
    )
    assert seen.generate_requests[0].prompt == "a prompt" and seen.generate_requests[0].negative_prompt == "no"
    assert dict(result.effective_configuration)["seed"] == str(seed)


@pytest.mark.asyncio
async def test_terminal_notification_failure_preserves_validated_completion():
    def reject_terminal(event):
        if event.kind == "terminal":
            raise RuntimeError("FAKE_SECRET_MUST_NOT_ESCAPE")

    result, _, seen = await run((Image(GOOD),), on_event=reject_terminal)
    assert result.completion.terminal_status == "OK" and result.completion.required_outputs_valid
    assert len(result.artifacts) == 1 and len(seen.generate_requests) == 1
    validate_png(result.artifacts[0].encoded, SIZE, SIZE, decoded_limit=64 << 20)
    assert result.warnings == ("Terminal event delivery failed after validated backend completion.",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "steps,code,stage,outcome,terminal",
    [
        (
            (Signpost(), Fail(Status.INTERNAL)),
            "grpc_internal",
            SubmissionStage.TERMINAL_OBSERVED,
            "unknown",
            "INTERNAL",
        ),
        ((Signpost(), Drop()), "connection_lost", SubmissionStage.POSSIBLY_SUBMITTED, "unknown", None),
        ((Signpost(),), "no_final_output", SubmissionStage.TERMINAL_OBSERVED, "unknown", "OK"),
        (
            (Image(GOOD, more_chunks=True),),
            "chunked_output_unsupported",
            SubmissionStage.POSSIBLY_SUBMITTED,
            "unknown",
            None,
        ),
        (
            (Image(pixel_tensor(192, 192)),),
            "tensor_dimensions_mismatch",
            SubmissionStage.POSSIBLY_SUBMITTED,
            "unknown",
            None,
        ),
        ((Image(GOOD[:40]),), "tensor_truncated", SubmissionStage.POSSIBLY_SUBMITTED, "unknown", None),
        ((Image(GOOD[:-9]),), "tensor_decode_failed", SubmissionStage.POSSIBLY_SUBMITTED, "unknown", None),
        ((Image(GOOD), Image(GOOD)), "unexpected_final_output", SubmissionStage.POSSIBLY_SUBMITTED, "unknown", None),
    ],
)
async def test_failures_never_become_success_and_keep_outcome_unknown(steps, code, stage, outcome, terminal):
    failure, events, _ = await run(steps)
    assert failure.code == code and failure.submission_stage == stage and failure.remote_outcome == outcome
    assert failure.terminal_status == terminal
    assert [e.kind for e in events].count("submission_possible") == 1 and "terminal" not in [e.kind for e in events]


@pytest.mark.asyncio
async def test_partial_final_before_an_error_is_kept_in_memory_only():
    failure, _, _ = await run((Image(GOOD), Fail(Status.INTERNAL)))
    assert failure.terminal_status == "INTERNAL" and failure.remote_outcome == RemoteOutcome.UNKNOWN
    (partial,) = failure.partial_artifacts
    assert partial.designation == Designation.PARTIAL and partial.encoded.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_oversize_tensor_is_rejected_before_any_decode(monkeypatch):
    def forbidden(*_):
        raise AssertionError("decoder must not run for rejected tensors")

    monkeypatch.setattr(codec.ImageBuffer, "from_tensor", forbidden)
    huge = bytearray(GOOD)
    struct.pack_into("<3I", huge, 24, 100_000, 100_000, 3)  # header claims a 100k x 100k image
    for tensor, limits, code in [
        (bytes(huge), None, "tensor_dimensions_mismatch"),
        (GOOD, ResourceLimits(transport_bytes=1000), "tensor_transport_limit"),
        (GOOD, ResourceLimits(tensor_bytes=1000), "tensor_decoded_limit"),
    ]:
        failure, _, _ = await run((Image(tensor),), limits=limits)
        assert failure.code == code and failure.partial_artifacts == ()


@pytest.mark.asyncio
async def test_deadline_expiry_after_dispatch_leaves_the_outcome_unknown():
    failure, _, _ = await run((Hang(),), timeouts=TimeoutPolicy(connect=1.0, total=0.5))
    assert failure.category == ErrorCategory.TIMEOUT
    assert (
        failure.submission_stage == SubmissionStage.POSSIBLY_SUBMITTED
        and failure.remote_outcome == RemoteOutcome.UNKNOWN
    )


@pytest.mark.asyncio
async def test_unknown_model_is_rejected_before_any_network_use():
    failure, events, seen = await run((Image(GOOD),), request=make_request(model_id="missing.ckpt"))
    assert failure.code == "unknown_model" and failure.remote_outcome == RemoteOutcome.NOT_STARTED
    assert {e.kind for e in events} <= {"preparing"} and seen.generate_requests == []
    for request in (make_request(batch_size=2),):
        invalid, events, seen = await run((Image(GOOD),), request=request)
        assert invalid.category == ErrorCategory.VALIDATION
        assert invalid.submission_stage == SubmissionStage.NOT_SUBMITTED
        assert invalid.remote_outcome == RemoteOutcome.NOT_STARTED
        assert {e.kind for e in events} <= {"preparing"} and seen.generate_requests == []


@pytest.mark.asyncio
async def test_one_lane_cancellation_while_waiting_and_close_refusal():
    async with scripted_server((Hang(),), ECHO) as server:
        adapter = await opened(server.host, server.port, timeouts=TimeoutPolicy(connect=1.0, total=10.0))
        await adapter.get_inventory(refresh=True)
        first_events, second_events = [], []
        first = asyncio.create_task(adapter.generate(make_request(), on_event=first_events.append))
        await asyncio.wait_for(server.seen.hanging.wait(), 5)
        second = asyncio.create_task(adapter.generate(make_request(request_id="r2"), on_event=second_events.append))
        await asyncio.sleep(0.3)
        assert server.seen.peak_active == 1 and len(server.seen.generate_requests) == 1
        with pytest.raises(RuntimeError):
            await adapter.aclose()  # refuses while generations are active
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert "submission_possible" not in [e.kind for e in second_events]  # never dispatched
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(server.seen.cancelled.wait(), 5)  # mid-stream cancellation closes the server stream
        await adapter.aclose()
        await adapter.aclose()  # idempotent
        assert len(server.seen.generate_requests) == 1

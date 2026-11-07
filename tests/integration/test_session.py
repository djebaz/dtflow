"""Shared session service: per-operation lifetime and state-lock scope, shared by the CLI and the desktop.

CLI behavior and exit codes stay covered by the existing CLI tests; this owns only what the extraction added.
"""

import pytest

from dtflow.composition import BackendSelection
from dtflow.core.models import ResolvedGenerationRequest, ValidationError
from dtflow.persistence.store import SQLiteAttempts, StateBusyError
from dtflow.services.session import generate_once, read_attempt, refresh_inventory

FAKE = BackendSelection("fake")


def request(identifier="request"):
    return ResolvedGenerationRequest(identifier, "fixture", "fake-fixture", 0)


@pytest.mark.asyncio
async def test_generate_once_saves_and_releases_the_state_lock(tmp_path):
    outcome = await generate_once(FAKE, tmp_path, request("one"))
    assert outcome.fake and outcome.final_image and outcome.output_paths and outcome.diagnostics == ()
    assert outcome.request.request_id == "one"
    evidence = await read_attempt(tmp_path, "one")
    assert evidence.attempt_id == outcome.attempt_id and evidence.state == "succeeded"
    assert await read_attempt(tmp_path, "missing") is None
    # The lock is held per operation only: a second owner can open the directory between operations.
    other = SQLiteAttempts(tmp_path)
    await other.open()
    await other.aclose()


@pytest.mark.asyncio
async def test_busy_state_fails_clearly_and_inventory_needs_no_lock(tmp_path):
    owner = SQLiteAttempts(tmp_path)
    await owner.open()
    try:
        with pytest.raises(StateBusyError):
            await generate_once(FAKE, tmp_path, request("busy"))
        assert (await refresh_inventory(FAKE, tmp_path)).entries  # read-only: no ownership needed
    finally:
        await owner.aclose()
    assert await read_attempt(tmp_path, "busy") is None  # nothing was dispatched while busy


@pytest.mark.asyncio
async def test_invalid_selection_never_opens_state(tmp_path):
    with pytest.raises(ValidationError):
        await generate_once(BackendSelection("local"), tmp_path / "state", request())
    assert not (tmp_path / "state").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,code,message,remote_outcome,expected_submissions",
    [
        ("connectivity", "connection_failed", "Connection refused.", "not_started", 2),
        ("timeout", "timed_out_before_submission", "Timed out.", "not_started", 2),
        ("connectivity", "connection_lost", "Connection lost after dispatch.", "unknown", 1),
        ("authentication", "unauthorized", "Permission denied.", "not_started", 1),
        ("resource_limit", "quota_exhausted", "Quota exhausted.", "failed", 1),
        ("protocol", "grpc_internal", "unknown error processing request", "unknown", 2),
    ],
)
async def test_repeated_transient_failure_stops_after_one_automatic_retry(
    tmp_path, monkeypatch, category, code, message, remote_outcome, expected_submissions
):
    """Offline acceptance: the retry cap and non-retryable outcomes protect against runaway submissions."""
    from dtflow.backends.fake import FakeBackend
    from dtflow.core.models import BackendError, BackendFailure, ErrorCategory, RemoteOutcome
    from dtflow.services import session
    from dtflow.services.generation import GenerationService

    # Skip only the delay; exercise the real shared session, service, attempts and retry policy.
    monkeypatch.setattr(session, "AUTO_RETRY_DELAY", 0)
    submitted = []

    class AlwaysFails(FakeBackend):
        async def generate(self, req, *, on_event):
            submitted.append(req)
            raise BackendError(
                BackendFailure(
                    ErrorCategory(category),
                    code,
                    message,
                    req.request_id,
                    remote_outcome=RemoteOutcome(remote_outcome),
                )
            )

    backend, store = AlwaysFails(), SQLiteAttempts(tmp_path)
    await store.open()
    await backend.open()
    try:
        with pytest.raises(BackendError):
            await session.run_generation(GenerationService(backend, store, tmp_path / "outputs"), request("original"))

        assert len(submitted) == expected_submissions
        assert [req.seed for req in submitted] == [0] * expected_submissions
        assert len({req.request_id for req in submitted}) == expected_submissions
        if expected_submissions == 2:
            assert dict(submitted[1].resolution_evidence)["retry.automatic_of_request"] == submitted[0].request_id
        records = await store.inspect()
        assert len(records) == expected_submissions
        assert {row["remote_outcome"] for row in records} == {remote_outcome}
        assert {row["request"]["seed"] for row in records} == {0}
    finally:
        await backend.aclose()
        await store.aclose()

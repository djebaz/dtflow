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

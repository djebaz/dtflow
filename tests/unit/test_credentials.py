"""Provider precedence only; cloud HTTP authentication belongs to 2C."""

import pytest

from dtflow.core.models import BackendError
from dtflow.services.credentials import EnvironmentCredentials, FakeSavedCredentials, resolve_credentials


@pytest.mark.asyncio
async def test_environment_presence_wins_without_saved_reads():
    saved = FakeSavedCredentials("saved-private")
    credential = await resolve_credentials(EnvironmentCredentials({"DRAWTHINGS_API_KEY": " env-private "}), saved)
    assert credential.source == "environment"
    assert credential.secret == "env-private"
    assert "env-private" not in repr(credential)
    assert saved.reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "  "])
async def test_empty_selected_environment_fails_without_fallback(value):
    saved = FakeSavedCredentials("saved")
    with pytest.raises(BackendError) as exc:
        await resolve_credentials(EnvironmentCredentials({"DRAWTHINGS_API_KEY": value}), saved)
    assert exc.value.failure.code == "empty_environment_key"
    assert saved.reads == 0


@pytest.mark.asyncio
async def test_missing_environment_uses_injected_saved_provider_only():
    saved = FakeSavedCredentials("saved")
    assert (await resolve_credentials(EnvironmentCredentials({}), saved)).source == "saved"
    with pytest.raises(BackendError) as exc:
        await resolve_credentials(EnvironmentCredentials({}), FakeSavedCredentials())
    assert exc.value.failure.code == "missing_credentials"

"""Provider precedence only; cloud HTTP authentication belongs to 2C."""

import pytest

from dtflow.core.models import BackendError
from dtflow.services.credentials import EnvironmentCredentials, FakeSavedCredentials, resolve_credentials


@pytest.mark.asyncio
async def test_saved_key_wins_over_environment():
    saved = FakeSavedCredentials("saved-private")
    credential = await resolve_credentials(EnvironmentCredentials({"DRAWTHINGS_API_KEY": " env-private "}), saved)
    assert credential.source == "saved"
    assert credential.secret == "saved-private"
    assert "saved-private" not in repr(credential)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "  "])
async def test_empty_selected_environment_fails_without_fallback(value):
    with pytest.raises(BackendError) as exc:
        await resolve_credentials(EnvironmentCredentials({"DRAWTHINGS_API_KEY": value}), FakeSavedCredentials())
    assert exc.value.failure.code == "empty_environment_key"


@pytest.mark.asyncio
async def test_environment_is_the_fallback_when_nothing_is_saved():
    env = EnvironmentCredentials({"DRAWTHINGS_API_KEY": "env"})
    assert (await resolve_credentials(env, FakeSavedCredentials())).source == "environment"
    with pytest.raises(BackendError) as exc:
        await resolve_credentials(EnvironmentCredentials({}), FakeSavedCredentials())
    assert exc.value.failure.code == "missing_credentials"


@pytest.mark.asyncio
async def test_optional_desktop_vault_discovery_is_deferred_and_off_caller_thread():
    import threading

    from dtflow.services.credentials import OptionalSavedCredentials

    caller = threading.get_ident()
    probes = []

    class Saved:
        supported = True

        def available(self):
            probes.append(threading.get_ident())
            return self.supported

        async def get(self):
            return await FakeSavedCredentials("fixture-secret").get()

    saved = Saved()
    provider = OptionalSavedCredentials(saved)
    assert probes == []
    # The saved key is consulted first, off the caller thread; an unavailable store falls back to the environment.
    assert (
        await resolve_credentials(EnvironmentCredentials({"DRAWTHINGS_API_KEY": "fixture-env"}), provider)
    ).source == "saved"
    assert len(probes) == 1
    saved.supported = False
    assert await provider.get() is None
    assert (
        await resolve_credentials(EnvironmentCredentials({"DRAWTHINGS_API_KEY": "fixture-env"}), provider)
    ).source == "environment"
    assert len(probes) == 3 and all(thread != caller for thread in probes)

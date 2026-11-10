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


@pytest.mark.asyncio
async def test_saved_key_alias_never_exposes_its_credential():
    """Reject secret-bearing names and ignore legacy unsafe aliases in diagnostics."""
    from dtflow.core.models import ValidationError
    from dtflow.diagnostics import cloud_key_data
    from dtflow.services.cloud_usage import key_identity
    from dtflow.services.credentials import SavedCredentials

    class Vault:
        passwords = {}

        def get_password(self, service, account):
            return self.passwords.get((service, account))

        def set_password(self, service, account, value):
            self.passwords[(service, account)] = value

    secret = "FAKE-PRIVATE-CLOUD-CREDENTIAL"
    saved = SavedCredentials(vault=Vault())
    key_id = saved.save(secret, "Studio")
    credential = await saved.get()
    assert cloud_key_data(key_identity(secret), "saved", alias=credential.alias)["key_alias"] == "Studio"

    with pytest.raises(ValidationError):
        saved.save(secret, secret)
    with pytest.raises(ValidationError):
        saved.rename(key_id, secret)

    saved._memory["keys"][0]["name"] = secret  # Simulate a pre-fix saved index.
    entry = saved.entries()[0]
    credential = await saved.get()
    event = cloud_key_data(key_identity(secret), credential.source, alias=credential.alias)
    assert entry.alias is None and credential.alias is None
    assert event["key_alias"] == entry.name
    assert secret not in entry.name and secret not in str(event)

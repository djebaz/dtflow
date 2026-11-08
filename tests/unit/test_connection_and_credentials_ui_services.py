"""Shared services introduced for the Qt shell: saved-key store, status presentation and checked composition."""

import pytest
from support.fake_vault import Vault

from dtflow.composition import BackendSelection
from dtflow.core.models import BackendError, ConnectionStatus, ValidationError
from dtflow.services.connection import check_connection, present_failure, present_status
from dtflow.services.credentials import SavedCredentials, SecureStorageUnavailable, credential_status


@pytest.mark.asyncio
async def test_saved_key_round_trip_precedence_status_and_no_plaintext_fallback():
    vault = Vault()
    saved = SavedCredentials(vault)
    status = credential_status({}, saved)
    assert (status["selected"], status["saved"]) == ("none", "absent")
    saved.save("  private-key  ")
    assert (await saved.get()).secret == "private-key"
    assert "private-key" not in repr(await saved.get())
    assert credential_status({}, saved)["selected"] == "saved"
    assert credential_status({"DRAWTHINGS_API_KEY": "env"}, saved)["selected"] == "saved"  # the saved key wins
    with pytest.raises(BackendError) as empty:
        saved.save("   ")
    assert empty.value.failure.code == "empty_saved_key"
    saved.remove()
    saved.remove()  # idempotent
    assert await saved.get() is None


def test_non_windows_vault_keyring_is_refused_never_used_as_plaintext_storage(monkeypatch):
    """Platform independent: the active keyring is replaced, so no platform query or real vault is touched."""
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: FailKeyring())
    assert SavedCredentials().available() is False
    with pytest.raises(SecureStorageUnavailable):
        SavedCredentials().save("x")
    assert credential_status({}, SavedCredentials())["saved"] == "unavailable"


@pytest.mark.parametrize(
    ("transport", "authentication", "level", "headline"),
    [
        ("ok", "ok", "ok", "Connected"),
        ("ok", "not_applicable", "ok", "Connected"),
        ("ok", "failed", "error", "Authentication failed"),
        ("failed", "unknown", "error", "Unreachable"),
        ("unknown", "unknown", "warning", "Status unknown"),
    ],
)
def test_status_presentation(transport, authentication, level, headline):
    status = ConnectionStatus("b", "host:1", "2026-01-01T00:00:00Z", transport, authentication, "unknown")
    shown = present_status(status)
    assert (shown.level, shown.headline) == (level, headline)


def test_failure_presentation_uses_codes_not_text():
    assert present_failure(ValidationError("bad")).headline == "Invalid selection"
    assert present_failure(TimeoutError()).headline == "Timed out"
    assert present_failure(RuntimeError("SECRET")).detail == "RuntimeError"


@pytest.mark.asyncio
async def test_check_connection_composes_fake_backend_and_saved_provider_selects_cloud(monkeypatch):
    monkeypatch.delenv("DRAWTHINGS_API_KEY", raising=False)  # a developer's live-test key must not reach this check
    assert (await check_connection(BackendSelection("fake"))).transport == "ok"
    with pytest.raises(ValidationError):  # no env key and no saved provider: refused before any network use
        await check_connection(BackendSelection("cloud"), saved=None)

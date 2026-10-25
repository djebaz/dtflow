"""Deterministic environment-first credential resolution plus the secure saved-key store."""

import asyncio
import os
from collections.abc import Mapping

from dtflow.core.models import BackendError, BackendFailure, Credential, CredentialProvider, ErrorCategory

KEY = "DRAWTHINGS_API_KEY"


class EnvironmentCredentials:
    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = os.environ if environ is None else environ

    async def get(self) -> Credential | None:
        if KEY not in self.environ:
            return None
        value = self.environ[KEY].strip()
        if not value:
            raise BackendError(
                BackendFailure(
                    ErrorCategory.CREDENTIALS, "empty_environment_key", "Selected environment credential is empty."
                )
            )
        return Credential("environment", value)


class FakeSavedCredentials:
    """Injected test provider only; not Windows Credential Manager storage."""

    def __init__(self, value: str | None = None):
        self._value = value
        self.reads = 0

    async def get(self) -> Credential | None:
        self.reads += 1
        if self._value is None:
            return None
        value = self._value.strip()
        if not value:
            raise BackendError(
                BackendFailure(ErrorCategory.CREDENTIALS, "empty_saved_key", "Selected saved credential is empty.")
            )
        return Credential("saved", value)


async def resolve_credentials(environment: CredentialProvider, saved: CredentialProvider) -> Credential:
    """Select one source; never switch after selected validation/auth failure."""
    credential = await environment.get()
    if credential is None:
        credential = await saved.get()
    if credential is None:
        raise BackendError(
            BackendFailure(ErrorCategory.CREDENTIALS, "missing_credentials", "Cloud credentials are missing.")
        )
    return credential


SERVICE = "DTFlow"
ACCOUNT = "drawthings-api-key"


class SecureStorageUnavailable(BackendError):
    """No secure operating-system store exists; a plaintext fallback is never offered."""

    def __init__(self):
        super().__init__(
            BackendFailure(
                ErrorCategory.CREDENTIALS,
                "secure_storage_unavailable",
                "Secure credential storage (Windows Credential Manager) is unavailable.",
            )
        )


def _windows_vault():
    """Return the keyring module only when its active backend is Windows Credential Manager."""
    try:
        import keyring
        from keyring.backends.Windows import WinVaultKeyring
    except Exception:  # missing module or non-Windows import failure
        raise SecureStorageUnavailable() from None
    if not isinstance(keyring.get_keyring(), WinVaultKeyring):
        raise SecureStorageUnavailable()
    return keyring


class SavedCredentials:
    """The saved DT+ key in Windows Credential Manager; the secret is never logged, echoed or written to a file."""

    def __init__(self, vault=None):
        self._vault = vault

    def _store(self):
        return self._vault if self._vault is not None else _windows_vault()

    def available(self) -> bool:
        try:
            self._store()
        except SecureStorageUnavailable:
            return False
        return True

    async def get(self) -> Credential | None:
        value = self._store().get_password(SERVICE, ACCOUNT)
        if value is None:
            return None
        if not value.strip():
            raise BackendError(
                BackendFailure(ErrorCategory.CREDENTIALS, "empty_saved_key", "Selected saved credential is empty.")
            )
        return Credential("saved", value.strip())

    def save(self, secret: str) -> None:
        if not secret.strip():
            raise BackendError(
                BackendFailure(ErrorCategory.CREDENTIALS, "empty_saved_key", "An empty API key cannot be saved.")
            )
        self._store().set_password(SERVICE, ACCOUNT, secret.strip())

    def remove(self) -> None:
        store = self._store()
        if store.get_password(SERVICE, ACCOUNT) is not None:
            store.delete_password(SERVICE, ACCOUNT)

    def present(self) -> bool:
        return self._store().get_password(SERVICE, ACCOUNT) is not None


class OptionalSavedCredentials:
    """Defer desktop vault discovery until a worker needs credentials, never a Qt getter."""

    def __init__(self, saved: SavedCredentials):
        self.saved = saved

    async def get(self) -> Credential | None:
        if not await asyncio.to_thread(self.saved.available):
            return None
        return await self.saved.get()


def credential_status(
    environ: Mapping[str, str] | None = None, saved: SavedCredentials | None = None
) -> dict[str, str]:
    """Which source cloud checks would select, without reading or returning any secret."""
    environ = os.environ if environ is None else environ
    if KEY in environ:
        return {"selected": "environment", "saved": "not consulted"}
    try:
        present = (saved or SavedCredentials()).present()
    except SecureStorageUnavailable:
        return {"selected": "none", "saved": "unavailable"}
    return {"selected": "saved" if present else "none", "saved": "present" if present else "absent"}

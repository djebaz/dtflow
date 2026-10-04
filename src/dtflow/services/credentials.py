"""Deterministic environment-first credential resolution; no secure storage yet."""

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

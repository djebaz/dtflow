"""Composition root: the one place that turns an explicit backend selection into a concrete adapter.

Services depend on the backend contract only. The CLI and a later Qt shell call this module; nothing falls back from one
backend to another, and credentials are read from the environment by providers, never passed on a command line.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dtflow.backends.drawthings.backend import DrawThingsBackend, DrawThingsEndpoint
from dtflow.backends.drawthings.cloud import DirectCloudBackend
from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.backends.drawthings.tls import TlsPolicy
from dtflow.backends.fake import FakeBackend
from dtflow.core.models import Backend, Credential, CredentialProvider, TimeoutPolicy, ValidationError
from dtflow.services.credentials import KEY, EnvironmentCredentials, resolve_credentials

BACKENDS = ("fake", "local", "cloud")
SHARED_SECRET = "DRAWTHINGS_SHARED_SECRET"


class _NoSavedCredentials:
    """Used where no secure store exists; the environment is then the only cloud source."""

    async def get(self) -> Credential | None:
        return None


class _CloudCredentials:
    """Resolve at first use through the shared precedence rule; selection never changes after a failure."""

    def __init__(self, environ: Mapping[str, str], saved: CredentialProvider):
        self._environment = EnvironmentCredentials(environ)
        self._saved = saved

    async def get(self) -> Credential:
        return await resolve_credentials(self._environment, self._saved)


class _SharedSecret:
    def __init__(self, environ: Mapping[str, str]):
        self._environ = environ

    async def get(self) -> Credential | None:
        value = self._environ.get(SHARED_SECRET, "").strip()
        return Credential("environment", value) if value else None


@dataclass(frozen=True, slots=True)
class BackendSelection:
    name: str
    endpoint: str | None = None
    cloud_concurrency: int = 4
    scenario: str = "success"


def parse_endpoint(text: str) -> DrawThingsEndpoint:
    host, separator, port = text.strip().rpartition(":")
    if not separator or not host or not port.isdigit():
        raise ValidationError("Endpoint must be HOST:PORT.")
    return DrawThingsEndpoint(host.strip("[]"), int(port))


def endpoint_label(selection: BackendSelection) -> str | None:
    """The credential-free identity a local backend reports for this selection, for cache matching."""
    if selection.name != "local" or not selection.endpoint:
        return None
    return parse_endpoint(selection.endpoint).label


def create_backend(
    selection: BackendSelection,
    *,
    timeouts: TimeoutPolicy | None = None,
    environ: Mapping[str, str] | None = None,
    tls: TlsPolicy | None = None,
    saved: CredentialProvider | None = None,
) -> Backend:
    """Build the explicitly selected backend; ``tls`` is the injection point for fixture trust only.

    ``saved`` is the secure saved-key provider (the Qt shell passes Credential Manager on Windows); the CLI passes none,
    so headless runs stay environment-only.
    """
    environ = os.environ if environ is None else environ
    if selection.name == "fake":
        return FakeBackend(scenario=selection.scenario)
    if selection.name == "local":
        if not selection.endpoint:
            raise ValidationError("The local backend requires --endpoint HOST:PORT.")
        endpoint = parse_endpoint(selection.endpoint)
        if tls is not None:
            endpoint = DrawThingsEndpoint(endpoint.host, endpoint.port, tls)
        return DrawThingsBackend(endpoint, timeouts=timeouts, shared_secret=_SharedSecret(environ))
    if selection.name == "cloud":
        if saved is None:
            saved = _NoSavedCredentials()
        if KEY not in environ and isinstance(saved, _NoSavedCredentials):
            raise ValidationError(f"The cloud backend requires {KEY} in the environment.")
        return DirectCloudBackend(
            CloudAuth(_CloudCredentials(environ, saved)), max_concurrency=selection.cloud_concurrency, timeouts=timeouts
        )
    raise ValidationError("Unknown backend selection.")

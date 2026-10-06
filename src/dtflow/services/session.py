"""Shared, Qt-independent generation session: the one path from a resolved request to a durable saved result.

The CLI and the desktop both call this module; neither re-implements it. Backend creation goes through the composition
root, and every operation is self-contained: it creates and opens its backend, takes the state directory's ownership
lock only while it needs durable state, and closes everything in order. Nothing holds the lock between operations, so a
CLI run next to an open desktop either succeeds between operations or fails with ``StateBusyError`` (nothing is
dispatched) instead of the desktop blocking it for the window's lifetime.
"""

import asyncio
import hashlib
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from dtflow.composition import BackendSelection, create_backend, endpoint_label
from dtflow.core.models import (
    AssetReconciliation,
    BackendError,
    CredentialProvider,
    Designation,
    EventSink,
    InventorySnapshot,
    PreflightDiagnostic,
    ResolvedGenerationRequest,
    TimeoutPolicy,
    ValidationError,
)
from dtflow.persistence.assets import AssetRegistry
from dtflow.persistence.inventory_cache import load_inventory, save_inventory
from dtflow.persistence.settings import load_cloud_concurrency
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.assets import AssetService
from dtflow.services.credentials import EnvironmentCredentials, resolve_credentials
from dtflow.services.generation import GenerationService
from dtflow.services.preflight import apply_trigger_prefixes

log = logging.getLogger(__name__)

ASSET_NOTE = (
    "Local registry view of what this machine measured and a server once acknowledged; not remote account "
    "inventory. Reuse is re-validated by a fresh metadata check."
)


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """Immutable result of one saved generation; ``final_image`` is the first FINAL artifact for display only."""

    attempt_id: str
    backend_id: str
    output_paths: tuple[str, ...]
    metadata_path: str
    warnings: tuple[str, ...]
    diagnostics: tuple[PreflightDiagnostic, ...]
    request: ResolvedGenerationRequest  # the effective request actually dispatched (after any trigger prefixes)
    fake: bool = False
    final_image: bytes | None = None


@dataclass(frozen=True, slots=True)
class AttemptEvidence:
    """What the durable store recorded for an attempt; the only authority for wording after a local stop."""

    attempt_id: str
    state: str
    submission_stage: str
    remote_outcome: str
    artifacts: tuple[str, ...] = ()
    failure_code: str | None = None


def selection_for(
    name: str,
    endpoint: str | None,
    state: Path,
    *,
    scenario: str = "success",
    delay: float = 0.0,
    previews: bool = False,
) -> BackendSelection:
    """The explicit backend selection, with the persisted cloud concurrency maximum applied for cloud.

    ``scenario``, ``delay`` and ``previews`` configure the fake backend's simulation and mean nothing elsewhere.
    """
    return BackendSelection(
        name, endpoint, load_cloud_concurrency(state) if name == "cloud" else 4, scenario, delay, previews
    )


def asset_validator(backend) -> AssetService | None:
    """Metadata-only validation for dispatch, available only where the backend can negotiate cloud assets."""
    return AssetService(backend) if hasattr(backend, "negotiate_asset") else None


async def bind_prepared(
    state: Path, backend_name: str, request: ResolvedGenerationRequest, asset_ids: list[str] | tuple[str, ...] | None
) -> ResolvedGenerationRequest:
    """Attach confirmed local receipts to the request; reads only the local registry and never uploads."""
    if not asset_ids:
        return request
    if backend_name != "cloud":
        raise ValidationError("--asset applies to the cloud backend only.")
    registry = AssetRegistry(state)
    await registry.open()
    try:
        prepared = tuple([await registry.prepared(asset_id) for asset_id in asset_ids])
    finally:
        await registry.aclose()
    selected = {item.identifier for item in request.loras}
    for item in prepared:
        if item.identifier not in selected:
            raise ValidationError(f"--asset {item.receipt_id[:8]} needs a matching --lora {item.identifier}:WEIGHT.")
    evidence = (*request.resolution_evidence, ("prepared_assets", ",".join(item.receipt_id for item in prepared)))
    return replace(request, prepared_assets=prepared, resolution_evidence=evidence)


@asynccontextmanager
async def open_session(
    selection: BackendSelection,
    state: Path,
    *,
    output: Path | None = None,
    timeouts: TimeoutPolicy | None = None,
    saved: CredentialProvider | None = None,
    durable: bool = True,
) -> AsyncIterator[GenerationService]:
    """Create, open and (always) close the selected backend and, when ``durable``, the state store.

    The backend is built before any state is touched, so invalid selections never open or lock state. ``durable=False``
    skips the store (and its ownership lock) for read-only operations such as inventory refresh; such a service cannot
    generate.
    """
    policy = timeouts or TimeoutPolicy()
    backend = create_backend(selection, timeouts=policy, saved=saved)
    store = SQLiteAttempts(state) if durable else None
    try:
        if store is not None:
            await store.open()
        async with asyncio.timeout(policy.connect):
            await backend.open()
        yield GenerationService(
            backend, store, output or state / "outputs", timeouts=policy, assets=asset_validator(backend)
        )
    finally:
        try:
            async with asyncio.timeout(policy.cleanup):
                await backend.aclose()
        finally:
            if store is not None:
                await store.aclose()


async def run_generation(
    service: GenerationService,
    request: ResolvedGenerationRequest,
    *,
    prefix: bool = False,
    on_event: EventSink | None = None,
) -> GenerationOutcome:
    """Refresh inventory, optionally apply trigger prefixes, run LoRA preflight, generate and save."""
    # Real adapters accept only inventory-listed models, so the generation path refreshes inventory first.
    snapshot = await service.get_inventory(refresh=True)
    if prefix:
        request = apply_trigger_prefixes(request, snapshot)
    diagnostics = await service.preflight(request) if request.loras else ()
    result = await service.generate(request, on_event=on_event)
    backend_id = service.backend.capabilities.backend_id
    final = next((x.encoded for x in result.backend_result.artifacts if x.designation == Designation.FINAL), None)
    return GenerationOutcome(
        result.attempt_id,
        backend_id,
        result.output_paths,
        result.metadata_path,
        result.backend_result.warnings,
        diagnostics,
        request,
        fake=backend_id == "fake",
        final_image=final,
    )


async def generate_once(
    selection: BackendSelection,
    state: Path,
    request: ResolvedGenerationRequest,
    *,
    prefix: bool = False,
    output: Path | None = None,
    timeouts: TimeoutPolicy | None = None,
    saved: CredentialProvider | None = None,
    on_event: EventSink | None = None,
    asset_ids: tuple[str, ...] = (),
) -> GenerationOutcome:
    """One self-contained generation: the desktop's entry point (the CLI composes the same two functions).

    ``asset_ids`` binds confirmed local receipts (cloud only, never uploads) before any backend or state is opened.
    """
    request = await bind_prepared(state, selection.name, request, asset_ids)
    async with open_session(selection, state, output=output, timeouts=timeouts, saved=saved) as service:
        return await run_generation(service, request, prefix=prefix, on_event=on_event)


class _NoSaved:
    async def get(self):
        return None


async def _cache_identity(
    selection: BackendSelection, saved: CredentialProvider | None
) -> tuple[str, str | None, tuple[str, str] | None] | None:
    """Cache kind, expected endpoint and credential for a selection, or ``None`` where nothing is cached."""
    if selection.name == "local":
        return "local", endpoint_label(selection), None
    if selection.name == "cloud":
        credential = await resolve_credentials(EnvironmentCredentials(), saved or _NoSaved())
        return "cloud", None, (credential.source, credential.secret)
    return None


async def cached_inventory(
    selection: BackendSelection,
    state: Path,
    *,
    saved: CredentialProvider | None = None,
    no_cache: bool = False,
) -> InventorySnapshot | None:
    """The disposable startup-prefill snapshot, or ``None`` on any miss; never used to gate a generation."""
    if no_cache:
        return None
    try:
        identity = await _cache_identity(selection, saved)
    except (BackendError, ValidationError):
        log.info("inventory cache miss backend=%s reason=identity", selection.name)
        return None
    if identity is None:
        return None
    kind, endpoint, credential = identity
    snapshot = await asyncio.to_thread(load_inventory, state, kind, endpoint=endpoint, credential=credential)
    log.info("inventory cache %s backend=%s", "hit" if snapshot is not None else "miss reason=absent_or_mismatch", kind)
    return snapshot


class InventoryDisplayMemory:
    """Last successful observations for presentation only, never generation/preflight evidence.

    Keep at most one snapshot per provider. Revalidate endpoint/selected credentials before reuse; store only a
    process-salted identity digest, never the credential. Persistent fallback uses the existing cache contract.
    """

    def __init__(self):
        self._salt = secrets.token_bytes(32)
        self._snapshots: dict[str, tuple[tuple, InventorySnapshot]] = {}

    async def _scope(self, selection, saved):
        identity = await _cache_identity(selection, saved)
        return self._scope_from_identity(selection, identity)

    def _scope_from_identity(self, selection, identity):
        if identity is None:
            return (selection.name, None, None)
        kind, endpoint, credential = identity
        fingerprint = None
        if credential is not None:
            source, key = credential
            fingerprint = (source, hashlib.sha256(self._salt + key.encode()).digest())
        return (kind, endpoint, fingerprint)

    async def restore(
        self,
        selection: BackendSelection,
        state: Path,
        *,
        saved: CredentialProvider | None = None,
        no_cache: bool = False,
    ) -> tuple[InventorySnapshot, str] | None:
        try:
            scope = await self._scope(selection, saved)
        except (BackendError, ValidationError):
            self._snapshots.pop(selection.name, None)
            return None
        previous = self._snapshots.get(selection.name)
        if previous is not None and previous[0] == scope:
            return previous[1], "remembered"
        self._snapshots.pop(selection.name, None)
        snapshot = await cached_inventory(selection, state, saved=saved, no_cache=no_cache)
        try:
            still_matches = await self._scope(selection, saved) == scope
        except (BackendError, ValidationError):
            still_matches = False
        if not still_matches:
            return None
        # A live refresh can finish while the disk read is pending. Never overwrite that newer observation.
        previous = self._snapshots.get(selection.name)
        if previous is not None and previous[0] == scope:
            return previous[1], "remembered"
        if snapshot is None:
            return None
        self._snapshots[selection.name] = (scope, snapshot)
        return snapshot, "cached"

    async def refresh(
        self,
        selection: BackendSelection,
        state: Path,
        *,
        saved: CredentialProvider | None = None,
        no_cache: bool = False,
    ) -> InventorySnapshot:
        scope = await self._scope(selection, saved)
        snapshot = await refresh_inventory(selection, state, saved=saved, no_cache=True)
        identity = await _cache_identity(selection, saved)
        # Failed refreshes never reach this assignment; credential changes never bind old evidence to a new key.
        if self._scope_from_identity(selection, identity) != scope:
            raise ValidationError("Inventory identity changed while refreshing; refresh for the current selection.")
        self._snapshots[selection.name] = (scope, snapshot)
        if not no_cache and identity is not None:
            kind, _, credential = identity
            try:
                await asyncio.to_thread(save_inventory, state, kind, snapshot, credential=credential)
            except OSError as exc:
                log.warning("inventory cache not written backend=%s reason=%s", kind, type(exc).__name__)
        return snapshot


async def refresh_inventory(
    selection: BackendSelection,
    state: Path,
    *,
    timeouts: TimeoutPolicy | None = None,
    saved: CredentialProvider | None = None,
    no_cache: bool = False,
) -> InventorySnapshot:
    """Explicit inventory refresh; takes no state lock because it records nothing durable.

    A successful refresh also rewrites the disposable cache unless ``no_cache``; a failed refresh leaves it untouched.
    """
    async with open_session(selection, state, timeouts=timeouts, saved=saved, durable=False) as service:
        snapshot = await service.get_inventory(refresh=True)
    log.info(
        "inventory refresh ok backend=%s entries=%d metadata=%s",
        selection.name,
        len(snapshot.entries),
        dict(snapshot.diagnostics).get("model_metadata", "unknown"),
    )
    if not no_cache:
        await remember_inventory(selection, state, snapshot, saved)
    return snapshot


async def remember_inventory(
    selection: BackendSelection, state: Path, snapshot: InventorySnapshot, saved: CredentialProvider | None = None
) -> None:
    """Best-effort write of the disposable cache; a failure is logged and never raised."""
    try:
        identity = await _cache_identity(selection, saved)
        if identity is None:
            return
        kind, _, credential = identity
        await asyncio.to_thread(save_inventory, state, kind, snapshot, credential=credential)
        log.info("inventory cache write backend=%s", kind)
    except (BackendError, ValidationError, OSError) as exc:
        log.warning("inventory cache not written backend=%s reason=%s", selection.name, type(exc).__name__)


async def read_attempt(state: Path, request_id: str) -> AttemptEvidence | None:
    """Recorded evidence for the attempt created for ``request_id`` (for example after a local stop), if any."""
    store = SQLiteAttempts(state)
    try:
        await store.open()
        for record in await store.inspect():
            if record["request_id"] == request_id:
                failure = record["failure"] or {}
                return AttemptEvidence(
                    record["attempt_id"],
                    record["state"],
                    record["submission_stage"],
                    record["remote_outcome"],
                    tuple(record["artifacts"]),
                    failure.get("code"),
                )
        return None
    finally:
        await store.aclose()


async def check_loras(
    selection: BackendSelection,
    state: Path,
    request: ResolvedGenerationRequest,
    *,
    asset_ids: tuple[str, ...] = (),
    timeouts: TimeoutPolicy | None = None,
    saved: CredentialProvider | None = None,
) -> tuple[PreflightDiagnostic, ...]:
    """Explicit LoRA preflight against a freshly refreshed inventory; dispatches nothing and takes no state lock."""
    request = await bind_prepared(state, selection.name, request, asset_ids)
    async with open_session(selection, state, timeouts=timeouts, saved=saved, durable=False) as service:
        await service.get_inventory(refresh=True)
        return await service.preflight(request)


def asset_view(record: dict) -> dict:
    keys = (
        "asset_id", "kind", "identifier", "state", "evidence", "source", "sha256", "size", "backend_id", "origin",
        "remote_id", "created_at_utc", "confirmed_at_utc", "updated_at_utc",
    )  # fmt: skip
    view = {key: record.get(key) for key in keys}
    if "operations" in record:
        view["operations"] = record["operations"]
    return view


@asynccontextmanager
async def open_assets(
    state: Path, *, saved: CredentialProvider | None = None, timeouts: TimeoutPolicy | None = None
) -> AsyncIterator[AssetService]:
    """The explicit cloud backend plus the exclusive asset writer registry; the backend is built before any state."""
    policy = timeouts or TimeoutPolicy()
    backend = create_backend(BackendSelection("cloud"), timeouts=policy, saved=saved)
    registry = AssetRegistry(state, writer=True)
    try:
        if not hasattr(backend, "negotiate_asset"):
            raise ValidationError("The selected backend cannot prepare cloud assets.")
        await registry.open()
        yield AssetService(backend, registry)
    finally:
        try:
            async with asyncio.timeout(policy.cleanup):
                await backend.aclose()
        finally:
            await registry.aclose()


async def prepare_asset(
    state: Path,
    file: Path,
    name: str | None = None,
    *,
    provenance: str,
    saved: CredentialProvider | None = None,
) -> dict:
    """The only operation that may upload: prepare one converted LoRA and return its local record."""
    async with open_assets(state, saved=saved) as service:
        prepared = await service.prepare(file, name, provenance=provenance)
        return await service.registry.get(prepared.receipt_id)


async def reconcile_asset(
    state: Path, asset_id: str, *, saved: CredentialProvider | None = None
) -> tuple[dict, AssetReconciliation]:
    """Metadata-only server check of one local record; never uploads."""
    async with open_assets(state, saved=saved) as service:
        return await service.reconcile(asset_id)


async def list_assets(state: Path) -> list[dict]:
    """Local registry receipts and unresolved operations (no network, no writer lock)."""
    registry = AssetRegistry(state)
    await registry.open()
    try:
        return [asset_view(record) for record in await registry.list()]
    finally:
        await registry.aclose()

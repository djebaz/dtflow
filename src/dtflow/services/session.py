"""Shared, Qt-independent generation session: the one path from a resolved request to a durable saved result.

The CLI and the desktop both call this module; neither re-implements it. Backend creation goes through the composition
root, and every operation is self-contained: it creates and opens its backend, takes the state directory's ownership
lock only while it needs durable state, and closes everything in order. Nothing holds the lock between operations, so a
CLI run next to an open desktop either succeeds between operations or fails with ``StateBusyError`` (nothing is
dispatched) instead of the desktop blocking it for the window's lifetime.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from dtflow.composition import BackendSelection, create_backend
from dtflow.core.models import (
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
from dtflow.persistence.settings import load_cloud_concurrency
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.assets import AssetService
from dtflow.services.generation import GenerationService
from dtflow.services.preflight import apply_trigger_prefixes

log = logging.getLogger(__name__)


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


def selection_for(name: str, endpoint: str | None, state: Path, *, scenario: str = "success") -> BackendSelection:
    """The explicit backend selection, with the persisted cloud concurrency maximum applied for cloud."""
    return BackendSelection(name, endpoint, load_cloud_concurrency(state) if name == "cloud" else 4, scenario)


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
) -> GenerationOutcome:
    """One self-contained generation: the desktop's entry point (the CLI composes the same two functions)."""
    async with open_session(selection, state, output=output, timeouts=timeouts, saved=saved) as service:
        return await run_generation(service, request, prefix=prefix, on_event=on_event)


async def refresh_inventory(
    selection: BackendSelection,
    state: Path,
    *,
    timeouts: TimeoutPolicy | None = None,
    saved: CredentialProvider | None = None,
) -> InventorySnapshot:
    """Explicit inventory refresh; takes no state lock because it records nothing durable."""
    async with open_session(selection, state, timeouts=timeouts, saved=saved, durable=False) as service:
        return await service.get_inventory(refresh=True)


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

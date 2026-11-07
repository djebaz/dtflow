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
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dtflow.composition import BackendSelection, create_backend, endpoint_label, image_endpoint_label
from dtflow.core.image_inputs import ImageInputSnapshot, PreparedImageInputs
from dtflow.core.image_operations import ImageOperationPermit, ImageOperationSupport, inspect_image_operation
from dtflow.core.model_profiles import exact_model_description_digest
from dtflow.core.models import (
    AssetReconciliation,
    BackendError,
    BackendEvent,
    CredentialProvider,
    Designation,
    EventSink,
    InventorySnapshot,
    PreflightDiagnostic,
    PreparedAsset,
    ResolvedGenerationRequest,
    TimeoutPolicy,
    ValidationError,
)
from dtflow.core.retry_policy import AUTO_RETRIES, AUTO_RETRY_DELAY, auto_retry_request, auto_retryable
from dtflow.core.tasks import settle
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.assets import AssetRegistry
from dtflow.persistence.gallery_snapshot import schedule_publish
from dtflow.persistence.inventory_cache import load_inventory, save_inventory
from dtflow.persistence.settings import load_cloud_concurrency
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.assets import AssetService
from dtflow.services.credentials import EnvironmentCredentials, resolve_credentials
from dtflow.services.generation import GenerationService
from dtflow.services.image_inputs import restore_image_inputs
from dtflow.services.image_preparation import prepare_image_files, validate_prepared_inputs
from dtflow.services.preflight import apply_trigger_prefixes

log = logging.getLogger(__name__)


def image_operation_status(
    selection: BackendSelection,
    model_id: str,
    operation: str,
    *,
    image_permits: tuple[ImageOperationPermit, ...] = (),
) -> ImageOperationSupport:
    """Read-only capability line: no backend/client/state open, auth or dispatch.

    Reads bundled local description data only; schedule off Qt for cold imports.
    Caller supplies reviewed permits. Probe status does not grant consent, and
    inspection never verifies deployed checkpoint behavior or creates a permit.
    It does not check installation/availability: keep inventory filtering separate
    and display owner-confirmed evidence, never a general compatible verdict.
    """
    backend_id = {"local": "drawthings-grpc", "cloud": "drawthings-cloud"}.get(selection.name)
    if backend_id is None:
        return ImageOperationSupport("unsupported", "Image editing requires a real selected backend.")
    try:
        endpoint = image_endpoint_label(selection)
    except ValidationError:
        return ImageOperationSupport("missing_endpoint", "Select a valid generation endpoint first.")
    return inspect_image_operation(
        backend_id,
        endpoint,
        model_id,
        operation,
        image_permits,
        exact_description_sha256=exact_model_description_digest(model_id),
    )


def owner_probe_permit(
    selection: BackendSelection, model_id: str, operation: str, evidence_id: str
) -> ImageOperationPermit:
    """Exact-scope owner probe permit; the caller must still pass ``allow_image_probe=True`` after user consent."""
    cloud = selection.name == "cloud"
    digest = exact_model_description_digest(model_id) if cloud else None  # None: nothing is sent for the file
    return ImageOperationPermit(
        backend_id="drawthings-cloud" if cloud else "drawthings-grpc",
        endpoint=image_endpoint_label(selection),
        model_id=model_id,
        operation=operation,
        evidence_id=evidence_id,
        status="owner_probe",
        model_description_sha256=digest,
    )


def image_edit_capability(selection: BackendSelection, model_id: str) -> ImageOperationSupport:
    """Read-only capability line for the editor: no client, credential, state or dispatch."""
    try:
        permit = owner_probe_permit(selection, model_id, "inpaint", "capability-check")
    except ValidationError as exc:
        return ImageOperationSupport("unsupported", str(exc))
    support = image_operation_status(selection, model_id, "inpaint", image_permits=(permit,))
    if support.status == "owner_probe":
        reason = "Unverified checkpoint. Edit image sends the source and mask to the backend; cloud may use credits."
        if selection.name == "cloud" and permit.model_description_sha256 is None:
            reason += " No published model description exists for this file; the cloud may return no image."
        return ImageOperationSupport(support.status, reason, support.evidence_id)
    return support


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


async def load_prepared(
    state: Path, backend_name: str, asset_ids: list[str] | tuple[str, ...]
) -> tuple[PreparedAsset, ...]:
    """Confirmed local receipts by id, from the local registry only (never uploads)."""
    if not asset_ids:
        return ()
    if backend_name != "cloud":
        raise ValidationError("--asset applies to the cloud backend only.")
    registry = AssetRegistry(state)
    await registry.open()
    try:
        return tuple([await registry.prepared(asset_id) for asset_id in asset_ids])
    finally:
        await registry.aclose()


async def bind_prepared(
    state: Path, backend_name: str, request: ResolvedGenerationRequest, asset_ids: list[str] | tuple[str, ...] | None
) -> ResolvedGenerationRequest:
    """Attach confirmed local receipts to the request; reads only the local registry and never uploads."""
    if not asset_ids:
        return request
    prepared = await load_prepared(state, backend_name, asset_ids)
    selected = {item.identifier for item in request.loras}
    for item in prepared:
        if item.identifier not in selected:
            raise ValidationError(f"--asset {item.receipt_id[:8]} needs a matching --lora {item.identifier}:WEIGHT.")
    evidence = (*request.resolution_evidence, ("prepared_assets", ",".join(item.receipt_id for item in prepared)))
    return replace(request, prepared_assets=prepared, resolution_evidence=evidence)


async def script_asset_choices(state: Path, backend_name: str) -> tuple[PreparedAsset, ...]:
    """Confirmed receipt pool for queues, scripts and workflows, independent of Generate's selected LoRAs.

    Local reads only. Actual dispatch still validates each selected receipt against
    the active backend/account; a confirmed historical record is not live consent.
    """
    if backend_name != "cloud":
        return ()
    records = await list_assets(state)
    choices = {
        record["identifier"]: record["asset_id"]
        for record in records
        if record["state"] == "confirmed" and record["kind"] == "lora"
    }
    return await load_prepared(state, backend_name, tuple(choices.values()))


@asynccontextmanager
async def open_session(
    selection: BackendSelection,
    state: Path,
    *,
    output: Path | None = None,
    timeouts: TimeoutPolicy | None = None,
    saved: CredentialProvider | None = None,
    durable: bool = True,
    image_permits: tuple[ImageOperationPermit, ...] = (),
    allow_image_probe: bool = False,
) -> AsyncIterator[GenerationService]:
    """Create, open and (always) close the selected backend and, when ``durable``, the state store.

    The backend is built before any state is touched, so invalid selections never open or lock state. ``durable=False``
    skips the store (and its ownership lock) for read-only operations such as inventory refresh; such a service cannot
    generate.
    """
    policy = timeouts or TimeoutPolicy()
    if type(allow_image_probe) is not bool or not isinstance(image_permits, tuple):
        raise ValidationError("Image permits require an immutable tuple and explicit boolean probe policy.")
    if (image_permits or allow_image_probe) and not durable:
        raise ValidationError("Image generation requires durable state ownership.")
    image_options = {}
    if image_permits or allow_image_probe:
        # The adapter calls this synchronous loader on its worker, never on Qt
        # or the orchestration loop, after the attempt snapshot is committed.
        image_options = {
            "image_permits": image_permits,
            "image_inputs_loader": lambda snapshot: restore_image_inputs(state, snapshot),
            "allow_image_probe": allow_image_probe,
        }
    backend = create_backend(selection, timeouts=policy, saved=saved, **image_options)
    store = SQLiteAttempts(state) if durable else None
    try:
        if store is not None:
            await store.open()
        async with asyncio.timeout(policy.connect):
            await backend.open()
        yield GenerationService(
            backend,
            store,
            output or state / "outputs",
            timeouts=policy,
            assets=asset_validator(backend),
            input_state=state if image_permits else None,
        )
    finally:
        try:
            async with asyncio.timeout(policy.cleanup):
                await backend.aclose()
        finally:
            if store is not None:
                await store.aclose()
                schedule_publish(state)


async def run_generation(
    service: GenerationService,
    request: ResolvedGenerationRequest,
    *,
    prefix: bool = False,
    on_event: EventSink | None = None,
    prepared_inputs: PreparedImageInputs | None = None,
    attempt_id: str | None = None,
) -> GenerationOutcome:
    """Refresh inventory, optionally apply trigger prefixes, run LoRA preflight, generate and save.

    A failure that ``auto_retryable`` accepts gets the one automatic retry (#241): after ``AUTO_RETRY_DELAY`` the
    same request (same settings and seed) is sent again under a new request identity, announced to ``on_event`` as a
    ``preparing``/``retrying`` event carrying that identity. A caller that names ``attempt_id`` links its attempts
    itself (Scripts, Workflows) and applies the same rule there, so no retry happens here for it."""
    if request.operation not in service.backend.capabilities.operations:
        raise ValidationError("This backend does not offer the requested operation; inventory/auth was not requested.")
    if request.operation != "txt2img":
        gate = getattr(service.backend, "validate_image_operation", None)
        if not callable(gate) or prepared_inputs is None or service.input_state is None:
            raise ValidationError("Image generation requires a scoped permit and prepared managed inputs.")
        gate(request)
        if (
            ImageInputSnapshot.from_prepared(
                request.image_inputs.attempt_id, prepared_inputs, preserve_outside=request.image_inputs.preserve_outside
            )
            != request.image_inputs
        ):
            raise ValidationError("Prepared image inputs do not match the requested snapshot.")
    elif prepared_inputs is not None:
        raise ValidationError("Text-to-image cannot include prepared image inputs.")
    # Real adapters accept only inventory-listed models, so the generation path refreshes inventory first.
    snapshot = await service.get_inventory(refresh=True)
    if prefix:
        request = apply_trigger_prefixes(request, snapshot)
    diagnostics = await service.preflight(request) if request.loras else ()
    options = {"prepared_inputs": prepared_inputs} if prepared_inputs is not None else {}
    if attempt_id is not None:
        options["attempt_id"] = attempt_id
    retries = 0
    while True:
        try:
            result = await service.generate(request, on_event=on_event, **options)
            break
        except BackendError as exc:
            if attempt_id is not None or retries >= AUTO_RETRIES or not auto_retryable(exc.failure):
                raise
            failure = exc.failure
        retries += 1
        request = auto_retry_request(request)
        diagnostic_event(
            log,
            "generation.auto_retry",
            level=logging.WARNING,
            data={"code": failure.code, "category": failure.category, "outcome": failure.remote_outcome},
        )
        _preparation_event(on_event, request, "retrying")
        await asyncio.sleep(AUTO_RETRY_DELAY)
    backend_id = service.backend.capabilities.backend_id
    final = next((x.encoded for x in result.backend_result.artifacts if x.designation == Designation.FINAL), None)
    if result.display_image is not None:
        final = result.display_image
    return GenerationOutcome(
        result.attempt_id,
        backend_id,
        result.output_paths,
        result.metadata_path,
        result.backend_result.warnings,
        diagnostics,
        result.request or request,
        fake=backend_id == "fake",
        final_image=final,
    )


def image_operation_request(
    request: ResolvedGenerationRequest,
    prepared_inputs: PreparedImageInputs,
    permit: ImageOperationPermit,
    *,
    strength: float,
    preserve_outside: bool,
) -> ResolvedGenerationRequest:
    """The image-operation request built from a txt2img settings base and prepared inputs, with a fresh identity.

    The operation follows the inputs (a mask makes it ``inpaint``); the snapshot names a placeholder attempt that the
    generation service replaces with the real one. The caller has already checked the permit's scope."""
    operation = "inpaint" if prepared_inputs.mask is not None else "img2img"
    snapshot = ImageInputSnapshot.from_prepared(uuid4().hex, prepared_inputs, preserve_outside=preserve_outside)
    return replace(
        request,
        request_id=uuid4().hex,
        operation=operation,
        strength=strength,
        image_inputs=snapshot,
        resolution_evidence=(
            *request.resolution_evidence,
            ("image_permit_evidence", permit.evidence_id),
            ("image_permit_status", permit.status),
        ),
    )


def _preparation_event(on_event, request, stage):
    """Report a local preparation milestone before backend events start."""
    if on_event is not None:
        on_event(BackendEvent(request.request_id, 0, "preparing", datetime.now(UTC).isoformat(), stage=stage))


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
    if request.operation != "txt2img":
        raise ValidationError(
            "Use the explicit permit-scoped image-generation entry; generic dispatch is txt2img-only."
        )
    _preparation_event(on_event, request, "binding_assets")
    request = await bind_prepared(state, selection.name, request, asset_ids)
    _preparation_event(on_event, request, "opening_session")
    async with open_session(selection, state, output=output, timeouts=timeouts, saved=saved) as service:
        _preparation_event(on_event, request, "session_ready")
        return await run_generation(service, request, prefix=prefix, on_event=on_event)


async def generate_image_once(
    selection: BackendSelection,
    state: Path,
    request: ResolvedGenerationRequest,
    prepared_inputs: PreparedImageInputs,
    *,
    permit: ImageOperationPermit,
    strength: float = 0.75,
    preserve_outside: bool = True,
    allow_image_probe: bool = False,
    prefix: bool = False,
    output: Path | None = None,
    timeouts: TimeoutPolicy | None = None,
    saved: CredentialProvider | None = None,
    on_event: EventSink | None = None,
    asset_ids: tuple[str, ...] = (),
) -> GenerationOutcome:
    """Explicit single-shot submission, never a preview or automatic acceptance run.

    Real generation may consume credits; cloud sends prepared source/mask off-machine.
    Supply an owner-reviewed exact permit, or explicitly authorize a probe for this
    invocation. No permits ship, no provider fallback or automatic retry is added.
    The base must be txt2img; each invocation gets fresh request/attempt identities.
    """
    if request.operation != "txt2img" or request.batch_size * request.batch_count != 1:
        raise ValidationError("Image generation requires a single-output txt2img settings base.")
    if not isinstance(prepared_inputs, PreparedImageInputs) or not isinstance(permit, ImageOperationPermit):
        raise ValidationError("Image generation requires immutable prepared inputs and an exact owner permit.")
    if type(preserve_outside) is not bool or type(allow_image_probe) is not bool:
        raise ValidationError("Preservation and owner-probe authorization must be explicit booleans.")
    operation = "inpaint" if prepared_inputs.mask is not None else "img2img"
    backend_id = {"local": "drawthings-grpc", "cloud": "drawthings-cloud"}.get(selection.name)
    if backend_id is None or (permit.backend_id, permit.model_id, permit.operation) != (
        backend_id,
        request.model_id,
        operation,
    ):
        raise ValidationError("Owner permit does not match the selected backend/checkpoint/operation.")
    permit.validate_scope(
        backend_id, image_endpoint_label(selection), request.model_id, operation, allow_probe=allow_image_probe
    )
    request = image_operation_request(
        request, prepared_inputs, permit, strength=strength, preserve_outside=preserve_outside
    )
    _preparation_event(on_event, request, "validating_image_inputs")
    await settle(asyncio.to_thread(validate_prepared_inputs, prepared_inputs))
    _preparation_event(on_event, request, "binding_assets")
    request = await bind_prepared(state, selection.name, request, asset_ids)
    _preparation_event(on_event, request, "opening_session")
    async with open_session(
        selection,
        state,
        output=output,
        timeouts=timeouts,
        saved=saved,
        image_permits=(permit,),
        allow_image_probe=allow_image_probe,
    ) as service:
        _preparation_event(on_event, request, "session_ready")
        return await run_generation(service, request, prefix=prefix, on_event=on_event, prepared_inputs=prepared_inputs)


async def generate_image_files_once(
    selection: BackendSelection,
    state: Path,
    request: ResolvedGenerationRequest,
    source: Path,
    *,
    permit: ImageOperationPermit,
    mask: Path | None = None,
    grow: int = 0,
    feather: float = 0.0,
    strength: float = 0.75,
    preserve_outside: bool = True,
    allow_image_probe: bool = False,
    prefix: bool = False,
    output: Path | None = None,
    timeouts: TimeoutPolicy | None = None,
    saved: CredentialProvider | None = None,
    on_event: EventSink | None = None,
    asset_ids: tuple[str, ...] = (),
) -> GenerationOutcome:
    """Explicit prepare-and-submit action; file decode/normalization runs on a worker.

    Files are read once into immutable snapshots; never reread during submission.
    Cancellation settles the preparation worker before returning, without dispatch.
    This function is not a safe preview entry: invoking it requests real generation.
    """
    prepared = await settle(
        asyncio.to_thread(
            prepare_image_files,
            source,
            request.width_px,
            request.height_px,
            mask=mask,
            grow=grow,
            feather=feather,
        )
    )
    return await generate_image_once(
        selection,
        state,
        request,
        prepared,
        permit=permit,
        strength=strength,
        preserve_outside=preserve_outside,
        allow_image_probe=allow_image_probe,
        prefix=prefix,
        output=output,
        timeouts=timeouts,
        saved=saved,
        on_event=on_event,
        asset_ids=asset_ids,
    )


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
        diagnostic_event(
            log,
            "inventory.cache_miss",
            level=logging.INFO,
            data={
                "backend": selection.name,
                "reason": "identity",
            },
        )
        return None
    if identity is None:
        return None
    kind, endpoint, credential = identity
    snapshot = await asyncio.to_thread(load_inventory, state, kind, endpoint=endpoint, credential=credential)
    diagnostic_event(
        log,
        "inventory.cache_read",
        level=logging.INFO,
        data={
            "hit": snapshot is not None,
            "backend": kind,
        },
    )
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
                diagnostic_event(
                    log,
                    "inventory.cache_not_written",
                    level=logging.WARNING,
                    data={
                        "backend": kind,
                        "reason": type(exc).__name__,
                    },
                )
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
    diagnostic_event(
        log,
        "inventory.refresh_ok",
        level=logging.INFO,
        data={
            "backend": selection.name,
            "entries": len(snapshot.entries),
            "metadata": dict(snapshot.diagnostics).get("model_metadata", "unknown"),
        },
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
        diagnostic_event(
            log,
            "inventory.cache_write",
            level=logging.INFO,
            data={
                "backend": kind,
            },
        )
    except (BackendError, ValidationError, OSError) as exc:
        diagnostic_event(
            log,
            "inventory.cache_not_written",
            level=logging.WARNING,
            data={
                "backend": selection.name,
                "reason": type(exc).__name__,
            },
        )


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

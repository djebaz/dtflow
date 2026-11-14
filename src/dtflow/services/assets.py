"""Shared asset preparation, reconciliation and dispatch-time validation.

Uploads happen here during explicit ``prepare`` or when dispatch-time ``ensure`` has a positive missing-file
response and a verified retained copy. The service owns orchestration and uncertainty rules; the adapter owns
HTTP and signed-URL handling; the registry owns durable receipts. Uncertain transfers are never retried blindly.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path, PureWindowsPath
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from dtflow.core.models import (
    accepted_name_changes,
    NameAlreadyAcceptedError,
    AssetBackend,
    AssetContent,
    AssetNegotiation,
    AssetReconciliation,
    BackendError,
    BackendFailure,
    ErrorCategory,
    KeyHoldings,
    PreparedAsset,
    RemoteOutcome,
    StorageListing,
    SubmissionStage,
    ValidationError,
)
from dtflow.core.retry_policy import REFRESH_FAILED_CODE, server_ended_without_result
from dtflow.core.tasks import settle
from dtflow.diagnostics import api_error_data, asset_ref_data, cloud_key_data, event as diagnostic_event
from dtflow.persistence.assets import AssetRegistry, AssetsBusyError, newest_per_name
from dtflow.persistence.store import PersistenceError
from functools import partial

from dtflow.services.lora_digest import cached_digest, remember
from dtflow.services.lora_family import LoraFamily, detect_lora_family, validate_converted_lora
from dtflow.services.lora_auto_naming import auto_prepare_metadata
from dtflow.services.lora_naming import LoraPrepareMetadata
from dtflow.services.lora_store import LoraStore
from dtflow.services.prepare_progress import CHECKING, CONFIRMING, EXISTS, UPLOADING, PrepareEvent

log = logging.getLogger(__name__)
CHUNK = 1024 * 1024

# One shielded operation per (origin, auth scope, content): identical concurrent prepares join it, anything else never does.
#: Seconds an outdated-receipt refresh that hit a DT+ limit is not tried again.
REFRESH_LIMIT_PAUSE = 900.0
#: Seconds a read of the per-name current receipts is reused.
NEWEST_TTL = 2.0
_INFLIGHT: dict[tuple, asyncio.Task] = {}
# Concurrent queue/workflow items needing the same restore or rebind join one operation, and distinct ones take
# turns, because the asset writer lock admits a single writer per state directory.
_SHARED: dict[tuple, asyncio.Task] = {}
_WRITER = asyncio.Lock()
# Receipts relinked or restored during this process, so later items still carrying the older reference are validated
# against the current one instead of repeating the same rebind for every item.
_CURRENT: dict[tuple, PreparedAsset] = {}
# Concurrent metadata checks may share an in-flight request, but every later submission asks DT+ again.
# A previous positive answer cannot prove that the remote copy still exists.
_CHECKS: dict[tuple, asyncio.Task] = {}
_SHARED_WAITERS: dict[asyncio.Task, int] = {}
_BATCHES: dict[tuple, dict[tuple[str, str, int], asyncio.Future]] = {}
_BATCH_TASKS: dict[tuple, asyncio.Task] = {}
_BATCH_INFLIGHT: dict[tuple, asyncio.Future] = {}


def _failure(
    category: ErrorCategory, code: str, message: str, outcome=RemoteOutcome.NOT_STARTED, *, diagnostics=()
) -> BackendError:
    stage = (
        SubmissionStage.NOT_SUBMITTED if outcome == RemoteOutcome.NOT_STARTED else SubmissionStage.POSSIBLY_SUBMITTED
    )
    return BackendError(BackendFailure(category, code, message, None, stage, outcome, diagnostics=diagnostics))


# The deployed server can reject an unavailable custom LoRA as gRPC PERMISSION_DENIED.
# This is a reason to check the selected bucket once, not evidence that upload is authorized.
# Generic rejection/quota/auth failures do not trigger an unrelated BYOM lookup.
_ASSET_REJECTION_CODES = {
    "grpc_not_found",
    "grpc_failed_precondition",
    "grpc_invalid_argument",
    "grpc_permission_denied",
}


def possible_missing_asset(failure: BackendFailure) -> bool:
    """Only a plausible missing-LoRA rejection may start a bounded metadata probe.

    DT+ also ends a request with its generic "unknown error processing request" when it cannot load a custom LoRA it was
    just told about (#361). Callers only ask this for requests that carry prepared LoRAs."""
    return failure.code in _ASSET_REJECTION_CODES or server_ended_without_result(failure)


def _observe(
    path: Path, *, cached: bool = False, on_progress: Callable[[int, int], None] | None = None
) -> tuple[os.stat_result, str]:
    """Hash incrementally (bounded memory) and require the file to be unchanged while it was read.

    ``cached=True`` reuses a digest the editor already computed in the background, valid only while the file's
    size and mtime are unchanged; any other caller always reads the bytes.
    """
    try:
        before = path.stat()
        if not path.is_file() or before.st_size < 1:
            raise ValidationError("Asset file must be a non-empty regular file.")
        if cached and (known := cached_digest(path, before)) is not None:
            if on_progress is not None:
                on_progress(before.st_size, before.st_size)
            return before, known
        digest, total = hashlib.sha256(), 0
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
                total += len(chunk)
                if on_progress is not None:
                    on_progress(total, before.st_size)
        after = path.stat()
    except OSError as exc:
        raise ValidationError("Asset file could not be read.") from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or total != before.st_size:
        raise _failure(ErrorCategory.VALIDATION, "asset_changed", "Asset file changed while it was hashed.")
    remember(path, before, digest.hexdigest())
    return before, digest.hexdigest()


class AssetValidator(Protocol):
    async def validate(self, prepared: PreparedAsset) -> None: ...

    async def ensure(self, prepared: PreparedAsset, on_stage: Callable[[str], None] | None = None) -> PreparedAsset: ...

    def forget(self, assets) -> None: ...


def _guarded(progress: Callable[[PrepareEvent], None] | None, reference: dict) -> Callable[[PrepareEvent], None] | None:
    """Progress is cosmetic: a callback that raises is logged once and dropped, never an upload failure."""
    if progress is None:
        return None
    alive = [True]

    def call(event: PrepareEvent) -> None:
        if not alive[0]:
            return
        try:
            progress(event)
        except Exception:
            alive[0] = False
            diagnostic_event(log, "assets.progress_callback_failed", level=logging.WARNING, data=reference.copy())

    return call


def _receipt_matches(remote_id: str, stored_key: str) -> bool:
    """Whether a receipt's remote reference points at the stored file with this delete key."""
    return remote_id == stored_key or (len(remote_id) >= 32 and remote_id in stored_key)


class AssetService:
    def __init__(
        self,
        backend: AssetBackend,
        registry: AssetRegistry | None = None,
        *,
        state: Path | None = None,
        store: LoraStore | None = None,
    ):
        """``registry`` may be omitted by callers that only ``validate``. ``store`` keeps a managed copy on every prepare;
        ``state`` additionally lets ``ensure`` restore a file DT+ positively reports missing (generation and queues)."""
        self.backend, self._registry = backend, registry
        self.state = Path(state) if state is not None else None
        self.store = store if store is not None else (LoraStore(self.state) if self.state is not None else None)
        self._failure_recovery: asyncio.Task | None = None
        self._recovery_waiters: dict[asyncio.Task, int] = {}
        self._recovery_checked: set[tuple[str, str, int]] = set()
        self._recovery_restored: set[tuple[str, str, int]] = set()
        # Outdated receipts already re-prepared from their changed file, shared by concurrent items (#361).
        self._refresh_failed: dict[tuple, tuple[float, BackendFailure]] = {}
        self._newest_cache: tuple[float, dict[str, PreparedAsset]] | None = None
        # The UI can receive individual names without adding them to diagnostic records.
        self.on_resolution: Callable[[dict], None] | None = None

    @property
    def registry(self) -> AssetRegistry:
        if self._registry is None:
            raise ValidationError("This operation needs the durable asset registry.")
        return self._registry

    def _key_diagnostics(self) -> dict:
        """Safe identity of this backend's pinned cloud key; never read mutable UI selection."""
        return cloud_key_data(
            getattr(self.backend, "credential_usage", None),
            getattr(self.backend, "credential_source", None),
            alias=getattr(self.backend, "credential_alias", None),
        )

    # -- prepare --------------------------------------------------------------------------------------------------

    async def prepare(
        self,
        path: Path,
        identifier: str | None = None,
        *,
        provenance: str = "",
        metadata: LoraPrepareMetadata | None = None,
        progress: Callable[[PrepareEvent], None] | None = None,
    ) -> PreparedAsset:
        """Explicit Prepare; derive a usable managed name before provider negotiation.

        ``progress`` receives stage events (checking, uploading with bytes, confirming, exists); it must not raise."""
        path = Path(path)
        progress_reference = asset_ref_data(filename=path.name)
        progress = _guarded(progress, progress_reference)
        if progress is not None:
            progress(PrepareEvent(CHECKING))
        managed_name = None
        if metadata is not None:
            try:
                selected_identifier = metadata.identifier
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            if identifier is not None and identifier != selected_identifier:
                raise ValidationError("Prepare identifier disagrees with the selected managed LoRA name.")
            identifier = managed_name = selected_identifier
        # The checking phase is two byte passes (hash, then the kept copy); each reports half of the phase.
        hashed = copied = None
        if progress is not None:
            hashed = lambda done, total: progress(PrepareEvent(CHECKING, done, 2 * total))  # noqa: E731
            copied = lambda done, total: progress(PrepareEvent(CHECKING, total + done, 2 * total))  # noqa: E731
        observe = partial(_observe, cached=True, **({"on_progress": hashed} if hashed else {}))
        stat, digest = await asyncio.to_thread(observe, path)
        await asyncio.to_thread(validate_converted_lora, path)
        if metadata is not None and not metadata.replace_existing:
            # Refuse a changed name before any copy or upload, so nothing is retained under the refused name.
            saved = await self.registry.lora_metadata(digest, stat.st_size)
            accepted = saved.get("managed_identifier") if saved else None
            if isinstance(accepted, str) and accepted_name_changes(accepted, identifier):
                raise NameAlreadyAcceptedError(
                    "This content already has an accepted managed filename. Choose explicit Re-Prepare to change it."
                )
        # Decide the technical filename before retaining bytes. Never create an
        # original-name copy merely to link it under the accepted name later.
        detected = await asyncio.to_thread(detect_lora_family, path)
        if metadata is None and identifier is None:
            saved = await self.registry.lora_metadata(digest, stat.st_size)
            accepted = saved.get("managed_identifier") if saved else None
            if isinstance(accepted, str):
                # Re-Prepare preserves the accepted technical identifier and receipts.
                identifier = managed_name = accepted
            else:
                metadata = await auto_prepare_metadata(self.registry, path.name, digest, stat.st_size, detected.family)
                if metadata is not None:
                    identifier = managed_name = metadata.identifier
        content = AssetContent("lora", identifier or path.name, digest, stat.st_size, provenance)
        progress_reference.update(asset_ref_data(sha256=digest))
        retained = None
        if self.store is not None:
            # One verified copy with its chosen name, indexed before any provider call.
            retain = partial(
                self.store.retain, managed_name=managed_name, **({"on_progress": copied} if copied else {})
            )
            retained = await asyncio.to_thread(retain, path, digest, stat.st_size)
            diagnostic_event(
                log,
                "assets.copy_kept",
                level=logging.INFO,
                data={**asset_ref_data(sha256=digest), "sha": digest[:8], "size": stat.st_size},
            )
        key = (
            self.backend.backend_id,
            self.backend.origin,
            self.backend.scope,
            content.identifier,
            content.sha256,
            content.size,
            metadata,
        )
        task = _INFLIGHT.get(key)
        if task is None or task.done():
            diagnostic_event(
                log,
                "assets.lora_family_detected",
                level=logging.INFO,
                data={
                    **asset_ref_data(sha256=digest),
                    "sha": digest[:8],
                    "family": detected.family or "unknown",
                    "family_source": detected.source,
                },
            )
            task = asyncio.create_task(
                self._prepare(
                    path,
                    content,
                    stat,
                    detected=detected,
                    metadata=metadata,
                    retained_name=retained.name if retained is not None else None,
                    managed_name=managed_name,
                    progress=progress,
                )
            )
            _INFLIGHT[key] = task
            task.add_done_callback(
                lambda done, key=key: _INFLIGHT.pop(key, None) if _INFLIGHT.get(key) is done else None
            )
            # Retrieve abandoned-waiter failures so they are never logged with provider detail.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        # Cancelling this caller must not cancel an operation another caller still awaits.
        receipt = await asyncio.shield(task)
        if self.store is not None and provenance != "auto_restore":
            # Only a confirmed upload makes this file the name's source and current version.
            await asyncio.to_thread(self.store.record_origin, path, digest, stat.st_size, content.identifier, stat)
            self._newest_cache = None
        return receipt

    async def _credential_id(self) -> str | None:
        """Real DT+ clients have a durable key identity; fixture backends retain their legacy path."""
        identity = getattr(self.backend, "asset_credential_identity", None)
        return await identity() if callable(identity) else None

    async def _prepare(
        self,
        path: Path,
        content: AssetContent,
        stat: os.stat_result,
        negotiation: AssetNegotiation | None = None,
        detected: LoraFamily | None = None,
        metadata: LoraPrepareMetadata | None = None,
        retained_name: str | None = None,
        managed_name: str | None = None,
        progress: Callable[[PrepareEvent], None] | None = None,
    ) -> PreparedAsset:
        backend = self.backend
        credential_id = await self._credential_id()
        if detected is None:
            await asyncio.to_thread(validate_converted_lora, path)
            detected = await asyncio.to_thread(detect_lora_family, path)
        asset_id, operation = await self.registry.begin(
            content,
            backend.backend_id,
            backend.origin,
            family=detected.family,
            lora_metadata=metadata,
        )
        operation_ref = asset_ref_data(sha256=content.sha256, asset_id=asset_id, operation_id=operation)
        diagnostic_event(
            log,
            "assets.prepare_started",
            level=logging.DEBUG,
            data={**operation_ref, "action": "restore" if content.provenance == "auto_restore" else "prepare"},
        )
        transferred = False
        try:
            if managed_name is not None and self.store is not None and retained_name != managed_name:
                # A converted source may already live inside the retained store. Upload from the
                # new path after an accepted-name rename, never from the now-missing old path.
                renamed = await asyncio.to_thread(
                    self.store.rename_accepted, content.sha256, content.size, managed_name
                )
                if renamed is not None and path.parent == self.store.directory:
                    path = renamed.path
                if renamed is not None and progress is not None:
                    progress(PrepareEvent(CHECKING, name=renamed.path.name))  # show the final managed name
            negotiation = negotiation or await backend.negotiate_asset(content)
            if negotiation.status == "exists":
                if progress is not None:
                    progress(PrepareEvent(EXISTS))
                receipt = await self.registry.confirm(
                    asset_id,
                    operation,
                    negotiation.reference,
                    "already_exists",
                    credential_id=credential_id,
                    identifier=content.identifier,
                )
                if content.provenance != "auto_restore":
                    diagnostic_event(
                        log,
                        "assets.prepared",
                        level=logging.INFO,
                        data={
                            **operation_ref,
                            "receipt": receipt.receipt_id,
                            "source": "already_exists",
                            "family": receipt.family or "unknown",
                            "family_source": receipt.family_source,
                            **self._key_diagnostics(),
                        },
                    )
                return receipt
            await self.registry.mark(
                asset_id,
                operation,
                "transferring",
                "transfer_started",
                protect_confirmed=credential_id is not None,
            )
            transferred = True
            if progress is None:
                await backend.upload_asset(path, content, negotiation)
            else:
                progress(PrepareEvent(UPLOADING, 0, content.size))
                await backend.upload_asset(
                    path,
                    content,
                    negotiation,
                    on_progress=lambda sent, total: progress(PrepareEvent(UPLOADING, sent, total)),
                )
        except asyncio.CancelledError:
            await settle(
                self._settle_unknown(
                    asset_id, operation, "cancelled" if transferred else "cancelled_early", transferred, credential_id
                )
            )
            raise
        except ValidationError:
            await settle(
                self.registry.mark(asset_id, operation, "failed", "retained_name_failed", protect_confirmed=True)
            )
            raise
        except BackendError as exc:
            failure = exc.failure
            definite = failure.remote_outcome in (RemoteOutcome.NOT_STARTED, RemoteOutcome.FAILED)
            await settle(
                self.registry.mark(
                    asset_id,
                    operation,
                    "failed" if definite or not transferred else "unknown",
                    failure.code,
                    protect_confirmed=credential_id is not None,
                )
            )
            raise
        except PersistenceError:
            # Persistence failed before any PUT began: nothing was transferred; leftover intent is swept to unknown.
            raise
        except Exception:
            await settle(self._settle_unknown(asset_id, operation, "unexpected_failure", transferred, credential_id))
            raise _failure(
                ErrorCategory.PROTOCOL, "asset_unexpected_failure", "Asset preparation failed.", RemoteOutcome.UNKNOWN
            ) from None
        # The transfer returned success; the server's own reference is still needed for a confirmed receipt.
        if progress is not None:
            progress(PrepareEvent(CONFIRMING))
        try:
            after = await backend.negotiate_asset(content)
        except asyncio.CancelledError:
            await settle(self._settle_unknown(asset_id, operation, "cancelled_after_transfer", True, credential_id))
            raise
        except BackendError as exc:
            await settle(self._settle_unknown(asset_id, operation, exc.failure.code, True, credential_id))
            raise _failure(
                ErrorCategory.PROTOCOL,
                "asset_upload_unconfirmed",
                "Upload finished but the server did not confirm the file; run `assets reconcile`. No retry was made.",
                RemoteOutcome.UNKNOWN,
            ) from None
        if after.status != "exists":
            await settle(self._settle_unknown(asset_id, operation, "post_upload_unconfirmed", True, credential_id))
            raise _failure(
                ErrorCategory.PROTOCOL,
                "asset_upload_unconfirmed",
                "Upload finished but the server does not list the file yet; run `assets reconcile`. No retry was made.",
                RemoteOutcome.UNKNOWN,
            )
        try:
            receipt = await self.registry.confirm(
                asset_id,
                operation,
                after.reference,
                "transfer",
                credential_id=credential_id,
                identifier=content.identifier,
            )
        except asyncio.CancelledError:
            await settle(self._settle_unknown(asset_id, operation, "cancelled_after_transfer", True, credential_id))
            raise
        except (PersistenceError, ValidationError):
            try:
                await settle(self._settle_unknown(asset_id, operation, "receipt_write_failed", True, credential_id))
            except Exception:
                diagnostic_event(
                    log,
                    "assets.receipt_write_failed",
                    level=logging.ERROR,
                    data={**operation_ref, "reason": "receipt_write_failed"},
                )
            raise _failure(
                ErrorCategory.PERSISTENCE,
                "asset_receipt_unwritten",
                "The file was uploaded but its receipt could not be saved; run `assets reconcile`. No retry was made.",
                RemoteOutcome.SUCCEEDED,
            ) from None
        if content.provenance != "auto_restore":
            diagnostic_event(
                log,
                "assets.prepared",
                level=logging.INFO,
                data={
                    **operation_ref,
                    "receipt": receipt.receipt_id,
                    "source": "transfer",
                    "family": receipt.family or "unknown",
                    "family_source": receipt.family_source,
                    **self._key_diagnostics(),
                },
            )
        return receipt

    async def _settle_unknown(
        self,
        asset_id: str,
        operation: str,
        evidence: str,
        transferred: bool,
        credential_id: str | None = None,
    ) -> None:
        await self.registry.mark(
            asset_id,
            operation,
            "unknown" if transferred else "failed",
            evidence,
            protect_confirmed=credential_id is not None,
        )

    # -- metadata-only checks -------------------------------------------------------------------------------------

    @staticmethod
    def _content(record_or_asset) -> AssetContent:
        get = record_or_asset.get if isinstance(record_or_asset, dict) else lambda k: getattr(record_or_asset, k)
        return AssetContent("lora", get("identifier"), get("sha256"), get("size"))

    async def check(self, content: AssetContent, expected: str | None) -> AssetReconciliation:
        """Fresh authenticated metadata check; never transfers. ``expected`` is the receipt's remote reference."""
        result, _ = await self._check(content, expected)
        return result

    async def _check(
        self, content: AssetContent, expected: str | None, answers: list | None = None
    ) -> tuple[AssetReconciliation, BackendFailure | None]:
        """Retain backend-neutral failure evidence for dispatch without persisting response bodies."""
        try:
            negotiation, answer = await self._negotiate_shared(content)
            if answers is not None:
                answers.append(answer)
        except BackendError as exc:
            code = exc.failure.code
            if exc.failure.category in (ErrorCategory.AUTHENTICATION, ErrorCategory.CREDENTIALS):
                return AssetReconciliation("unavailable", code), exc.failure
            return AssetReconciliation("unknown", code), exc.failure
        if negotiation.status == "upload_required":
            return AssetReconciliation("upload_required", "server_has_no_such_file"), None
        if expected is not None and negotiation.reference != expected:
            return AssetReconciliation("unknown", "reference_mismatch", negotiation.reference), None
        return AssetReconciliation("confirmed", "server_validated", negotiation.reference), None

    def forget(self, assets) -> None:
        """Forget local receipt rebinding after a failed generation; DT+ checks are always fresh."""
        for item in assets:
            _CURRENT.pop((item.backend_id, item.origin, self.backend.scope, item.receipt_id), None)

    async def _negotiate_shared(self, content: AssetContent):
        key = (
            self.backend.backend_id,
            self.backend.origin,
            self.backend.scope,
            content.identifier,
            content.sha256,
            content.size,
        )
        task = _CHECKS.get(key)
        answer = "shared with another item in this app"
        if task is None or task.done():
            answer = "asked DT+"
            task = asyncio.create_task(self.backend.negotiate_asset(content))
            _CHECKS[key] = task
            task.add_done_callback(lambda done, key=key: _CHECKS.pop(key, None) if _CHECKS.get(key) is done else None)
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        negotiation = await asyncio.shield(task)
        return negotiation, answer

    @staticmethod
    def _identity(content: AssetContent) -> tuple[str, str, int]:
        return content.identifier, content.sha256, content.size

    async def _negotiate_many(
        self, contents: tuple[AssetContent, ...], credential_id: str
    ) -> dict[tuple[str, str, int], AssetNegotiation]:
        """Join in-flight BYOM lookups by exact key, state directory and LoRA identity.

        A batch can close for new distinct assets when HTTP starts; its existing members
        remain joinable until the response settles, including when the queue has ten waiters.
        """
        scope = (
            asyncio.get_running_loop(),
            str(self.state.resolve()) if self.state is not None else "",
            self.backend.backend_id,
            self.backend.origin,
            credential_id,
        )
        pending = _BATCHES.setdefault(scope, {})
        awaited = {}
        for content in contents:
            identity = self._identity(content)
            key = (*scope, *identity)
            future = _BATCH_INFLIGHT.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                _BATCH_INFLIGHT[key] = future
                pending[identity] = future

                def finished(done, key=key):
                    if _BATCH_INFLIGHT.get(key) is done:
                        _BATCH_INFLIGHT.pop(key, None)
                    if not done.cancelled():
                        done.exception()  # Observe a failed lookup if all consumers were cancelled.

                future.add_done_callback(finished)
            awaited[identity] = future

        if pending and (scope not in _BATCH_TASKS or _BATCH_TASKS[scope].done()):

            async def flush() -> None:
                await asyncio.sleep(0.02)
                batch = _BATCHES.pop(scope, {})
                _BATCH_TASKS.pop(scope, None)  # Let new distinct assets start a later batch.
                if not batch:
                    return
                unique = tuple(AssetContent("lora", *identity) for identity in batch)
                try:
                    negotiate = getattr(self.backend, "negotiate_assets", None)
                    if callable(negotiate):
                        answers = {}
                        for start in range(0, len(unique), 64):
                            answers.update(await negotiate(unique[start : start + 64]))
                    else:
                        answers = {self._identity(item): await self.backend.negotiate_asset(item) for item in unique}
                    for identity, future in batch.items():
                        if not future.done():
                            future.set_result(answers[identity])
                except BaseException as exc:
                    for future in batch.values():
                        if not future.done():
                            future.set_exception(exc)

            task = asyncio.create_task(flush())
            _BATCH_TASKS[scope] = task
            task.add_done_callback(
                lambda done, key=scope: _BATCH_TASKS.pop(key, None) if _BATCH_TASKS.get(key) is done else None
            )
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return {identity: await asyncio.shield(future) for identity, future in awaited.items()}

    async def ensure_many(
        self,
        prepared_assets: tuple[PreparedAsset, ...],
        on_stage: Callable[[str], None] | None = None,
        *,
        force: bool = False,
        restored_out: list[tuple[str, str, int]] | None = None,
        on_resolution: Callable[[dict], None] | None = None,
    ) -> tuple[PreparedAsset, ...]:
        """Resolve one logical set of LoRAs and report one aggregate outcome, never one success log per LoRA."""
        if not prepared_assets:
            return ()
        credential_id = await self._credential_id()
        if credential_id is None:
            # Existing fixture backends keep the historical validation contract.
            verified = tuple(
                [await self._verify(item, on_stage, restoring=self.state is not None) for item in prepared_assets]
            )
            return await self._with_families(verified)
        if self.state is None:
            # Stateless callers only validate exact remote references; they cannot retain or restore bytes.
            return tuple([await self._verify(item, on_stage, restoring=False) for item in prepared_assets])

        identities = {self._identity(self._content(item)): item for item in prepared_assets}
        outcomes: dict[tuple[str, str, int], str] = {}
        names = {key: PureWindowsPath(item.identifier).name for key, item in identities.items()}
        current: tuple[str, str, int] | None = None
        pending: tuple[tuple[str, str, int], ...] = ()
        outcome = "inconclusive"
        error_message = ""
        try:
            prepared_assets = await self._current(prepared_assets, on_stage)
            identities = {self._identity(self._content(item)): item for item in prepared_assets}
            names = {key: PureWindowsPath(item.identifier).name for key, item in identities.items()}
            registry = AssetRegistry(self.state)
            await registry.open()
            try:
                existing = [
                    await registry.scoped(item.receipt_id, credential_id, item.identifier) for item in prepared_assets
                ]
            finally:
                await registry.aclose()
            missing = []
            for index, item in enumerate(prepared_assets):
                current = self._identity(self._content(item))
                if (item.backend_id, item.origin) != (self.backend.backend_id, self.backend.origin):
                    raise _failure(ErrorCategory.VALIDATION, "prepared_asset_scope", "LoRA backend mismatch.")
                known = existing[index]
                if known is not None and (known.sha256, known.size, known.identifier) != (
                    item.sha256,
                    item.size,
                    item.identifier,
                ):
                    raise _failure(
                        ErrorCategory.VALIDATION, "prepared_asset_identity", "LoRA receipt identity changed."
                    )
                if force or known is None:
                    missing.append((index, item, known))
                else:
                    outcomes[current] = "reused"
            current = None
            if not missing:
                outcome = "completed"
                return await self._with_families(tuple(existing))

            pending = tuple(dict.fromkeys(self._identity(self._content(item)) for _, item, _ in missing))
            contents = tuple(dict.fromkeys(self._content(item) for _, item, _ in missing))
            try:
                negotiations = await self._negotiate_many(contents, credential_id)
            except BackendError:
                raise
            except Exception as exc:
                raise _failure(
                    ErrorCategory.CONNECTIVITY, "asset_check_failed", "DT+ asset check failed; nothing uploaded."
                ) from exc
            results = list(existing)
            for index, item, known in missing:
                current = self._identity(self._content(item))
                negotiation = negotiations.get(current)
                if negotiation is None:
                    raise _failure(
                        ErrorCategory.PROTOCOL,
                        "asset_check_inconclusive",
                        "DT+ returned no usable status for this LoRA; nothing was uploaded.",
                    )
                if negotiation.status == "exists":
                    if known is not None and known.remote_id == negotiation.reference:
                        results[index] = known
                        outcomes[current] = "matched"
                    else:
                        results[index] = await self._shared(
                            "key-rebind:" + negotiation.reference,
                            item,
                            lambda item=item, ref=negotiation.reference: self._rebind(
                                item, PureWindowsPath(item.identifier).name, ref, credential_id=credential_id
                            ),
                            scope_id=credential_id,
                        )
                        outcomes[current] = "relinked"
                elif negotiation.status == "upload_required":
                    results[index] = await self._shared(
                        "key-restore",
                        item,
                        lambda item=item, negotiation=negotiation: self._restore(
                            item,
                            PureWindowsPath(item.identifier).name,
                            on_stage,
                            negotiation=negotiation,
                            credential_id=credential_id,
                        ),
                        scope_id=credential_id,
                    )
                    outcomes[current] = "restored"
                    if restored_out is not None:
                        restored_out.append(self._identity(self._content(item)))
                else:
                    raise _failure(ErrorCategory.PROTOCOL, "asset_check_inconclusive", "DT+ asset status is unknown.")
                current = None
            outcome = "completed"
            return await self._with_families(tuple(results))
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except BackendError as exc:
            affected = (current,) if current is not None else tuple(key for key in pending if key not in outcomes)
            if current is not None and exc.failure.remote_outcome != RemoteOutcome.UNKNOWN:
                outcomes[current] = "failed"
                outcome = "failed"
            else:
                # An uncertain transfer may already exist remotely; never call it a proven failure.
                outcome = "inconclusive"
            if affected:
                labels = [names[key] for key in dict.fromkeys(affected)]
                subject = ("LoRA " if len(labels) == 1 else "LoRAs ") + ", ".join(labels)
                # Preserve the provider's already-projected explanation in the named UI feedback.
                details = dict(exc.failure.diagnostics)
                message = str(
                    details.get("interpretation_message") or details.get("server_message") or exc.failure.message
                )
                if not (len(labels) == 1 and f"LoRA {labels[0]}" in message):
                    message = f"{subject}: {message}"
                error_message = message
                raise BackendError(replace(exc.failure, message=message)) from exc
            error_message = exc.failure.message
            raise
        except Exception as exc:
            if current is not None:
                outcomes[current] = "failed"
                outcome = "failed"
                error_message = f"LoRA {names[current]}: asset resolution failed ({type(exc).__name__})."
            else:
                affected = [names[key] for key in identities if key not in outcomes]
                if affected:
                    error_message = f"LoRAs {', '.join(affected)}: asset resolution failed ({type(exc).__name__})."
            raise
        finally:
            counts = {
                kind: sum(value == kind for value in outcomes.values())
                for kind in ("reused", "matched", "relinked", "restored", "failed")
            }
            report = {
                "total": len(identities),
                **counts,
                "unresolved": len(identities) - len(outcomes),
                "outcome": outcome,
                # These display names are delivered to UI owners, never to the diagnostic event.
                "restored_names": tuple(names[key] for key, kind in outcomes.items() if kind == "restored"),
                "failed_names": tuple(names[key] for key, kind in outcomes.items() if kind == "failed"),
                "error": error_message,
            }
            level = (
                logging.WARNING
                if outcome in ("failed", "inconclusive")
                else logging.INFO
                if counts["relinked"] or counts["restored"]
                else logging.DEBUG
            )
            diagnostic_event(
                log,
                "assets.resolution_summary",
                level=level,
                data={
                    key: report[key]
                    for key in ("total", "reused", "matched", "relinked", "restored", "failed", "unresolved", "outcome")
                }
                | self._key_diagnostics(),
            )
            receiver = on_resolution if on_resolution is not None else self.on_resolution
            if receiver is not None:
                try:
                    receiver(report)
                except Exception:
                    log.warning("Asset resolution status could not be displayed.")

    async def _with_families(self, assets: tuple[PreparedAsset, ...]) -> tuple[PreparedAsset, ...]:
        """Resolve effective architecture from the same local content record used by all execution paths."""
        if not assets or self.state is None:
            return assets
        registry = AssetRegistry(self.state)
        await registry.open()
        try:
            return await registry.resolve_prepared(assets)
        finally:
            await registry.aclose()

    async def _newest_map(self) -> dict[str, PreparedAsset]:
        """The current receipt of every LoRA name, read at most every ``NEWEST_TTL`` seconds."""
        cached = self._newest_cache
        if cached is not None and time.monotonic() - cached[0] < NEWEST_TTL:
            return cached[1]
        registry = AssetRegistry(self.state)
        await registry.open()
        try:
            choices = await registry.choices()
        finally:
            await registry.aclose()
        current = await asyncio.to_thread(self.store.current_by_name) if self.store is not None else {}
        newest = {item.identifier: item for item in newest_per_name(choices, current)}
        self._newest_cache = (time.monotonic(), newest)
        return newest

    async def _current(
        self, prepared_assets: tuple[PreparedAsset, ...], on_stage: Callable[[str], None] | None = None
    ) -> tuple[PreparedAsset, ...]:
        """Swap each receipt for the current one of its name before anything is checked or sent (#361).

        A LoRA converted again under the same name has new bytes, so its name must never resolve to the old hash:
        the receipt most recently prepared on purpose wins (else the content first seen last), and a receipt whose
        source file now holds different bytes is prepared again from that file (a real upload when DT+ asks for
        one). A refresh that cannot finish fails the request with the reason instead of sending the old bytes."""
        newest = await self._newest_map()
        credential = await self._credential_id()
        current = []
        for item in prepared_assets:
            latest = newest.get(item.identifier)
            if latest is not None and (latest.backend_id, latest.origin) == (item.backend_id, item.origin):
                item = latest
            current.append(await self._refreshed_from_source(item, credential, on_stage))
        return tuple(current)

    async def _refresh(self, item: PreparedAsset, path: Path, on_stage: Callable[[str], None] | None) -> PreparedAsset:
        """Measure the changed source file once and, if its bytes differ, prepare them again under the item's name.

        Runs under the exclusive asset writer like a restore, with its own registry, so Stop can cancel it."""
        stat, digest = await asyncio.to_thread(_observe, path)
        if (digest, stat.st_size) == (item.sha256, item.size):
            # Touched but identical: stamp the measurement so the file is not read again.
            await asyncio.to_thread(
                self.store.record_origin, path, digest, stat.st_size, item.identifier, stat, mark=False
            )
            return item
        await asyncio.to_thread(self.store.retain, path, digest, stat.st_size)
        if on_stage is not None:
            on_stage("restoring_assets")
        registry = AssetRegistry(self.state, writer=True)
        try:
            await settle(registry.open())
            fresh = await AssetService(self.backend, registry)._prepare(
                path, AssetContent("lora", item.identifier, digest, stat.st_size, "outdated_refresh"), stat
            )
        finally:
            await settle(registry.aclose())
        await asyncio.to_thread(self.store.record_origin, path, digest, stat.st_size, item.identifier, stat)
        self._newest_cache = None
        diagnostic_event(
            log,
            "assets.outdated_receipt_refreshed",
            level=logging.INFO,
            data={
                **asset_ref_data(sha256=fresh.sha256, asset_id=fresh.receipt_id),
                "old_sha": item.sha256[:8],
                "new_sha": fresh.sha256[:8],
                **self._key_diagnostics(),
            },
        )
        return fresh

    async def _refreshed_from_source(
        self, item: PreparedAsset, credential: str | None, on_stage: Callable[[str], None] | None
    ) -> PreparedAsset:
        if self.store is None:
            return item
        identity = (credential, *self._identity(self._content(item)))
        blocked = self._refresh_failed.get(identity)
        if blocked is not None and time.monotonic() < blocked[0]:
            raise BackendError(blocked[1])
        path = await asyncio.to_thread(self.store.changed_origin, item.sha256, item.size, item.identifier)
        if path is None:
            return item
        # Concurrent queue slots share one measurement, copy and upload, and the last one to leave cancels it.
        # Nothing is cached on success: the source stamp and the current-version marker are re-written by the refresh,
        # so the next look at the file answers from them, and putting an older file back is seen like any change.
        try:
            fresh = await self._shared(
                "refresh", item, lambda: self._refresh(item, path, on_stage), scope_id=credential
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise self._refresh_failed_error(identity, item, exc) from None
        self._refresh_failed.pop(identity, None)
        return fresh

    def _refresh_failed_error(self, identity: tuple, item: PreparedAsset, exc: Exception) -> BackendError:
        """A refresh that did not finish: name the LoRA and fail the request (runners pause on this code).

        A DT+ limit error is remembered for ``REFRESH_LIMIT_PAUSE`` so a long run does not hammer it; every other cause
        (busy writer, connectivity, a locked file) is transient and is tried again on the next request."""
        if isinstance(exc, BackendError):
            reason, limited = exc.failure.code, exc.failure.category == ErrorCategory.RESOURCE_LIMIT
        elif isinstance(exc, AssetsBusyError):
            reason, limited = "assets_busy", False
        else:
            reason, limited = type(exc).__name__, False
        name = PureWindowsPath(item.identifier).name
        error = _failure(
            ErrorCategory.VALIDATION,
            REFRESH_FAILED_CODE,
            f"LoRA {name} changed on disk but could not be prepared again ({reason}). Nothing was submitted; "
            "the run pauses. Run Prepare for it again.",
            diagnostics=(("asset", name), ("cause", reason)),
        )
        if limited:
            self._refresh_failed[identity] = (time.monotonic() + REFRESH_LIMIT_PAUSE, error.failure)
        diagnostic_event(
            log,
            "assets.outdated_refresh_failed",
            level=logging.WARNING,
            data={
                **asset_ref_data(sha256=item.sha256, asset_id=item.receipt_id),
                "reason": reason,
                "limited": limited,
            },
        )
        return error

    async def recover_missing(
        self,
        prepared_assets: tuple[PreparedAsset, ...],
        on_stage: Callable[[str], None] | None = None,
        *,
        failure: BackendFailure | None = None,
    ) -> bool:
        """Check the LoRAs after a failed request. When that failure was DT+'s generic "unknown error processing
        request" (not a missing-asset rejection), a check that itself fails is logged and ignored: the request then
        gets its normal automatic retry instead of pausing a queue or hiding the original error (#361)."""
        try:
            return await self._recover_missing(prepared_assets, on_stage)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if failure is None or not server_ended_without_result(failure):
                raise
            diagnostic_event(
                log,
                "assets.recovery_check_failed",
                level=logging.WARNING,
                data={"reason": exc.failure.code if isinstance(exc, BackendError) else type(exc).__name__},
            )
            return False

    async def _recover_missing(
        self, prepared_assets: tuple[PreparedAsset, ...], on_stage: Callable[[str], None] | None = None
    ) -> bool:
        """Bound each distinct key/LoRA to one check while sharing concurrent recovery work.

        Unrelated assets are not exhausted by a previous recovery. A second simultaneous
        caller joins the active probe; after it settles, previously unchecked assets can
        have their own bounded episode. Successful restoration counts only for the caller's
        own LoRAs, not for an unrelated branch.
        """
        if not prepared_assets or self.state is None or await self._credential_id() is None:
            return False
        # The request still carries the receipts it was built with; the check and its bookkeeping use the current ones.
        prepared_assets = await self._current(prepared_assets, on_stage)
        wanted = {self._identity(self._content(item)) for item in prepared_assets}
        while True:
            if wanted & self._recovery_restored:
                return True
            running = self._failure_recovery
            if running is not None and not running.done():
                task = running
            else:
                pending = tuple(
                    item
                    for item in prepared_assets
                    if self._identity(self._content(item)) not in self._recovery_checked
                )
                if not pending:
                    return False
                self._recovery_checked.update(self._identity(self._content(item)) for item in pending)

                async def probe(items: tuple[PreparedAsset, ...]) -> set[tuple[str, str, int]]:
                    restored: list[tuple[str, str, int]] = []
                    await self.ensure_many(items, on_stage, force=True, restored_out=restored)
                    result = set(restored)
                    self._recovery_restored.update(result)
                    return result

                task = asyncio.create_task(probe(pending))
                self._failure_recovery = task
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            self._recovery_waiters[task] = self._recovery_waiters.get(task, 0) + 1
            try:
                await asyncio.shield(task)
            finally:
                remaining = self._recovery_waiters[task] - 1
                if remaining:
                    self._recovery_waiters[task] = remaining
                else:
                    self._recovery_waiters.pop(task, None)
                    if not task.done():
                        task.cancel()

    async def validate(self, prepared: PreparedAsset) -> None:
        """Dispatch-time guard without restoration: require current server validation or fail without uploading."""
        await self._verify(prepared, None, restoring=False)

    async def ensure(self, prepared: PreparedAsset, on_stage: Callable[[str], None] | None = None) -> PreparedAsset:
        """Validate the receipt; only a positive "upload required" answer restores it from the retained local copy.

        Returns the receipt to submit with: the original when the server already has the file, otherwise the freshly
        confirmed one. One restoration is attempted per asset per call; the old receipt remains in the registry."""
        return (await self.ensure_many((prepared,), on_stage))[0]

    async def _verify(self, prepared: PreparedAsset, on_stage, *, restoring: bool) -> PreparedAsset:
        # Display only the local basename, through shared privacy projection; never the remote reference or path.
        asset = (
            api_error_data(
                message=PureWindowsPath(prepared.identifier).name,
                private=(prepared.remote_id,),
            ).get("server_message")
            or "[asset]"
        )
        if prepared.backend_id != self.backend.backend_id or prepared.origin != self.backend.origin:
            raise _failure(
                ErrorCategory.VALIDATION,
                "prepared_asset_scope",
                f"LoRA {asset}: prepared asset belongs to a different backend scope. Nothing was submitted.",
                diagnostics=(("asset", asset),),
            )
        if restoring:
            prepared = await self._latest(prepared)
        answers: list[str] = []
        result, cause = await self._check(self._content(prepared), prepared.remote_id, answers)
        if result.status == "confirmed":
            return prepared
        diagnostic_event(
            log,
            {"upload_required": "assets.missing_on_dtplus"}.get(
                result.status,
                "assets.reference_changed" if result.evidence == "reference_mismatch" else "assets.check_inconclusive",
            ),
            level=logging.INFO,
            data={
                **asset_ref_data(
                    sha256=prepared.sha256,
                    asset_id=prepared.receipt_id,
                    filename=PureWindowsPath(prepared.identifier).name,
                ),
                "answer": answers[0] if answers else "unknown",
                "evidence": result.evidence,
                "restoring": restoring,
            },
        )
        if restoring and result.status == "upload_required":
            return await self._shared("restore", prepared, lambda: self._restore(prepared, asset, on_stage))
        if restoring and result.evidence == "reference_mismatch" and result.reference:
            return await self._shared(
                "rebind:" + result.reference, prepared, lambda: self._rebind(prepared, asset, result.reference)
            )
        problem = _failure(
            ErrorCategory.VALIDATION,
            "prepared_asset_" + result.status,
            f"LoRA {asset} is not validated by the server ({result.evidence}); nothing was uploaded or submitted.",
        )
        details = {"validation_reason": result.evidence}
        if cause is not None:
            # Only the adapter's projected evidence crosses this boundary, never provider objects or references.
            details.update(cause.diagnostics)
            details["cause_code"] = cause.code
            details["cause_category"] = cause.category.value
        details["asset"] = asset
        raise BackendError(
            BackendFailure(
                problem.failure.category,
                problem.failure.code,
                problem.failure.message,
                diagnostics=tuple(sorted(details.items())),
            )
        ) from None

    async def _latest(self, prepared: PreparedAsset) -> PreparedAsset:
        """The newest confirmed reference this machine knows for the same receipt.

        Saved queue items, "Edit as new" copies and scripts keep the reference they were built with. A later rebind or
        restore updated the local registry, so use that record (once per session) instead of re-discovering the
        change, and let the normal DT+ check below confirm it. The saved request itself is never rewritten."""
        key = (prepared.backend_id, prepared.origin, self.backend.scope, prepared.receipt_id)
        known = _CURRENT.get(key)
        if known is None and self.state is not None:
            registry = AssetRegistry(self.state)
            try:
                await registry.open()
                try:
                    known = await registry.prepared(prepared.receipt_id)
                finally:
                    await registry.aclose()
            except (PersistenceError, ValidationError):
                known = None
            if known is not None:
                _CURRENT[key] = known
        if known is not None and (known.sha256, known.size) == (prepared.sha256, prepared.size):
            return known
        return prepared

    async def _shared(
        self, action: str, prepared: PreparedAsset, operation, *, scope_id: str | None = None
    ) -> PreparedAsset:
        """One operation per (action, scope, content); callers needing it concurrently join it."""
        key = (
            action,
            str(self.state.resolve()) if self.state is not None else "",
            self.backend.backend_id,
            self.backend.origin,
            scope_id or self.backend.scope,
            prepared.identifier,
            prepared.sha256,
            prepared.size,
        )
        task = _SHARED.get(key)
        if task is None or task.done():

            async def serialized() -> PreparedAsset:
                async with _WRITER:
                    return await operation()

            task = asyncio.create_task(serialized())
            _SHARED[key] = task
            task.add_done_callback(lambda done, key=key: _SHARED.pop(key, None) if _SHARED.get(key) is done else None)
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        _SHARED_WAITERS[task] = _SHARED_WAITERS.get(task, 0) + 1
        try:
            return await asyncio.shield(task)
        finally:
            remaining = _SHARED_WAITERS[task] - 1
            if remaining:
                _SHARED_WAITERS[task] = remaining
            else:
                _SHARED_WAITERS.pop(task, None)
                # One Stop must not abort a restore still needed by another queued item.
                # With no consumers left, cancellation can reach the transport/registry.
                if not task.done():
                    # A new consumer must not join a cancelled operation still cleaning up.
                    if _SHARED.get(key) is task:
                        _SHARED.pop(key, None)
                    task.cancel()

    async def _rebind(
        self, prepared: PreparedAsset, asset: str, reference: str, *, credential_id: str | None = None
    ) -> PreparedAsset:
        """The server holds these exact bytes (matched by SHA-256 and size) under this reference.

        Nothing is uploaded: the receipt gets its first association for this key or updates an earlier one;
        previous evidence remains in the operation history."""
        known = _CURRENT.get((prepared.backend_id, prepared.origin, self.backend.scope, prepared.receipt_id))
        if credential_id is None and known is not None and known.remote_id == reference:
            return known  # Only the legacy single-scope cache can authorize this shortcut.
        registry = AssetRegistry(self.state, writer=True)
        try:
            try:
                await settle(registry.open())
            except AssetsBusyError:
                raise _failure(
                    ErrorCategory.VALIDATION,
                    "prepared_asset_restore_busy",
                    f"LoRA {asset} has a newer DT+ reference, but another process is preparing assets. Nothing was submitted.",
                ) from None
            content = self._content(prepared)
            asset_id, operation = await registry.begin(content, prepared.backend_id, prepared.origin, "rebind")
            rebound = await registry.confirm(
                asset_id,
                operation,
                reference,
                "reconciled",
                credential_id=credential_id,
                identifier=prepared.identifier,
            )
        finally:
            await settle(registry.aclose())
        _CURRENT[(rebound.backend_id, rebound.origin, self.backend.scope, rebound.receipt_id)] = rebound
        return rebound

    async def _restore(
        self,
        prepared: PreparedAsset,
        asset: str,
        on_stage: Callable[[str], None] | None,
        *,
        negotiation: AssetNegotiation | None = None,
        credential_id: str | None = None,
    ) -> PreparedAsset:
        """Upload the retained copy once under the exclusive asset writer, then return its confirmed receipt.

        The writer spans the network operation because the registry's active intent and its recovery semantics
        are owned by that writer. Releasing it mid-upload would let another writer mark the transfer interrupted.
        """
        store = self.store or LoraStore(self.state)
        try:
            retained = await asyncio.to_thread(store.find, prepared.sha256, prepared.size)
            if retained is None:
                diagnostic_event(
                    log,
                    "assets.restore_blocked",
                    level=logging.WARNING,
                    data={
                        **asset_ref_data(sha256=prepared.sha256, asset_id=prepared.receipt_id),
                        "reason": "no_local_copy",
                    },
                )
                raise _failure(
                    ErrorCategory.VALIDATION,
                    "prepared_asset_no_local_copy",
                    f"LoRA {asset} is missing on DT+ and no verified local copy is kept; run `assets prepare` again."
                    " Nothing was submitted.",
                )
            await asyncio.to_thread(store.verify, retained)
        except ValidationError as exc:
            diagnostic_event(
                log,
                "assets.restore_blocked",
                level=logging.WARNING,
                data={
                    **asset_ref_data(sha256=prepared.sha256, asset_id=prepared.receipt_id),
                    "reason": "local_copy_invalid",
                },
            )
            raise _failure(
                ErrorCategory.VALIDATION,
                "prepared_asset_local_copy_invalid",
                f"LoRA {asset} is missing on DT+ and its kept local copy is unusable ({exc}). Nothing was submitted.",
            ) from None
        if on_stage is not None:
            on_stage("restoring_assets")
        registry = AssetRegistry(self.state, writer=True)
        try:
            try:
                await settle(registry.open())
            except AssetsBusyError:
                raise _failure(
                    ErrorCategory.VALIDATION,
                    "prepared_asset_restore_busy",
                    f"LoRA {asset} is missing on DT+, but another process is preparing assets. Nothing was submitted.",
                ) from None
            if credential_id is not None:
                # Once DT+ positively reports a missing copy, no sibling can trust this key's old receipt.
                await registry.invalidate_scope(prepared.receipt_id, credential_id, prepared.identifier)
            # The restore already has a shared task and verified source bytes. Avoid a separately shielded
            # explicit-Prepare task so Stop can cancel the upload and settle its registry state.
            restored = await AssetService(self.backend, registry)._prepare(
                retained.path,
                AssetContent("lora", prepared.identifier, prepared.sha256, prepared.size, "auto_restore"),
                await asyncio.to_thread(retained.path.stat),
                negotiation=negotiation,
            )
        finally:
            await settle(registry.aclose())
        if (restored.sha256, restored.size) != (prepared.sha256, prepared.size):
            raise _failure(
                ErrorCategory.PROTOCOL, "asset_restore_mismatch", "Restored asset does not match its record."
            )
        _CURRENT[(restored.backend_id, restored.origin, self.backend.scope, restored.receipt_id)] = restored
        return restored

    # -- DT+ storage ------------------------------------------------------------------------------------------------

    async def storage_listing(self, content: AssetContent) -> StorageListing:
        """The selected key's stored files as DT+ reports them. Read-only; nothing is uploaded or changed."""
        lister = getattr(self.backend, "list_stored_assets", None)
        if lister is None:
            raise ValidationError("The selected backend cannot list DT+ storage.")
        listing = await lister(content)
        if not listing.files:
            return listing
        keys, seen = await self._local_receipt_keys(listing.files)
        return replace(listing, local_keys=keys, local_seen=seen)

    async def _local_receipt_keys(self, files: tuple) -> tuple[frozenset[str] | None, dict[str, str]]:
        """Delete keys of listed files that one of this key's confirmed local receipts refers to (None if unreadable),
        with the newest time this computer confirmed each one."""
        try:
            credential = await self.backend.asset_credential_identity()
            references = await self.registry.confirmed_scope_rows(credential)
        except (PersistenceError, AssetsBusyError, BackendError, ValidationError, OSError) as exc:
            diagnostic_event(
                log,
                "assets.storage_receipts_unread",
                level=logging.WARNING,
                data={"reason": type(exc).__name__, "files": len(files)},
            )
            return None, {}
        seen: dict[str, str] = {}
        for item in files:
            times = [ref[3] for ref in references if _receipt_matches(ref[2], item.key)]
            if times:
                seen[item.key] = max(times)
        return frozenset(seen), seen

    async def receipt_snapshot(self) -> tuple[str, tuple[tuple[str, str, str, str], ...]]:
        """This key's opaque identity and its confirmed receipts, read before a stored-list request is made."""
        credential = await self.backend.asset_credential_identity()
        return credential, await self.registry.confirmed_scope_rows(credential)

    async def key_holdings(
        self, listing: StorageListing, snapshot: tuple[str, tuple[tuple[str, str, str, str], ...]] | None = None
    ) -> KeyHoldings:
        """Compare this key's confirmed receipts with a complete stored list (read-only; nothing is changed here).

        Only an explicit list DT+ gave in full (over-limit answer, nothing skipped; an empty list counts) is evidence.
        Without one nothing is judged missing and the receipts alone say what the key probably holds. ``snapshot`` is
        the receipts read *before* the list was requested, so a receipt confirmed while the request ran is never judged
        against the older list. Receipts the list lacks come back in ``stale`` with this key's opaque identity for the
        caller to apply through ``AssetRegistry.invalidate_observed``; this method never opens the exclusive writer."""
        try:
            credential, rows = snapshot if snapshot is not None else await self.receipt_snapshot()
        except (PersistenceError, AssetsBusyError, BackendError, ValidationError, OSError) as exc:
            diagnostic_event(log, "assets.holdings_unread", level=logging.WARNING, data={"reason": type(exc).__name__})
            return KeyHoldings(False, frozenset(), 0, 0, False)
        complete = listing.over_limit and listing.complete
        stale = (
            tuple(
                (asset_id, identifier, remote_id, seen)
                for asset_id, identifier, remote_id, seen in rows
                if not any(_receipt_matches(remote_id, item.key) for item in listing.files)
            )
            if complete
            else ()
        )
        diagnostic_event(
            log,
            "assets.holdings_compared",
            level=logging.INFO,
            data={
                "listed": len(listing.files),
                "receipts": len(rows),
                "complete": complete,
                "stale": len(stale),
                **self._key_diagnostics(),
            },
        )
        gone = {identifier for _, identifier, _, _ in stale}
        return KeyHoldings(
            complete,
            frozenset(identifier for _, identifier, _, _ in rows) - gone,
            len(rows),
            0,
            True,
            credential,
            stale,
        )

    async def delete_storage(self, selected: tuple) -> dict:
        """Delete the owner-selected stored files under this key, once, then invalidate matching local receipts.

        ``selected`` are ``StoredAsset`` entries from this key's own listing. After DT+ accepts the request, this key's
        confirmed receipts whose remote reference matches a deleted entry stop counting as confirmed; shared bytes,
        retained copies and other keys' receipts are untouched. No retry: a failure leaves receipts as they were.
        """
        deleter = getattr(self.backend, "delete_stored_assets", None)
        if deleter is None:
            raise ValidationError("The selected backend cannot delete DT+ storage.")
        keys = tuple(item.key for item in selected)
        if not keys:
            raise ValidationError("Choose at least one stored file to delete.")
        await deleter(keys)
        invalidated = 0
        try:
            credential = await self.backend.asset_credential_identity()
            deleted = set(keys)
            for asset_id, identifier, remote_id in await self.registry.confirmed_scope_references(credential):
                if any(_receipt_matches(remote_id, key) for key in deleted):
                    await self.registry.invalidate_scope(asset_id, credential, identifier)
                    invalidated += 1
        except (PersistenceError, AssetsBusyError, BackendError, ValidationError, OSError) as exc:
            diagnostic_event(
                log,
                "assets.storage_receipts_not_updated",
                level=logging.WARNING,
                data={"reason": type(exc).__name__, "deleted": len(keys)},
            )
            return {"deleted": len(keys), "receipts_invalidated": invalidated, "receipts_updated": False}
        diagnostic_event(
            log,
            "assets.storage_deleted",
            level=logging.INFO,
            data={"deleted": len(keys), "receipts_invalidated": invalidated, **self._key_diagnostics()},
        )
        return {"deleted": len(keys), "receipts_invalidated": invalidated, "receipts_updated": True}

    async def reconcile(self, asset_id: str, identifier: str | None = None) -> tuple[dict, AssetReconciliation]:
        """Check one named LoRA against the selected DT+ key without uploading or changing other keys."""
        record = await self.registry.get(asset_id)
        if record is None:
            raise ValidationError("No local asset receipt has that identifier.")
        if (record["backend_id"], record["origin"]) != (self.backend.backend_id, self.backend.origin):
            raise ValidationError("The record belongs to a different backend scope than the selected credential.")
        name = identifier or record["identifier"]
        content = AssetContent("lora", name, record["sha256"], record["size"])
        credential_id = await self._credential_id()
        scoped = await self.registry.scoped(asset_id, credential_id, name) if credential_id is not None else None
        was_confirmed = scoped is not None if credential_id is not None else record["state"] == "confirmed"
        previous = (
            scoped.remote_id
            if scoped is not None
            else (record["remote_id"] if credential_id is None and was_confirmed else None)
        )
        _, operation = await self.registry.begin(content, record["backend_id"], record["origin"], "reconcile")
        result = await self.check(content, previous)
        if result.status == "confirmed":
            await self.registry.confirm(
                asset_id,
                operation,
                result.reference,
                "reconciled",
                credential_id=credential_id,
                identifier=name,
            )
        elif result.status == "upload_required":
            if credential_id is not None:
                await self.registry.invalidate_scope(asset_id, credential_id, name)
            # A missing scoped copy is not evidence that another key lost its receipt.
            await self.registry.mark(
                asset_id,
                operation,
                "unknown" if was_confirmed else "failed",
                "reconcile_" + result.evidence,
                asset=credential_id is None,
            )
        else:
            # Unavailable or inconsistent checks cannot invalidate a confirmed receipt.
            await self.registry.mark(asset_id, operation, "unknown", "reconcile_" + result.evidence, asset=False)
        return await self.registry.get(asset_id), result

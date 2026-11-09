"""Shared asset preparation, reconciliation and dispatch-time validation.

Upload happens only here, only on an explicit ``prepare``. Generation and queue dispatch call ``validate`` (metadata
only). The service owns orchestration and uncertainty rules; the adapter owns HTTP and signed-URL handling; the
registry owns durable receipts. Nothing here retries a transfer that may have stored data.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path, PureWindowsPath
from collections.abc import Callable
from typing import Protocol

from dtflow.core.models import (
    AssetBackend,
    AssetContent,
    AssetReconciliation,
    BackendError,
    BackendFailure,
    ErrorCategory,
    PreparedAsset,
    RemoteOutcome,
    SubmissionStage,
    ValidationError,
)
from dtflow.core.tasks import settle
from dtflow.diagnostics import api_error_data, event as diagnostic_event
from dtflow.persistence.assets import AssetRegistry, AssetsBusyError
from dtflow.persistence.store import PersistenceError
from dtflow.services.lora_store import LoraStore

log = logging.getLogger(__name__)
CHUNK = 1024 * 1024

# One shielded operation per (origin, auth scope, content): identical concurrent prepares join it, anything else never does.
_INFLIGHT: dict[tuple, asyncio.Task] = {}
# Concurrent queue/workflow items needing the same restore or rebind join one operation, and distinct ones take
# turns, because the asset writer lock admits a single writer per state directory.
_SHARED: dict[tuple, asyncio.Task] = {}
_WRITER = asyncio.Lock()


def _failure(
    category: ErrorCategory, code: str, message: str, outcome=RemoteOutcome.NOT_STARTED, *, diagnostics=()
) -> BackendError:
    stage = (
        SubmissionStage.NOT_SUBMITTED if outcome == RemoteOutcome.NOT_STARTED else SubmissionStage.POSSIBLY_SUBMITTED
    )
    return BackendError(BackendFailure(category, code, message, None, stage, outcome, diagnostics=diagnostics))


def _observe(path: Path) -> tuple[os.stat_result, str]:
    """Hash incrementally (bounded memory) and require the file to be unchanged while it was read."""
    try:
        before = path.stat()
        if not path.is_file() or before.st_size < 1:
            raise ValidationError("Asset file must be a non-empty regular file.")
        digest, total = hashlib.sha256(), 0
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
                total += len(chunk)
        after = path.stat()
    except OSError as exc:
        raise ValidationError("Asset file could not be read.") from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or total != before.st_size:
        raise _failure(ErrorCategory.VALIDATION, "asset_changed", "Asset file changed while it was hashed.")
    return before, digest.hexdigest()


class AssetValidator(Protocol):
    async def validate(self, prepared: PreparedAsset) -> None: ...

    async def ensure(self, prepared: PreparedAsset, on_stage: Callable[[str], None] | None = None) -> PreparedAsset: ...


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

    @property
    def registry(self) -> AssetRegistry:
        if self._registry is None:
            raise ValidationError("This operation needs the durable asset registry.")
        return self._registry

    # -- prepare --------------------------------------------------------------------------------------------------

    async def prepare(self, path: Path, identifier: str | None = None, *, provenance: str = "") -> PreparedAsset:
        """Explicit, potentially network-writing preparation of one converted LoRA file."""
        path = Path(path)
        stat, digest = await asyncio.to_thread(_observe, path)
        content = AssetContent("lora", identifier or path.name, digest, stat.st_size, provenance)
        if self.store is not None:
            # Keep the durable copy first, so a failed or interrupted upload never costs the user the file.
            kept = await asyncio.to_thread(self.store.retain, path, digest, stat.st_size)
            diagnostic_event(
                log, "assets.copy_kept", level=logging.INFO, data={"size": stat.st_size, "name": kept.name}
            )
        key = (self.backend.backend_id, self.backend.origin, self.backend.scope, content.sha256, content.size)
        task = _INFLIGHT.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._prepare(path, content, stat))
            _INFLIGHT[key] = task
            task.add_done_callback(
                lambda done, key=key: _INFLIGHT.pop(key, None) if _INFLIGHT.get(key) is done else None
            )
            # Retrieve abandoned-waiter failures so they are never logged with provider detail.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        # Cancelling this caller must not cancel an operation another caller still awaits.
        return await asyncio.shield(task)

    async def _prepare(self, path: Path, content: AssetContent, stat: os.stat_result) -> PreparedAsset:
        backend = self.backend
        asset_id, operation = await self.registry.begin(content, backend.backend_id, backend.origin)
        transferred = False
        try:
            negotiation = await backend.negotiate_asset(content)
            if negotiation.status == "exists":
                receipt = await self.registry.confirm(asset_id, operation, negotiation.reference, "already_exists")
                diagnostic_event(
                    log,
                    "assets.prepared",
                    level=logging.INFO,
                    data={
                        "receipt": receipt.receipt_id,
                        "source": "already_exists",
                    },
                )
                return receipt
            await self.registry.mark(asset_id, operation, "transferring", "transfer_started")
            transferred = True
            await backend.upload_asset(path, content, negotiation)
        except asyncio.CancelledError:
            await settle(
                self._settle_unknown(
                    asset_id, operation, "cancelled" if transferred else "cancelled_early", transferred
                )
            )
            raise
        except BackendError as exc:
            failure = exc.failure
            definite = failure.remote_outcome in (RemoteOutcome.NOT_STARTED, RemoteOutcome.FAILED)
            await settle(
                self.registry.mark(
                    asset_id, operation, "failed" if definite or not transferred else "unknown", failure.code
                )
            )
            raise
        except PersistenceError:
            # Persistence failed before any PUT began: nothing was transferred; leftover intent is swept to unknown.
            raise
        except Exception:
            await settle(self._settle_unknown(asset_id, operation, "unexpected_failure", transferred))
            raise _failure(
                ErrorCategory.PROTOCOL, "asset_unexpected_failure", "Asset preparation failed.", RemoteOutcome.UNKNOWN
            ) from None
        # The transfer returned success; the server's own reference is still needed for a confirmed receipt.
        try:
            after = await backend.negotiate_asset(content)
        except BackendError as exc:
            await settle(self._settle_unknown(asset_id, operation, exc.failure.code, True))
            raise _failure(
                ErrorCategory.PROTOCOL,
                "asset_upload_unconfirmed",
                "Upload finished but the server did not confirm the file; run `assets reconcile`. No retry was made.",
                RemoteOutcome.UNKNOWN,
            ) from None
        if after.status != "exists":
            await settle(self._settle_unknown(asset_id, operation, "post_upload_unconfirmed", True))
            raise _failure(
                ErrorCategory.PROTOCOL,
                "asset_upload_unconfirmed",
                "Upload finished but the server does not list the file yet; run `assets reconcile`. No retry was made.",
                RemoteOutcome.UNKNOWN,
            )
        try:
            receipt = await self.registry.confirm(asset_id, operation, after.reference, "transfer")
        except (PersistenceError, ValidationError):
            try:
                await settle(self._settle_unknown(asset_id, operation, "receipt_write_failed", True))
            except Exception:
                diagnostic_event(
                    log,
                    "assets.receipt_write_failed",
                    level=logging.ERROR,
                    data={},
                )
            raise _failure(
                ErrorCategory.PERSISTENCE,
                "asset_receipt_unwritten",
                "The file was uploaded but its receipt could not be saved; run `assets reconcile`. No retry was made.",
                RemoteOutcome.SUCCEEDED,
            ) from None
        diagnostic_event(
            log,
            "assets.prepared",
            level=logging.INFO,
            data={
                "receipt": receipt.receipt_id,
                "source": "transfer",
            },
        )
        return receipt

    async def _settle_unknown(self, asset_id: str, operation: str, evidence: str, transferred: bool) -> None:
        await self.registry.mark(asset_id, operation, "unknown" if transferred else "failed", evidence)

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
        self, content: AssetContent, expected: str | None
    ) -> tuple[AssetReconciliation, BackendFailure | None]:
        """Retain backend-neutral failure evidence for dispatch without persisting response bodies."""
        try:
            negotiation = await self.backend.negotiate_asset(content)
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

    async def validate(self, prepared: PreparedAsset) -> None:
        """Dispatch-time guard without restoration: require current server validation or fail without uploading."""
        await self._verify(prepared, None, restoring=False)

    async def ensure(self, prepared: PreparedAsset, on_stage: Callable[[str], None] | None = None) -> PreparedAsset:
        """Validate the receipt; only a positive "upload required" answer restores it from the retained local copy.

        Returns the receipt to submit with: the original when the server already has the file, otherwise the freshly
        confirmed one. One restoration is attempted per asset per call; the old receipt remains in the registry."""
        return await self._verify(prepared, on_stage, restoring=self.state is not None)

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
        result, cause = await self._check(self._content(prepared), prepared.remote_id)
        if result.status == "confirmed":
            return prepared
        diagnostic_event(
            log,
            "assets.validation_not_confirmed",
            level=logging.INFO,
            data={"asset": asset, "status": result.status, "evidence": result.evidence, "restoring": restoring},
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

    async def _shared(self, action: str, prepared: PreparedAsset, operation) -> PreparedAsset:
        """One operation per (action, scope, content); callers needing it concurrently join it."""
        key = (action, self.backend.backend_id, self.backend.origin, self.backend.scope, prepared.sha256, prepared.size)
        task = _SHARED.get(key)
        if task is None or task.done():

            async def serialized() -> PreparedAsset:
                async with _WRITER:
                    return await operation()

            task = asyncio.create_task(serialized())
            _SHARED[key] = task
            task.add_done_callback(lambda done, key=key: _SHARED.pop(key, None) if _SHARED.get(key) is done else None)
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    async def _rebind(self, prepared: PreparedAsset, asset: str, reference: str) -> PreparedAsset:
        """The server holds these exact bytes (it looked them up by SHA-256 and size) under a newer reference.

        Nothing is uploaded: the receipt adopts the server's current reference for this scope, and the operation log
        keeps the earlier evidence."""
        registry = AssetRegistry(self.state, writer=True)
        try:
            await registry.open()
        except AssetsBusyError:
            raise _failure(
                ErrorCategory.VALIDATION,
                "prepared_asset_restore_busy",
                f"LoRA {asset} has a newer DT+ reference, but another process is preparing assets. Nothing was submitted.",
            ) from None
        try:
            content = self._content(prepared)
            asset_id, operation = await registry.begin(content, prepared.backend_id, prepared.origin, "rebind")
            rebound = await registry.confirm(asset_id, operation, reference, "reconciled")
        finally:
            await registry.aclose()
        diagnostic_event(
            log, "assets.rebound", level=logging.INFO, data={"asset": asset, "receipt": rebound.receipt_id}
        )
        return rebound

    async def _restore(
        self, prepared: PreparedAsset, asset: str, on_stage: Callable[[str], None] | None
    ) -> PreparedAsset:
        """Upload the retained copy once under a short-lived asset writer, then return the confirmed receipt."""
        store = self.store or LoraStore(self.state)
        try:
            retained = await asyncio.to_thread(store.find, prepared.sha256, prepared.size)
            if retained is None:
                diagnostic_event(
                    log,
                    "assets.restore_blocked",
                    level=logging.WARNING,
                    data={"asset": asset, "reason": "no_local_copy"},
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
                data={"asset": asset, "reason": "local_copy_invalid"},
            )
            raise _failure(
                ErrorCategory.VALIDATION,
                "prepared_asset_local_copy_invalid",
                f"LoRA {asset} is missing on DT+ and its kept local copy is unusable ({exc}). Nothing was submitted.",
            ) from None
        diagnostic_event(
            log, "assets.restore_started", level=logging.INFO, data={"asset": asset, "size": retained.size}
        )
        if on_stage is not None:
            on_stage("restoring_assets")
        registry = AssetRegistry(self.state, writer=True)
        try:
            await registry.open()
        except AssetsBusyError:
            raise _failure(
                ErrorCategory.VALIDATION,
                "prepared_asset_restore_busy",
                f"LoRA {asset} is missing on DT+, but another process is preparing assets. Nothing was submitted.",
            ) from None
        try:
            restored = await AssetService(self.backend, registry).prepare(
                retained.path, prepared.identifier, provenance="auto_restore"
            )
        finally:
            await registry.aclose()
        if (restored.sha256, restored.size) != (prepared.sha256, prepared.size):
            raise _failure(
                ErrorCategory.PROTOCOL, "asset_restore_mismatch", "Restored asset does not match its record."
            )
        diagnostic_event(
            log, "assets.restored", level=logging.INFO, data={"asset": asset, "receipt": restored.receipt_id}
        )
        return restored

    async def reconcile(self, asset_id: str) -> tuple[dict, AssetReconciliation]:
        """Metadata-only server validation of one local record under the current credential; never a PUT."""
        record = await self.registry.get(asset_id)
        if record is None:
            raise ValidationError("No local asset receipt has that identifier.")
        if (record["backend_id"], record["origin"]) != (self.backend.backend_id, self.backend.origin):
            raise ValidationError("The record belongs to a different backend scope than the selected credential.")
        content = self._content(record)
        _, operation = await self.registry.begin(content, record["backend_id"], record["origin"], "reconcile")
        was_confirmed = record["state"] == "confirmed"
        result = await self.check(content, record["remote_id"] if was_confirmed else None)
        if result.status == "confirmed":
            await self.registry.confirm(asset_id, operation, result.reference, "reconciled")
        elif result.status == "upload_required":
            # The server positively lacks the file: a receipt that claimed otherwise is stale, an unresolved record failed.
            await self.registry.mark(
                asset_id, operation, "unknown" if was_confirmed else "failed", "reconcile_" + result.evidence
            )
        else:
            # An unreachable, unauthorized or inconsistent check says nothing certain, so the record itself is kept.
            await self.registry.mark(asset_id, operation, "unknown", "reconcile_" + result.evidence, asset=False)
        return await self.registry.get(asset_id), result

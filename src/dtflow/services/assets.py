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
from pathlib import Path
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
from dtflow.persistence.assets import AssetRegistry
from dtflow.persistence.store import PersistenceError

log = logging.getLogger(__name__)
CHUNK = 1024 * 1024

# One shielded operation per (origin, auth scope, content): identical concurrent prepares join it, anything else never does.
_INFLIGHT: dict[tuple, asyncio.Task] = {}


def _failure(category: ErrorCategory, code: str, message: str, outcome=RemoteOutcome.NOT_STARTED) -> BackendError:
    stage = (
        SubmissionStage.NOT_SUBMITTED if outcome == RemoteOutcome.NOT_STARTED else SubmissionStage.POSSIBLY_SUBMITTED
    )
    return BackendError(BackendFailure(category, code, message, None, stage, outcome))


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


class AssetService:
    def __init__(self, backend: AssetBackend, registry: AssetRegistry | None = None):
        """``registry`` may be omitted by callers that only ``validate`` (generation and queue dispatch)."""
        self.backend, self._registry = backend, registry

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
                log.info("asset prepared receipt=%s source=already_exists", receipt.receipt_id)
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
                log.error("asset receipt and unknown marker could not be written; the next writer will sweep it")
            raise _failure(
                ErrorCategory.PERSISTENCE,
                "asset_receipt_unwritten",
                "The file was uploaded but its receipt could not be saved; run `assets reconcile`. No retry was made.",
                RemoteOutcome.SUCCEEDED,
            ) from None
        log.info("asset prepared receipt=%s source=transfer", receipt.receipt_id)
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
        try:
            negotiation = await self.backend.negotiate_asset(content)
        except BackendError as exc:
            code = exc.failure.code
            if exc.failure.category in (ErrorCategory.AUTHENTICATION, ErrorCategory.CREDENTIALS):
                return AssetReconciliation("unavailable", code)
            return AssetReconciliation("unknown", code)
        if negotiation.status == "upload_required":
            return AssetReconciliation("upload_required", "server_has_no_such_file")
        if expected is not None and negotiation.reference != expected:
            return AssetReconciliation("unknown", "reference_mismatch", negotiation.reference)
        return AssetReconciliation("confirmed", "server_validated", negotiation.reference)

    async def validate(self, prepared: PreparedAsset) -> None:
        """Dispatch-time guard: require current server validation of the exact reference, or fail without uploading."""
        if prepared.backend_id != self.backend.backend_id or prepared.origin != self.backend.origin:
            raise _failure(
                ErrorCategory.VALIDATION, "prepared_asset_scope", "Prepared asset belongs to a different backend scope."
            )
        result = await self.check(self._content(prepared), prepared.remote_id)
        if result.status != "confirmed":
            raise _failure(
                ErrorCategory.VALIDATION,
                "prepared_asset_" + result.status,
                f"Prepared asset {prepared.identifier!r} is not validated by the server ({result.evidence}); "
                "nothing was uploaded or submitted.",
            )

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

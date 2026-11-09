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
from pathlib import Path, PureWindowsPath
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from dtflow.core.models import (
    AssetBackend,
    AssetContent,
    AssetNegotiation,
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
    """Only a plausible missing-LoRA rejection may start a bounded metadata probe."""
    return failure.code in _ASSET_REJECTION_CODES


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

    def forget(self, assets) -> None: ...


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
        # The UI can receive individual names without adding them to diagnostic records.
        self.on_resolution: Callable[[dict], None] | None = None

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
        key = (
            self.backend.backend_id,
            self.backend.origin,
            self.backend.scope,
            content.identifier,
            content.sha256,
            content.size,
        )
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
    ) -> PreparedAsset:
        backend = self.backend
        credential_id = await self._credential_id()
        asset_id, operation = await self.registry.begin(content, backend.backend_id, backend.origin)
        transferred = False
        try:
            negotiation = negotiation or await backend.negotiate_asset(content)
            if negotiation.status == "exists":
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
                        data={"receipt": receipt.receipt_id, "source": "already_exists"},
                    )
                return receipt
            await self.registry.mark(
                asset_id, operation, "transferring", "transfer_started", asset=credential_id is None
            )
            transferred = True
            await backend.upload_asset(path, content, negotiation)
        except asyncio.CancelledError:
            await settle(
                self._settle_unknown(
                    asset_id, operation, "cancelled" if transferred else "cancelled_early", transferred, credential_id
                )
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
                    asset=credential_id is None,
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
                    data={},
                )
            raise _failure(
                ErrorCategory.PERSISTENCE,
                "asset_receipt_unwritten",
                "The file was uploaded but its receipt could not be saved; run `assets reconcile`. No retry was made.",
                RemoteOutcome.SUCCEEDED,
            ) from None
        if content.provenance != "auto_restore":
            diagnostic_event(
                log, "assets.prepared", level=logging.INFO,
                data={"receipt": receipt.receipt_id, "source": "transfer"},
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
            asset=credential_id is None,
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
            return tuple(
                [await self._verify(item, on_stage, restoring=self.state is not None) for item in prepared_assets]
            )
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
                    item.sha256, item.size, item.identifier
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
                return tuple(existing)

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
                            "key-rebind:" + negotiation.reference, item,
                            lambda item=item, ref=negotiation.reference: self._rebind(
                                item, PureWindowsPath(item.identifier).name, ref, credential_id=credential_id
                            ), scope_id=credential_id,
                        )
                        outcomes[current] = "relinked"
                elif negotiation.status == "upload_required":
                    results[index] = await self._shared(
                        "key-restore", item,
                        lambda item=item, negotiation=negotiation: self._restore(
                            item, PureWindowsPath(item.identifier).name, on_stage, negotiation=negotiation,
                            credential_id=credential_id,
                        ), scope_id=credential_id,
                    )
                    outcomes[current] = "restored"
                    if restored_out is not None:
                        restored_out.append(self._identity(self._content(item)))
                else:
                    raise _failure(ErrorCategory.PROTOCOL, "asset_check_inconclusive", "DT+ asset status is unknown.")
                current = None
            outcome = "completed"
            return tuple(results)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except BackendError as exc:
            affected = (current,) if current is not None else tuple(
                key for key in pending if key not in outcomes
            )
            if current is not None:
                outcomes[current] = "failed"
                outcome = "failed"
            else:
                outcome = "inconclusive"
            if affected:
                labels = [names[key] for key in dict.fromkeys(affected)]
                subject = ("LoRA " if len(labels) == 1 else "LoRAs ") + ", ".join(labels)
                message = exc.failure.message
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
                },
            )
            receiver = on_resolution if on_resolution is not None else self.on_resolution
            if receiver is not None:
                try:
                    receiver(report)
                except Exception:
                    log.warning("Asset resolution status could not be displayed.")

    async def recover_missing(
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
                "asset": asset,
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
                    data={"reason": "no_local_copy"},
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
                data={"reason": "local_copy_invalid"},
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

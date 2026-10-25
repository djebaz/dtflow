"""Shared Draw Things gRPC connection, inventory, generation and lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import betterproto
from drawthings_py.generated.dt_grpc import image_service
from grpclib.client import Channel
from grpclib.config import Configuration
from grpclib.const import Cardinality

from dtflow.backends.drawthings.codec import (
    TensorRejected,
    check_final,
    decode_final,
    decode_preview_artifact,
)
from dtflow.backends.drawthings.errors import api_error, api_failure, diagnostics, error, failure, from_exception
from dtflow.backends.drawthings.inventory import build_snapshot
from dtflow.backends.drawthings.tls import TlsPolicy
from dtflow.backends.drawthings.versions import bundled_base_version
from dtflow.backends.drawthings.wire import CLIENT_NAME, build_generation_message
from dtflow.core.image_inputs import ImageInputSnapshot, PreparedImageInputs
from dtflow.core.image_operations import ImageOperationPermit
from dtflow.core.model_profiles import exact_model_description_digest, exact_model_spec, fallback_spec
from dtflow.core.models import (
    BackendError,
    BackendEvent,
    Capabilities,
    CompletionEvidence,
    ConnectionStatus,
    CredentialProvider,
    Designation,
    ErrorCategory,
    EventSink,
    FileIdentity,
    GenerationResult,
    ImageArtifact,
    InventorySnapshot,
    RemoteOutcome,
    ResolvedGenerationRequest,
    ResourceLimits,
    SubmissionStage,
    TimeoutPolicy,
    ValidationError,
    eligible_model_ids,
)
from dtflow.core.tasks import settle
from dtflow.diagnostics import event as diagnostic_event

log = logging.getLogger(__name__)
GENERATE_METHOD = "/ImageGenerationService/GenerateImage"

BACKEND_ID = "drawthings-grpc"


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class DrawThingsEndpoint:
    host: str
    port: int
    tls: TlsPolicy = field(default_factory=TlsPolicy.drawthings_local)

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValidationError("Endpoint host is required.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValidationError("Endpoint port must be in 1..65535.")

    @property
    def label(self) -> str:
        """Credential-free endpoint identity."""
        return f"grpcs://{self.host}:{self.port}"


class DrawThingsBackend:
    """One channel to one Draw Things server; explicit trust and no silent fallback."""

    backend_id = BACKEND_ID
    max_concurrency = 1
    unlisted_model_bridge = True

    def __init__(
        self,
        endpoint: DrawThingsEndpoint,
        *,
        timeouts: TimeoutPolicy | None = None,
        limits: ResourceLimits | None = None,
        shared_secret: CredentialProvider | None = None,
        image_permits: tuple[ImageOperationPermit, ...] = (),
        image_inputs_loader: Callable[[ImageInputSnapshot], PreparedImageInputs] | None = None,
        allow_image_probe: bool = False,
    ):
        self.endpoint = endpoint
        self.timeouts = timeouts or TimeoutPolicy()
        self.limits = limits or ResourceLimits()
        self._shared_secret = shared_secret
        self._channel: Channel | None = None
        self._closing = False
        self._closed = False
        self._active = 0
        self._lane = asyncio.Semaphore(self.max_concurrency)
        self._inventory: InventorySnapshot | None = None
        self._stale_reason: str | None = None
        self._model_specs: dict[str, dict] = {}
        self._image_inputs_loader = image_inputs_loader
        self._allow_image_probe = allow_image_probe
        self._image_permits, self._image_specs = self.validate_image_permits(
            endpoint, image_permits, image_inputs_loader, allow_image_probe
        )

    @classmethod
    def validate_image_permits(cls, endpoint, image_permits, image_inputs_loader, allow_image_probe):
        """Pure preflight, also used before composition allocates cloud auth clients."""
        if type(allow_image_probe) is not bool or not isinstance(image_permits, tuple):
            raise ValidationError("Image-operation permits must be an immutable tuple with explicit probe policy.")
        permits, specs = {}, {}
        for permit in image_permits:
            if not isinstance(permit, ImageOperationPermit):
                raise ValidationError("Image-operation permit is malformed.")
            permit.validate_scope(
                cls.backend_id, endpoint.label, permit.model_id, permit.operation, allow_probe=allow_image_probe
            )
            if image_inputs_loader is None or not callable(image_inputs_loader):
                raise ValidationError("Image operations require a managed input loader.")
            key = (permit.model_id, permit.operation)
            if key in permits:
                raise ValidationError("Duplicate exact image-operation permits are refused.")
            spec = exact_model_spec(permit.model_id)
            digest = exact_model_description_digest(permit.model_id)
            if (
                cls.backend_id == "drawthings-cloud" or permit.model_description_sha256 is not None
            ) and digest != permit.model_description_sha256:
                raise ValidationError("The published model description differs from the owner permit.")
            permits[key] = permit
            specs[permit.model_id] = spec if cls.backend_id == "drawthings-cloud" else None
        return permits, specs

    # -- contract ------------------------------------------------------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        """Model identifiers come from the last inventory; before any inventory no model is accepted."""
        model_ids = eligible_model_ids(self._inventory) if self._inventory is not None else ()
        return Capabilities(
            self.backend_id,
            operations=("txt2img", *sorted({p.operation for p in self._image_permits.values()})),
            model_ids=model_ids,
            max_concurrency=self.max_concurrency,
            batch_size=1,
            batch_count=1,
            lora_support="selection",
            limits=self.limits,
            unlisted_model_bridge=self.unlisted_model_bridge,
        )

    def validate_image_operation(self, request: ResolvedGenerationRequest) -> ImageOperationPermit:
        """Exact owner gate before inventory/auth calls; a broad capability is insufficient."""
        permit = self._image_permits.get((request.model_id, request.operation))
        if permit is None:
            raise ValidationError("No owner permit exists for this exact backend/checkpoint/image operation.")
        permit.validate_scope(
            self.backend_id,
            self.endpoint.label,
            request.model_id,
            request.operation,
            allow_probe=self._allow_image_probe,
        )
        if request.image_inputs is None or self._image_inputs_loader is None:
            raise ValidationError("Image-operation managed inputs are missing.")
        return permit

    async def open(self) -> None:
        """Create the channel; the connection itself is established lazily and bounded by the connect deadline."""
        if self._closing or self._closed:
            raise error(ErrorCategory.CONNECTIVITY, "backend_closed", "Backend is closed.")
        if self._channel is None:
            tls = self.endpoint.tls
            config = Configuration(ssl_target_name_override=tls.server_name) if tls.server_name else None
            self._channel = Channel(self.endpoint.host, self.endpoint.port, ssl=tls.build_context(), config=config)

    async def check_connection(self) -> ConnectionStatus:
        """Non-generative evidence; transport, authentication and inventory stay distinct."""
        checked = now()
        base = {"endpoint": self.endpoint.label}

        def status(transport, authentication, inventory, **diag):
            return ConnectionStatus(
                self.backend_id,
                self.endpoint.label,
                checked,
                transport,
                authentication,
                inventory,
                diagnostics(**diag),
            )

        try:
            await self._connect()
        except BackendError as exc:
            detail = dict(exc.failure.diagnostics)
            kind = detail.get("kind")
            return status(
                "failed",
                "unknown",
                "unknown",
                transport_code=exc.failure.code,
                **({"transport_kind": kind} if kind else {}),
                **{
                    key: value
                    for key, value in detail.items()
                    if key in ("server_message", "status", "operation", "http_status")
                },
            )
        try:
            reply, secret_sent = await self._echo()
        except BackendError as exc:
            detail = {
                key: value
                for key, value in exc.failure.diagnostics
                if key in ("server_message", "status", "operation", "http_status")
            }
            if exc.failure.category == ErrorCategory.AUTHENTICATION:
                return status("ok", "failed", "unknown", authentication_code=exc.failure.code, **detail)
            return status("failed", "unknown", "unknown", transport_code=exc.failure.code, **detail)
        if reply.shared_secret_missing:
            api_failure(
                ErrorCategory.AUTHENTICATION,
                "shared_secret_missing",
                "Server requires a valid shared secret.",
                operation="Echo",
                transport="grpc",
                diagnostics=diagnostics(status="OK"),
            )
            return status(
                "ok",
                "failed",
                "unknown",
                shared_secret="rejected_or_missing" if secret_sent else "required_not_configured",
            )
        snapshot = build_snapshot(
            self.backend_id,
            reply,
            observed_at_utc=checked,
            refreshed=True,
            secret_sent=secret_sent,
            endpoint=self.endpoint.label,
        )
        return status("ok", "ok" if secret_sent else "not_applicable", "ok", entries=len(snapshot.entries), **base)

    async def get_inventory(self, refresh: bool = False) -> InventorySnapshot:
        """Return the cached snapshot or fetch a fresh one.

        A failed refresh raises and leaves the previous snapshot flagged stale (never presented as fresh); a failed
        authentication clears it, because the server no longer accepts our identity.
        """
        if self._inventory is not None and not refresh:
            return self._with_staleness(self._inventory)
        try:
            await self._connect()
            reply, secret_sent = await self._echo()
        except BackendError as exc:
            if exc.failure.category == ErrorCategory.AUTHENTICATION:
                self._inventory = self._stale_reason = None
            elif self._inventory is not None:
                self._stale_reason = exc.failure.code
            raise
        if reply.shared_secret_missing:
            self._inventory = self._stale_reason = None
            raise api_error(
                ErrorCategory.AUTHENTICATION,
                "shared_secret_rejected_or_missing",
                "Server requires a shared secret that was not accepted.",
                diagnostics=diagnostics(shared_secret="sent" if secret_sent else "not_configured"),
                operation="Echo",
                transport="grpc",
            )
        self._inventory = build_snapshot(
            self.backend_id,
            reply,
            observed_at_utc=now(),
            refreshed=True,
            secret_sent=secret_sent,
            endpoint=self.endpoint.label,
        )
        self._stale_reason = None
        return self._inventory

    async def get_file_identities(
        self, identifiers: tuple[str, ...], *, with_sha256: tuple[str, ...] = ()
    ) -> tuple[FileIdentity, ...]:
        """Return server-side existence and optional SHA-256 without transferring file bytes."""
        if not isinstance(identifiers, tuple) or any(
            not isinstance(name, str) or not name or "/" in name or "\\" in name for name in identifiers
        ):
            raise ValidationError("File identities require checkpoint basenames.")
        hash_names = set(with_sha256)
        if not hash_names.issubset(set(identifiers)):
            raise ValidationError("SHA-256 may be requested only for listed file identifiers.")
        if not identifiers:
            return ()
        began = time.monotonic()
        await self._connect()
        secret = await self._secret()
        request = image_service.FileListRequest(files=list(identifiers), files_with_hash=list(with_sha256))
        if secret:
            request.shared_secret = secret
        stub = image_service.ImageGenerationServiceStub(self._require_channel())
        try:
            response = await stub.files_exist(request, timeout=self.timeouts.inventory)
        except Exception as exc:
            raise BackendError(
                from_exception(
                    exc,
                    phase="inventory",
                    operation="FilesExist",
                    private=(secret or "", *identifiers),
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
            ) from None
        if len(response.existences) != len(response.files):
            raise api_error(
                ErrorCategory.PROTOCOL,
                "file_identity_incomplete",
                "Server returned incomplete file existence results.",
                operation="FilesExist",
                transport="grpc",
                elapsed_ms=(time.monotonic() - began) * 1000,
                diagnostics=diagnostics(result_count=len(response.files), existence_count=len(response.existences)),
            )
        by_name: dict[str, FileIdentity] = {}
        for index, name in enumerate(response.files):
            exists = bool(response.existences[index]) if index < len(response.existences) else False
            digest = response.hashes[index] if index < len(response.hashes) else b""
            if digest and len(digest) != 32:
                raise api_error(
                    ErrorCategory.PROTOCOL,
                    "file_hash_invalid",
                    "Server returned an invalid file hash.",
                    diagnostics=diagnostics(result_count=len(response.files)),
                    operation="FilesExist",
                    transport="grpc",
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
            by_name[str(name)] = FileIdentity(str(name), exists, digest.hex() if digest else None)
        missing = [name for name in identifiers if name not in by_name]
        if missing:
            raise api_error(
                ErrorCategory.PROTOCOL,
                "file_identity_incomplete",
                "Server omitted requested file identity results.",
                diagnostics=diagnostics(missing=len(missing)),
                operation="FilesExist",
                transport="grpc",
                elapsed_ms=(time.monotonic() - began) * 1000,
            )
        diagnostic_event(
            log,
            "backend.api_completed",
            level=logging.DEBUG,
            data={
                "transport": "grpc",
                "operation": "FilesExist",
                "status": "OK",
                "requested": len(identifiers),
                "present": sum(item.exists for item in by_name.values()),
                "elapsed_ms": (time.monotonic() - began) * 1000,
            },
        )
        return tuple(by_name[name] for name in identifiers)

    async def generate(self, request: ResolvedGenerationRequest, *, on_event: EventSink) -> GenerationResult:
        """Run one text-to-image generation; the total deadline covers prepare, lane wait, connect, auth, stream and decode.

        ``submission_possible`` is emitted immediately before the request is sent. Decoded partial finals on a failure
        are attached to the failure in memory only; the durable store does not persist their bytes.
        """
        if self._closing or self._closed:
            raise error(ErrorCategory.CONNECTIVITY, "backend_closed", "Backend is closed.", request.request_id)
        if request.operation not in self.capabilities.operations:
            raise error(
                ErrorCategory.VALIDATION,
                "unsupported_operation",
                "Image-operation dispatch is not offered by this adapter.",
                request.request_id,
            )
        if request.operation != "txt2img":
            self.validate_image_operation(request)
        started = time.monotonic()
        stage = SubmissionStage.NOT_SUBMITTED
        sequence = 0
        finals: list[ImageArtifact] = []
        warnings: tuple[str, ...] = ()
        stream = None
        metadata = {}

        def remaining() -> float:
            return max(0.0, self.timeouts.total - (time.monotonic() - started))

        def emit(kind: str, **payload) -> None:
            nonlocal sequence
            sequence += 1
            try:
                on_event(BackendEvent(request.request_id, sequence, kind, now(), **payload))
            except Exception as exc:
                raise BackendError(
                    self._failure(request, ErrorCategory.PROTOCOL, "event_sink_failure", "Event sink failed.", stage)
                ) from exc

        if (
            not (self.unlisted_model_bridge and request.allow_unlisted_model)
            and request.model_id not in self.capabilities.model_ids
        ):
            raise error(
                ErrorCategory.VALIDATION,
                "unknown_model",
                "Model is not in the server inventory; refresh the inventory first.",
                request.request_id,
            )
        limits = self.limits
        expected = request.batch_size * request.batch_count
        self._active += 1
        try:
            async with asyncio.timeout(remaining()):
                model = (
                    next((e for e in self._inventory.entries if e.identifier == request.model_id), None)
                    if self._inventory
                    else None
                )
                base_version = (model.architecture if model else None) or bundled_base_version(request.model_id)
                if request.operation == "txt2img":
                    emit("preparing", stage="resolving_model")
                    spec = await self._model_spec(request.model_id)
                    emit("preparing", stage="encoding_request")
                    wire = build_generation_message(request, await self._secret(), base_version, spec)
                else:
                    # Never refresh/derive cloud descriptions on this path. The
                    # exact published entry and digest were gated before open/auth.
                    spec = self._image_specs[request.model_id]
                    base_version = (spec or {}).get("version") or base_version
                    emit("preparing", stage="preparing_image_inputs")
                    prepared = await settle(asyncio.to_thread(self._image_inputs_loader, request.image_inputs))
                    emit("preparing", stage="resolving_credentials")
                    secret = await self._secret()
                    emit("preparing", stage="encoding_request")
                    wire = await settle(
                        asyncio.to_thread(
                            build_generation_message,
                            request,
                            secret,
                            base_version,
                            spec,
                            prepared=prepared,
                            image_permit=self.validate_image_operation(request),
                            backend_id=self.backend_id,
                            endpoint=self.endpoint.label,
                            allow_image_probe=self._allow_image_probe,
                            limits=limits,
                        )
                    )
                if spec:
                    diagnostic_event(
                        log,
                        "backend.model_description_selected",
                        level=logging.DEBUG,
                        data={
                            "model_id": request.model_id,
                            "advertised": request.model_id in self._model_specs,
                            "field_count": len(spec),
                        },
                    )
                if request.loras:
                    diagnostic_event(
                        log,
                        "backend.loras_selected",
                        level=logging.DEBUG,
                        data={
                            "lora_count": len(request.loras),
                            "base_version": base_version,
                            "prepared_count": len(request.prepared_assets),
                        },
                    )
                emit("preparing", stage="wire_validated")
                emit("preparing", stage="waiting_execution_lane")
                async with self._lane:
                    emit("preparing", stage="connecting_backend")
                    await self._connect()
                    emit("preparing", stage="preparing_grpc_metadata")
                    metadata = await self._generation_metadata(wire.message)
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    stage = SubmissionStage.POSSIBLY_SUBMITTED
                    emit("submission_possible", stage="grpc_request")
                    channel = self._require_channel()
                    received = 0
                    async with channel.request(
                        GENERATE_METHOD,
                        Cardinality.UNARY_STREAM,
                        image_service.ImageGenerationRequest,
                        image_service.ImageGenerationResponse,
                        timeout=remaining(),
                        metadata=metadata,
                    ) as stream:
                        await stream.send_message(wire.message, end=True)
                        request_sent_at = time.monotonic()
                        emit("preparing", stage="waiting_backend_response")
                        diagnostic_event(
                            log,
                            "backend.generation_request_sent",
                            level=logging.INFO,
                            data={"transport": "grpc", "backend": self.backend_id},
                        )
                        previews = 0
                        first_response = True
                        async for response in stream:
                            if first_response:
                                first_response = False
                                diagnostic_event(
                                    log,
                                    "backend.generation_first_response",
                                    level=logging.INFO,
                                    data={
                                        "transport": "grpc",
                                        "backend": self.backend_id,
                                        "wait_ms": round((time.monotonic() - request_sent_at) * 1000),
                                    },
                                )
                                emit("preparing", stage="backend_response_received")
                            self._log_response(response)
                            if response.chunk_state == image_service.ChunkState.MORE_CHUNKS:
                                raise self._reject(request, "chunked_output_unsupported", stage, finals)
                            for signpost in (response.current_signpost,) if response.current_signpost else ():
                                self._signpost(signpost, request, emit)
                            if response.preview_image:
                                previews += 1
                                preview = await settle(
                                    asyncio.to_thread(decode_preview_artifact, response.preview_image, previews, limits)
                                )
                                if preview is not None:
                                    emit("preview", artifact=preview)
                            for data in response.generated_images:
                                if len(finals) >= min(expected, limits.final_count):
                                    raise self._reject(request, "unexpected_final_output", stage, finals)
                                received += len(data)
                                try:
                                    width, height, channels = check_final(data, request, limits)
                                    if received > limits.transport_bytes:
                                        raise TensorRejected("tensor_transport_limit")
                                    artifact = await settle(
                                        asyncio.to_thread(
                                            decode_final, data, width, height, channels, len(finals) + 1, limits
                                        )
                                    )
                                except TensorRejected as exc:
                                    raise self._reject(request, exc.code, stage, finals) from exc
                                finals.append(artifact)
                                emit("artifact_received", artifact=artifact)
                    if log.isEnabledFor(logging.DEBUG):
                        for label in ("initial_metadata", "trailing_metadata"):
                            items = getattr(stream, label, None)
                            if items:
                                diagnostic_event(
                                    log,
                                    "backend.grpc_metadata_observed",
                                    level=logging.DEBUG,
                                    data={"stage": label, "observed": True},
                                )
                    stage = SubmissionStage.TERMINAL_OBSERVED
                    if len(finals) != expected:
                        code = "no_final_output" if not finals else "incomplete_final_output"
                        raise self._reject(request, code, stage, finals, terminal="OK")
                    try:
                        emit("terminal", stage="OK")
                    except BackendError:
                        # Notification failure cannot erase terminal OK and validated final artifacts.
                        warnings = ("Terminal event delivery failed after validated backend completion.",)
        except BackendError as exc:
            if exc.failure.request_id is None:
                raise BackendError(replace(exc.failure, request_id=request.request_id)) from None
            raise
        except Exception as exc:
            trailers = stream is not None and getattr(stream, "trailing_metadata", None) is not None
            problem = from_exception(
                exc,
                phase="generation",
                operation="GenerateImage",
                request_id=request.request_id,
                stage=stage,
                trailers=trailers,
                private=(
                    request.prompt,
                    request.negative_prompt,
                    *(item.remote_id for item in request.prepared_assets),
                    *(metadata or {}).values(),
                ),
                response_headers=(
                    *(getattr(stream, "initial_metadata", None) or {}).items(),
                    *(getattr(stream, "trailing_metadata", None) or {}).items(),
                ),
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
            raise BackendError(replace(problem, partial_artifacts=self._partials(finals))) from None
        finally:
            self._active -= 1
        diagnostic_event(
            log,
            "backend.api_completed",
            level=logging.DEBUG,
            data={
                "transport": "grpc",
                "operation": "GenerateImage",
                "status": "OK",
                "final_count": len(finals),
                "elapsed_ms": (time.monotonic() - started) * 1000,
            },
        )
        server = self._inventory.server_identity if self._inventory is not None else "unknown"
        return GenerationResult(
            request.request_id,
            self.backend_id,
            server,
            tuple(finals),
            wire.effective_configuration,
            CompletionEvidence("OK", True),
            warnings,
        )

    @staticmethod
    def _log_response(response) -> None:
        """Development diagnostics: which non-image fields the server set, never image bytes."""
        if not log.isEnabledFor(logging.DEBUG):
            return
        seen = {}
        if response.tags:
            seen["tag_count"] = len(response.tags)
        if response.download_size:
            seen["download_size"] = response.download_size
        if response.remote_download is not None:
            seen["remote_download_present"] = True
        if response.scale_factor:
            seen["scale_factor"] = response.scale_factor
        if response.signposts:
            seen["signposts"] = [betterproto.which_one_of(item, "signpost")[0] for item in response.signposts]
        if response.current_signpost:
            seen["signpost"] = betterproto.which_one_of(response.current_signpost, "signpost")[0]
        if response.preview_image:
            seen["preview_bytes"] = len(response.preview_image)
        if response.generated_images:
            seen["images"] = len(response.generated_images)
            seen["image_bytes"] = [len(item) for item in response.generated_images]
        if seen:
            diagnostic_event(log, "backend.response_observed", level=logging.DEBUG, data=seen)

    @staticmethod
    def _partials(finals: list[ImageArtifact]) -> tuple[ImageArtifact, ...]:
        return tuple(replace(x, designation=Designation.PARTIAL) for x in finals)

    def _failure(self, request, category, code, message, stage, **extra):
        outcome = RemoteOutcome.NOT_STARTED if stage == SubmissionStage.NOT_SUBMITTED else RemoteOutcome.UNKNOWN
        return failure(category, code, message, request.request_id, stage, outcome, **extra)

    def _reject(self, request, code, stage, finals, terminal: str | None = None) -> BackendError:
        """A protocol-level rejection of received data: never success, and the remote outcome stays unknown."""
        return BackendError(
            api_failure(
                ErrorCategory.PROTOCOL,
                code,
                "Received output was rejected.",
                request.request_id,
                stage,
                RemoteOutcome.NOT_STARTED if stage == SubmissionStage.NOT_SUBMITTED else RemoteOutcome.UNKNOWN,
                terminal_status=terminal,
                partial_artifacts=self._partials(finals),
                diagnostics=diagnostics(phase="generation", reason=code),
                operation="GenerateImage",
                transport="grpc",
            )
        )

    @staticmethod
    def _signpost(signpost, request: ResolvedGenerationRequest, emit) -> None:
        name, value = betterproto.which_one_of(signpost, "signpost")
        if name == "sampling":
            emit("progress", stage="sampling", completed=int(value.step), total=request.steps)
        elif name:
            emit("progress", stage=name)

    async def aclose(self) -> None:
        """Stop admission, refuse to close under an active generation, then close the channel."""
        self._closing = True
        if self._active:
            raise RuntimeError("Cancel and await owned generations before closing the backend.")
        if self._channel is not None:
            channel, self._channel = self._channel, None
            channel.close()
        self._closed = True

    # -- internals -----------------------------------------------------------------------------------------------

    def _with_staleness(self, snapshot: InventorySnapshot) -> InventorySnapshot:
        refreshed = replace(snapshot, refreshed=False)
        if self._stale_reason is None:
            return refreshed
        return replace(
            refreshed, diagnostics=(*refreshed.diagnostics, ("stale", "true"), ("stale_reason", self._stale_reason))
        )

    def _require_channel(self) -> Channel:
        if self._closing or self._closed or self._channel is None:
            raise error(ErrorCategory.CONNECTIVITY, "backend_closed", "Backend is closed or not open.")
        return self._channel

    async def _generation_metadata(self, message):
        """Authorize the exact wire message before any generation submission becomes possible."""
        return None

    async def _secret(self) -> str | None:
        if self._shared_secret is None:
            return None
        credential = await self._shared_secret.get()
        return credential.secret if credential is not None else None

    async def _connect(self) -> None:
        """Establish transport (including TLS) so failures here are provably before any request bytes."""
        channel = self._require_channel()
        began = time.monotonic()
        try:
            async with asyncio.timeout(self.timeouts.connect):
                await channel.__connect__()
        except Exception as exc:
            raise BackendError(
                from_exception(
                    exc,
                    phase="connect",
                    operation="connect",
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
            ) from None

    async def _echo(self):
        began = time.monotonic()
        secret = await self._secret()
        request = image_service.EchoRequest(name=CLIENT_NAME)
        if secret:
            request.shared_secret = secret
        stub = image_service.ImageGenerationServiceStub(self._require_channel())
        try:
            reply = await stub.echo(request, timeout=self.timeouts.inventory)
        except Exception as exc:
            raise BackendError(
                from_exception(
                    exc,
                    phase="inventory",
                    operation="Echo",
                    private=(secret or "",),
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
            ) from None
        diagnostic_event(
            log,
            "backend.api_completed",
            level=logging.DEBUG,
            data={
                "transport": "grpc",
                "operation": "Echo",
                "status": "OK",
                "elapsed_ms": (time.monotonic() - began) * 1000,
            },
        )
        self._remember_specs(reply)
        return reply, bool(secret)

    def _remember_specs(self, reply) -> None:
        """Keep the server's own model descriptions (Echo ``override.models``) keyed by file name."""
        override = reply.override
        try:
            items = json.loads(override.models) if override is not None and override.models else []
        except ValueError:
            return
        if not isinstance(items, list):
            return
        self._model_specs = {
            item["file"]: item for item in items if isinstance(item, dict) and isinstance(item.get("file"), str)
        }
        diagnostic_event(
            log,
            "backend.model_descriptions_observed",
            level=logging.DEBUG,
            data={"model_count": len(self._model_specs)},
        )

    async def _model_spec(self, model_id: str) -> dict | None:
        """The model's description for DT+ cloud, so the server runs it with its full specification.

        DT+ resolves most checkpoints from its public model list; newer ones (e.g. FLUX.1 dev) are described only by the
        client's ``override.models``; when the server does not advertise one either, the bundled community description is
        used. Local servers already know their own files, so nothing is sent there.
        """
        if self.backend_id != "drawthings-cloud":
            return None
        if model_id not in self._model_specs:
            try:
                await self._echo()
            except BackendError:
                return None
        return self._model_specs.get(model_id) or fallback_spec(model_id)

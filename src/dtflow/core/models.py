"""Backend-neutral immutable contracts; request expressibility is not backend acceptance."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from dtflow.core.image_inputs import ImageInputSnapshot


class SubmissionStage(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    POSSIBLY_SUBMITTED = "possibly_submitted"
    TERMINAL_OBSERVED = "terminal_observed"


class RemoteOutcome(StrEnum):
    NOT_STARTED = "not_started"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED_CONFIRMED = "cancelled_confirmed"


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    CREDENTIALS = "credentials"
    AUTHENTICATION = "authentication"
    CONNECTIVITY = "connectivity"
    TIMEOUT = "timeout"
    REMOTE_REJECTION = "remote_rejection"
    PROTOCOL = "protocol"
    DECODING = "decoding"
    RESOURCE_LIMIT = "resource_limit"
    CANCELLATION = "cancellation"
    PERSISTENCE = "persistence"


class Sampler(StrEnum):
    """Backend-neutral sampler ids; the first two predate the others and keep their saved spelling."""

    EULER_A = "euler_a"
    DPMPP_2M_KARRAS = "dpmpp_2m_karras"
    DDIM = "ddim"
    PLMS = "plms"
    DPMPP_SDE_KARRAS = "dpmpp_sde_karras"
    UNIPC = "unipc"
    LCM = "lcm"
    EULER_A_SUBSTEP = "euler_a_substep"
    DPMPP_SDE_SUBSTEP = "dpmpp_sde_substep"
    TCD = "tcd"
    EULER_A_TRAILING = "euler_a_trailing"
    DPMPP_SDE_TRAILING = "dpmpp_sde_trailing"
    DPMPP_2M_AYS = "dpmpp_2m_ays"
    EULER_A_AYS = "euler_a_ays"
    DPMPP_SDE_AYS = "dpmpp_sde_ays"
    DPMPP_2M_TRAILING = "dpmpp_2m_trailing"
    DDIM_TRAILING = "ddim_trailing"
    UNIPC_TRAILING = "unipc_trailing"
    UNIPC_AYS = "unipc_ays"
    TCD_TRAILING = "tcd_trailing"


class Designation(StrEnum):
    FINAL = "final"
    PREVIEW = "preview"
    PARTIAL = "partial"


class ValidationError(ValueError):
    """Invalid application input; no remote submission is implied."""


Metadata = tuple[tuple[str, str], ...]
MAX_QUEUE_ITEMS = 1000  # one queue's item ceiling; the planner checks it before enumerating
MAX_QUEUE_CONCURRENCY = 10  # configurable client policy; provider limits still apply
SEED_LIMIT = 4294967295


def validate_metadata(value: Metadata) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(x, str) for x in item) for item in value
    ):
        raise ValidationError("Metadata must be an immutable tuple of string pairs.")


def integer(value: int, low: int, high: int, field_name: str) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{field_name} must be an integer in {low}..{high}.")


@dataclass(frozen=True, slots=True)
class LoRASelection:
    identifier: str
    weight: float

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValidationError("LoRA identifier is required.")
        if type(self.weight) not in (int, float) or not math.isfinite(self.weight):
            raise ValidationError("LoRA weight must be finite.")


ASSET_KINDS = ("lora",)
PREPARATION_STATES = ("intent", "transferring", "confirmed", "unknown", "failed")
RECONCILIATION_STATUSES = ("confirmed", "upload_required", "unavailable", "unknown")


def _asset_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise ValidationError(f"{name} is required.")


def _asset_reference(value: object, name: str) -> None:
    _asset_text(value, name)
    # A signed URL is a temporary credential; it can never be an identifier that is stored or reported.
    if "://" in value or "?" in value or any(char.isspace() for char in value):
        raise ValidationError(f"{name} must be an identifier, not a URL or credential.")


def _asset_digest(value: object) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValidationError("Asset SHA-256 must be 64 lowercase hexadecimal characters.")


def _asset_size(value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValidationError("Asset size must be a positive integer.")


@dataclass(frozen=True, slots=True)
class AssetContent:
    """Local content identity of one converted file: what was measured, never what a server said."""

    kind: str
    identifier: str
    sha256: str
    size: int
    provenance: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ASSET_KINDS:
            raise ValidationError("Unsupported asset kind.")
        _asset_reference(self.identifier, "Asset identifier")
        _asset_digest(self.sha256)
        _asset_size(self.size)
        if not isinstance(self.provenance, str):
            raise ValidationError("Asset provenance must be text.")


@dataclass(frozen=True, slots=True)
class PreparedAsset:
    """A server-acknowledged reference to uploaded content, scoped to one backend origin and tied to a local receipt."""

    backend_id: str
    origin: str
    identifier: str
    remote_id: str
    sha256: str
    size: int
    receipt_id: str
    confirmed_at_utc: str

    def __post_init__(self) -> None:
        for name in ("backend_id", "origin", "receipt_id", "confirmed_at_utc"):
            _asset_text(getattr(self, name), name)
        _asset_reference(self.identifier, "Asset identifier")
        _asset_reference(self.remote_id, "Remote asset identifier")
        _asset_digest(self.sha256)
        _asset_size(self.size)


@dataclass(frozen=True, slots=True)
class AssetNegotiation:
    """Current storage state for one content identity; ``ticket`` is an opaque adapter-private upload handle."""

    status: str  # "exists" or "upload_required"
    reference: str | None = None
    ticket: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.status == "exists":
            _asset_reference(self.reference, "Remote asset identifier")
        elif self.status != "upload_required" or self.reference is not None:
            raise ValidationError("Unsupported asset negotiation.")


@dataclass(frozen=True, slots=True)
class AssetReconciliation:
    """Outcome of a fresh metadata-only check; ``unavailable`` means the scope cannot answer, not that it is absent."""

    status: str
    evidence: str
    reference: str | None = None

    def __post_init__(self) -> None:
        if self.status not in RECONCILIATION_STATUSES:
            raise ValidationError("Unsupported reconciliation status.")
        _asset_text(self.evidence, "Reconciliation evidence")


@dataclass(frozen=True, slots=True)
class ResolvedGenerationRequest:
    request_id: str
    prompt: str
    model_id: str
    seed: int
    negative_prompt: str = ""
    width_px: int = 128
    height_px: int = 128
    steps: int = 4
    guidance: float = 1.0
    sampler_id: Sampler = Sampler.EULER_A
    operation: str = "txt2img"
    batch_size: int = 1
    batch_count: int = 1
    loras: tuple[LoRASelection, ...] = ()
    resolution_evidence: Metadata = ()
    prepared_assets: tuple[PreparedAsset, ...] = ()
    allow_unlisted_model: bool = False
    schema_version: int = 2
    strength: float = 1.0
    image_inputs: ImageInputSnapshot | None = None

    def __post_init__(self) -> None:
        for name in ("request_id", "model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"{name} is required.")
        if not isinstance(self.prompt, str) or not isinstance(self.negative_prompt, str):
            raise ValidationError("Prompts must be strings.")
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValidationError("Unsupported resolved-request schema.")
        if self.operation not in ("txt2img", "img2img", "inpaint"):
            raise ValidationError("Unsupported image generation operation.")
        if type(self.strength) not in (int, float) or not math.isfinite(self.strength) or not 0 <= self.strength <= 1:
            raise ValidationError("strength must be finite in 0..1.")
        if self.operation == "txt2img":
            if self.image_inputs is not None or self.strength != 1:
                raise ValidationError("Text-to-image must have no image inputs and strength 1.")
        else:
            # Runtime import avoids the content-record -> ValidationError import cycle.
            from dtflow.core.image_inputs import ImageInputSnapshot

            if not isinstance(self.image_inputs, ImageInputSnapshot):
                raise ValidationError("Image operations require immutable managed input references.")
            if (self.batch_size, self.batch_count) != (1, 1):
                raise ValidationError("Image operations support one output per submission.")
            if (self.width_px, self.height_px) != (
                self.image_inputs.fit.target_width,
                self.image_inputs.fit.target_height,
            ):
                raise ValidationError("Image-operation dimensions must match prepared input fitting.")
            if (self.operation == "inpaint") != (self.image_inputs.mask is not None):
                raise ValidationError("Inpainting requires masks; img2img must not contain masks.")
        integer(self.seed, 0, SEED_LIMIT, "seed")
        for name in ("width_px", "height_px"):
            value = getattr(self, name)
            integer(value, 128, 2048, name)
            if value % 64:
                raise ValidationError(f"{name} must be a multiple of 64.")
        integer(self.steps, 1, 150, "steps")
        if type(self.guidance) not in (int, float) or not math.isfinite(self.guidance) or not 0 <= self.guidance <= 50:
            raise ValidationError("guidance must be finite in 0..50.")
        if not isinstance(self.sampler_id, Sampler):
            raise ValidationError("Unsupported sampler.")
        integer(self.batch_size, 1, 4, "batch_size")
        integer(self.batch_count, 1, 4, "batch_count")
        if self.batch_size * self.batch_count > 4:
            raise ValidationError("At most four outputs may be requested.")
        if not isinstance(self.loras, tuple) or any(not isinstance(x, LoRASelection) for x in self.loras):
            raise ValidationError("LoRAs must be an immutable tuple.")
        validate_metadata(self.resolution_evidence)
        if not isinstance(self.prepared_assets, tuple) or any(
            not isinstance(x, PreparedAsset) for x in self.prepared_assets
        ):
            raise ValidationError("Prepared assets must be an immutable tuple.")
        selected = {item.identifier for item in self.loras}
        if len({x.identifier for x in self.prepared_assets}) != len(self.prepared_assets) or any(
            x.identifier not in selected for x in self.prepared_assets
        ):
            raise ValidationError("Each prepared asset must match exactly one selected LoRA.")


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    final_count: int = 4
    artifact_bytes: int = 32 * 1024 * 1024
    final_bytes: int = 64 * 1024 * 1024
    transport_bytes: int = 64 * 1024 * 1024
    tensor_bytes: int = 64 * 1024 * 1024
    preview_bytes: int = 2 * 1024 * 1024
    buffer_budget: int = 320 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            integer(getattr(self, name), 1, 2**31, name)


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    connect: float = 10.0
    inventory: float = 10.0
    http: float = 30.0
    total: float = 600.0
    cleanup: float = 5.0
    idle: float | None = None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if value is None and name == "idle":
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValidationError(f"{name} timeout must be positive and finite.")


@dataclass(frozen=True, slots=True)
class CancellationEvidence:
    requested: bool = False
    local_stopped: bool = False
    remote_confirmed: bool = False
    outcome_unknown: bool = False


@dataclass(frozen=True, slots=True)
class BackendFailure:
    category: ErrorCategory
    code: str
    message: str
    request_id: str | None = None
    submission_stage: SubmissionStage = SubmissionStage.NOT_SUBMITTED
    remote_outcome: RemoteOutcome = RemoteOutcome.NOT_STARTED
    terminal_status: str | None = None
    timeout_phase: str | None = None
    diagnostics: Metadata = ()
    partial_artifacts: tuple[ImageArtifact, ...] = ()
    cancellation: CancellationEvidence = field(default_factory=CancellationEvidence)

    def __post_init__(self) -> None:
        validate_metadata(self.diagnostics)
        if not isinstance(self.partial_artifacts, tuple):
            raise ValidationError("Partial artifacts must be immutable.")


class BackendError(Exception):
    """Transport-independent failure with independent submission evidence."""

    def __init__(self, failure: BackendFailure):
        super().__init__(failure.message)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    backend_id: str
    endpoint: str
    checked_at_utc: str
    transport: str
    authentication: str
    inventory: str
    diagnostics: Metadata = ()

    def __post_init__(self) -> None:
        validate_metadata(self.diagnostics)


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    identifier: str
    kind: str
    display_name: str
    architecture: str | None
    availability: str
    metadata_source: str
    source_revision: str | None = None
    trigger_prefix: str | None = None
    default_weight: float | None = None


@dataclass(frozen=True, slots=True)
class FileIdentity:
    identifier: str
    exists: bool
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValidationError("File identity requires an identifier.")
        if self.sha256 is not None:
            digest = self.sha256.lower()
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValidationError("File SHA-256 must be 64 hexadecimal characters.")
            object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True, slots=True)
class LoRABackendAvailability:
    availability: str
    evidence_source: str
    sha256: str | None = None
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class LoRAInventoryEntry:
    identifier: str
    display_name: str
    architecture: str | None
    trigger_prefix: str | None
    default_weight: float | None
    local_grpc: LoRABackendAvailability
    dt_cloud: LoRABackendAvailability


@dataclass(frozen=True, slots=True)
class InventorySource:
    backend_id: str
    server_identity: str
    endpoint: str | None
    observed_at_utc: str
    refreshed: bool
    diagnostics: Metadata = ()

    def __post_init__(self) -> None:
        validate_metadata(self.diagnostics)


@dataclass(frozen=True, slots=True)
class LoRAInventorySnapshot:
    resolved_at_utc: str
    entries: tuple[LoRAInventoryEntry, ...]
    local_source: InventorySource
    cloud_source: InventorySource

    def __post_init__(self) -> None:
        if not isinstance(self.local_source, InventorySource) or not isinstance(self.cloud_source, InventorySource):
            raise ValidationError("LoRA inventory sources must be immutable typed values.")
        if not isinstance(self.entries, tuple) or any(not isinstance(x, LoRAInventoryEntry) for x in self.entries):
            raise ValidationError("LoRA inventory entries must be immutable typed values.")


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    backend_id: str
    server_identity: str
    observed_at_utc: str
    refreshed: bool
    entries: tuple[InventoryEntry, ...]
    diagnostics: Metadata = ()
    endpoint: str | None = None

    def __post_init__(self) -> None:
        validate_metadata(self.diagnostics)
        if not isinstance(self.entries, tuple) or any(not isinstance(x, InventoryEntry) for x in self.entries):
            raise ValidationError("Inventory entries must be immutable typed values.")


def eligible_model_ids(snapshot: InventorySnapshot) -> tuple[str, ...]:
    """Identifiers accepted as base models, shared by the request gate and the desktop model list.

    Unclassified files qualify only when the server supplied no model metadata at all; otherwise a classified
    inventory excludes LoRAs, controls and other files.
    """
    metadata = dict(snapshot.diagnostics).get("model_metadata") == "present"
    return tuple(
        entry.identifier
        for entry in snapshot.entries
        if entry.kind == "model" or (entry.kind == "unknown" and not metadata)
    )


@dataclass(frozen=True, slots=True)
class PreflightDiagnostic:
    identifier: str
    architecture_match: str
    availability: str
    generation_evidence: str
    code: str
    source: str


@dataclass(frozen=True, slots=True)
class Capabilities:
    backend_id: str
    operations: tuple[str, ...] = ("txt2img",)
    samplers: tuple[Sampler, ...] = tuple(Sampler)
    model_ids: tuple[str, ...] = ()
    max_concurrency: int = 1
    batch_size: int = 1
    batch_count: int = 1
    lora_support: str = "unsupported"
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    unlisted_model_bridge: bool = False


@dataclass(frozen=True, slots=True)
class ImageArtifact:
    artifact_id: str
    designation: Designation
    media_type: str
    encoded: bytes = field(repr=False)
    width_px: int
    height_px: int
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        validate_metadata(self.metadata)
        if not isinstance(self.encoded, bytes):
            raise ValidationError("Encoded artifacts must be immutable bytes.")
        if not isinstance(self.designation, Designation):
            raise ValidationError("Artifact designation must be explicit.")


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    terminal_status: str
    required_outputs_valid: bool


@dataclass(frozen=True, slots=True)
class GenerationResult:
    request_id: str
    backend_id: str
    server_identity: str
    artifacts: tuple[ImageArtifact, ...]
    effective_configuration: Metadata
    completion: CompletionEvidence
    warnings: tuple[str, ...] = ()
    backend_elapsed_seconds: float | None = None

    def __post_init__(self) -> None:
        validate_metadata(self.effective_configuration)
        if not isinstance(self.artifacts, tuple) or any(not isinstance(x, ImageArtifact) for x in self.artifacts):
            raise ValidationError("Result artifacts must be immutable typed values.")
        if not isinstance(self.warnings, tuple) or any(not isinstance(x, str) for x in self.warnings):
            raise ValidationError("Warnings must be immutable strings.")
        if self.backend_elapsed_seconds is not None and (
            type(self.backend_elapsed_seconds) not in (int, float)
            or not math.isfinite(self.backend_elapsed_seconds)
            or self.backend_elapsed_seconds < 0
        ):
            raise ValidationError("Backend elapsed time must be finite and nonnegative.")


@dataclass(frozen=True, slots=True)
class BackendEvent:
    request_id: str
    sequence: int
    kind: str
    timestamp_utc: str
    stage: str | None = None
    completed: int | None = None
    total: int | None = None
    artifact: ImageArtifact | None = None
    failure: BackendFailure | None = None


EventSink = Callable[[BackendEvent], None]


class Backend(Protocol):
    @property
    def capabilities(self) -> Capabilities: ...
    async def open(self) -> None: ...
    async def check_connection(self) -> ConnectionStatus: ...
    async def get_inventory(self, refresh: bool = False) -> InventorySnapshot: ...
    async def generate(self, request: ResolvedGenerationRequest, *, on_event: EventSink) -> GenerationResult: ...
    async def aclose(self) -> None: ...


class AssetBackend(Protocol):
    """Storage-side asset operations; deliberately separate from generation and with no list or delete."""

    @property
    def backend_id(self) -> str: ...
    @property
    def origin(self) -> str: ...
    @property
    def scope(self) -> str: ...
    async def negotiate_asset(self, content: AssetContent) -> AssetNegotiation: ...
    async def upload_asset(self, path: Path, content: AssetContent, negotiation: AssetNegotiation) -> None: ...


@dataclass(frozen=True, slots=True)
class Credential:
    source: str
    secret: str = field(repr=False)
    alias: str | None = None  # Explicit saved name; never the generated display fallback.


class CredentialProvider(Protocol):
    async def get(self) -> Credential | None: ...


@dataclass(frozen=True, slots=True)
class SavedGeneration:
    attempt_id: str
    output_paths: tuple[str, ...]
    metadata_path: str
    backend_result: GenerationResult
    request: ResolvedGenerationRequest | None = None
    display_image: bytes | None = field(default=None, repr=False)

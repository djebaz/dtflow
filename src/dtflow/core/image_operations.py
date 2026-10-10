"""Owner-reviewed image-operation scope; a permit is not automatic compatibility discovery."""

from dataclasses import dataclass

from dtflow.core.models import ValidationError


@dataclass(frozen=True, slots=True)
class ImageOperationPermit:
    """Exact backend/endpoint/checkpoint/operation acceptance or explicit owner probe.

    No permits ship with the application. A caller supplies a reviewed evidence ID;
    probe permits additionally require allow_image_probe=True for that invocation.
    These records contain no credentials, images or private evidence-file paths.
    """

    backend_id: str
    endpoint: str
    model_id: str
    operation: str
    evidence_id: str
    status: str = "owner_verified"
    model_description_sha256: str | None = None

    def __post_init__(self):
        if self.backend_id not in ("drawthings-grpc", "drawthings-cloud"):
            raise ValidationError("Image-operation permits require a real selected backend.")
        if (
            not isinstance(self.endpoint, str) or not self.endpoint.startswith("grpcs://")
            or not self.endpoint.removeprefix("grpcs://") or any(c in self.endpoint for c in "@?#\r\n")
        ):
            raise ValidationError("Image-operation permit requires a credential-free endpoint identity.")
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValidationError("Image-operation permit requires an exact checkpoint identifier.")
        if self.operation not in ("img2img", "inpaint"):
            raise ValidationError("Image-operation permit requires img2img or inpaint.")
        if self.status not in ("owner_verified", "owner_probe"):
            raise ValidationError("Image-operation permit status is unsupported.")
        if (
            not isinstance(self.evidence_id, str) or not 1 <= len(self.evidence_id) <= 128
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in self.evidence_id)
        ):
            raise ValidationError("Permit evidence ID must be a path-free identifier of at most 128 characters.")
        digest = self.model_description_sha256
        if digest is not None and (
            not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValidationError("Permit model-description digest must be SHA-256.")
        if self.backend_id == "drawthings-cloud" and digest is None:
            raise ValidationError("Cloud image-operation permits require an exact model-description digest.")

    def validate_scope(self, backend_id: str, endpoint: str, model_id: str, operation: str, *, allow_probe: bool = False):
        if type(allow_probe) is not bool:
            raise ValidationError("Owner-probe authorization must be boolean.")
        if (self.backend_id, self.endpoint, self.model_id, self.operation) != (backend_id, endpoint, model_id, operation):
            raise ValidationError("Image-operation permit does not match the selected exact scope.")
        if self.status == "owner_probe" and not allow_probe:
            raise ValidationError("Owner-probe image generation requires explicit per-invocation authorization.")

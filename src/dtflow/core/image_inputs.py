"""Immutable backend-neutral image-preparation evidence; no provider or image-library types."""

import math
from dataclasses import dataclass, field

from dtflow.core.models import ValidationError

IMAGE_PIXEL_LIMIT = 16_000_000
IMAGE_BYTE_LIMIT = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImageContent:
    """Canonical PNG content snapshot, measured before any provider serialization.

    Encoded content is kept outside the durable request JSON. A later submission slice
    persists it and puts only its content reference in the resolved request.
    """

    encoded: bytes = field(repr=False)
    sha256: str
    width: int
    height: int
    channels: int

    def __post_init__(self):
        if not isinstance(self.encoded, bytes) or not 0 < len(self.encoded) <= IMAGE_BYTE_LIMIT:
            raise ValidationError("Image content is empty or exceeds 64 MiB.")
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or self.width < 1
            or self.height < 1
            or self.width * self.height > IMAGE_PIXEL_LIMIT
        ):
            raise ValidationError("Image content dimensions exceed the 16 million pixel limit.")
        if type(self.channels) is not int or self.channels not in (1, 3):
            raise ValidationError("Prepared image content must be grayscale or RGB.")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.sha256)
        ):
            raise ValidationError("Image content needs a SHA-256 digest.")


@dataclass(frozen=True, slots=True)
class ImageFit:
    """Source -> generation-target mapping in oriented pixels; right/bottom exclusive."""

    source_width: int
    source_height: int
    target_width: int
    target_height: int
    left: int
    top: int
    fitted_width: int
    fitted_height: int

    def __post_init__(self):
        values = (
            self.source_width,
            self.source_height,
            self.target_width,
            self.target_height,
            self.fitted_width,
            self.fitted_height,
        )
        if any(type(x) is not int or x < 1 for x in values):
            raise ValidationError("Image fitting dimensions must be positive integers.")
        if type(self.left) is not int or type(self.top) is not int or self.left < 0 or self.top < 0:
            raise ValidationError("Image fitting offsets must be nonnegative integers.")
        if self.left + self.fitted_width > self.target_width or self.top + self.fitted_height > self.target_height:
            raise ValidationError("Fitted image exceeds the generation target.")

    @property
    def crop_box(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.left + self.fitted_width, self.top + self.fitted_height


@dataclass(frozen=True, slots=True)
class PreparedImageInputs:
    """Immutable original-grid and fitted-grid evidence; not a generation request."""

    source: ImageContent
    image: ImageContent
    fit: ImageFit
    source_mask: ImageContent | None = None
    mask: ImageContent | None = None
    blend_mask: ImageContent | None = None
    grow: int = 0
    feather: float = 0.0
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValidationError("Unsupported image-preparation schema.")
        if (self.source.width, self.source.height) != (self.fit.source_width, self.fit.source_height):
            raise ValidationError("Prepared source does not match fitting evidence.")
        if (self.image.width, self.image.height) != (self.fit.target_width, self.fit.target_height):
            raise ValidationError("Prepared image does not match generation target.")
        if self.source.channels != 3 or self.image.channels != 3:
            raise ValidationError("Prepared source and image must be RGB.")
        masks = (self.source_mask, self.mask, self.blend_mask)
        if any(x is None for x in masks) and not all(x is None for x in masks):
            raise ValidationError("Prepared inpainting needs all three mask snapshots.")
        for content, size in (
            (self.source_mask, (self.source.width, self.source.height)),
            (self.blend_mask, (self.source.width, self.source.height)),
            (self.mask, (self.image.width, self.image.height)),
        ):
            if content is not None and (content.channels != 1 or (content.width, content.height) != size):
                raise ValidationError("Prepared mask dimensions/channels do not match its grid.")
        if type(self.grow) is not int or not -64 <= self.grow <= 64:
            raise ValidationError("Mask grow must be an integer in -64..64 source pixels.")
        if type(self.feather) not in (int, float) or not math.isfinite(self.feather) or not 0 <= self.feather <= 64:
            raise ValidationError("Mask feather must be finite in 0..64 source pixels.")
        if self.mask is None and (self.grow or self.feather):
            raise ValidationError("Mask adjustments require prepared masks.")

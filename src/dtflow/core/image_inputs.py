"""Immutable backend-neutral image-preparation evidence; no provider or image-library types."""

import math
from dataclasses import dataclass, field

from dtflow.core.models import ValidationError

IMAGE_PIXEL_LIMIT = 16_000_000
IMAGE_BYTE_LIMIT = 64 * 1024 * 1024
INPUT_SET_BYTE_LIMIT = 128 * 1024 * 1024
INPUT_NAMES = ("source", "image", "source_mask", "mask", "blend_mask")


@dataclass(frozen=True, slots=True)
class ImageContent:
    """Canonical PNG content snapshot, measured before any provider serialization.

    Encoded content is kept outside durable reference JSON. Snapshot helpers persist
    it separately; resolved-request/submission integration remains a later slice.
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


@dataclass(frozen=True, slots=True)
class ImageReference:
    """Content identity inside an attempt-owned input set; never an external path."""

    sha256: str
    byte_count: int
    width: int
    height: int
    channels: int

    def __post_init__(self):
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.sha256)
        ):
            raise ValidationError("Image reference needs a SHA-256 digest.")
        if type(self.byte_count) is not int or not 0 < self.byte_count <= IMAGE_BYTE_LIMIT:
            raise ValidationError("Image reference is empty or exceeds 64 MiB.")
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or self.width < 1
            or self.height < 1
            or self.width * self.height > IMAGE_PIXEL_LIMIT
        ):
            raise ValidationError("Image reference dimensions exceed the pixel limit.")
        if type(self.channels) is not int or self.channels not in (1, 3):
            raise ValidationError("Image reference must be grayscale or RGB.")

    @classmethod
    def from_content(cls, content: ImageContent) -> "ImageReference":
        return cls(content.sha256, len(content.encoded), content.width, content.height, content.channels)


@dataclass(frozen=True, slots=True)
class ImageInputSnapshot:
    """JSON-safe durable input references, independent of the execution DB schema.

    An attempt ID selects the owned directory. Names are fixed by INPUT_NAMES, not
    supplied by a request or manifest. Encoded bytes and private paths are absent.
    """

    attempt_id: str
    source: ImageReference
    image: ImageReference
    fit: ImageFit
    source_mask: ImageReference | None = None
    mask: ImageReference | None = None
    blend_mask: ImageReference | None = None
    grow: int = 0
    feather: float = 0.0
    preserve_outside: bool = True
    schema_version: int = 1

    def __post_init__(self):
        if (
            not isinstance(self.attempt_id, str)
            or len(self.attempt_id) != 32
            or any(c not in "0123456789abcdef" for c in self.attempt_id)
        ):
            raise ValidationError("Input snapshot requires a lowercase 32-character attempt ID.")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValidationError("Unsupported input-snapshot schema.")
        if type(self.preserve_outside) is not bool:
            raise ValidationError("Preserve outside mask must be boolean.")
        if not isinstance(self.fit, ImageFit):
            raise ValidationError("Input snapshot requires fitting evidence.")
        for name in INPUT_NAMES:
            item = getattr(self, name)
            if (name in ("source", "image") or item is not None) and not isinstance(item, ImageReference):
                raise ValidationError("Input snapshot requires immutable content references.")
        if sum(getattr(self, name).byte_count for name in INPUT_NAMES if getattr(self, name) is not None) > INPUT_SET_BYTE_LIMIT:
            raise ValidationError("Prepared input set exceeds 128 MiB.")
        # Apply the preparation record's grid/options invariants without creating
        # placeholder byte payloads or depending on an image library in core.
        if self.source.channels != 3 or self.image.channels != 3:
            raise ValidationError("Input source and fitted image must be RGB.")
        if (self.source.width, self.source.height) != (self.fit.source_width, self.fit.source_height):
            raise ValidationError("Input source does not match fitting evidence.")
        if (self.image.width, self.image.height) != (self.fit.target_width, self.fit.target_height):
            raise ValidationError("Input image does not match generation target.")
        masks = (self.source_mask, self.mask, self.blend_mask)
        if any(x is None for x in masks) and not all(x is None for x in masks):
            raise ValidationError("Input snapshots require all three masks together.")
        for item, size in (
            (self.source_mask, (self.source.width, self.source.height)),
            (self.blend_mask, (self.source.width, self.source.height)),
            (self.mask, (self.image.width, self.image.height)),
        ):
            if item is not None and (item.channels != 1 or (item.width, item.height) != size):
                raise ValidationError("Input mask does not match its recorded grid.")
        if type(self.grow) is not int or not -64 <= self.grow <= 64:
            raise ValidationError("Mask grow must be an integer in -64..64 source pixels.")
        if type(self.feather) not in (int, float) or not math.isfinite(self.feather) or not 0 <= self.feather <= 64:
            raise ValidationError("Mask feather must be finite in 0..64 source pixels.")
        if self.mask is None and (self.grow or self.feather or self.preserve_outside):
            raise ValidationError("Mask adjustments/compositing require inpainting masks.")

    @classmethod
    def from_prepared(
        cls, attempt_id: str, prepared: PreparedImageInputs, *, preserve_outside: bool = True
    ) -> "ImageInputSnapshot":
        if not isinstance(prepared, PreparedImageInputs):
            raise ValidationError("Image preparation evidence is required.")
        if type(preserve_outside) is not bool:
            raise ValidationError("Preserve outside mask must be boolean.")
        references = {
            name: ImageReference.from_content(getattr(prepared, name)) if getattr(prepared, name) is not None else None
            for name in INPUT_NAMES
        }
        return cls(
            attempt_id, **references, fit=prepared.fit, grow=prepared.grow, feather=prepared.feather,
            preserve_outside=preserve_outside if prepared.mask is not None else False,
        )

    @classmethod
    def from_record(cls, record: dict) -> "ImageInputSnapshot":
        """Strict nested decoding: no ignored fields, schema coercion or paths."""
        try:
            if not isinstance(record, dict) or set(record) != set(cls.__dataclass_fields__):
                raise ValidationError("Input snapshot fields/schema must be explicit.")
            values = dict(record)
            values["fit"] = ImageFit(**values["fit"])
            for name in INPUT_NAMES:
                if values.get(name) is not None:
                    values[name] = ImageReference(**values[name])
            return cls(**values)
        except (TypeError, ValueError, KeyError) as exc:
            raise ValidationError("Malformed input-snapshot record.") from exc

"""Worker-only source/mask tensor encoding, not generation dispatch.

The upstream server unwraps 32-byte SHA-256 references against request.contents.
PNG input identities differ from wire tensor identities. Encoding must precede
cloud authorization; this helper neither authorizes nor sends a request.
"""

import hashlib
import struct
from dataclasses import dataclass, field

from drawthings_py.image.image_buffer import ImageBuffer

from dtflow.core.image_inputs import PreparedImageInputs
from dtflow.core.models import ResourceLimits, ValidationError
from dtflow.services.image_preparation import image_content_pixels, validate_prepared_inputs

HEADER_BYTES = 68


@dataclass(frozen=True, slots=True)
class EncodedImageInputs:
    """Provider-local immutable fields for a future operation-specific request."""

    image: bytes = field(repr=False)
    mask: bytes | None = field(repr=False)
    contents: tuple[bytes, ...] = field(repr=False)
    width: int
    height: int

    @property
    def byte_count(self) -> int:
        return sum(map(len, self.contents))


def _check_tensor(tensor: bytes, width: int, height: int, *, mask: bool) -> None:
    """Check the SDK's two distinct CCV layouts, dtype and exact uncompressed size."""
    expected_header = (
        (0, 1, 2, 0x01000, 0, height, width, 0, 0)
        if mask else (0, 1, 2, 0x20000, 0, 1, height, width, 3)
    )
    expected_size = HEADER_BYTES + width * height * (1 if mask else 6)
    if (
        len(tensor) != expected_size
        or struct.unpack_from("<9I", tensor) != expected_header
        or any(tensor[36:HEADER_BYTES])
    ):
        raise ValidationError("Encoded input tensor does not match the required CCV layout.")


def encode_image_inputs(
    prepared: PreparedImageInputs, limits: ResourceLimits = ResourceLimits()
) -> EncodedImageInputs:
    """Encode fitted RGB float16 [-1,1] plus optional uint8 0/2 mask.

    White/edit pixels become provider value 2 (selected at requested strength),
    never 1 (forced pure-noise fill). Black/padding stays 0. No feather/blur/outset
    is hidden in the provider mask; inward blend alpha remains local evidence.
    Call on a worker. This does not establish backend/checkpoint compatibility.
    """
    if not isinstance(prepared, PreparedImageInputs):
        raise ValidationError("Image preparation evidence is required.")
    width, height = prepared.fit.target_width, prepared.fit.target_height
    image_size = HEADER_BYTES + width * height * 6
    mask_size = HEADER_BYTES + width * height if prepared.mask is not None else 0
    if image_size > limits.tensor_bytes or mask_size > limits.tensor_bytes:
        raise ValidationError("Image input tensor exceeds the decoded byte limit.")
    if image_size + mask_size > limits.transport_bytes:
        raise ValidationError("Image input contents exceed the transport byte limit.")
    validate_prepared_inputs(prepared)
    image = ImageBuffer(image_content_pixels(prepared.image), width, height, 3).to_tensor()
    _check_tensor(image, width, height, mask=False)
    contents = [image]
    image_reference = hashlib.sha256(image).digest()
    mask_reference = None
    if prepared.mask is not None:
        pixels = image_content_pixels(prepared.mask)
        mask = ImageBuffer(pixels, width, height, 1).to_binary_mask(use_alpha=False, threshold=127)
        _check_tensor(mask, width, height, mask=True)
        # Independently verify every SDK mask byte, not only the header/polarity label.
        table = bytes(2 if value == 255 else 0 for value in range(256))
        if mask[HEADER_BYTES:] != pixels.translate(table):
            raise ValidationError("Encoded mask polarity does not match the prepared binary support.")
        mask_reference = hashlib.sha256(mask).digest()
        contents.append(mask)
    return EncodedImageInputs(image_reference, mask_reference, tuple(contents), width, height)

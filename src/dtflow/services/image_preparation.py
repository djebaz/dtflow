"""Headless, non-generative source/mask preparation and explicit editor-mask I/O.

No provider calls, persistence, metadata copying or source writes. Callers run these
bounded CPU operations on a worker, never on the Qt/orchestration event-loop thread.
"""

import hashlib
import io
import math
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

from dtflow.core.image_inputs import (
    IMAGE_BYTE_LIMIT,
    IMAGE_PIXEL_LIMIT,
    ImageContent,
    ImageFit,
    PreparedImageInputs,
    max_mask_grow,
    validate_source_dimensions,
)
from dtflow.core.models import ValidationError

ALGORITHM = "dtflow-image-preparation-v1"
MASK_THRESHOLD = 127  # strictly greater -> selected; canonical binary output is 0/255


def _binary_mask(mask: Image.Image) -> Image.Image:
    if mask.mode.startswith("I;16") or mask.mode == "I":
        values = mask.convert("I")
        low, high = values.getextrema()
        if low < 0 or high > 65535:
            raise ValidationError("Integer masks must use unsigned 16-bit values in 0..65535.")
        # Full-range scaling, never per-image contrast stretching or Pillow's
        # clipping I->L conversion. I->L point requires a 65536-entry LUT.
        return values.point([255 if value // 257 > MASK_THRESHOLD else 0 for value in range(65536)], "L")
    if mask.mode == "F":
        raise ValidationError("Floating-point masks are unsupported; use an 8-bit or unsigned 16-bit mask.")
    return mask.convert("L").point(lambda value: 255 if value > MASK_THRESHOLD else 0)


def _check_mask_fit(source: Image.Image, target: Image.Image, fit: ImageFit) -> None:
    selected = source.histogram()[255]
    retained = target.histogram()[255]
    # Normalize by the exact fitted grids, not padding or rounded scale factors.
    # This is an area guard, not a connected-component/topology guarantee.
    if retained * fit.source_width * fit.source_height * 4 < selected * fit.fitted_width * fit.fitted_height * 3:
        raise ValidationError(
            "Mask loses over 25% of selected area at generation resolution; increase target size or broaden the selection."
        )


def _adjust_binary(mask: Image.Image, grow: int) -> Image.Image:
    if not grow:
        return mask.copy()
    border = abs(grow)
    if border > max_mask_grow(mask.width, mask.height):
        raise ValidationError("Mask grow/shrink exceeds the bounded pixel-pass budget; reduce grow or source size.")
    padded = ImageOps.expand(mask, border=border, fill=0)
    kernel = ImageFilter.MaxFilter if grow > 0 else ImageFilter.MinFilter
    for _ in range(border):
        padded = padded.filter(kernel(3))
    return padded.crop((border, border, border + mask.width, border + mask.height))


def mask_coverage(pixels: bytes, width: int, height: int) -> float:
    """Exact selected percentage of supplied binary pixels; no PNG decode/resample."""
    validate_source_dimensions(width, height)
    if not isinstance(pixels, bytes) or len(pixels) != width * height:
        raise ValidationError("Mask coverage requires immutable bytes matching the source grid.")
    selected = pixels.count(b"\xff")
    if selected + pixels.count(b"\x00") != len(pixels):
        raise ValidationError("Mask pixels must be binary 0/255.")
    return 100.0 * selected / len(pixels)


@dataclass(frozen=True, slots=True)
class MaskPreview:
    binary: ImageContent
    blend: ImageContent
    coverage_percent: float
    approximate: bool = True


def preview_mask_pixels(
    pixels: bytes,
    width: int,
    height: int,
    *,
    source_width: int,
    source_height: int,
    grow: int = 0,
    feather: float = 0.0,
) -> MaskPreview:
    """Bounded thumbnail-only approximation, not canonical generation evidence.

    Caller supplies an already downsampled binary grid at most 512x512; no source
    decode, RGB fit, durable write or inference. Run on a worker and discard stale
    replies in the editor. Coverage refers to this adjusted thumbnail, not the
    full source or eventual fitted mask. Subpixel grow/strokes may disappear.
    """
    validate_source_dimensions(width, height)
    validate_source_dimensions(source_width, source_height)
    if width > min(512, source_width) or height > min(512, source_height):
        raise ValidationError("Mask preview must be a downsampled grid of at most 512x512.")
    mask_coverage(pixels, width, height)
    if type(grow) is not int or abs(grow) > max_mask_grow(source_width, source_height):
        raise ValidationError("Mask grow exceeds the source-size work budget.")
    if type(feather) not in (int, float) or not math.isfinite(feather) or not 0 <= feather <= 64:
        raise ValidationError("Mask feather must be finite in 0..64 source pixels.")
    scale = min(width / source_width, height / source_height)
    mask = _adjust_binary(Image.frombytes("L", (width, height), pixels), round(grow * scale))
    blend = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(feather * scale))) if feather else mask
    coverage = 100.0 * mask.histogram()[255] / (width * height)
    return MaskPreview(_content(mask), _content(blend), coverage)


def load_mask_bytes(encoded: bytes, source_width: int, source_height: int) -> ImageContent:
    """Load an unadjusted editor mask; grow/feather must not be baked into edits."""
    validate_source_dimensions(source_width, source_height)
    image = _decode(encoded, "Mask image")
    if image.size != (source_width, source_height):
        raise ValidationError("Mask dimensions must match the EXIF-oriented source image.")
    mask = _binary_mask(image)
    if mask.getbbox() is None:
        raise ValidationError("Inpainting mask is empty after threshold.")
    return _content(mask)


def load_mask_file(path: Path, source_width: int, source_height: int) -> ImageContent:
    """Worker file-read wrapper for the unadjusted editor mask."""
    return load_mask_bytes(_read(path, "Mask image"), source_width, source_height)


def save_mask_file(path: Path, mask: ImageContent) -> None:
    """Worker-only, explicit new-file save of the unadjusted source-grid editor mask.

    No overwrite, automatic parent creation, generation or durable attempt commit.
    Caller must pass the editor's base mask, not prepared.source_mask (which already
    includes grow). Failed writes can leave a partial new file for inspection.
    """
    image = _verified(mask)
    if image.mode != "L" or image.getbbox() is None:
        raise ValidationError("Mask save requires a nonempty source-grid binary mask.")
    mask_coverage(image.tobytes(), image.width, image.height)
    try:
        with Path(path).open("xb") as stream:
            stream.write(mask.encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ValidationError("Mask could not be saved as a new file; existing files are never overwritten.") from exc


def save_mask_pixels_file(path: Path, pixels: bytes, width: int, height: int) -> None:
    """Worker save wrapper for the unadjusted binary editor buffer, not a preview."""
    if mask_coverage(pixels, width, height) == 0:
        raise ValidationError("Mask save requires a nonempty source-grid binary mask.")
    save_mask_file(path, _content(Image.frombytes("L", (width, height), pixels)))


def _read(path: Path, label: str) -> bytes:
    """Bound actual bytes read, not only a racy filesystem size observation."""
    try:
        with Path(path).open("rb") as stream:
            encoded = stream.read(IMAGE_BYTE_LIMIT + 1)
    except OSError as exc:
        raise ValidationError(f"{label} could not be read.") from exc
    if not encoded or len(encoded) > IMAGE_BYTE_LIMIT:
        raise ValidationError(f"{label} is empty or exceeds 64 MiB.")
    return encoded


def _decode(encoded: bytes, label: str) -> Image.Image:
    if not isinstance(encoded, bytes) or not 0 < len(encoded) <= IMAGE_BYTE_LIMIT:
        raise ValidationError(f"{label} is empty or exceeds 64 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(encoded)) as opened:
                if opened.width * opened.height > IMAGE_PIXEL_LIMIT:
                    raise ValidationError(f"{label} exceeds 16 million pixels.")
                if getattr(opened, "n_frames", 1) != 1 and opened.format != "MPO":
                    raise ValidationError(f"{label} must be a single image; animated inputs are unsupported.")
                opened.seek(0)
                return ImageOps.exif_transpose(opened).copy()
    except ValidationError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError(f"{label} could not be decoded safely.") from exc


def _content(image: Image.Image) -> ImageContent:
    """Encode fresh pixels without inherited EXIF, ICC, prompts or arbitrary PNG chunks."""
    clean = Image.new(image.mode, image.size)
    clean.paste(image)
    stream = io.BytesIO()
    clean.save(stream, format="PNG")
    encoded = stream.getvalue()
    return ImageContent(encoded, hashlib.sha256(encoded).hexdigest(), image.width, image.height, len(image.getbands()))


def _verified(content: ImageContent) -> Image.Image:
    if hashlib.sha256(content.encoded).hexdigest() != content.sha256:
        raise ValidationError("Prepared image content digest mismatch.")
    if not content.encoded.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValidationError("Prepared content must be canonical PNG.")
    image = _decode(content.encoded, "Prepared content")
    expected_mode = "RGB" if content.channels == 3 else "L"
    if image.info:
        raise ValidationError("Prepared PNG content must not contain inherited metadata.")
    if image.mode != expected_mode or image.size != (content.width, content.height):
        raise ValidationError("Prepared image content does not match its recorded pixels.")
    return image


def validate_prepared_inputs(prepared: PreparedImageInputs) -> None:
    """Validate pixels as well as immutable record invariants before durable use.

    This worker-only boundary also applies to restored snapshots: hashes alone do
    not establish modes, polarity, fitting, padding or mask support.
    """
    from dtflow.core.image_inputs import INPUT_NAMES, INPUT_SET_BYTE_LIMIT

    if not isinstance(prepared, PreparedImageInputs):
        raise ValidationError("Image preparation evidence is required.")
    if (
        sum(len(getattr(prepared, name).encoded) for name in INPUT_NAMES if getattr(prepared, name) is not None)
        > INPUT_SET_BYTE_LIMIT
    ):
        raise ValidationError("Prepared input set exceeds 128 MiB.")
    source = _verified(prepared.source)
    image = _verified(prepared.image)
    fit = prepared.fit
    if fit != _fit(source.size, image.size):
        raise ValidationError("Prepared fitting evidence does not match the canonical transform.")
    fitted = Image.new("RGB", image.size, "white")
    fitted.paste(source.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.LANCZOS), (fit.left, fit.top))
    if ImageChops.difference(image, fitted).getbbox() is not None:
        raise ValidationError("Prepared image pixels do not match the recorded source/fit.")
    if prepared.mask is None:
        return
    binary = _verified(prepared.source_mask)
    mask = _verified(prepared.mask)
    blend = _verified(prepared.blend_mask)
    for item in (binary, mask):
        if item.getbbox() is None or any(
            count for value, count in enumerate(item.histogram()) if value not in (0, 255)
        ):
            raise ValidationError("Prepared masks must be nonempty binary 0/255.")
    fitted_mask = Image.new("L", image.size, 0)
    fitted_mask.paste(
        binary.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.NEAREST), (fit.left, fit.top)
    )
    if ImageChops.difference(mask, fitted_mask).getbbox() is not None:
        raise ValidationError("Prepared mask pixels do not match the recorded fit/padding.")
    _check_mask_fit(binary, mask, fit)
    wanted_blend = (
        ImageChops.multiply(binary, binary.filter(ImageFilter.GaussianBlur(prepared.feather)))
        if prepared.feather
        else binary
    )
    if ImageChops.difference(blend, wanted_blend).getbbox() is not None:
        raise ValidationError("Prepared blend mask does not match bounded feather evidence.")


def image_content_pixels(content: ImageContent) -> bytes:
    """Verified interleaved RGB or grayscale bytes; no Pillow object crosses services."""
    return _verified(content).tobytes()


def _fit(source: tuple[int, int], target: tuple[int, int]) -> ImageFit:
    width, height = target
    if any(type(x) is not int or not 128 <= x <= 2048 or x % 64 for x in target):
        raise ValidationError("Generation dimensions must be multiples of 64 in 128..2048.")
    scale = min(width / source[0], height / source[1])
    fitted_width = min(width, max(1, round(source[0] * scale)))
    fitted_height = min(height, max(1, round(source[1] * scale)))
    return ImageFit(
        source[0],
        source[1],
        width,
        height,
        (width - fitted_width) // 2,
        (height - fitted_height) // 2,
        fitted_width,
        fitted_height,
    )


def prepare_image_bytes(
    source_bytes: bytes,
    width: int,
    height: int,
    *,
    mask_bytes: bytes | None = None,
    grow: int = 0,
    feather: float = 0.0,
) -> PreparedImageInputs:
    """Snapshot oriented RGB/source mask and jointly fit them without stretching.

    Mask alpha is ignored: luminance strictly >127 selects pixels. Masks must match
    the oriented source grid; no implicit mask resize. Grow/shrink is a square-kernel
    source-pixel adjustment: +/-64 is an absolute bound, additionally restricted
    by the padded-pixel-pass budget for the source dimensions. Feather is Gaussian
    edge softness clipped *inside* the final binary support, not a promise of
    anatomy-aware segmentation.
    """
    if type(grow) is not int or not -64 <= grow <= 64:
        raise ValidationError("Mask grow must be an integer in -64..64 source pixels.")
    if type(feather) not in (int, float) or not math.isfinite(feather) or not 0 <= feather <= 64:
        raise ValidationError("Mask feather must be finite in 0..64 source pixels.")
    if mask_bytes is None and (grow or feather):
        raise ValidationError("Mask adjustments require a mask.")
    oriented = _decode(source_bytes, "Source image").convert("RGBA")
    source = Image.new("RGBA", oriented.size, "white")
    source.alpha_composite(oriented)
    source = source.convert("RGB")
    fit = _fit(source.size, (width, height))
    fitted = Image.new("RGB", (width, height), "white")
    fitted.paste(
        source.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.LANCZOS),
        (fit.left, fit.top),
    )
    if mask_bytes is None:
        return PreparedImageInputs(_content(source), _content(fitted), fit)
    mask = _decode(mask_bytes, "Mask image")
    if mask.size != source.size:
        raise ValidationError("Mask dimensions must match the EXIF-oriented source image.")
    mask = _binary_mask(mask)
    # Radius-one passes match square-radius morphology, with black source edges.
    mask = _adjust_binary(mask, grow)
    if mask.getbbox() is None:
        raise ValidationError("Inpainting mask is empty after threshold/adjustments.")
    blend = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(feather))) if feather else mask.copy()
    fitted_mask = Image.new("L", (width, height), 0)
    fitted_mask.paste(
        mask.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.NEAREST),
        (fit.left, fit.top),
    )
    if fitted_mask.getbbox() is None:
        raise ValidationError("Selected mask disappears at the generation resolution; enlarge the region or target.")
    _check_mask_fit(mask, fitted_mask, fit)
    return PreparedImageInputs(
        _content(source),
        _content(fitted),
        fit,
        _content(mask),
        _content(fitted_mask),
        _content(blend),
        grow,
        float(feather),
    )


def prepare_image_files(
    source: Path,
    width: int,
    height: int,
    *,
    mask: Path | None = None,
    grow: int = 0,
    feather: float = 0.0,
) -> PreparedImageInputs:
    """Read external inputs once; returned snapshots never depend on their mutable paths."""
    return prepare_image_bytes(
        _read(source, "Source image"),
        width,
        height,
        mask_bytes=_read(mask, "Mask image") if mask is not None else None,
        grow=grow,
        feather=feather,
    )


def composite_image(prepared: PreparedImageInputs, generated_png: bytes) -> ImageContent:
    """Return a source-sized composite; preserve prepared-source pixels outside support.

    Raw generated bytes must be durably retained separately by the caller. This pure
    function does not register outputs, update attempts or establish provider success.
    """
    if prepared.mask is None or prepared.blend_mask is None:
        raise ValidationError("Compositing requires prepared inpainting masks.")
    generated = _decode(generated_png, "Generated image")
    if generated.size != (prepared.fit.target_width, prepared.fit.target_height):
        raise ValidationError("Generated dimensions do not match the prepared generation target.")
    generated = generated.convert("RGB").crop(prepared.fit.crop_box)
    generated = generated.resize((prepared.source.width, prepared.source.height), Image.Resampling.LANCZOS)
    source = _verified(prepared.source)
    alpha = _verified(prepared.blend_mask)
    binary = _verified(prepared.source_mask)
    if any(count for value, count in enumerate(binary.histogram()) if value not in (0, 255)):
        raise ValidationError("Prepared source mask must be binary.")
    # Reject forged/inconsistent support instead of allowing a blend mask to modify
    # pixels which the authoritative binary mask says must be preserved.
    outside_alpha = ImageChops.multiply(alpha, ImageOps.invert(binary))
    if outside_alpha.getbbox() is not None:
        raise ValidationError("Blend mask extends outside selected mask support.")
    return _content(Image.composite(generated, source, alpha))

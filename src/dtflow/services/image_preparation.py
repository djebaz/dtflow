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
    note: str = "Approximate preview; subpixel grow may not be visible. Exact mask is prepared for submission."


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


def mask_png(pixels: bytes, width: int, height: int) -> bytes:
    """Canonical metadata-free grayscale PNG of a source-grid 0/255 mask buffer."""
    if len(pixels) != width * height:
        raise ValidationError("Mask pixels do not match the source size.")
    return _content(Image.frombytes("L", (width, height), pixels)).encoded


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


def adjusted_mask_alpha(
    pixels: bytes, width: int, height: int, *, source_width: int, source_height: int, grow: int, feather: float
) -> bytes:
    """Raw 8-bit alpha of the mask after Grow and Edge feather, for the editor's overlay (thumbnail grid only)."""
    return _blend_bytes(pixels, width, height, source_width, source_height, grow, feather)


def _blend_bytes(pixels, width, height, source_width, source_height, grow, feather) -> bytes:
    validate_source_dimensions(width, height)
    if width > 512 or height > 512 or len(pixels) != width * height:
        raise ValidationError("Mask preview must be a downsampled grid of at most 512x512.")
    if type(grow) is not int or abs(grow) > max_mask_grow(source_width, source_height):
        raise ValidationError("Mask grow exceeds the source-size work budget.")
    if type(feather) not in (int, float) or not math.isfinite(feather) or not 0 <= feather <= 64:
        raise ValidationError("Mask feather must be finite in 0..64 source pixels.")
    scale = min(width / source_width, height / source_height)
    mask = _adjust_binary(Image.frombytes("L", (width, height), pixels), round(grow * scale))
    if feather:
        mask = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(feather * scale)))
    return mask.tobytes()


def load_source_file(path: Path) -> ImageContent:
    """Editor-side source snapshot: EXIF-oriented, alpha flattened on white, RGB8; no fitting or resizing."""
    return load_source_bytes(_read(path, "Source image"))


def load_source_bytes(encoded: bytes) -> ImageContent:
    """``load_source_file`` for encoded bytes already in memory (for example a workflow node's IMAGE)."""
    oriented = _decode(encoded, "Source image").convert("RGBA")
    flat = Image.new("RGBA", oriented.size, "white")
    flat.alpha_composite(oriented)
    validate_source_dimensions(*flat.size)
    return _content(flat.convert("RGB"))


def prepare_edit_inputs(
    source: ImageContent, mask_pixels: bytes, width: int, height: int, *, grow: int = 0, feather: float = 0.0
) -> PreparedImageInputs:
    """Fit an editor source snapshot and its painted binary source-grid mask for one dispatch (worker only)."""
    if mask_coverage(mask_pixels, source.width, source.height) == 0:
        raise ValidationError("Paint a mask before editing.")
    mask = _content(Image.frombytes("L", (source.width, source.height), mask_pixels))
    return prepare_image_bytes(source.encoded, width, height, mask_bytes=mask.encoded, grow=grow, feather=feather)


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


def source_size(encoded: bytes) -> tuple[int, int]:
    """Oriented (width, height) of an encoded source image, as ``prepare_image_bytes`` sees it."""
    return _decode(encoded, "Source image").size


def _source_rgb(encoded: bytes) -> Image.Image:
    oriented = _decode(encoded, "Source image").convert("RGBA")
    source = Image.new("RGBA", oriented.size, "white")
    source.alpha_composite(oriented)
    return source.convert("RGB")


def _fit_image(source: Image.Image, width: int, height: int) -> tuple[ImageFit, Image.Image]:
    fit = _fit(source.size, (width, height))
    fitted = Image.new("RGB", (width, height), "white")
    fitted.paste(
        source.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.LANCZOS),
        (fit.left, fit.top),
    )
    return fit, fitted


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
    source = _source_rgb(source_bytes)
    fit, fitted = _fit_image(source, width, height)
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


def prepare_script_image_bytes(encoded: bytes, *, multiple: int = 1) -> tuple[PreparedImageInputs, dict]:
    """Normalize one selected script image, optionally centre-cropping its owned copy.

    ``multiple=1`` imposes no source-grid constraint. A larger script-authored
    multiple trims both oriented dimensions downward; never resize/upscale or
    modify the external file. Too-small images fail rather than producing zero
    dimensions. The fitted 128x128 companion is internal; JS receives source.
    """
    if type(multiple) is not int or not 1 <= multiple <= 4096:
        raise ValidationError("Script image multiple must be an integer within 1..4096.")
    image = _source_rgb(encoded)
    original_width, original_height = image.size
    width = original_width - original_width % multiple
    height = original_height - original_height % multiple
    if not width or not height:
        raise ValidationError(
            f"Selected image is {original_width}x{original_height}; it is too small for a {multiple}-pixel grid. "
            "Cropping cannot enlarge an image; choose a larger image or change the script's multiple."
        )
    left = (original_width - width) // 2
    top = (original_height - height) // 2
    if (width, height) != image.size:
        image = image.crop((left, top, left + width, top + height))
    fit, fitted = _fit_image(image, 128, 128)
    prepared = PreparedImageInputs(_content(image), _content(fitted), fit)
    crop = {
        "multiple": multiple,
        "mode": "center",
        "source_width": original_width,
        "source_height": original_height,
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }
    return prepared, crop


def prepare_script_image_file(source: Path, *, multiple: int = 1) -> tuple[PreparedImageInputs, dict]:
    """Read a selected external image once, then prepare its independent copy."""
    return prepare_script_image_bytes(_read(source, "Script image"), multiple=multiple)


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


# --- workflow file nodes (#316): ComfyUI's Load Image, Load Image (as Mask) and Upscale Image ----------------------

_SCALE_FILTERS = {
    "nearest-exact": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "area": Image.Resampling.BOX,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _eight_bit(image: Image.Image) -> Image.Image:
    """16-bit and integer grayscale images scaled to 8 bits (``value // 257``) instead of Pillow's clipping conversion."""
    if image.mode.startswith("I;16") or image.mode == "I":
        values = image.convert("I")
        low, high = values.getextrema()
        if low < 0 or high > 65535:
            raise ValidationError("Integer images must use unsigned 16-bit values in 0..65535.")
        return values.point([value // 257 for value in range(65536)], "L")
    if image.mode == "F":
        raise ValidationError("Floating-point images are unsupported; use an 8-bit or unsigned 16-bit image.")
    return image


def _inverted_alpha(image: Image.Image) -> Image.Image:
    """ComfyUI's MASK of an image: ``1 - alpha`` (transparent = masked); all zero without alpha."""
    if "A" in image.getbands():
        return ImageChops.invert(image.getchannel("A"))
    return Image.new("L", image.size, 0)


def _read_file(path: str, label: str) -> bytes:
    file = Path(path)
    if not file.is_file():
        raise ValidationError(f"{label} was not found: {path}")
    return _read(file, label)


def check_image_bytes(encoded: bytes) -> tuple[int, int]:
    """Decode-check the bytes of a file chosen for a workflow file node and return ``(width, height)``."""
    image = _decode(encoded, "The image file")
    return image.width, image.height


def check_image_file(path: str | Path) -> tuple[int, int]:
    """Decode-check a file chosen for a workflow file node and return its ``(width, height)``."""
    image = _decode(_read_file(str(path), "The image file"), "The image file")
    return image.width, image.height


def load_workflow_image(path: str) -> tuple[bytes, bytes, int, int]:
    """Read an image file like ComfyUI's Load Image: ``(RGB PNG, mask PNG, width, height)``.

    EXIF rotation is applied, alpha is dropped from the image and becomes the inverted mask (an all-zero mask when the
    file has none). The file must be a single image; the pixel and byte limits of image preparation apply."""
    image = _eight_bit(_decode(_read_file(path, "The image file"), "The image file"))
    try:
        rgb = image.convert("RGB")
        mask = _inverted_alpha(image.convert("RGBA") if image.mode == "P" else image)
    except (OSError, ValueError) as exc:
        raise ValidationError("The image file could not be converted safely.") from exc
    return _png(rgb), _png(mask), rgb.width, rgb.height


def load_workflow_mask(path: str, channel: str) -> tuple[bytes, int, int]:
    """Read a mask file like ComfyUI's Load Image (as Mask): ``(grayscale PNG, width, height)``.

    ``alpha`` is the inverted alpha; ``red``, ``green`` and ``blue`` are the channel values as they are."""
    image = _eight_bit(_decode(_read_file(path, "The mask file"), "The mask file"))
    try:
        if channel == "alpha":
            mask = _inverted_alpha(image.convert("RGBA") if image.mode == "P" else image)
        else:
            index = {"red": 0, "green": 1, "blue": 2}.get(channel)
            if index is None:
                raise ValidationError(f"Unknown mask channel {channel!r}.")
            mask = image.convert("RGB").getchannel(index)
    except (OSError, ValueError) as exc:
        raise ValidationError("The mask file could not be converted safely.") from exc
    return _png(mask), mask.width, mask.height


def scale_workflow_image(encoded: bytes, method: str, width: int, height: int, crop: str) -> bytes:
    """ComfyUI's Upscale Image on one PNG: ``width``/``height`` 0 keep the aspect ratio from the other side (both 0
    returns the image unchanged), ``crop="center"`` fills the box and trims the overflow evenly, ``disabled`` stretches."""
    filter_ = _SCALE_FILTERS.get(method)
    if filter_ is None:
        raise ValidationError(f"Unknown upscale method {method!r}.")
    image = _decode(encoded, "Source image").convert("RGB")
    if width == 0 and height == 0:
        return _png(image)
    old_width, old_height = image.size
    if width == 0:
        width = max(1, round(old_width * height / old_height))
    elif height == 0:
        height = max(1, round(old_height * width / old_width))
    validate_source_dimensions(width, height)
    if crop == "center":
        old_aspect, new_aspect = old_width / old_height, width / height
        x = y = 0
        if old_aspect > new_aspect:
            x = round((old_width - old_width * (new_aspect / old_aspect)) / 2)
        elif old_aspect < new_aspect:
            y = round((old_height - old_height * (old_aspect / new_aspect)) / 2)
        image = image.crop((x, y, old_width - x, old_height - y))
    return _png(image.resize((width, height), filter_))


def mask_selects_pixels(encoded: bytes) -> bool:
    """True when the mask PNG selects anything (a pixel brighter than 50%, the Inpaint threshold)."""
    return _binary_mask(_decode(encoded, "Mask image")).getbbox() is not None


def mask_to_image_bytes(encoded: bytes) -> bytes:
    """ComfyUI's Convert Mask to Image: the mask as a grayscale RGB PNG (white = masked)."""
    mask = _eight_bit(_decode(encoded, "Mask image"))
    return _png(mask.convert("L").convert("RGB"))


def image_to_mask_bytes(encoded: bytes, channel: str) -> bytes:
    """ComfyUI's Convert Image to Mask: one channel of an image as a grayscale PNG (white = masked)."""
    index = {"red": 0, "green": 1, "blue": 2}.get(channel)
    if index is None:
        raise ValidationError("DTFlow images have no alpha channel; choose red, green or blue.")
    return _png(_decode(encoded, "Source image").convert("RGB").getchannel(index))


def restore_source_frame(encoded: bytes, fit: ImageFit) -> bytes | None:
    """Crop a generated image fitted into a larger valid box back to the source frame (#316).

    Image steps pad a source that is not a valid generation size; an inpaint that keeps the outside already comes back
    source-sized (None here), while any other result is cropped to the fitted area and resized to the source size, so
    the padding never reaches the next step."""
    image = _decode(encoded, "Generated image")
    if image.size == (fit.source_width, fit.source_height):
        return None
    restored = (
        image.convert("RGB").crop(fit.crop_box).resize((fit.source_width, fit.source_height), Image.Resampling.LANCZOS)
    )
    return _png(restored)

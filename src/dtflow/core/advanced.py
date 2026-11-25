"""Optional advanced Draw Things settings of one generation request (#539).

Every field is optional: ``None`` means "not chosen", so the checkpoint profile and the library default stay in charge
and a basic request is byte-for-byte what it was before. Hires Fix is expressed as one user-facing factor over the
requested final size; the first-pass size is derived from it (or set explicitly) and is what the provider receives.
Expressibility here is not provider acceptance: DT+ honouring a field is a separate, observed fact.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from dtflow.core.models import ValidationError, integer, is_finite_number

HIRES_MIN_FACTOR = 1.0  # a factor of 1 is "no Hires Fix"
HIRES_MAX_FACTOR = 4.0
HIRES_DEFAULT_FACTOR = 2.0  # what switching Hires Fix on without a choice means
DIMENSION_STEP = 64  # the provider stores first-pass dimensions in 64-pixel units
FIRST_PASS_MIN = 128
SEED_MODES = ("Legacy", "TorchCpuCompatible", "ScaleAlike", "NvidiaGpuCompatible")


@dataclass(frozen=True, slots=True)
class AdvancedSettings:
    """Chosen advanced values. ``hires_fix_factor`` of ``None`` or 1 is off; the explicit first-pass size needs it on."""

    hires_fix_factor: float | None = None
    hires_fix_width: int | None = None  # explicit first-pass size; otherwise derived from the factor
    hires_fix_height: int | None = None
    hires_fix_strength: float | None = None  # share of steps at full size; never the image-to-image denoise strength
    seed_mode: str | None = None
    tiled_decoding: bool | None = None

    def __post_init__(self) -> None:
        if self.hires_fix_factor is not None and (
            not is_finite_number(self.hires_fix_factor)
            or not HIRES_MIN_FACTOR <= self.hires_fix_factor <= HIRES_MAX_FACTOR
        ):
            raise ValidationError(f"hires_fix factor must be between {HIRES_MIN_FACTOR:g} and {HIRES_MAX_FACTOR:g}.")
        for name in ("hires_fix_width", "hires_fix_height"):
            value = getattr(self, name)
            if value is None:
                continue
            if not self.hires_fix_active:
                raise ValidationError("hires_fix first-pass size needs Hires Fix on (factor above 1).")
            integer(value, FIRST_PASS_MIN, 2048, name)
            if value % DIMENSION_STEP:
                raise ValidationError(f"{name} must be a multiple of {DIMENSION_STEP}.")
        if self.hires_fix_strength is not None:
            if not is_finite_number(self.hires_fix_strength) or not 0 <= self.hires_fix_strength <= 1:
                raise ValidationError("hires_fix strength must be finite in 0..1.")
            if not self.hires_fix_active:
                raise ValidationError("hires_fix strength needs Hires Fix on (factor above 1).")
        if self.seed_mode is not None and self.seed_mode not in SEED_MODES:
            raise ValidationError("seed_mode is not a Draw Things seed mode.")
        if self.tiled_decoding is not None and type(self.tiled_decoding) is not bool:
            raise ValidationError("tiled_decoding must be on or off.")

    @property
    def hires_fix_active(self) -> bool:
        return self.hires_fix_factor is not None and self.hires_fix_factor > HIRES_MIN_FACTOR

    @property
    def is_default(self) -> bool:
        """True when nothing is chosen, so the request carries no advanced values at all."""
        return all(getattr(self, item.name) is None for item in fields(self))

    @classmethod
    def from_record(cls, record: Any) -> AdvancedSettings:
        """Read a stored or imported mapping; unknown or malformed content is refused, never dropped."""
        if isinstance(record, cls):
            return record
        if not isinstance(record, dict) or set(record) - {item.name for item in fields(cls)}:
            raise ValidationError("advanced settings are malformed.")
        return cls(**record)

    def to_record(self) -> dict[str, Any]:
        """Only the chosen values, so an untouched field never appears in a saved document."""
        return {item.name: getattr(self, item.name) for item in fields(self) if getattr(self, item.name) is not None}


HIRES_FIELDS = ("hires_fix_factor", "hires_fix_width", "hires_fix_height", "hires_fix_strength")
# What each kind of request can carry. Exposure is not support: a field is listed only where the pinned inventory marks
# the mode as a candidate (Hires Fix and tiled decoding: images; seed mode: every mode), never for an unverified one.
SCOPE_FIELDS = {
    "image": (*HIRES_FIELDS, "seed_mode", "tiled_decoding"),  # text-to-image, image-to-image and Inpaint
    "pose": ("seed_mode", "tiled_decoding"),  # the Controlnet pose hint is final-size, so no first pass
    "video": ("seed_mode",),
}
SCOPE_NAMES = {"image": "image", "pose": "Controlnet pose", "video": "video"}
FIELD_NAMES = {name: "hires_fix" for name in HIRES_FIELDS} | {
    "seed_mode": "seed_mode",
    "tiled_decoding": "tiled_decoding",
}


def scope_for(*, video: bool, pose: bool) -> str:
    return "video" if video else "pose" if pose else "image"


def unsupported_fields(advanced: AdvancedSettings | None, scope: str) -> tuple[str, ...]:
    """Display names of chosen fields the given kind of request cannot carry (empty when all can)."""
    if advanced is None:
        return ()
    allowed = SCOPE_FIELDS[scope]
    names = (
        FIELD_NAMES[item.name]
        for item in fields(advanced)
        if getattr(advanced, item.name) is not None and item.name not in allowed
    )
    return tuple(dict.fromkeys(names))


def restrict(advanced: AdvancedSettings | None, scope: str | None) -> AdvancedSettings | None:
    """The chosen values a request of this kind carries; ``None`` scope (no request) carries nothing."""
    if advanced is None or scope is None:
        return None
    allowed = SCOPE_FIELDS[scope]
    result = replace(advanced, **{item.name: None for item in fields(advanced) if item.name not in allowed})
    return None if result.is_default else result


def snap_first_pass(value: float) -> int:
    """The nearest supported first-pass dimension (halves round up)."""
    return max(FIRST_PASS_MIN, int(value / DIMENSION_STEP + 0.5) * DIMENSION_STEP)


def first_pass_size(width: int, height: int, advanced: AdvancedSettings | None) -> tuple[int, int] | None:
    """The first-pass size for a requested final size, or ``None`` when Hires Fix is off.

    Size is always the final output. The factor divides it and the result is rounded to the provider's increment; an
    explicit width or height replaces only its own axis.
    """
    if advanced is None or not advanced.hires_fix_active:
        return None
    factor = advanced.hires_fix_factor
    return (
        advanced.hires_fix_width or snap_first_pass(width / factor),
        advanced.hires_fix_height or snap_first_pass(height / factor),
    )


def check_first_pass(width: int, height: int, advanced: AdvancedSettings | None) -> None:
    """A first pass that is not smaller than the final size is a mistake the user should see, not a silent no-op."""
    size = first_pass_size(width, height, advanced)
    if size is not None and (size[0] > width or size[1] > height or size == (width, height)):
        raise ValidationError(
            f"hires_fix first pass {size[0]}x{size[1]} is not smaller than the final size {width}x{height}; "
            "raise the final size or the factor."
        )


def wire_fields(width: int, height: int, advanced: AdvancedSettings | None) -> dict[str, float | bool | int | str]:
    """Draw Things configuration fields for the chosen values (Hires Fix as its first-pass size)."""
    if advanced is None:
        return {}
    values: dict[str, float | bool | int | str] = {}
    size = first_pass_size(width, height, advanced)
    if size is not None:
        values.update(hires_fix=True, hires_fix_width=size[0], hires_fix_height=size[1])
        if advanced.hires_fix_strength is not None:
            values["hires_fix_strength"] = advanced.hires_fix_strength
    if advanced.seed_mode is not None:
        values["seed_mode"] = advanced.seed_mode
    if advanced.tiled_decoding is not None:
        values["tiled_decoding"] = advanced.tiled_decoding
    return values


def summary(width: int, height: int, advanced: AdvancedSettings | None) -> str:
    """One readable line of what will be sent, e.g. ``Hires Fix 2×: 512x512 → 1024x1024``; empty when nothing is chosen."""
    if advanced is None or advanced.is_default:
        return ""
    parts = []
    size = first_pass_size(width, height, advanced)
    if size is not None:
        text = f"Hires Fix {advanced.hires_fix_factor:g}×: first pass {size[0]}x{size[1]} → final {width}x{height}"
        if advanced.hires_fix_strength is not None:
            text += f", strength {advanced.hires_fix_strength:g}"
        parts.append(text)
    if advanced.seed_mode is not None:
        parts.append(f"seed mode {advanced.seed_mode}")
    if advanced.tiled_decoding is not None:
        parts.append("tiled decoding " + ("on" if advanced.tiled_decoding else "off"))
    return "; ".join(parts)

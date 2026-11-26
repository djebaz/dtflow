"""ControlNet request semantics, family compatibility and desktop defaults."""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dtflow.core.models import ValidationError, is_finite_number

# community-models ce4b2ae34a2ef7899fa014f019d04f2632dabe6f, controlnets/*/metadata.json.
# Draw Things a982e3dafee0a10a56c1f16a38b3d272eb821e8d ControlModel.swift:
# Union 1 uses pose mode 4; Union 2 is a regular controlnet without mode embedding.
CONTROLS = (
    "controlnet_union_pro_flux_1_dev_1.0_q8p.ckpt",
    "controlnet_union_pro_flux_1_dev_2.0_q8p.ckpt",
    "controlnet_union_pro_flux_1_dev_1.0_q5p.ckpt",
    "controlnet_union_pro_flux_1_dev_2.0_q5p.ckpt",
)
# Pinned community SDXL Union specification.
SDXL_POSE_PROBE_CONTROL = "controlnet_xinsir_union_promax_sdxl_1.0_f16.ckpt"
# Official ControlNetZoo.swift, draw-things-community cb2d4c61d1b58816ce1843481e74293a240e5bf4.
SD15_POSE_PROBE_CONTROL = "controlnet_openpose_1.x_v1.1_f16.ckpt"
SDXL_CANNY_PROBE_CONTROL = "controlnet_canny_sdxl_v1.0_mid_f16.ckpt"
SD15_CANNY_PROBE_CONTROL = "controlnet_canny_1.x_v1.1_f16.ckpt"
SD15_DEPTH_PROBE_CONTROL = "controlnet_depth_1.x_v1.1_f16.ckpt"
SD15_SEG_PROBE_CONTROL = "controlnet_seg_1.x_v1.1_f16.ckpt"
SDXL_DEPTH_PROBE_CONTROL = "controlnet_depth_sdxl_v1.0_mid_f16.ckpt"

# Native input and model family for each official control.
OFFICIAL_PROBE_CONTROLS = {
    SD15_POSE_PROBE_CONTROL: ("pose", "sd_1"),
    SD15_CANNY_PROBE_CONTROL: ("canny", "sd_1"),
    SD15_DEPTH_PROBE_CONTROL: ("depth", "sd_1"),
    SD15_SEG_PROBE_CONTROL: ("seg", "sd_1"),
    SDXL_CANNY_PROBE_CONTROL: ("canny", "sdxl"),
    SDXL_DEPTH_PROBE_CONTROL: ("depth", "sdxl"),
}
XINSIR_PROBE_INPUTS = ("pose", "canny", "depth", "seg")


def control_family(checkpoint: str) -> str:
    """Return the model family required by a pinned control, including historical Union controls."""
    if checkpoint in CONTROLS:
        return "flux_1"
    if checkpoint == SDXL_POSE_PROBE_CONTROL:
        return "sdxl"
    return OFFICIAL_PROBE_CONTROLS[checkpoint][1]


MODELS = ("flux_1_dev_f16.ckpt", "flux_1_dev_q5p.ckpt", "flux_1_dev_q8p.ckpt")
# Draw Things "Control Force" and the step range over which the control applies (fractions of the run).
CONTROL_MODES = ("Balanced", "Prompt", "Control")


# The checkpoint families pose guidance is offered for: catalogue ``base_model`` key and its display label. Add a family
# here (with its control in CONTROLS) and every check, message and default below follows; nothing else names FLUX.1.
POSE_FAMILIES = (("flux_1", "FLUX.1"), ("sd_1", "SD 1.x"), ("sdxl", "SDXL"))
DEFAULT_POSE_FAMILY = POSE_FAMILIES[0][0]
POSE_FAMILY_LABEL = ", ".join(label for _, label in POSE_FAMILIES[:-1]) + " or " + POSE_FAMILIES[-1][1]
INPUT_LABELS = {"pose": "Pose", "canny": "Canny", "depth": "Depth", "seg": "Segmentation"}


@dataclass(frozen=True, slots=True)
class ControlDefaults:
    """The settings a new pick of a control and input type starts from; never applied to restored or saved values."""

    strength: float
    guidance_start: float
    guidance_end: float
    control_mode: str


@dataclass(frozen=True, slots=True)
class DesktopControl:
    """One desktop control with input types and suggested settings, independent of quality evidence."""

    checkpoint: str
    label: str
    family: str
    types: tuple[tuple[str, ControlDefaults], ...]

    @property
    def inputs(self) -> tuple[str, ...]:
        return tuple(kind for kind, _ in self.types)

    def defaults(self, input_type: str) -> ControlDefaults | None:
        return next((values for kind, values in self.types if kind == input_type), None)


_DESKTOP_DATASET = Path(__file__).parents[1] / "resources" / "catalogue" / "controlnet_desktop.json"


def _encodable(checkpoint: str, input_type: str) -> bool:
    """Whether DTFlow has a request encoding for this control and input (wire.py follows the same constants)."""
    if checkpoint in CONTROLS or checkpoint == SDXL_POSE_PROBE_CONTROL:
        return input_type in XINSIR_PROBE_INPUTS
    native = OFFICIAL_PROBE_CONTROLS.get(checkpoint)
    return native is not None and native[0] == input_type


@cache
def _load_desktop_controls() -> tuple[tuple[DesktopControl, ...], frozenset[str]]:
    """Read the bundled table; a malformed entry or one without a request encoding is a packaging error."""
    data = json.loads(_DESKTOP_DATASET.read_text(encoding="utf-8"))
    controls = []
    for item in data["controls"]:
        control = DesktopControl(
            checkpoint=item["checkpoint"],
            label=item["label"],
            family=item["family"],
            types=tuple(
                (kind, ControlDefaults(float(v[0]), float(v[1]), float(v[2]), v[3]))
                for kind, v in item["types"].items()
            ),
        )
        families = {family for family, _ in POSE_FAMILIES}
        if (
            control.family not in families
            or control.family != control_family(control.checkpoint)
            or not control.inputs
            or any(kind not in INPUT_LABELS or not _encodable(control.checkpoint, kind) for kind in control.inputs)
            or any(
                values.control_mode not in CONTROL_MODES
                or not 0 < values.strength <= 2
                or not 0 <= values.guidance_start < values.guidance_end <= 1
                for _, values in control.types
            )
        ):
            raise ValueError(f"controlnet_desktop.json: {control.checkpoint} has no supported family or encoding.")
        controls.append(control)
    retired = frozenset(data.get("retired", ()))
    if len({c.checkpoint for c in controls}) != len(controls) or retired & {c.checkpoint for c in controls}:
        raise ValueError("controlnet_desktop.json: duplicate or retired control entries.")
    return tuple(controls), retired


def desktop_controls() -> tuple[DesktopControl, ...]:
    """The desktop table, read once per run on first use and then served from memory."""
    return _load_desktop_controls()[0]


def retired_controls() -> frozenset[str]:
    """Controls removed from new desktop selections; records naming them are reported, never replaced."""
    return _load_desktop_controls()[1]


def desktop_control(checkpoint: str | None) -> DesktopControl | None:
    return next((control for control in desktop_controls() if control.checkpoint == checkpoint), None)


def desktop_admits(control: DesktopControl, model_id: str, base_model: str | None, modes=None) -> bool:
    """Offer a control for its model family and text-to-image checkpoints."""
    return (modes is None or "t2i" in modes) and supports_pose(
        model_id, base_model, families=(control.family,)
    )


RETIRED_CONTROL_NOTE = "The saved control, Union Pro 1.0, is retired. Choose Union Pro 2.0 to continue."


def desktop_offer_problem(checkpoint, input_type: str, model_id: str, base_model=None, modes=None) -> str:
    """Why the desktop table does not offer this control and type with this checkpoint; empty when it does.

    The one check behind the editor lists, Gallery restore, queue drafts (every model of the plan) and template save.
    Session admission uses the same control family without desktop retirement restrictions."""
    if checkpoint in retired_controls():
        return RETIRED_CONTROL_NOTE
    control = desktop_control(checkpoint)
    label = INPUT_LABELS.get(input_type, str(input_type))
    if control is None:
        return "This control is not offered in desktop Controlnet. Choose another control."
    if input_type not in control.inputs:
        return f"{control.label} does not offer {label}. Choose another control or control type."
    if not desktop_admits(control, model_id, base_model, modes):
        name = model_id.removesuffix(".ckpt") or "this checkpoint"
        return f"{control.label} is not offered with {name}. Choose another control or checkpoint."
    return ""


def supports_pose(model_id: str, base_model: str | None = None, *, families: tuple[str, ...] | None = None) -> bool:
    """Whether the checkpoint belongs to a Controlnet family (for FLUX.1: dev, schnell, krea, depth, de_distill, ...).

    The catalogue's family of the checkpoint (``base_model``) decides when it is known; only an unclassified checkpoint
    falls back to the family key as a file-name prefix (``flux_1_``)."""
    families = families or tuple(family for family, _ in POSE_FAMILIES)
    if base_model:
        family = {"flux1": "flux_1", "v1": "sd_1", "sdxl_base_v0.9": "sdxl", "ssd_1b": "sdxl"}.get(
            base_model, base_model
        )
        return family in families
    name = model_id.strip().lower()
    return name.endswith(".ckpt") and any(
        name.startswith(f"{family}_") or (family == "sdxl" and name.startswith("sd_xl_")) for family in families
    )


# community-models controlnets/union-pro-flux-1-dev-{1.0,2.0}/metadata.json (same revision as above). Draw Things builds
# its control specification mapping from the request's MetadataOverride.controlNets, so the server applies a community
# control only when that specification is sent with the request.
_UNION_SPECS = {
    "1.0": ("Union Pro (FLUX.1 [dev], ControlNet)", "controlnetunion", [5, 10]),
    "2.0": ("Union Pro 2.0 (FLUX.1 [dev], ControlNet)", "controlnet", [6, 0]),
}


def control_spec(checkpoint: str) -> dict:
    """Pinned community control specification, as the Draw Things override payload expects it."""
    if checkpoint == SDXL_POSE_PROBE_CONTROL:
        # community-models ce4b2ae34a2ef7899fa014f019d04f2632dabe6f,
        # controlnets/xinsir-union-promax-sdxl-1.0/metadata.json.
        return {
            "name": "Xinsir Union ProMax (SDXL, ControlNet)",
            "file": checkpoint,
            "version": "sdxl_base_v0.9",
            "type": "controlnetunion",
            "global_average_pooling": False,
            "transformer_blocks": [],
        }
    if checkpoint not in CONTROLS:
        raise ValidationError("Only the pinned Union pose controls have a known specification.")
    name, kind, blocks = _UNION_SPECS["1.0" if "_1.0_" in checkpoint else "2.0"]
    return {
        "name": name,
        "file": checkpoint,
        "version": "flux1",
        "type": kind,
        "global_average_pooling": False,
        "transformer_blocks": blocks,
    }


@dataclass(frozen=True, slots=True)
class PoseControl:
    checkpoint: str
    strength: float
    input_type: str = "pose"
    evidence: tuple[tuple[str, str], ...] = ()
    control_mode: str = "Balanced"
    guidance_start: float = 0.0
    guidance_end: float = 1.0

    def __post_init__(self):
        if self.checkpoint == SDXL_POSE_PROBE_CONTROL:
            if self.input_type not in XINSIR_PROBE_INPUTS:
                raise ValidationError("Unsupported Xinsir control input.")
        elif self.checkpoint in OFFICIAL_PROBE_CONTROLS:
            if self.input_type != OFFICIAL_PROBE_CONTROLS[self.checkpoint][0]:
                raise ValidationError("The input type must match the official control checkpoint.")
        elif self.checkpoint in CONTROLS:
            if self.input_type not in XINSIR_PROBE_INPUTS:
                raise ValidationError("Unsupported FLUX.1 Union control input.")
        else:
            raise ValidationError("Only pinned controls have a supported request encoding.")
        # A conservative POC subset of the native weight control, not denoising strength.
        if not is_finite_number(self.strength) or not 0 < self.strength <= 2:
            raise ValidationError(
                "Pose strength must be finite and greater than 0, at most 2; omit control for baseline."
            )
        if self.control_mode not in CONTROL_MODES:
            raise ValidationError("Pose control mode must be Balanced, Prompt or Control.")
        for value in (self.guidance_start, self.guidance_end):
            if not is_finite_number(value) or not 0 <= value <= 1:
                raise ValidationError("Pose start and end must be between 0 and 1.")
        if not self.guidance_start < self.guidance_end:
            raise ValidationError("Pose start must be before its end.")
        from dtflow.core.models import validate_metadata

        validate_metadata(self.evidence)

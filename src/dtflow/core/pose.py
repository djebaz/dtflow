"""Body-pose control specifications and narrow, explicitly selected provider experiments."""

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
# #632: CLI-only feasibility probe. Do not add this to the desktop control or family choices until verified.
SDXL_POSE_PROBE_CONTROL = "controlnet_xinsir_union_promax_sdxl_1.0_f16.ckpt"
# Official ControlNetZoo.swift, draw-things-community cb2d4c61d1b58816ce1843481e74293a240e5bf4.
SD15_POSE_PROBE_CONTROL = "controlnet_openpose_1.x_v1.1_f16.ckpt"
SD15_POSE_PROBE_MODEL = "sd_v1.5_f16.ckpt"
SDXL_CANNY_PROBE_CONTROL = "controlnet_canny_sdxl_v1.0_mid_f16.ckpt"
SDXL_CANNY_PROBE_MODELS = ("sd_xl_base_1.0_f16.ckpt", "sd_xl_base_1.0_q6p_q8p.ckpt")
SDXL_POSE_PROBE_MODELS = (*SDXL_CANNY_PROBE_MODELS, "sd_xl_turbo_f16.ckpt")
SD15_CANNY_PROBE_CONTROL = "controlnet_canny_1.x_v1.1_f16.ckpt"
SD15_DEPTH_PROBE_CONTROL = "controlnet_depth_1.x_v1.1_f16.ckpt"
SD15_SEG_PROBE_CONTROL = "controlnet_seg_1.x_v1.1_f16.ckpt"
SDXL_DEPTH_PROBE_CONTROL = "controlnet_depth_sdxl_v1.0_mid_f16.ckpt"

# SD 1.x probes use the bundled catalogue family; SDXL probes retain exact model admission.
SD1_PROBE_CONTROLS = (
    SD15_POSE_PROBE_CONTROL,
    SD15_CANNY_PROBE_CONTROL,
    SD15_DEPTH_PROBE_CONTROL,
    SD15_SEG_PROBE_CONTROL,
)
# Production controls remain in CONTROLS.
OFFICIAL_PROBE_CONTROLS = {
    SD15_POSE_PROBE_CONTROL: ("pose", ()),
    SD15_CANNY_PROBE_CONTROL: ("canny", ()),
    SD15_DEPTH_PROBE_CONTROL: ("depth", ()),
    SD15_SEG_PROBE_CONTROL: ("seg", ()),
    SDXL_CANNY_PROBE_CONTROL: ("canny", SDXL_POSE_PROBE_MODELS),
    SDXL_DEPTH_PROBE_CONTROL: ("depth", SDXL_POSE_PROBE_MODELS),
}
XINSIR_PROBE_INPUTS = ("pose", "canny", "depth", "seg")


def probe_models(checkpoint: str) -> tuple[str, ...] | None:
    """Return the exact model allowlist for a CLI-only control, or None for production controls."""
    if checkpoint == SDXL_POSE_PROBE_CONTROL:
        return SDXL_POSE_PROBE_MODELS
    if checkpoint in SD1_PROBE_CONTROLS:
        return None  # family admission, not an exact checkpoint allowlist
    entry = OFFICIAL_PROBE_CONTROLS.get(checkpoint)
    return entry[1] if entry else None


MODELS = ("flux_1_dev_f16.ckpt", "flux_1_dev_q5p.ckpt", "flux_1_dev_q8p.ckpt")
# Only this checkpoint with Union Pro 2.0 q5p has been confirmed (#633/#635; Union Pro 1.0 at 0.7 in #502). Other FLUX.1 variants may be tried and
# are labelled "not verified"; a checkpoint moves here only when the owner confirms it.
VERIFIED_MODELS = ("flux_1_dev_q5p.ckpt",)
# Draw Things "Control Force" and the step range over which the control applies (fractions of the run).
CONTROL_MODES = ("Balanced", "Prompt", "Control")


# The checkpoint families pose guidance is offered for: catalogue ``base_model`` key and its display label. Add a family
# here (with its control in CONTROLS) and every check, message and default below follows; nothing else names FLUX.1.
POSE_FAMILIES = (("flux_1", "FLUX.1"), ("sd_1", "SD 1.x"), ("sdxl", "SDXL"))
DEFAULT_POSE_FAMILY = POSE_FAMILIES[0][0]
POSE_FAMILY_LABEL = ", ".join(label for _, label in POSE_FAMILIES[:-1]) + " or " + POSE_FAMILIES[-1][1]
INPUT_LABELS = {"pose": "Pose", "canny": "Canny", "depth": "Depth", "seg": "Segmentation"}


@dataclass(frozen=True, slots=True)
class DesktopControl:
    """One control the desktop offers (#635), read from ``controlnet_desktop.json``: family, input types, evidence.

    ``inputs`` lists only the input types with owner-observed DT+ results; ``models`` narrows the family to exact
    checkpoints when only those were observed; ``verified_models`` are the checkpoints the owner saw work with it."""

    checkpoint: str
    label: str
    family: str
    inputs: tuple[str, ...]
    verified: bool
    verified_models: tuple[str, ...]
    evidence: str
    models: tuple[str, ...] | None = None


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
            inputs=tuple(item["inputs"]),
            verified=bool(item["verified"]),
            verified_models=tuple(item.get("verified_models", ())),
            evidence=item.get("evidence", ""),
            models=tuple(item["models"]) if item.get("models") is not None else None,
        )
        families = {family for family, _ in POSE_FAMILIES}
        if (
            control.family not in families
            or not control.inputs
            or any(kind not in INPUT_LABELS or not _encodable(control.checkpoint, kind) for kind in control.inputs)
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
    """Whether the desktop offers ``control`` with this checkpoint: its exact models, or its catalogue family (SD 1.x
    text-to-image checkpoints only, as the session requires)."""
    if control.models is not None:
        return model_id in control.models
    if control.family == "sd_1" and modes is not None and "t2i" not in modes:
        return False
    return supports_pose(model_id, base_model, families=(control.family,))


def supports_pose(model_id: str, base_model: str | None = None, *, families: tuple[str, ...] | None = None) -> bool:
    """Whether the checkpoint belongs to a Controlnet family (for FLUX.1: dev, schnell, krea, depth, de_distill, ...).

    The catalogue's family of the checkpoint (``base_model``) decides when it is known; only an unclassified checkpoint
    falls back to the family key as a file-name prefix (``flux_1_``)."""
    families = families or tuple(family for family, _ in POSE_FAMILIES)
    if base_model:
        return base_model in families
    name = model_id.strip().lower()
    return name.endswith(".ckpt") and any(name.startswith(f"{family}_") for family in families)


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
            raise ValidationError("Only pinned controls are offered by this experiment.")
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

"""Body-pose control specifications and narrow, explicitly selected provider experiments."""

from dataclasses import dataclass

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
SDXL_CANNY_PROBE_CONTROL = "controlnet_canny_sdxl_v1.0_mid_f16.ckpt"
SDXL_POSE_PROBE_MODELS = ("sd_xl_base_1.0_f16.ckpt", "sd_xl_base_1.0_q6p_q8p.ckpt")

MODELS = ("flux_1_dev_f16.ckpt", "flux_1_dev_q5p.ckpt", "flux_1_dev_q8p.ckpt")
# Only this checkpoint with Union Pro 1.0 q5p at 0.7 has been confirmed (#502). Other FLUX.1 variants may be tried and
# are labelled "not verified"; a checkpoint moves here only when the owner confirms it.
VERIFIED_MODELS = ("flux_1_dev_q5p.ckpt",)
# Draw Things "Control Force" and the step range over which the control applies (fractions of the run).
CONTROL_MODES = ("Balanced", "Prompt", "Control")


# The checkpoint families pose guidance is offered for: catalogue ``base_model`` key and its display label. Add a family
# here (with its control in CONTROLS) and every check, message and default below follows; nothing else names FLUX.1.
POSE_FAMILIES = (("flux_1", "FLUX.1"),)
DEFAULT_POSE_FAMILY = POSE_FAMILIES[0][0]
POSE_FAMILY_LABEL = " or ".join(label for _, label in POSE_FAMILIES)


def supports_pose(model_id: str, base_model: str | None = None) -> bool:
    """Whether the checkpoint belongs to a pose family (for FLUX.1: dev, schnell, krea, depth, de_distill, ...).

    The catalogue's family of the checkpoint (``base_model``) decides when it is known; only an unclassified checkpoint
    falls back to the family key as a file-name prefix (``flux_1_``)."""
    families = tuple(family for family, _ in POSE_FAMILIES)
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
        if self.checkpoint == SDXL_CANNY_PROBE_CONTROL:
            if self.input_type != "canny":
                raise ValidationError("The official SDXL Canny probe requires a Canny edge map.")
        elif self.checkpoint not in (*CONTROLS, SDXL_POSE_PROBE_CONTROL) or self.input_type != "pose":
            raise ValidationError("Only pinned Union pose controls are offered by this experiment.")
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

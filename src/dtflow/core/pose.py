"""Narrow FLUX.1 Dev pose experiment, not a general control compatibility matrix."""

import math
from dataclasses import dataclass

from dtflow.core.models import ValidationError

# community-models ce4b2ae34a2ef7899fa014f019d04f2632dabe6f, controlnets/*/metadata.json.
# Draw Things a982e3dafee0a10a56c1f16a38b3d272eb821e8d ControlModel.swift:
# Union 1 uses pose mode 4; Union 2 is a regular controlnet without mode embedding.
CONTROLS = (
    "controlnet_union_pro_flux_1_dev_1.0_q8p.ckpt",
    "controlnet_union_pro_flux_1_dev_2.0_q8p.ckpt",
    "controlnet_union_pro_flux_1_dev_1.0_q5p.ckpt",
    "controlnet_union_pro_flux_1_dev_2.0_q5p.ckpt",
)
MODELS = ("flux_1_dev_f16.ckpt", "flux_1_dev_q5p.ckpt", "flux_1_dev_q8p.ckpt")

# community-models controlnets/union-pro-flux-1-dev-{1.0,2.0}/metadata.json (same revision as above). Draw Things builds
# its control specification mapping from the request's MetadataOverride.controlNets, so the server applies a community
# control only when that specification is sent with the request.
_UNION_SPECS = {
    "1.0": ("Union Pro (FLUX.1 [dev], ControlNet)", "controlnetunion", [5, 10]),
    "2.0": ("Union Pro 2.0 (FLUX.1 [dev], ControlNet)", "controlnet", [6, 0]),
}


def control_spec(checkpoint: str) -> dict:
    """Community specification of one pinned Union Pro control, as the Draw Things override payload expects it."""
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

    def __post_init__(self):
        if self.checkpoint not in CONTROLS or self.input_type != "pose":
            raise ValidationError("Only the two pinned Union pose controls are offered by this experiment.")
        # A conservative POC subset of the native weight control, not denoising strength.
        if type(self.strength) not in (int, float) or not math.isfinite(self.strength) or not 0 < self.strength <= 2:
            raise ValidationError("Pose strength must be finite and greater than 0, at most 2; omit control for baseline.")
        from dtflow.core.models import validate_metadata

        validate_metadata(self.evidence)

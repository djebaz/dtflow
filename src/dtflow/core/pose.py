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
)
MODELS = ("flux_1_dev_f16.ckpt", "flux_1_dev_q5p.ckpt", "flux_1_dev_q8p.ckpt")


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

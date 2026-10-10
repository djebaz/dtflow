"""Worker-facing durable image input boundary, without UI or provider access."""

from pathlib import Path

from dtflow.core.image_inputs import ImageInputSnapshot, PreparedImageInputs
from dtflow.persistence.image_inputs import load_input_snapshot, save_input_snapshot
from dtflow.services.image_preparation import validate_prepared_inputs


def snapshot_image_inputs(
    state: Path, attempt_id: str, prepared: PreparedImageInputs, *, preserve_outside: bool = True
) -> ImageInputSnapshot:
    """Validate and exclusively commit a prepared set under a fresh attempt ID.

    Call on an owned worker while holding the application's durable dispatch
    ownership. This does not create a generation attempt or dispatch anything.
    Inpainting defaults to compositing; img2img records preservation as false.
    """
    snapshot = ImageInputSnapshot.from_prepared(attempt_id, prepared, preserve_outside=preserve_outside)
    validate_prepared_inputs(prepared)
    return save_input_snapshot(state, snapshot, prepared)


def restore_image_inputs(state: Path, snapshot: ImageInputSnapshot) -> PreparedImageInputs:
    """Read immutable managed inputs, then verify grids/polarity/fit before use."""
    prepared = load_input_snapshot(state, snapshot)
    validate_prepared_inputs(prepared)
    return prepared

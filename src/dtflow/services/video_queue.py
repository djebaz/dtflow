"""Local-only video queue admission and durable source capture."""

from dataclasses import asdict, replace
from pathlib import Path

from dtflow.core.models import ResolvedGenerationRequest, ValidationError
from dtflow.core.request_records import request_from_record
from dtflow.core.video import check_request_fields, is_video_model
from dtflow.persistence.video_inputs import retain_source, source_bytes


def validate_video(request: ResolvedGenerationRequest, backend: str) -> None:
    if request.num_frames is None:
        if is_video_model(request.model_id):
            raise ValidationError("Capture video Duration from Generate before creating a video queue.")
        return
    if backend != "cloud":
        raise ValidationError("Video queues require DT+ Cloud; no provider was changed.")
    if request.prepared_assets or request.image_inputs is not None:
        raise ValidationError("Video requests cannot carry LoRA receipts or image-edit/pose snapshots.")
    request_from_record(asdict(request))
    check_request_fields(request.model_id, request.num_frames, request.start_image)
    if request.width_px * request.height_px > 2048 * 2048:
        raise ValidationError("Video output is limited to 4,194,304 pixels per frame.")


def check_source(request: ResolvedGenerationRequest) -> bytes | None:
    """Same original bytes/orientation as Generate's wire; pixel decoding stays off Qt."""
    if request.start_image is None:
        return None
    from dtflow.services.image_preparation import validate_video_source_bytes

    data = source_bytes(Path(request.start_image), request.start_image_sha256)
    validate_video_source_bytes(data)
    return data


def capture_requests(state: Path, backend: str, requests, composer_source):
    """Retain each distinct source once, and bind authoring evidence to the same durable content."""
    for request in requests:
        validate_video(request, backend)
    retained = {}
    captured = []
    for request in requests:
        if request.num_frames is not None and request.start_image is not None:
            key = request.start_image, request.start_image_sha256
            if key not in retained:
                data = check_source(request)
                retained[key] = retain_source(state, data, request.start_image_sha256)
            request = replace(request, start_image=retained[key])
        captured.append(request)
    if composer_source is not None and retained:
        base = dict(composer_source["base"])
        key = base.get("start_image"), base.get("start_image_sha256")
        if key in retained:
            base["start_image"] = retained[key]
            composer_source = {**composer_source, "base": base}
    return captured, composer_source

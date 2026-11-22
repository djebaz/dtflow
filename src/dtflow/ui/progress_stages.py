"""Labels for observed events only; no provider calls or inferred completion."""

from dtflow.core.models import BackendEvent


def event_stage(event: BackendEvent) -> str:
    if event.kind == "preparing":
        return {
            "retrying": "Retrying automatically in a few seconds",
            "binding_assets": "Checking prepared asset references",
            "opening_session": "Opening connection",
            "session_ready": "Connection ready",
            "validating_image_inputs": "Validating image inputs",
            "validating_request": "Validating generation request",
            "validating_assets": "Validating prepared assets",
            "restoring_assets": "Restoring LoRA on DT+",
            "checking_loras": "Checking LoRA compatibility",
            "checking_output_storage": "Checking output storage",
            "creating_attempt": "Creating generation attempt",
            "waiting_buffer_budget": "Waiting for memory budget",
            "waiting_video_resources": "Waiting for local video resources",
            "video_preparing": "Preparing video",
            "saving_image_snapshot": "Saving image input snapshot",
            "recording_submission_state": "Recording submission state",
            "waiting_backend_response": "Waiting for the provider's reply",
            "backend_response_received": "Request accepted; waiting for first progress",
            "resolving_model": "Looking up model details",
            "preparing_image_inputs": "Preparing image inputs",
            "resolving_credentials": "Checking credentials",
            "encoding_request": "Encoding generation request",
            "wire_validated": "Request validated",
            "waiting_execution_lane": "Waiting for a free slot",
            "waiting_provider_slot": "Waiting for a free slot",
            "connecting_backend": "Connecting",
            "preparing_grpc_metadata": "Preparing request headers",
        }.get(event.stage, "Preparing request")
    if event.kind == "submission_possible":
        return "Sending request; waiting for response"
    if event.kind == "terminal":
        return "Finished; checking and saving output"
    if event.kind == "progress":
        return "Generating" if event.stage == "sampling" else (event.stage or "Progress").replace("_", " ")
    return ""

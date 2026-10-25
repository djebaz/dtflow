"""Labels for observed events only; no provider calls or inferred completion."""


def event_stage(event):
    if event.kind == "preparing":
        return {
            "binding_assets": "Checking prepared asset references",
            "opening_session": "Opening backend session",
            "session_ready": "Backend session ready",
            "validating_image_inputs": "Validating image inputs",
            "validating_request": "Validating generation request",
            "validating_assets": "Validating prepared assets",
            "checking_loras": "Checking LoRA compatibility",
            "checking_output_storage": "Checking output storage",
            "creating_attempt": "Creating generation attempt",
            "waiting_buffer_budget": "Waiting for memory budget",
            "saving_image_snapshot": "Saving image input snapshot",
            "recording_submission_state": "Recording submission state",
            "waiting_backend_response": "Request sent; waiting for backend response",
            "backend_response_received": "Backend response received",
            "resolving_model": "Resolving model description",
            "preparing_image_inputs": "Preparing image inputs",
            "resolving_credentials": "Resolving backend credentials",
            "encoding_request": "Encoding generation request",
            "wire_validated": "Request validated",
            "waiting_execution_lane": "Waiting for execution slot",
            "connecting_backend": "Connecting to backend",
            "preparing_grpc_metadata": "Preparing gRPC metadata",
        }.get(event.stage, "Preparing request")
    if event.kind == "submission_possible":
        return "Submitting request / waiting for backend"
    if event.kind == "terminal":
        return "Backend finished; validating and saving output"
    if event.kind == "progress":
        return "Generating" if event.stage == "sampling" else (event.stage or "Backend progress").replace("_", " ")
    return ""

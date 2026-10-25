"""Labels for observed events only; no provider calls or inferred completion."""


def event_stage(event):
    if event.kind == "preparing":
        return {
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

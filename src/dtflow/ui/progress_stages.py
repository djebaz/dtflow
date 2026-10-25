"""Labels for observed events only; no provider calls or inferred completion."""


def event_stage(event):
    if event.kind == "preparing":
        return "Request validated" if event.stage == "wire_validated" else "Preparing request"
    if event.kind == "submission_possible":
        return "Submitting request / waiting for backend"
    if event.kind == "terminal":
        return "Backend finished; validating and saving output"
    if event.kind == "progress":
        return "Generating" if event.stage == "sampling" else (event.stage or "Backend progress").replace("_", " ")
    return ""

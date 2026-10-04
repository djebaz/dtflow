"""Local live-check diagnosis: separate "app not running" and model-browsing hints from real failures."""

import pytest

REFUSED = "ConnectionRefusedError"


UNREACHABLE_FIX = (
    "Start Draw Things and enable its API server, or check DTFLOW_LIVE_HOST/DTFLOW_LIVE_PORT and the firewall."
)
BROWSING_FIX = "In the Draw Things app turn on 'Enable Model Browsing', then rerun the live checks."


def warn_live(request, message: str, fix: str | None = None) -> None:
    """Retain a warning (and how to resolve it) on this check; the terminal summary prints it in color."""
    request.node.user_properties.append(("warning", message))
    if fix:
        request.node.user_properties.append(("fix", fix))


def unreachable_reason(code: str | None, kind: str | None, target: str) -> str | None:
    """Return a warning for "no local server answered", or None for any other failure.

    A refused connection means nothing is listening (app closed or its API server disabled). A connect deadline is
    the same condition on some platforms, or a wrong IP/firewall. Other network errors (routing, DNS, reset) are not
    treated as "app not launched" and stay failures.
    """
    if code == "connect_failed" and kind == REFUSED:
        return (
            f"Nothing is listening at {target} (connection refused): Draw Things is not running or its API server "
            "is off. Launch the app and enable its API server."
        )
    if code == "connect_deadline":
        return (
            f"No Draw Things response from {target} before the connect deadline: app not launched, wrong IP/port "
            "or a firewall."
        )
    return None


def skip_if_local_unreachable(request, code: str | None, kind: str | None, target: str) -> None:
    reason = unreachable_reason(code, kind, target)
    if reason is not None:
        warn_live(request, reason, UNREACHABLE_FIX)
        pytest.skip(reason)


def warn_if_model_browsing_off(request, snapshot) -> None:
    """Echo lists no files when "Enable Model Browsing" is off; an empty model folder looks the same."""
    if dict(snapshot.diagnostics).get("files") == "0":
        warn_live(
            request,
            "Echo returned no model files: 'Enable Model Browsing' is probably off in Draw Things, so local file "
            "visibility and LoRA SHA-256 checks may be incomplete.",
            BROWSING_FIX,
        )


def connect_hint(kind: str | None) -> str:
    """Extra guidance for failures that must stay failures but have a likely local cause."""
    if kind == "ConnectionResetError":
        return " The server reset the connection during connect: check that TLS is enabled in the Draw Things app."
    return ""


def fail_local(target: str, code: str | None, kind: str | None) -> None:
    """Fail a live local check with a short, actionable message instead of a deep traceback."""
    detail = f"{code or 'unknown'}" + (f" ({kind})" if kind else "")
    pytest.fail(f"Local Draw Things check failed at {target}: {detail}.{connect_hint(kind)}", pytrace=False)

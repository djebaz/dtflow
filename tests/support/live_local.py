"""Local live-check diagnosis: separate "app not running" and model-browsing hints from real failures."""

import warnings

import pytest

REFUSED = "ConnectionRefusedError"


def warn_live(request, message: str) -> None:
    """Emit a pytest warning and retain it in this check's ``results.json`` entry."""
    request.node.user_properties.append(("warning", message))
    warnings.warn(message, UserWarning, stacklevel=2)


def unreachable_reason(code: str | None, kind: str | None, target: str) -> str | None:
    """Return a warning for "no local server answered", or None for any other failure.

    A refused connection means nothing is listening (app closed or its API server disabled) A connect deadline is
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
        warn_live(request, reason)
        pytest.skip(reason)


def warn_if_model_browsing_off(request, snapshot) -> None:
    """Echo lists no files when "Enable Model Browsing" is off; an empty model folder looks the same."""
    if dict(snapshot.diagnostics).get("files") == "0":
        warn_live(
            request,
            "Echo returned no model files: 'Enable Model Browsing' is probably off in Draw Things, so local file "
            "visibility and LoRA SHA-256 checks may be incomplete.",
        )


def connect_hint(kind: str | None) -> str:
    """Extra guidance for failures that must stay failures but have a likely local cause."""
    if kind == "ConnectionResetError":
        return " The server reset the connection during connect: check that TLS is enabled in the Draw Things app."
    return ""

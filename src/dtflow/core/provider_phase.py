"""The provider-phase boundary an adapter reports to the service that admitted its call.

A generation holds a provider-capacity slot only while DT+ samples it. The service binds one idempotent callback for the
duration of ``backend.generate``; an adapter calls :func:`provider_done` once its own stream has ended (all output
received, video stream closed) so the slot is freed before local decoding, encoding and saving. Sampling progress seen by
the service frees it earlier. Without a bound callback the call does nothing, so direct callers and fake adapters are
unaffected. This is an in-process lifecycle hook, not an event: it is never recorded, logged or shown.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# Open calls (provider phase plus download/decode/save) allowed per provider slot, and so the cloud adapter lane size.
OVERLAP = 2

_DONE: ContextVar[Callable[[], None] | None] = ContextVar("dtflow_provider_done", default=None)


@contextmanager
def bound(callback: Callable[[], None] | None) -> Iterator[None]:
    """Make ``callback`` the call's provider-done hook while the adapter runs."""
    token = _DONE.set(callback)
    try:
        yield
    finally:
        _DONE.reset(token)


def provider_done() -> None:
    """The provider has finished sending; only local work remains for this call."""
    callback = _DONE.get()
    if callback is not None:
        callback()

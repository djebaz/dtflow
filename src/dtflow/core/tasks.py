"""Owned worker completion across cancellation."""

import asyncio


async def settle(awaitable):
    """Wait for already-started work before propagating caller cancellation."""
    task = asyncio.ensure_future(awaitable)
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            interrupted = True
        except Exception as exc:
            if interrupted:
                raise asyncio.CancelledError from exc
            raise
    if interrupted:
        raise asyncio.CancelledError
    return result

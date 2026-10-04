"""Admission budget for owned generation/codec buffers, not process RSS."""

import asyncio


class BufferBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._condition = asyncio.Condition()

    async def acquire(self, amount: int) -> None:
        if amount > self.limit:
            raise ValueError("Request exceeds the buffer admission budget.")
        async with self._condition:
            await self._condition.wait_for(lambda: self.used + amount <= self.limit)
            self.used += amount

    async def release(self, amount: int) -> None:
        async with self._condition:
            self.used -= amount
            self._condition.notify_all()

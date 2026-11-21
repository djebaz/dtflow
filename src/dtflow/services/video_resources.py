"""Process-local admission for owned video pipelines; bounds are policy, not measured process RSS."""

import asyncio
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dtflow.core.models import MAX_QUEUE_CONCURRENCY, BackendError, BackendFailure, ErrorCategory, ValidationError
from dtflow.core.tasks import settle

MIB = 1024 * 1024
TENSOR_LIMIT = 64 * MIB
AUDIO_LIMIT = 64 * MIB
MP4_LIMIT = 256 * MIB
# Encoder MP4 + muxed MP4 + bounded raw audio, with 24 MiB for container/packet overhead.
SCRATCH_RESERVE = 2 * MP4_LIMIT + AUDIO_LIMIT + 24 * MIB
# Final MP4 (256 MiB), poster (at most 32 MiB), and bounded sidecar/commit overhead.
OUTPUT_RESERVE = 300 * MIB
DISK_HEADROOM = 256 * MIB
MEMORY_BUDGET = 4 * 1024 * MIB
_lock = threading.Lock()
_active: dict["VideoLease", tuple[int, dict[int, tuple[Path, int]]]] = {}


class VideoLease:
    def __init__(self, request, output: Path):
        self.request, self.output = request, Path(output)
        # Conservative admission accounting for frame conversion, bounded payload queues, audio and poster.
        # External SDK/FFmpeg allocations and whole-process RSS are not certified by this estimate.
        self.memory = 192 * MIB + request.width_px * request.height_px * 3 * 16

    def _disks(self):
        self.output.mkdir(parents=True, exist_ok=True)
        disks = {}
        for path, amount in ((Path(tempfile.gettempdir()), SCRATCH_RESERVE), (self.output, OUTPUT_RESERVE)):
            volume = path.stat().st_dev
            previous = disks.get(volume, (path, 0))
            disks[volume] = path, previous[1] + amount
        return disks

    def _check_disks(self, disks, *, wait_for_reserved: bool = False, include_peers: bool = True) -> bool:
        waiting = False
        for volume, (path, amount) in disks.items():
            reserved = (
                sum(held.get(volume, (path, 0))[1] for owner, (_, held) in _active.items() if owner is not self)
                if include_peers
                else 0
            )
            if shutil.disk_usage(path).free < reserved + amount + DISK_HEADROOM:
                if wait_for_reserved and reserved:
                    # Another admitted clip can release its temporary files/reservation. Keep this item
                    # unsubmitted, cancellable and outside _active until capacity is available.
                    waiting = True
                    continue
                raise ValidationError(
                    "Insufficient free storage for this video and the other admitted clips. Nothing was submitted."
                )
        return not waiting

    def acquire(self) -> bool:
        with _lock:
            if (
                len(_active) >= MAX_QUEUE_CONCURRENCY
                or sum(value[0] for value in _active.values()) + self.memory > MEMORY_BUDGET
            ):
                return False
            disks = self._disks()
            if not self._check_disks(disks, wait_for_reserved=True):
                return False
            _active[self] = self.memory, disks
            return True

    def recheck(self):
        with _lock:
            # Admission already accounted for peers. Their materialized files now reduce free space;
            # charging their full reservations again would double-count and reject admitted clips.
            # Keep this clip's complete budget plus headroom: a conservative bound on its remaining need.
            self._check_disks(_active[self][1], include_peers=False)

    def release(self):
        with _lock:
            _active.pop(self, None)


def recheck_video_storage(request_id: str) -> None:
    """Revalidate a service-owned reservation after backend connection/auth/lane awaits, before submission."""
    with _lock:
        for owner, (_, disks) in _active.items():
            if owner.request.request_id == request_id:
                owner._check_disks(disks, include_peers=False)
                return


@asynccontextmanager
async def video_admission(request, output):
    lease = VideoLease(request, output)
    if request.width_px * request.height_px > 2048 * 2048 or lease.memory > MEMORY_BUDGET:
        raise BackendError(
            BackendFailure(
                ErrorCategory.RESOURCE_LIMIT,
                "video_memory_limit",
                "Video exceeds the bounded frame/payload policy; nothing was submitted.",
                request.request_id,
            )
        )
    try:
        try:
            # The lease object exists before the worker starts; cancellation cannot lose an acquired reservation.
            while not await settle(asyncio.to_thread(lease.acquire)):
                await asyncio.sleep(0.1)
        except (OSError, ValueError) as exc:
            raise BackendError(
                BackendFailure(
                    ErrorCategory.RESOURCE_LIMIT,
                    "video_storage_limit",
                    "Insufficient or unavailable video storage for this clip and existing reservations. Nothing was submitted.",
                    request.request_id,
                )
            ) from exc
        yield lease
    finally:
        lease.release()

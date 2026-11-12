"""Shared Qt glue for user-confirmed detection-model downloads (Generate's editor and the Workflows page, #223).

One inline confirmation lists every missing catalogue entry with its licence (D7); the original action button turns
into Confirm and Cancel lives in the shared overlay. The download runs ``model_provisioning.download_models`` on the
runner's worker with truthful progress in the shared activity bar and a Cancel action. No native dialogs.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.persistence.person_models import PersonModels, load_person_models
from dtflow.services.model_provisioning import CatalogueEntry, DownloadCancelled, ProvisionProgress, download_models
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.inline_feedback import confirm_action
from dtflow.ui.latest_relay import LatestRelay

KEY = "models-download"
TITLE = "Download detection models"
_MIB = 1024 * 1024


def confirmation_text(entries: Sequence[CatalogueEntry]) -> str:
    """The single preflight text: what is downloaded, from where, its size and licence."""
    total = sum(entry.size_mib for entry in entries)
    lines = [
        f"DTFlow will download {len(entries)} model file{'s' if len(entries) != 1 else ''} (about {total:.0f} MiB) "
        "directly from each publisher into its models folder, check each SHA-256 and model contract, and use them "
        "for local detection. DTFlow does not redistribute these models; their licences apply:",
    ]
    for entry in entries:
        lines.append(
            f"• {entry.name} (about {entry.size_mib:.1f} MiB) · Licence: {entry.licence} · {entry.licence_url}"
        )
    lines.append("Your own model files chosen with Models… are kept.")
    return "\n".join(lines)


def _progress_detail(progress: ProvisionProgress, entries: Sequence[CatalogueEntry]):
    roles = [entry.role for entry in entries]
    position = (
        f" ({roles.index(progress.role) + 1} of {len(roles)})" if progress.role in roles and len(roles) > 1 else ""
    )
    stage = progress.stage + position
    if progress.total:
        percent = min(100, progress.done * 100 // progress.total)
        amount = f"{progress.done / _MIB:.1f} / {progress.total / _MIB:.1f} MiB"
        return percent, 100, f"{stage} · {amount}", f"{percent}%"
    if progress.done:
        return 0, 0, f"{stage} · {progress.done / _MIB:.1f} MiB", None
    return 0, 0, stage, None


def is_downloading(page) -> bool:
    return bool(getattr(page.window(), "_model_download_running", False))


def confirm_and_download(
    parent_page,
    state_dir: Path,
    entries: Sequence[CatalogueEntry],
    runner,
    on_done: Callable[[PersonModels | None, Exception | None], None],
    *,
    button,
) -> None:
    """Arm one inline confirmation on ``button`` listing ``entries`` with their licences; on Confirm, download them.

    ``on_done(models, error)`` runs on the GUI thread after the run ends: ``models`` is the freshly reloaded
    ``person_models.json`` (also after a failure or cancel, since earlier entries may have been installed; None when
    it cannot be read) and ``error`` is None, a ``DownloadCancelled`` or another exception. Nothing starts when
    ``entries`` is empty, another download is running or the runner is closed."""
    entries = tuple(entries)
    if not entries or runner.closed or is_downloading(parent_page):
        return
    confirm_action(
        parent_page,
        button,
        TITLE,
        confirmation_text(entries),
        lambda: start_download(parent_page, state_dir, entries, runner, on_done),
    )


def start_download(
    parent_page,
    state_dir: Path,
    entries: Sequence[CatalogueEntry],
    runner,
    on_done: Callable[[PersonModels | None, Exception | None], None],
) -> None:
    """Run the already-confirmed download now (the confirmation step is ``confirm_and_download``)."""
    entries = tuple(entries)
    window = parent_page.window()
    if not entries or runner.closed or is_downloading(parent_page):
        return
    window._model_download_running = True
    cancel = threading.Event()
    strip = activity_strip(parent_page)

    def show(progress: ProvisionProgress):
        if strip is not None:
            done, total, detail, count = _progress_detail(progress, entries)
            strip.update(KEY, done, total, detail=detail, count=count)

    relay = LatestRelay(show, parent_page, cancel)
    runner.shutdown_started.connect(relay.cancel_work)
    if strip is not None:
        strip.start(KEY, TITLE, stop=cancel.set, stop_text="Cancel download", action_text="Cancel")

    def worker():
        error = None
        try:
            download_models(Path(state_dir), entries, on_progress=relay, cancel=cancel)
        except Exception as exc:  # reported to the GUI; the reload below still runs
            error = exc
        try:
            models = load_person_models(Path(state_dir))
        except ValidationError:
            models = None
        return models, error

    async def work():
        return await asyncio.to_thread(worker)

    def done(value, failure):
        relay.close()
        relay.deleteLater()
        window._model_download_running = False
        models, error = value if failure is None else (None, failure)
        if strip is not None:
            if error is None:
                count = len(entries)
                strip.complete(
                    KEY, TITLE, f"Installed {count} model{'s' if count != 1 else ''}; SHA-256 and contract verified."
                )
            elif isinstance(error, (DownloadCancelled, asyncio.CancelledError)):
                strip.complete(KEY, "Download stopped", str(error) or "Download cancelled.", "stopped")
            else:
                strip.complete(KEY, TITLE, str(error) or "The download failed.", "error")
        on_done(models, error)

    runner.submit(work(), done)

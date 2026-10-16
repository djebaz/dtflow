"""One submitted-job orchestrator with early run evidence, state, and delivery."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from .config import JobConfig, atomic_write_json, submitted_job as make_submitted_job, utc_now
from .logging_utils import attach_run_log, configure_global_logger, runtime_log_path
from .paths import ProjectPaths
from .results import allocate_output_path, create_minimal_bundle
from .runtime import is_colab_runtime, prepare_runtime


def ensure_persistent_storage(persistent_root: Path, logger) -> None:
    if is_colab_runtime() and str(persistent_root).startswith("/content/"):
        if not str(persistent_root).startswith("/content/drive/MyDrive/"):
            raise ValueError(
                "Colab persistent_root under /content must be Drive-backed; "
                "use /content/drive/MyDrive/..."
            )
        drive_root = Path("/content/drive")
        if not (drive_root / "MyDrive").is_dir():
            logger.info("STAGE START: MOUNT_PERSISTENT_STORAGE")
            from google.colab import drive  # type: ignore[import-not-found]

            drive.mount(str(drive_root))
            logger.info("STAGE COMPLETE: MOUNT_PERSISTENT_STORAGE")
    persistent_root.mkdir(parents=True, exist_ok=True)


def _sync_runtime_log(global_log: Path, paths: ProjectPaths) -> Path:
    destination = paths.persistent_log_root / global_log.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(global_log, destination)
    return destination


def _copy_final_output(output_path: Path, paths: ProjectPaths, run_id: str) -> Path:
    destination = paths.exports_root / run_id / output_path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output_path, destination)
    companion = output_path.with_suffix(".conversion.json")
    if companion.is_file():
        shutil.copy2(companion, destination.with_suffix(".conversion.json"))
    return destination


def _seed_run_log(run_log: Path) -> None:
    bootstrap = os.environ.get("COLAB_SUBMISSION_BOOTSTRAP_LOG")
    run_log.parent.mkdir(parents=True, exist_ok=True)
    if bootstrap and Path(bootstrap).is_file():
        shutil.copy2(bootstrap, run_log)
    else:
        run_log.touch()


def _sync_run_evidence(
    runtime_run_log: Path,
    runtime_manifest: Path,
    runtime_submitted_job: Path,
    persistent_run_root: Path,
) -> tuple[Path, Path, Path]:
    persistent_run_log = persistent_run_root / "logs" / "full_run.log"
    persistent_manifest = persistent_run_root / "run_manifest.json"
    persistent_submitted_job = persistent_run_root / "submitted_job.json"
    persistent_run_log.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runtime_run_log, persistent_run_log)
    shutil.copy2(runtime_manifest, persistent_manifest)
    shutil.copy2(runtime_submitted_job, persistent_submitted_job)
    return persistent_run_log, persistent_manifest, persistent_submitted_job


def _write_persistent_locator(config: JobConfig) -> Path | None:
    if not str(config.persistent_root).startswith("/content/drive/MyDrive/"):
        return None
    registry = Path("/content/drive/MyDrive/production_colab_registry") / (
        config.project_id + ".json"
    )
    atomic_write_json(
        registry,
        {
            "project_id": config.project_id,
            "persistent_root": str(Path(config.persistent_root).expanduser()),
            "updated_utc": utc_now(),
        },
    )
    return registry


def run_complete_job(
    config: JobConfig,
    project_root: Path,
    *,
    submitted_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config.validate()
    frozen_job = submitted_job or make_submitted_job(config)
    runtime_root = Path(os.environ.get("COLAB_RUNTIME_ROOT", config.runtime_root)).expanduser()
    runtime_root.mkdir(parents=True, exist_ok=True)
    global_log = runtime_log_path(runtime_root / "logs")
    logger = configure_global_logger(global_log)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    provisional_root = runtime_root / "active_runs" / run_id
    run_log = provisional_root / "logs" / "full_run.log"
    manifest = provisional_root / "run_manifest.json"
    submitted_path = provisional_root / "submitted_job.json"
    _seed_run_log(run_log)
    run_handler = attach_run_log(logger, run_log)

    state: dict[str, Any] = {
        "run_id": run_id,
        "submission_id": frozen_job["submission_id"],
        "status": "SUBMITTED",
        "submitted_utc": frozen_job["submitted_utc"],
        "configuration": asdict(config),
        "resolved_project_revision": os.environ.get(
            "COLAB_PROJECT_RESOLVED_REVISION", config.project_revision
        ),
        "submitted_project_revision": frozen_job["submitted_project_revision"],
        "global_log": str(global_log),
        "run_log": str(run_log),
        "submitted_job_path": str(submitted_path),
        "bootstrap_log": os.environ.get("COLAB_SUBMISSION_BOOTSTRAP_LOG", ""),
    }
    atomic_write_json(submitted_path, frozen_job)
    atomic_write_json(manifest, state)
    paths: ProjectPaths | None = None
    persistent_run_root: Path | None = None
    persistent_run_log: Path | None = None
    persistent_manifest: Path | None = None
    persistent_submitted_path: Path | None = None

    def checkpoint() -> None:
        nonlocal persistent_run_log, persistent_manifest, persistent_submitted_path
        atomic_write_json(manifest, state)
        if persistent_run_root is not None:
            (
                persistent_run_log,
                persistent_manifest,
                persistent_submitted_path,
            ) = _sync_run_evidence(run_log, manifest, submitted_path, persistent_run_root)

    try:
        logger.info("RUN START: %s", run_id)
        state["status"] = "RESTORING_PERSISTENT_STATE"
        checkpoint()
        persistent_root = Path(
            os.environ.get("COLAB_PERSISTENT_ROOT", config.persistent_root)
        ).expanduser()
        ensure_persistent_storage(persistent_root, logger)
        paths = ProjectPaths.create(project_root, config)
        locator = _write_persistent_locator(config)

        persistent_run_root = paths.runs_root / run_id
        state.update(
            {
                "runtime_run_log": str(run_log),
                "run_log": str(persistent_run_root / "logs" / "full_run.log"),
                "submitted_job_path": str(persistent_run_root / "submitted_job.json"),
                "persistent_locator": str(locator) if locator else "",
            }
        )
        checkpoint()
        atomic_write_json(paths.state_root / "pending_job.json", frozen_job)

        state["status"] = "PREPARING_RUNTIME"
        checkpoint()
        state["runtime"] = prepare_runtime(paths, config, logger)
        checkpoint()

        state["status"] = "VALIDATING"
        checkpoint()
        if not config.input_url:
            raise ValueError("Set input_url to a Civitai LoRA URL in the configuration form")

        state["status"] = "PROCESSING"
        checkpoint()
        # Import domain code only after the missing dependency delta is installed.
        from .pipeline import execute_pipeline

        output_path = allocate_output_path(paths.output_root, config.output_name)
        conversion, cache_event = execute_pipeline(
            config,
            output_path,
            cache_root=paths.cache_root,
            logger=logger,
        )
        state["cache"] = cache_event
        state["conversion"] = conversion

        # Save the final output first; persistence and optional browser delivery follow.
        persistent_output = _copy_final_output(output_path, paths, run_id)
        logger.info("RUN COMPLETE: output=%s", output_path)
        persistent_global_log = _sync_runtime_log(global_log, paths)
        state.update(
            {
                "status": "COMPLETE",
                "completed_utc": utc_now(),
                "output_path": str(output_path),
                "persistent_output_path": str(persistent_output),
                "persistent_global_log": str(persistent_global_log),
            }
        )
        checkpoint()
        assert (
            persistent_run_log is not None
            and persistent_manifest is not None
            and persistent_submitted_path is not None
        )
        minimal_bundle = None
        persistent_minimal_bundle = None
        checkpoint()
        _sync_runtime_log(global_log, paths)
        assert persistent_run_log is not None and persistent_manifest is not None
        result = {
            "run_id": run_id,
            "status": "COMPLETE",
            "output_path": str(output_path),
            "persistent_output_path": str(persistent_output),
            "manifest_path": str(persistent_manifest),
            "run_log": str(persistent_run_log),
            "runtime_run_log": str(run_log),
            "global_log": str(global_log),
        }
        if minimal_bundle is not None and persistent_minimal_bundle is not None:
            result.update(
                {
                    "minimal_bundle_path": str(minimal_bundle),
                    "persistent_minimal_bundle_path": str(persistent_minimal_bundle),
                }
            )
        return result
    except KeyboardInterrupt:
        state.update({"status": "INTERRUPTED", "updated_utc": utc_now()})
        logger.exception("RUN INTERRUPTED")
        checkpoint()
        if paths is not None:
            _sync_runtime_log(global_log, paths)
        raise
    except Exception as exc:
        state.update({"status": "FAILED", "updated_utc": utc_now(), "error": repr(exc)})
        logger.exception("RUN FAILED")
        checkpoint()
        if paths is not None:
            _sync_runtime_log(global_log, paths)
        raise
    finally:
        logger.removeHandler(run_handler)
        run_handler.close()


def list_previous_runs(config: JobConfig, project_root: Path, *, limit: int = 5) -> list[dict[str, str]]:
    paths = ProjectPaths.create(project_root, config)
    records = []
    for manifest in sorted(paths.runs_root.glob("*/run_manifest.json"), reverse=True)[:limit]:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            records.append({"run_id": payload["run_id"], "status": payload["status"]})
        except (OSError, ValueError, KeyError):
            records.append({"run_id": manifest.parent.name, "status": "UNREADABLE"})
    return records

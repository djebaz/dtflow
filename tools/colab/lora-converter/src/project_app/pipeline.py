"""Download, convert and persist one Civitai LoRA; no generation or training."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from .cache import JsonStageCache, content_fingerprint
from .config import atomic_write_json
from .converter import UPSTREAM_REVISION, BUILD_REVISION, SOURCE_PATCH_REVISION, prepare_converter, convert, validate_checkpoint
from .downloads import resolve_lora, download_lora


def _secret(name):
    if not name:
        return ""
    try:
        from google.colab import userdata
    except ImportError:
        return os.environ.get(name, "")
    try:
        return userdata.get(name)
    except userdata.SecretNotFoundError:
        return ""
    except Exception:
        raise RuntimeError("Colab secret access failed; allow notebook access or clear civitai_secret_name for a public download") from None


def execute_pipeline(config, output_path, *, cache_root, logger):
    token = _secret(config.civitai_secret_name)
    logger.info("STAGE START: RESOLVE_CIVITAI")
    metadata = resolve_lora(config.input_url, token)
    persistent = cache_root.parent
    public_metadata = {key: value for key, value in metadata.items() if key != "download_url"}
    atomic_write_json(persistent / "inputs" / f"{metadata['source_sha256']}.json", public_metadata)
    semantic_inputs = {
        "project_id": config.project_id,
        "project_revision": os.environ.get("COLAB_PROJECT_RESOLVED_REVISION", config.project_revision or "embedded-template"),
        "source_sha256": metadata["source_sha256"],
        "converter_revision": UPSTREAM_REVISION,
        "converter_source_patch": SOURCE_PATCH_REVISION,
        "build_revision": BUILD_REVISION,
        "model_version": config.model_version,
        "scale_factor": config.scale_factor,
    }
    stage_cache = JsonStageCache(cache_root, "lora_conversion", "official-importer-v1")
    lookup = stage_cache.load(semantic_inputs) if config.reuse_cache else None
    cached = None
    if lookup is not None and lookup.status == "hit":
        candidate = Path(lookup.value["checkpoint"])
        if candidate.is_file() and content_fingerprint(candidate) == lookup.value["sha256"]:
            validate_checkpoint(candidate)
            cached = candidate
    if cached:
        logger.info("CACHE HIT: official conversion checksum verified")
        cache_event = lookup.event()
        # A prior successful checkpoint is sufficient; do not rebuild Swift.
        shutil.copy2(cached, output_path)
    else:
        logger.info("CACHE MISS: conversion needs execution")
        source = download_lora(metadata, persistent / "inputs", token, logger)
        logger.info("STAGE START: PREPARE_CONVERTER")
        root = Path(config.runtime_root) / "converter" / (UPSTREAM_REVISION[:16] + "-" + BUILD_REVISION)
        binary = prepare_converter(root, config.build_jobs, logger)
        logger.info("STAGE START: CONVERT_LORA")
        convert(binary, source, output_path, config, logger)
        checkpoint_hash = content_fingerprint(output_path)
        if config.reuse_cache:
            # Save one reusable copy outside run directories. No additional weight ZIP.
            cache_checkpoint = cache_root / "checkpoints" / (checkpoint_hash + ".ckpt")
            cache_checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_checkpoint.with_suffix(".ckpt.tmp")
            shutil.copy2(output_path, temporary)
            temporary.replace(cache_checkpoint)
            value = {"checkpoint": str(cache_checkpoint), "sha256": checkpoint_hash}
            cache_event = stage_cache.save(semantic_inputs, value).event()
        else:
            cache_event = {"status": "disabled", "reason": "reuse_disabled", "key": stage_cache.key(semantic_inputs)}
    validate_checkpoint(output_path)
    record = {
        **public_metadata,
        "output_sha256": content_fingerprint(output_path),
        "output_bytes": output_path.stat().st_size,
        "converter_revision": UPSTREAM_REVISION,
        "converter_source_patch": SOURCE_PATCH_REVISION,
        "forced_model_version": config.model_version or None,
        "scale_factor": config.scale_factor,
        "cache": cache_event,
        "validation": "SQLite integrity only; runtime LoRA application is not verified",
    }
    atomic_write_json(output_path.with_suffix(".conversion.json"), record)
    logger.info("STAGE COMPLETE: CONVERT_LORA output=%s", output_path)
    return record, cache_event

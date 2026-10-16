"""Canonical configuration and immutable submitted-job serialization."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
from urllib.parse import urlsplit
import uuid

# Current canonical hosts and legacy aliases documented by Civitai's own source.
CIVITAI_HOSTS = frozenset({"civitai.com", "www.civitai.com", "civitai.red", "www.civitai.red", "civitai.green", "www.civitai.green", "civitai.buzz", "www.civitai.buzz"})


def civitai_origin(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in CIVITAI_HOSTS:
        raise ValueError("Use an HTTPS Civitai URL (.com, .red, .green or .buzz)")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Civitai URLs must not contain credentials or a custom port")
    host = parsed.hostname.removeprefix("www.")
    # Legacy domains moved to .com; preserve the .red API for .red inputs.
    return "https://" + ("civitai.red" if host == "civitai.red" else "civitai.com")


POLICY_REVISION = "production-colab-v4"

# Canonical presentation metadata for the generated Colab form. Defaults and
# Python types live only on JobConfig; the builder verifies that every field is
# presented exactly once.
FORM_SECTIONS = (
    (
        "Project source",
        (
            "project_id",
            "project_source_mode",
            "project_repository_url",
            "project_revision",
            "project_subdirectory",
            "project_source_path",
        ),
    ),
    ("Civitai LoRA", ("input_url", "civitai_secret_name")),
    ("Conversion", ("model_version", "scale_factor", "build_jobs")),
    ("Output", ("output_name", "auto_download")),
    ("Persistence and execution", ("persistent_root", "runtime_root", "reuse_cache")),
)

FORM_CONTROLS = {
    "project_id": None,
    "project_source_mode": '["embedded", "repository", "local"]',
    "project_repository_url": '{type:"string"}',
    "project_revision": '{type:"string"}',
    "project_subdirectory": '{type:"string"}',
    "project_source_path": '{type:"string"}',
    "input_url": '{type:"string"}',
    "civitai_secret_name": '{type:"string"}',
    "model_version": '{type:"string"}',
    "scale_factor": '{type:"number"}',
    "build_jobs": '{type:"integer"}',
    "output_name": '{type:"string"}',
    "auto_download": '{type:"boolean"}',
    "persistent_root": '{type:"string"}',
    "runtime_root": '{type:"string"}',
    "reuse_cache": '{type:"boolean"}',
}


@dataclass(frozen=True)
class JobConfig:
    project_id: str = 'lora-converter'
    project_source_mode: str = 'embedded'
    project_repository_url: str = ''
    project_revision: str = ''
    project_subdirectory: str = ''
    project_source_path: str = ""
    input_url: str = "https://civitai.red/api/download/models/3404818?fileId=3296039"
    civitai_secret_name: str = "CIVITAI_API_TOKEN"
    model_version: str = ""
    scale_factor: float = 1.0
    build_jobs: int = 1
    output_name: str = "civitai_3404818_3296039.ckpt"
    auto_download: bool = True
    persistent_root: str = '/content/drive/MyDrive/production_colab/lora-converter'
    runtime_root: str = '/content/production_colab/lora-converter'
    reuse_cache: bool = True

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.project_id):
            raise ValueError("project_id must use lowercase letters, numbers, and single hyphens")
        if self.project_source_mode not in {"embedded", "repository", "local"}:
            raise ValueError(f"Unsupported project_source_mode: {self.project_source_mode}")
        if not self.output_name.strip():
            raise ValueError("output_name must not be blank")
        if Path(self.output_name).name != self.output_name:
            raise ValueError("output_name must be a filename, not a nested path")
        if self.input_url:
            parsed = urlsplit(self.input_url)
            civitai_origin(self.input_url)
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("URL must not include credentials or a fragment")
            from urllib.parse import parse_qs
            if set(parse_qs(parsed.query)) - {"modelVersionId", "fileId", "type", "format", "size", "fp"}:
                raise ValueError("Remove URL tokens; use the named Colab secret for authentication")
        if self.output_name != Path(self.output_name).name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.ckpt", self.output_name):
            raise ValueError("output_name must be a safe filename ending in .ckpt")
        if not 1 <= self.build_jobs <= 4:
            raise ValueError("build_jobs must be between 1 and 4")
        if not 0 < self.scale_factor <= 10:
            raise ValueError("scale_factor must be greater than 0 and at most 10")
        if self.model_version and not re.fullmatch(r"[A-Za-z0-9_.-]+", self.model_version):
            raise ValueError("model_version must be an upstream Draw Things version identifier")
        if not self.runtime_root.strip() or not self.persistent_root.strip():
            raise ValueError("runtime_root and persistent_root must not be blank")
        persistent = Path(self.persistent_root).resolve()
        runtime = Path(self.runtime_root).resolve()
        roots_overlap = False
        for child, parent in ((persistent, runtime), (runtime, persistent)):
            try:
                child.relative_to(parent)
            except ValueError:
                continue
            roots_overlap = True
        if roots_overlap:
            raise ValueError("persistent_root and runtime_root must not overlap")

        repository = urlsplit(self.project_repository_url) if self.project_repository_url else None
        if repository and (repository.username or repository.password or repository.query):
            raise ValueError(
                "project_repository_url must not contain credentials or signed query strings"
            )
        if self.project_source_mode == "repository":
            if not self.project_repository_url:
                raise ValueError("Repository mode requires project_repository_url")
            if not re.fullmatch(r"[0-9a-fA-F]{40}", self.project_revision):
                raise ValueError("Repository mode requires an immutable 40-character commit SHA")
        if self.project_source_mode == "local" and (
            not self.project_source_path or self.project_revision != "local"
        ):
            raise ValueError("Local mode requires project_source_path and project_revision='local'")
        subdirectory = PurePosixPath(self.project_subdirectory or ".")
        if subdirectory.is_absolute() or ".." in subdirectory.parts:
            raise ValueError("project_subdirectory must be a safe relative path")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def submitted_job(config: JobConfig, *, submitted_utc: str | None = None) -> dict[str, Any]:
    config.validate()
    return {
        "policy_revision": POLICY_REVISION,
        "submission_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8],
        "submitted_utc": submitted_utc or utc_now(),
        "submitted_project_revision": (
            config.project_revision
            if config.project_source_mode != "embedded"
            else "embedded-template"
        ),
        "configuration": asdict(config),
    }


def save_pending_job(config: JobConfig, state_root: Path) -> Path:
    pending = state_root / "pending_job.json"
    atomic_write_json(pending, submitted_job(config))
    return pending


def load_pending_job(path: Path) -> tuple[JobConfig, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("policy_revision") != POLICY_REVISION:
        raise ValueError(
            f"Pending job policy {payload.get('policy_revision')!r} does not match {POLICY_REVISION!r}"
        )
    if (
        not payload.get("submission_id")
        or not payload.get("submitted_utc")
        or not payload.get("submitted_project_revision")
    ):
        raise ValueError("Pending job is missing immutable submission identity")
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise ValueError("Pending job has no configuration object")
    config = JobConfig(**configuration)
    config.validate()
    return config, payload

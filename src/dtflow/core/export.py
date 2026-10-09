"""Provider-neutral options and outcomes for copy-only saved-image export."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExportOptions:
    include_prompts: bool = True
    json_sidecars: bool = False
    separate_noise_blank: bool = True


@dataclass(frozen=True, slots=True)
class ExportResult:
    destination: str
    total: int
    exported: int
    failed: int
    cancelled: bool
    warnings: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    noise: int = 0
    blank: int = 0
    skipped: int = 0

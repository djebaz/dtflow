"""Provider-neutral options and outcomes for copy-only saved-image export."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExportOptions:
    include_prompts: bool = True
    json_sidecars: bool = False


@dataclass(frozen=True, slots=True)
class ExportResult:
    destination: str
    total: int
    exported: int
    failed: int
    cancelled: bool
    warnings: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()

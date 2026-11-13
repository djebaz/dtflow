"""SD 1.x family (table driven, mappings key ``sd1_unet``)."""

from __future__ import annotations

from dtflow.services.lora_convert.families.tables import convert_tables
from dtflow.services.lora_convert.registry import FamilySpec, register

register(
    FamilySpec(
        key="sd1",
        label="SD 1.x",
        exact_architecture="v1",
        status="verified",
        experimental=False,
        kind="tables",
        run=lambda st, request, ctx: convert_tables(st, "sd1", request.scale, request.ignore_unmatched, ctx),
        notes="Swift-compared on one file; generation effect confirmed on DT+ by the owner (2026-11-13)",
    )
)

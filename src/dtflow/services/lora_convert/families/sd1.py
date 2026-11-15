"""SD 1.x family (table driven, mappings key ``sd1_unet``)."""

from __future__ import annotations

from dtflow.services.lora_convert.families.tables import convert_tables
from dtflow.services.lora_convert.registry import FamilyCode, register

register(
    FamilyCode(
        key="sd1",
        kind="tables",
        run=lambda st, request, ctx: convert_tables(st, "sd1", request.scale, request.ignore_unmatched, ctx),
        notes="Swift-compared on one file; generation effect confirmed on DT+ by the owner (2026-11-13)",
    )
)

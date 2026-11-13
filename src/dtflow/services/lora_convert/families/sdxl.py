"""SDXL family (table driven). Experimental: converted files checked on DT+, but some SDXL LoRAs fail to convert."""

from __future__ import annotations

from dtflow.services.lora_convert.families.tables import convert_tables
from dtflow.services.lora_convert.registry import FamilySpec, register

register(
    FamilySpec(
        key="sdxl",
        label="SDXL",
        exact_architecture="sdxl_base_v0.9",
        status="experimental",
        experimental=True,
        kind="tables",
        run=lambda st, request, ctx: convert_tables(st, "sdxl", request.scale, request.ignore_unmatched, ctx),
        notes="Converted files verified on DT+ and against iOS output; some SDXL LoRAs fail to convert; requires allow_experimental",
    )
)

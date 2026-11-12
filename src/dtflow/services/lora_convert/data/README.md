# lora_mappings.json

SD 1.x and SDXL layer tables of Draw Things' `ModelMapping.swift`, extracted at the pinned
revision (`DT_REVISION` in `provenance.py`) by `tools/drawthings/extract_swift_mappings.py`:

    python tools/drawthings/extract_swift_mappings.py <ModelMapping.swift> src/dtflow/services/lora_convert/data/lora_mappings.json <revision>

Regenerate only when the pinned Draw Things revision changes, and bump `CONVERTER_VERSION` if the output bytes could change.

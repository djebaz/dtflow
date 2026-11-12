# lora_mappings.json

SD 1.x and SDXL layer tables of Draw Things' `ModelMapping.swift`, extracted at the pinned
revision (`DT_REVISION` in `provenance.py`) by `tools/drawthings/extract_swift_mappings.py`:

    python tools/drawthings/extract_swift_mappings.py <ModelMapping.swift> src/dtflow/services/lora_convert/data/lora_mappings.json <revision>

Regenerate only when the pinned Draw Things revision changes, and bump `CONVERTER_VERSION` if the output bytes could change.

## Canonical copy

`lora_mappings.json` in this folder is the canonical copy used by the converter. The copy in
`tools/drawthings/` is the frozen proof-of-concept input (history proof) and is intentionally left
untouched, so `tools/drawthings/extract_swift_mappings.py` still describes the old location; when it
is run, pass this folder's path as OUT.json and do not rely on the tools copy.

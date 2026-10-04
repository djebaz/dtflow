# Draw Things gRPC capability inventory

**Status:** Source-inspected contract plus bounded owner-run live evidence.  
**Upstream source inspected:** drawthingsai/draw-things-community at \`e51089d5229b3147e13a21cfe0c379deff851715\`.  
**Python client inspected:** drawthings-py at \`e459f75a430ce5bdd21ce80f70b19e846682f511\`.

## Purpose

Record what DTFlow can and cannot learn from the current Draw Things gRPC service without generation or mutation. This document is evidence for inventory and custom-LoRA planning; it is not a claim that every server build implements every optional field identically.

## Current service surface

The public \`ImageGenerationService\` contract exposes six RPCs:

| RPC | Read-only for this investigation | Probe policy | Information available |
| --- | --- | --- | --- |
| \`Echo\` | Yes | Called | message, file list, structured metadata override, shared-secret requirement, optional compute thresholds, server identifier |
| \`FilesExist\` | Yes | Called | per-file existence and optional SHA-256 for names requested in \`filesWithHash\` |
| \`Pubkey\` | Yes | Called | message and public-key string, if implemented by the target server |
| \`Hours\` | Yes | Called | compute threshold structure, if implemented by the target server |
| \`GenerateImage\` | No | Never called by this probe | generation stream; excluded because it can perform inference/remote work |
| \`UploadFile\` | No | Never called by this probe | file mutation/upload; excluded |

The current gRPC contract has no download-file RPC. DTFlow therefore cannot retrieve \`custom_lora.json\`, community catalog JSON, checkpoint bytes, or any other server-side file contents through these RPCs.

## Echo

The local server implementation can return:

- \`message\`
- \`files\`: non-empty top-level \`.ckpt\` files visible in the model directories when model browsing is enabled
- \`override.models\`
- \`override.loras\`
- \`override.controlNets\`
- \`override.textualInversions\`
- \`override.upscalers\`
- \`sharedSecretMissing\`
- optional \`thresholds\`
- \`serverIdentifier\`

The override categories are JSON encoded inside protobuf byte fields. The capability probe decodes the JSON directly and preserves every field rather than reducing it to DTFlow's current \`InventoryEntry\`.

### LoRA provenance limit

The local server serializes downloaded entries from \`LoRAZoo.availableSpecifications\`. Current upstream source builds that list from official \`builtinSpecifications\` plus entries loaded from \`custom_lora.json\`.

The serialized \`LoRAZoo.Specification\` contains metadata such as name, file, prefix, version, consistency/LoHa/modifier flags, decoder data, weight, and note. It does **not** contain a field identifying whether the entry originated from the official list, community catalog, or local/custom registry.

Therefore:

- \`echo.override\` is evidence of structured metadata, not origin.
- A LoRA returned through Echo cannot be labeled official/community/custom from the gRPC payload alone.
- Origin needs separate evidence such as known official/community catalog identity or DTFlow-owned custom registration.

## FilesExist

The inspected non-proxy local server:

1. checks every requested filename through \`ModelZoo.isModelDownloaded\`;
2. for names also present in \`filesWithHash\`, resolves the downloaded path;
3. reads the file and returns SHA-256 in the parallel \`hashes\` array.

Owner-run live evidence on 2026-10-04 confirmed this behavior on the target iPhone Draw Things server for:

\`the_body_version_m_zit_512_r16_adamw8bit_weighted_bf16_womanonly_000008000_lora_f16.ckpt\`

The gRPC server returned:

\`a069707aef7a69ec9dc867b9b5bcace2f560030030ad44cf1f15979622a6aec8\`

matching the independently measured converted-checkpoint SHA-256.

This establishes a no-checkpoint-transfer migration path for already-converted LoRAs when the local Draw Things server is reachable.

### Metadata registry candidates

Current source stores several local registries by basename in the model-download area, including:

- \`custom_lora.json\`
- \`custom.json\`
- \`custom_controlnet.json\`
- \`custom_textual_inversions.json\`
- \`custom_upscaler.json\`
- \`custom_configs.json\`

The community LoRA catalog is cached separately as \`net/loras.json\` and \`net/loras_sha256.json\`.

The capability probe asks \`FilesExist\` about the registry basenames and requests hashes for them. A positive response proves only that a file with that basename is addressable through the server's downloaded-file lookup and provides its content hash. It does **not** reveal the JSON contents or the member filenames inside a registry.

## Pubkey and Hours

The inspected local \`ImageGenerationServiceImpl\` currently returns empty/default responses for \`Pubkey\` and \`Hours\`. Other server variants may differ, so the probe records their actual deployed responses instead of assuming the source implementation is universal.

Echo also has an optional threshold field. The probe records Echo thresholds separately from \`Hours\`.

## Diagnostic tool

Run:

\`\`\`powershell
uv run python .\tools\drawthings\grpc_capabilities_probe.py -a 192.168.1.142 -o .\tmp\grpc_capabilities.json
\`\`\`

To fingerprint selected server-side files as part of the same safe probe:

\`\`\`powershell
uv run python .\tools\drawthings\grpc_capabilities_probe.py -a 192.168.1.142 -f "the_body_version_m_zit_512_r16_adamw8bit_weighted_bf16_womanonly_000008000_lora_f16.ckpt" -o .\tmp\grpc_capabilities.json
\`\`\`

\`DRAWTHINGS_SHARED_SECRET\` is used when present. The secret value is never written to the report.

The probe intentionally does not hash every Echo-listed checkpoint because local \`FilesExist(filesWithHash=...)\` reads the file to compute SHA-256; hashing an entire model inventory could require reading many gigabytes from device storage.

## Inventory implications

The existing local inventory cache should continue to represent the current server observation. A separate custom-LoRA durable registry can enrich only LoRAs with independent custom-asset evidence.

Useful gRPC-derived fields include:

- server identifier
- endpoint
- exact converted checkpoint filename
- structured LoRA metadata fields
- converted checkpoint SHA-256
- observation time

gRPC currently does not directly provide:

- custom/community/official provenance
- file byte size through \`FilesExist\`
- \`custom_lora.json\` contents
- community catalog contents
- DT+ BYOM reference

DTFlow should not infer provenance from \`metadata_source="echo.override"\`, filename patterns, or absence from one inventory.

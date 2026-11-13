# Draw Things LoRA `.ckpt` format and the Python converter (#354, #355)

What a Draw Things LoRA `.ckpt` contains, how the integrated `dtflow.services.lora_convert` package converts `.safetensors` locally (module CLI and the Prepare editor), and where comparison to the upstream Swift importer stops being evidence. The historical Swift reference uses Draw Things rev `db37e65` (Swift 6.1.2, CPU-only Linux) unless stated otherwise. The original `tools/drawthings/` script remains unchanged as a reference proof of concept.

## Container

A `.ckpt` is an SQLite database written through s4nnc, not a PyTorch checkpoint.

```sql
CREATE TABLE tensors (name TEXT, type INTEGER, format INTEGER, datatype INTEGER,
                      dim BLOB, data BLOB, PRIMARY KEY (name))
```

For a converted f16 LoRA every row has:

| column | value |
| --- | --- |
| `type` | `1` (CPU) |
| `format` | `1` (NHWC) for 2-D tensors; `2` (NCHW) is assumed for 4-D conv tensors, unverified |
| `datatype` | `0x20000` (f16) |
| `dim` | 12 little endian int32 (48 bytes), the shape padded with zeros |
| `data` | raw little endian fp16, row major |

Example (Krea 2, 512 tensors, 229 MB): `__dit__[t-down-0-0]__down__`, f16 32x16384; `__dit__[t-down-0-0]__up__`, f16 6144x32.

Datatype flags seen in other files (not produced by the converter): `0x01000` u8, `0x02000` i32, `0x04000` f32, `0x08000` i64, `0x10000` f64, `0x20000` f16, `0x40000` quantized (`qx`), `0x80000` bf16.

### Codecs

A converted f16 LoRA is stored as plain f16 with no codec. The other Draw Things encoders (`fpzip`, `q6p`, `q8p`, `ezm7`, external data) are used for quantized models; the Python converter does not write them and none of the verified files use them. Their byte layouts are not documented here (unverified). LoRAs are normally kept f16.

## Tensor names

`__<prefix>__[<nnc name>]__up__` and `__down__`, one pair per layer.

- Prefixes: `dit` (transformer models), `unet`, `unet_fixed` (SDXL cross-attention that Draw Things computes once), `text_model` and `te2__text_model` (SD/SDXL text encoders).
- NNC names follow the model graph: Krea 2 `t-to_q-<block>-0`; Z Image `t-q-<i>-0` and `t-ffn-<i>-ffn_down_proj-0-0`; Flux 1 `t-x_q-<block>-0` with `c_*` (text) and `x_*` (image) layers (19 double blocks then 38 single blocks in one numbering); SD 1.x/SDXL use literal ids like `t-26-0` from the big tables in `ModelMapping.swift` (extracted to `tools/drawthings/lora_mappings.json`).

## Conversion rules (ported from `ModelOp.LoRAImporter`)

- **Float16**: BF16 and F32 become F16 by truncation toward zero (ccv), not round to nearest. The converter does it with integer bit operations, so it does not depend on the numpy version. F16 sources are copied as they are. F32 sources: unverified.
- **Scaling**: `up` is multiplied by `alpha/rank*sqrt(scale)` when `|alpha-rank| > 1e-5`, otherwise by `sqrt(scale)` if scale is not 1. `down` is multiplied by `sqrt(scale)`. Computed in f32, then truncated to f16. Rank is the rank of the whole fused tensor. alpha different from rank and `--scale` are unverified.
- **Fused layers** are split into several targets: equal row parts (Krea 2/Z Image fused qkv, SD GEGLU two-way split), or explicit offsets (Flux single `linear1`: 0/3072/6144/9216 rows). If the off-diagonal blocks of `up` are all zero bytes, `up` is cut block diagonally and `down` by rows; otherwise every target gets its `up` rows and the whole `down`. Flux single `linear2` fuses two inputs: the whole `up` goes to both targets and `down` is cut by columns at 0/3072.
- **Unmatched keys**: the Swift importer ignores them silently. The Python converter stops with an error (a half converted LoRA is worse than none); for SD 1.x/SDXL `--ignore-unmatched` restores the Swift behaviour.
- **Not supported** (error pointing to the Colab notebook): LoHa, LoKr, Tucker, diff keys, embeddings, DoRA, bias, quantized output.

## Families and status

| family | status | reference compared | DT+ generation |
| --- | --- | --- | --- |
| Krea 2 | verified | 512 tensors, 0 differ; two Windows files also matched iOS (474 and 512 tensors) | effect confirmed (owner) |
| Z Image Turbo | verified (refiner and context refiner names unverified) | 660 tensors, 0 differ | effect confirmed (owner) |
| SD 1.x | verified (text encoder and conv paths unverified) | 588 tensors, 0 differ | applied on DT+ (owner report 2026-11-13) |
| Flux 1 | verified on two kohya LoRAs and a synthetic diffusers file | 1596 / 1596 / 1064 tensors, 0 differ | applied on DT+ (owner report 2026-11-13) |
| SDXL | **experimental**, explicit confirmation required | no Swift reference; the converted DTFlow files were checked against iOS-converted files (owner report) | converted files passed the with/without-LoRA test (owner); **some SDXL LoRAs still fail to convert**, so coverage is incomplete |

Statuses are owner smoke on specific files, not exhaustive family coverage. The registry (`FamilySpec.status`) is the single source for the Prepare badge, CLI text and this table.

### Swift reference limits

- The reference is built on CPU-only Linux. On the SD3/PixArt/SDXL text-encoding path `LoRAImporter.swift:829` calls `toGPU(0)`, which asserts a CPU memory tensor (`ccv_nnc_tensor.c:82`), so every SDXL LoRA crashes. SDXL stays experimental until a build that skips `toGPU`, or a Mac, produces an oracle. A cheaper check: diff against an SDXL LoRA `.ckpt` made by Draw Things itself with `inspect_ckpt.py`; that would also settle the 4-D format flag.
- For a family, compare a Swift made ckpt and a Python made one with `python tools/drawthings/inspect_ckpt.py A.ckpt B.ckpt` and require 0 differing tensors and 0 differing data bytes.

## Integrated Windows converter (#409)

Use the packaged Python service from a repository checkout with `uv`; this is not yet a standalone executable subcommand:

```powershell
# Convert a single, explicitly selected file.
uv run python -m dtflow.services.lora_convert "C:/LoRAs/example.safetensors" "C:/Converted/example.ckpt" --family krea2

# Convert a case-insensitive filename subset with analysis.
uv run python -m dtflow.services.lora_convert "C:/LoRAs/*ball*" "C:/Converted" --family krea2 -w -a
```

- `--family` names the **source** architecture: `krea2`, `zimage`, `flux1`, `sd1` or `sdxl`. When it is omitted, each file's family is detected from its header only (tensor names and shapes, never weights, filename or `__metadata__`): a file that cannot be identified fails with the detector's reason (a batch continues and exits 1, a single file exits 2) and an explicit `--family` always overrides detection. SDXL is experimental and requires `-xp`/`--experimental`. Detection is a suggestion, not a guarantee: a partial Flux 1 file with Flux-only module names is detected and converted by the CLI, but the converted-output check cannot identify it, so the app's Prepare refuses that output.
- One explicit `.safetensors` file creates one `.ckpt`; a folder or quoted filename glob selects a non-recursive batch. Filename glob patterns support `*`, `?`, and brackets, are case-insensitive and automatically filter to the `.safetensors` extension. Thus `*BALL*` can match `CockAndBall2.safetensors`. Directory names cannot contain wildcards; quote patterns in PowerShell.
- Batch outputs use the original basenames with `.ckpt`. By default existing destinations are **skipped**; `-w/--overwrite` stages replacements and only commits them after a successful conversion. Independent batch failures do not stop other files. The service-level API itself never overwrites an existing checkpoint.
- Normal batches write schema 1 JSON reports under `OUTPUT/reports/lora-convert-v1-<timestamp>.json`. `-a/--analyze` writes schema 2 reports for single files or batches, including source dtype/ranks/alpha, module coverage, shape warnings, a `source_family_detection` object (outcome, family, candidates, reason, evidence) and the **output CKPT** structure/family check. Without `--family` the report's top-level `family` is `"auto"` and each item records its `family` and `family_source` (`detected` or `selected`). Neither check can prove numeric equivalence or visual effect. Single-file reports are stored below the destination's parent `reports/` directory.
- `--scale` is optional; `--ignore-unmatched` applies only to SD 1.x/SDXL. F16 output only; Q8P output and unsupported LoRA extensions are not provided. The historical `tools/drawthings/lora_to_ckpt.py` and Colab conversion route remain available for reference and research.

**Desktop:** The Prepare editor takes `.ckpt` files and `.safetensors` sources. A `.safetensors` row suggests its family from the header (preselected only when exactly one family fits; the choice stays editable), and the separate explicit **Prepare N ready LoRAs** action converts it locally to an F16 `.ckpt` (reusing a retained verified conversion of the same source bytes, family, scale and converter version) and then runs the usual DT+ upload. Selecting a file never converts or uploads. Details: [architecture](../architecture/lora-convert.md). Reuse across converter versions is deferred ([#403](https://github.com/djebaz/dtflow/issues/403)).

### Verified real-file evidence (2026-11-13)

The owner converted two Krea 2 LoRAs on Windows with `-a`, then compared the results with Draw Things **iOS**-converted checkpoints. The outputs contained **474** and **512** tensors; both had zero missing/extra tensors, zero shape/datatype differences and zero tensor-data hash differences. Both generated outputs passed the existing CKPT structural check and were detected as `krea_2`. After manual Prepare, actual DT+ generation with/without each LoRA showed the expected effect. This is **two-sample owner smoke**, not broad family, packaged Windows executable or future UI-path validation. Integrated [full CI](https://github.com/djebaz/dtflow/actions/runs/43327453328) on the pre-format stack passed **695 tests, 3 skipped**; the subsequent Ruff-format fixes were not run through full CI again. [PR #409](https://github.com/djebaz/dtflow/pull/409) records details.

### Descriptive metadata (#414)

DTFlow reads the safetensors header's descriptive `__metadata__` (kohya `ss_*`, ModelSpec `modelspec.*`) only to show it and to suggest Trigger Words from an explicit declaration. It never decides the family or the conversion, is read best effort (anything unusable is ignored), keeps a bounded subset without dataset folders or training comments, and is not written into the `.ckpt`. Field spellings follow trainer source research, not yet a collection of real files. See the [architecture note](../architecture/lora-convert.md).

## Still unverified

4-D conv format flag, F32 sources, alpha different from rank, `--scale`, Z Image refiner names, the SDXL LoRAs that still fail to convert, and anything not listed above.

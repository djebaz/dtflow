# Draw Things LoRA `.ckpt` format and the Python converter (#354, #355)

What a Draw Things LoRA `.ckpt` contains, how `tools/drawthings/lora_to_ckpt.py` produces it from a safetensors LoRA, and where the Swift reference stops being usable. Everything here was checked against files made by the Swift `lora-convert` tool (Draw Things rev db37e65, Swift 6.1.2, CPU-only Linux) unless it says "unverified".

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

| family | status | reference compared |
| --- | --- | --- |
| Krea 2 | verified | 512 tensors, 0 differ |
| Z Image Turbo | verified (refiner and context refiner names unverified) | 660 tensors, 0 differ |
| SD 1.x | verified for the reference file (text encoder and conv paths unverified) | 588 tensors, 0 differ |
| Flux 1 | verified on two kohya LoRAs and a synthetic diffusers file | 1596 / 1596 / 1064 tensors, 0 differ |
| SDXL | experimental, no reference | none |

### Swift reference limits

- The reference is built on CPU-only Linux. On the SD3/PixArt/SDXL text-encoding path `LoRAImporter.swift:829` calls `toGPU(0)`, which asserts a CPU memory tensor (`ccv_nnc_tensor.c:82`), so every SDXL LoRA crashes. SDXL stays experimental until a build that skips `toGPU`, or a Mac, produces an oracle. A cheaper check: diff against an SDXL LoRA `.ckpt` made by Draw Things itself with `inspect_ckpt.py`; that would also settle the 4-D format flag.
- For a family, compare a Swift made ckpt and a Python made one with `python tools/drawthings/inspect_ckpt.py A.ckpt B.ckpt` and require 0 differing tensors and 0 differing data bytes.

## Using the converter

```
python tools/drawthings/lora_to_ckpt.py in.safetensors out.ckpt --family krea2|zimage|flux1|sd1|sdxl [--scale 1.0] [--ignore-unmatched] [--experimental]
```

- Needs numpy >= 1.21 (the repo pins 2.3.5) and the standard library only; runs on native Windows.
- `--family sdxl` needs `--experimental`. An existing output file is never overwritten. The source LoRA is read with plain file reads and closed afterwards, so it is not locked on Windows.
- Unsupported families, keys or shapes stop with an error that points to the Colab notebook (`tools/colab/dtflow_lora_minimal_convert.ipynb`).
- Safety checks: every module needs both up and down with matching rank, duplicate modules are rejected, fused Flux layers must have the exact expected shapes.

## Still unverified

4-D conv format flag, F32 sources, alpha different from rank, `--scale`, Z Image refiner names, SDXL as a whole, and anything not listed above.

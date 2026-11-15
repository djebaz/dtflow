# Local q8p CLI experiment (#353)

## Scope and decisions

- Add `dtflow lora inspect`, `compare` and `quantize`; the writer requires explicit `-e/--experimental` and a separate new `.ckpt` output.
- No changes to safetensors conversion, Prepare, retained-store authority, upload, generation or UI. No provider or credential access from these commands.
- Share tensor codecs for raw F16, q8p and ezm7. Restrict the initial writer to structurally recognized Krea 2, Z Image and SDXL Base; this is not all-family acceptance.
- Use optional `quantization` dependency group `kmeans1d==0.5.1` (MIT, Windows ABI3 wheels) for native optimal 1D clustering rather than porting ccv or using random k-means. Its same-objective DP/SMAWK implementation is not claimed bit-identical to ccv; reports identify it explicitly and reference comparison quantifies differences.
- Match upstream q8p block layout (16,384 values, 256 F16 centroids, byte indices), codec identifiers and the `raw byte size + 4` encoder capacity. Implement ezm7 fallback and preserve raw F16 if encoding does not fit, as upstream does.
- Read one bounded tensor at a time, report source/output hashes and aggregate numerical errors without private tensor names/paths, reverify source identity, and publish a verified same-directory temporary output without overwriting.
- Only persistent diagnostics through the existing logging pipeline; stdout CLI JSON is derived evidence that the owner can explicitly redirect. No parallel logging configuration or automatic report sink.

## Work

- [x] Implement strict read-only container inspection and shared decoding.
- [x] Implement bounded reference comparison and experimental encoding.
- [x] Bind local CLI commands, progress, safe reports and fixed diagnostic reasons.
- [x] Synchronize dependency metadata, architecture, CLI guide, status and release notes.
- [x] Review source and run permitted static checks; prepare draft PR with unchanged ACs.
- [x] Add owner-requested CPU auto-detection, bounded spawned block workers, serial/explicit override and elapsed/ETA progress.
- [x] Add a separate F16 → q8p Colab notebook and canonical markerized source, leaving the Swift converter notebook unchanged.

## Evidence boundary

Initial implementation did not authorize tests, generated test fixtures, renders or provider calls. The owner subsequently authorized local checks on the supplied F16 files. Numerical equivalence, conversion time, full-app/native/package and DT+ acceptance remain separate. No production go decision is implied by adding an experimental writer.

Prescribed environment readiness, focused Ruff lint/format, AST/TOML parsing, CLI group/writer help and lock consistency passed. Optional `kmeans1d==0.5.1` imported successfully after uv synchronized the explicitly selected group. Source review covered input snapshots/fresh hash revalidation, bounded blobs/decompression, domain errors, private-path-free diagnostics, exclusive publication and cancellation cleanup. Keep draft; no Actions were requested because credits are exhausted.

## Owner-authorized local checks, 2026-11-15

Code revision: `71661d93f32dd76ea65ce70c517aa0afaaeff7fe`, Windows, Python 3.13.13, SQLite 3.53.1, kmeans1d 0.5.1. Disposable scripts/reports remain ignored under `tmp/`; no private filenames or reference data are committed.

| Pair | Tensors per file | Full inspect-both + compare seconds | Sample encoding | Whole-file blocks |
| --- | ---: | ---: | --- | ---: |
| Krea 2 | 474 | 16.908 | Three q8p blocks and both ezm7 tensors | 5,922 |
| SDXL | 2,112 | 17.285 | Three q8p blocks | 8,006 |
| Z Image | 660 | 14.017 | Three q8p blocks, including an 8,192-value block | 5,520 |

- Full input/reference decoding and matching inventory checks passed for every pair. All 139,680 sampled encoded/decoded values equaled the corresponding iOS decoded F16 values; this does not establish whole-file equivalence.
- Original and iOS reference SHA-256 values were unchanged after each check. Existing destinations were rejected with `destination_exists`; all three sentinel contents stayed unchanged. No provider work was requested; source inspection confirms these commands do not open provider/state services.
- Full 16,384-value q8p samples took 2.719–3.525 seconds; the 8,192-value sample took 1.373 seconds and ezm7 samples took at most 0.001 seconds. Whole-file extrapolation is hours, not measured completion. The owner accepted long offline runs and subsequently reported roughly 1–1.5 seconds per block; speed alone is not a blocker.
- The initial whole-file Krea 2 attempt was force-stopped before publication. Redirected progress was not visible live; isolated block probes completed. Removed only that invocation's tiny staging file/journal, not originals or published outputs. No complete experimental CKPT exists from this run; successful publication and end-to-end conversion time/size remain unchecked.
- AC-1 now has source and local preservation evidence. AC-2 remains incomplete for successful whole-file writer publication; AC-3 lacks a completed conversion time/output size. No DT+ acceptance/quality evidence (AC-4). Provisional outcome: continue only as a codec/performance experiment, not production integration.

Upstream pins and reference measurements are consolidated in [issue #353](https://github.com/djebaz/dtflow/issues/353). The public iOS UI handler is not available in the inspected tree; q8p plus ezm7 policy is grounded in the supplied samples and shared codec, not a claim to reproduce every app decision.

## Multicore and Colab follow-up

Auto-selection uses `os.process_cpu_count()` (minimum one; Windows maximum 61); explicit `-j/--jobs` overrides and 1 avoids the process pool. One reusable spawn pool encodes blocks within each tensor, with no more pending blocks than workers. Assembly and SQLite/file authority remain in the parent. More CPUs do not help a tensor with fewer blocks, and each worker adds memory/startup overhead. Parent-owned Ctrl+C stops new admission and drains admitted blocks; domain errors are pickle-safe and broken workers have a fixed diagnostic reason. Progress shows elapsed time and an approximate remaining encoding time from completed blocks, not final validation time.

The new standalone Colab notebook uses the same subprocess CLI, a fresh checkout and an isolated locked uv environment, with explicit upload/Drive input and optional output/report downloads. Existing Swift/safetensors notebooks are unchanged. The owner-supplied Colab inventory has NumPy 2.4.6/Pillow 11.3.0; those kernel packages are not changed or used by the separate DTFlow environment. The notebook source revision is user-selectable and its resolved SHA is recorded.

No further conversion, CPU benchmark, provider call or Actions run was launched for this follow-up while the owner was testing. Earlier serial evidence does not establish multicore equivalence, cancellation, speedup, ETA accuracy or Colab acceptance. These remain owner smoke checks; do not tick whole-file writer acceptance from static checks.

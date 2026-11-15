# Local q8p CLI experiment (#353)

## Scope and decisions (initial CLI experiment)

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

Auto-selection uses `os.process_cpu_count()` (minimum one; Windows maximum 61); explicit `-j/--jobs` overrides and 1 avoids the process pool. The first implementation drained each tensor before advancing; the owner reported only one busy CPU on a 12-CPU Colab runtime even with 0/12 workers. The corrected reusable spawn pool schedules a global ordered stream of blocks across tensor boundaries, initially with no more pending tasks than workers, including small-tensor fallback tasks (superseded by the bounded CPU-overlap follow-up below). Assembly and SQLite/file authority remain in the parent. Parent value/assembly buffers and native worker overhead grow with the number of admitted tensors/workers. Parent-owned Ctrl+C stops new admission and drains admitted blocks; domain errors are pickle-safe and broken workers have a fixed diagnostic reason. Progress shows elapsed time and an approximate remaining encoding time from completed blocks, not final validation time. Corrected utilization is not yet measured.

The standalone Colab notebook uses the same command/service through a minimal subprocess launcher and its separate uv-generated script lock for NumPy/kmeans1d only, with explicit upload/Drive input and optional output/report downloads. Existing Swift/safetensors notebooks are unchanged. The owner-supplied Colab inventory has NumPy 2.4.6/Pillow 11.3.0; those kernel packages are not changed or used by the separate script environment. The notebook source revision is user-selectable and its resolved SHA is recorded. Only 18 manifest-listed source/launcher/lock files are downloaded; no Git checkout, desktop dependency environment or copied quantization algorithm. Retained-store import in family detection is now lazy and remains available to ordinary app callers.

No further conversion, CPU benchmark, provider call or Actions run was launched for this follow-up while the owner was testing. Earlier serial evidence does not establish multicore equivalence, cancellation, speedup, ETA accuracy or Colab acceptance. These remain owner smoke checks; do not tick whole-file writer acceptance from static checks.

Owner setup correction: cell 1 exposes GitHub username and Colab secret ID, never the token itself. A blank ID permits anonymous access; unavailable named secrets fail clearly. The owner's additional request to avoid a full repository download supersedes the initial Git fallback approach: resolve branch/tag/SHA through the GitHub API, download manifest-listed files at that SHA, and verify blob hashes. Authentication stays in memory-only request headers, never URLs/arguments/config or raw error output, and is discarded before uv runs. Owner execution of their custom cell is evidence for the setup problem, not acceptance of this adapted minimal setup or a full conversion. Regenerated notebook/code-cell lint and syntax checks are static evidence only.

Owner-reported minimal setup failure reproduced outside the checkout on Windows: the script lock inherited repository `prerelease=allow`, but the downloaded bundle had no project config, so uv defaulted to `if-necessary` and refused `--locked`. The standalone script now declares its own `[tool.uv] prerelease="allow"`; the exact locked launcher help command then passed in that independent 18-file bundle, installing only NumPy/kmeans1d. This is launcher/dependency setup evidence, not a conversion or Linux/Colab run. Setup now streams combined uv/launcher output and raises a fixed failure with its exit code, rather than presenting only a captured-less `CalledProcessError`.

## Owner completed-run evidence (supersedes earlier pending writer status)

The owner supplied two schema-1 reports, inspected read-only. Same input SHA and 420 Z Image tensors, 7,034,880 values, 14,798,848 input bytes. Baseline `d7e3c957`: 455.531 seconds, 12 workers configured but owner-observed effectively single-core. Corrected `03f5e7a6`: 103.483 seconds, 12 workers, with the owner temporarily removing `--locked` to bypass the setup mismatch. Ratio 4.40× for this one comparison. Both report exactly the same output SHA, 7,901,184 bytes, 420 q8p tensors, 46.609% reduction, MAE 0.0007284091546619107, RMSE 0.001384864821778998 and maximum absolute error 0.037109375. Reported output identity is byte-for-byte equal; the downloaded CKPT was not independently rehashed here. A successful report follows reopen/decode/source verification and exclusive publication in the shared implementation.

This supplies whole-file CLI outcome and measured-size/time evidence for AC-2/AC-3, in addition to AC-1 preservation/source-boundary evidence. Go for the opt-in experiment (AC-5), not production/Prepare integration. AC-4 DT+ effect/quality remains unverified, as do other-family whole-file writes, cancellation/fault behavior and the latest locked-setup/startup-label corrections on Colab. No GPU speedup or universal CPU scaling is claimed. The CLI now prints service-resolved detected logical CPUs and actual worker count before encoding, identifying auto/explicit mode, and records detected CPUs beside workers in JSON. That small presentation/report addition received static checks only; no new conversion was started.

## Review follow-up: Krea 2 GPU evidence (2026-11-15)

The owner supplied a full-file GPU report at revision `2cdeb7f6786e8428462088cc51524f9a8124c3c7` (NVIDIA L4, approximate FP32 Lloyd clustering, installed PyTorch rather than the CPU optimal 1D clustering path). It reports **194,363,392 → 100,569,088 bytes** (48.257% reduction), **474 tensors** (472 q8p, two ezm7), **11.632 s** elapsed, 7.373 s GPU encoding, 750 batches and zero nonconverged batches. It records successful whole-file publication and numerical differences from F16, including RMSE 0.000374652; downloaded output bytes were not independently rehashed here.

A separate 256-block sample report, using the same reported F16 input SHA, measured 0.354 s for its warmed GPU pipeline (not a whole-file conversion). On that sample, GPU-vs-F16 RMSE was 0.000378990 and reference-vs-F16 RMSE 0.000134157. The owner-provided iOS reference's original-weight provenance was not verified; apparent numerical advantage cannot be treated as a controlled algorithm or visual-quality result. The earlier 103.483 s Z Image CPU conversion used another file and must not be used to calculate a CPU/GPU speedup factor.

**Open review findings (source-only, no code change):**

- Provide an `os.cpu_count()` fallback or equivalent for notebook kernels below Python 3.13: the GPU full-file notebook calls `quantize_file()` directly in the kernel, which currently uses `os.process_cpu_count()`. This is a potential Python 3.12 failure despite the isolated CPU-launcher Python version.
- Verify the GPU output against its downloaded digest and compare F16/iOS/GPU only once all three artifacts are proven to derive from the same original weights.
- Complete separately authorized DT+ smoke with the same model, prompt, seed and LoRA weight; assess provider acceptance and real LoRA effect/quality. AC-4 remains unchecked. Maintain distinct CPU and GPU timing scopes, and keep GPU nonconvergence reports distinct from image quality.

**Go/no-go:** go for the opt-in experiment only; no default GPU encoding, Prepare integration, or production compatibility claim. No agent tests, renders, conversion, provider operations or Actions were run in this review.

## Managed-library desktop revision

The owner superseded the standalone Generate picker/Save As UX with per-row actions in Connection → Custom LoRAs. The full header toggles the section; table height is 420 px. The local adapter stages outside state, preserves the F16, retains a verified separate q8p and records additive source/output content relationships without transferring receipts. Existing verified output disables repeat conversion; missing/damaged output does not. Prepare uses the selected key’s local confirmations and opens the existing approval editor, including from the post-conversion success action; it never uploads automatically. CLI/notebook behavior is unchanged. Existing AC IDs and levels remain unchanged; this desktop/retention scope extension is disclosed in the PR. Static checks only; owner smoke must cover conversion, restart, removal, cancellation, key changes and explicit Prepare. No tests, fixtures, renders or provider calls are authorized by this revision.

## CPU overlap and small-task batching

After owner smoke of managed quantization succeeded, the owner requested less idle time around small tensor tasks. Keep the worker count unchanged, prefetch up to 2× workers, collect any completed task, and refill before ordered assembly/writing. A 4× workers reordering window (plus the emitted batch) bounds delayed-result buffering. Group tasks of at most 4,096 elements into at most eight tasks / 16,384 elements per worker call; palette boundaries and each individual encoding remain unchanged. Cancellation is checked before further admission; queued work is cancelled best effort and running bounded batches drain. Reports expose the limits. The serial CPU and custom GPU paths are unchanged. Static inspection only: performance, byte equivalence and revised cancellation behavior have not been exercised. No tests, fixtures, conversions, renders, provider calls or Actions were run.

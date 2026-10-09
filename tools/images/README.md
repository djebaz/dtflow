# Pixel-statistics detector PoC

`detect_noise.py` is a standalone, offline diagnostic, not part of DTFlow export or job handling. It uses the existing Pillow dependency, no model or network service.

From the repository root in PowerShell:

```powershell
uv run python tools/images/detect_noise.py --help
uv run python tools/images/detect_noise.py -s 'C:\Users\dje\Documents\GitHub\dtflow\tmp\smoke3' -o 'tmp/noise-report.json'
```

Pass multiple files/folders after `-s/--source` to compare selected images. Folder scans are flat unless `-r/--recursive` is specified. `-o/--out` writes a new JSON file only; without it, JSON goes to stdout. The output parent directory must exist. Reports contain local paths, but no prompts or copied generation metadata. Keep reports private if paths are sensitive.

## Metrics and interpretation

Analysis uses the first frame, EXIF orientation, alpha composited onto white, and an aspect-preserving LANCZOS thumbnail of at most 256 pixels per side. The report includes original/analysis sizes and exact provisional thresholds.

- `saturated_fraction`: fraction of pixels with HSV saturation at least 200/255.
- `extreme_channel_fraction`: fraction of RGB channel samples at most 5 or at least 250 (not fraction of pixels).
- `neighbor_rgb_difference`: mean absolute RGB difference between horizontal/vertical neighbours, divided by 255.
- `luma_mean`, `luma_stddev`: Pillow L-channel statistics divided by 255; `luma_entropy_bits` is histogram entropy and does not determine the noise flag.
- `coarse_residual_rms`: RGB RMS difference from a BOX-downsampled, bilinear-reconstructed 32-pixel coarse image, divided by 255.
- `coarse_residual_to_rgb_stddev`: residual RMS divided by RGB standard deviation (floor 1/255); this ratio can exceed one. This is a spatial proxy, not an FFT spectrum.

Four noise signals are saturation >=0.30, extreme channels >=0.20, neighbour difference >=0.12, and a joint residual test (RMS >=0.10 and residual/stddev >=0.75). `possible_noise` requires at least three signals plus neighbour difference >=0.08. `possible_blank` takes priority when luminance standard deviation <0.01. `noise_signal_count` counts rules, **not confidence, probability or an aesthetic score**.

Labels are `possible_noise`, `possible_blank`, `no_obvious_failure`, and `unreadable`. Review flagged images manually. A clean flag does not establish a great or correct generation: anatomy errors, unwanted composition and prompt mismatch can pass. Foliage, textures, sharp patterns, artwork and intentional solid colours can trigger false positives. Resize, image dimensions, compression and white alpha compositing affect metrics.

**Thresholds are uncalibrated.** They have not been validated on the supplied pair or a labelled corpus. Next step: owner review of flags across known-good and known-bad examples before considering production use.

## Safety and limits

No source writes, deletion, regeneration, job-status updates or provider calls. Inputs are limited to 10000 unique files, 64 MiB encoded per file and 32 million pixels; limits/read failures produce `unreadable`, not evidence of a failed generation. Existing report files are never overwritten. An interrupted run may leave an incomplete report; choose a new name for the next run. Exit code 0 means processing completed (including advisory flags), 1 means at least one unreadable input, and 2 means CLI/input/report-path error.

Tests and image smoke runs are deferred to the owner under fast-implement. Static syntax/import/ruff/help checks do not establish detection accuracy.

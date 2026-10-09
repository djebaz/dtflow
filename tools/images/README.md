# Pixel-statistics detector PoC

`detect_noise.py` is a standalone, offline diagnostic, with the same pixel-statistics service as DTFlow export sorting; it does not own export or job handling. It uses the existing Pillow dependency, no model or network service.

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

**Version 2** also flags noise when all three spatial conditions hold: neighbour difference >=0.08, residual RMS >=0.18 and residual/stddev >=0.65. This independent route reports `joint_spatial_noise_rule` when the original route did not flag the image. `noise_signal_count` still counts only the original four signals; it is not a threshold for this alternate route. Blank detection retains priority. Reports use algorithm identity `dtflow-pixel-statistics-poc-v2`.

Labels are `possible_noise`, `possible_blank`, `no_obvious_failure`, and `unreadable`. Review flagged images manually. A clean flag does not establish a great or correct generation: anatomy errors, unwanted composition and prompt mismatch can pass. Foliage, textures, sharp patterns, artwork and intentional solid colours can trigger false positives. Resize, image dimensions, compression and white alpha compositing affect metrics.

**Thresholds are provisional and sample-fitted, not independently validated.** Owner-labelled runs found zero flags in 262 good images and 11/12 noise detections in the failed folder. Smoke3 review identified two noise misses; cached-statistics evaluation of the added spatial rule catches exactly those two additional images while still flagging zero of the 262 good examples. These reused samples do not establish general accuracy. Independent examples and owner review remain necessary before production use.

## Safety and limits

The CLI makes no source writes, deletion, regeneration, job-status updates or provider calls. The Connection export checkbox optionally uses the shared service to route newly exported copies only into noise/blank folders; it does not modify originals. Inputs are limited to 10000 unique files, 64 MiB encoded per file and 32 million pixels; limits/read failures produce `unreadable`, not evidence of a failed generation. Existing report files are never overwritten. An interrupted run may leave an incomplete report; choose a new name for the next run. Exit code 0 means processing completed (including advisory flags), 1 means at least one unreadable input, and 2 means CLI/input/report-path error.

No automated tests were added or run under fast-implement. Subsequent image detector runs were explicitly requested by the owner; independent validation of version 2 remains pending. Static syntax/import/ruff/help checks do not establish detection accuracy.

## Reference JPEG/EXIF exporter

`export_jpeg_exif_poc.py` preserves the original reference experiment, independent of application export. It requires explicit paths and has no owner-specific defaults or imports from ignored tmp scripts:

```powershell
uv run python tools/images/export_jpeg_exif_poc.py -s '<saved-attempt-folder>' -o 'tmp/reference-jpeg-export'
```

It supports RGB PNGs only and includes built-in EXIF/pixel/original-preservation verification. It writes JPG, JSON and verification reports and uses numeric collision suffixes. Unlike production export, it intentionally retains **full private source metadata**, including prompts, paths and asset references; do not share its exports blindly. Its filename date is source mtime, not verified generation time. For portable privacy, deduplication and historical creation/Date taken fields, use the application export instead. Copying the script does not execute its verification.

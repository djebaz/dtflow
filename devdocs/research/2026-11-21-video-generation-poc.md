# Video generation POC on DT+ (#521)

Status: throwaway tool `tools/video_poc/video_poc.py` (PR #523). It is not product code: no change to the backend, request model, limits, dependencies or `uv.lock`, and it calls three private backend methods (`_connect`, `_model_spec`, `_generation_metadata`) to reuse the app's connection and authorization path. All results below are owner-reported Windows runs of 2026-11-21 against DT+ Cloud, not agent execution. The planned product work (request/wire, result path, storage, gallery, Generate mode, queue, packaging) is tracked in the video generation plan issues, not here.

## What the tool does

One call sends exactly one paid DT+ generation through `build_generation_message` with `num_frames` added, reads the answer with its own relaxed receiver, streams the frames to disk and into the installed `ffmpeg` (libx264, yuv420p, faststart), muxes audio in a second pass, checks the MP4 with `ffprobe`, and always writes `tmp/video-poc-YYYYMMDD-HHMMSS.log` (nine sections: header, arguments, profile and request, wire, timings, receiver record, encode, ffprobe versus request, verdict). The key comes the way the app takes it (saved key first, then `DRAWTHINGS_API_KEY`) and never reaches the log. `ffmpeg` and `ffprobe` must be on `PATH` (no bundling); if one is missing the tool stops before any request with exit code 3. It is cloud only.

Run from the repository root:

```
uv run python tools/video_poc/video_poc.py -p "a red fox runs through fresh snow, cinematic"
```

| Argument | Meaning |
|---|---|
| `-m` | model file (default `ltx_2.3_22b_distilled_1.1_q6p.ckpt`) |
| `-p` | prompt (required) |
| `-sz WIDTHxHEIGHT` | size; each side is adapted to the nearest multiple of 64 (ties up, 128..2048) and the adjustment is logged. Default 320x320 for LTX, the preset's native size for other models |
| `-f`, `-s`, `-g`, `-sh` | frames, steps, guidance, shift; default from the model's profile or preset (LTX distilled: 121 frames, 8 steps, guidance 1, shift 5), the log says where each value came from |
| `-sd` | seed |
| `-i` | one or more start images (cover-fitted to the request size, sent as one tensor with N frames) |
| `-o`, `-kf`, `-nomp4` | output folder, keep the PNG frames, skip the encode |
| `-q` | no live progress line (the log is the same) |
| `-to` | total timeout in seconds (default 900) |
| `-lg` | log file path |

While DT+ works the console shows one updating progress line (stage, sampling step, frames and MB received); the first minutes are normally "waiting for DT+" because DT+ sends nothing for a long time. Exit codes: 0 ok, 1 failed, 2 invalid arguments or missing key, 3 tool missing.

## Findings

| Topic | Finding |
|---|---|
| Models | `ltx_2.3_22b_distilled_1.1_q6p.ckpt`, `wan_v2.1_1.3b_480p_q8p.ckpt` and `wan_v2.1_14b_i2v_480p_q8p.ckpt` are served to a normal key |
| Response | N one-frame tensors (fpzip) plus, for LTX, one audio tensor `[2, samples]` float32; every message `LAST_CHUNK`; `downloadSize` equals the received total; no CUs visible |
| Rates | LTX 25 fps, audio 48 kHz (121 frames, 4.84 s); Wan 16 fps, no audio (81 frames); ffprobe matched the request in every run |
| Size | LTX 121 frames: about 32 MiB at 320x320, 47 MiB at 448x320, 86 MiB at 384x640 (above the production 64 MiB transport cap). Wan 81 frames at 320x192: 11 MB (1.3B), 15 MB (14B i2v) |
| Time (first content / stream) | LTX 320x320 88.6 s / 107.2 s; 384x640 196.5 s / 242.9 s; Wan 1.3B about 30 s / 48 s; Wan 14B i2v 126.2 s / 137.5 s |
| Start image | One image (one-frame tensor in `image`, sent uncompressed) is accepted and followed by LTX and by Wan 14B i2v; the t2v Wan 1.3B ignores it |
| Several start images | Accepted, full clip returned, but LTX-2.3 uses only the first frame (still image that zooms in). Continuation stays the chaining route: last frame as the next start image |
| Quality | LTX clean with sound. Wan at 320x192 (far below its native 832x448): follows prompt and image but very bad quality and slow; LTX preferred at the sizes tried, native-size Wan not tested |
| Wan defaults | LTX-tuned values (8 steps, guidance 1) on Wan gave only moving colour spots; the profile values (30 steps, guidance 5) give a near real picture, so the tool defaults from the profile |
| Packaging | In-app playback needs `PySide6.QtMultimedia`, which is in PySide6-Addons (wheel 40.5 MB, about 105 MB unpacked), not in PySide6-Essentials |

## Decision

Storage option B (owner, 2026-11-21): one MP4 per attempt plus a poster image; PNG versus JPG for the poster is left open for the real feature. Option C (animated WebP/GIF) has no audio.

## Not run

Owner chose not to smoke test deliberate error cases (`-to` timeout, missing `ffmpeg` stop on a Windows machine). Open optional normal runs: `-kf`, `-nomp4`, a larger LTX size (`-sz 1280x768`), a native-size Wan run, local Draw Things. Wan 2.2 refiner wiring is not implemented.

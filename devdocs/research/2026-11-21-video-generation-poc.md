# Video generation POC on DT+ (#521)

Status: **experimental, paid DT+ Cloud probe** `tools/video_poc/video_poc.py` (initial PR #523, expanded in PR #538). It is **not product code**: it uses private Draw Things backend connection/authorization methods and does not authorize production checkpoint support. The original findings below came from owner Windows runs on 2026-11-21; the PR #538 follow-up evidence is recorded later in this note. All runtime/provider findings are owner evidence, not agent-run generation. Production video entered DTFlow in #526 / PR #527; further model support, parameter exposure and video UX are tracked separately in #532, #539 and #540.

## What the tool does

One call sends exactly one paid DT+ generation through `build_generation_message` with `num_frames` added, reads the answer with its own relaxed receiver, and **streams frames directly into `ffmpeg`** (libx264, yuv420p, faststart) by default, **without writing still images**. It can optionally retain PNG/JPEG frames or skip MP4 creation. It muxes generated audio when available, checks the MP4 with `ffprobe`, and always writes `tmp/video-poc-YYYYMMDD-HHMMSS.log` (nine sections: header, arguments, profile and request, wire, timings, receiver record, encode, ffprobe versus request, verdict). The key comes the way the app takes it (saved key first, then `DRAWTHINGS_API_KEY`) and never reaches the log. `ffmpeg` and `ffprobe` must be on `PATH` (no bundling); if one is missing the tool stops before any request with exit code 3. It is cloud only.

Run from the repository root:

```
uv run python tools/video_poc/video_poc.py -p "a red fox runs through fresh snow, cinematic"
```

| Argument | Meaning |
|---|---|
| `-m` | model file (default `ltx_2.3_22b_distilled_1.1_q6p.ckpt`) |
| `-p` | prompt (required) |
| `-sz` | Final size: `WIDTHxHEIGHT`, `auto`, `WIDTHxauto`, or `autoxHEIGHT`. Sides snap to multiples of 64 (128..2048). With an image and no explicit size, approximate the reference aspect at the model's default pixel budget; `auto` requires a start image. The log shows requested and effective dimensions. |
| `-f`, `-s`, `-g`, `-sh` | frames, steps, guidance, shift; default from the model's profile or preset (LTX distilled: 121 frames, 8 steps, guidance 1, shift 5), the log says where each value came from |
| `-sd` | seed |
| `-i` | one or more start images (cover-fitted to the request size, sent as one tensor with N frames) |
| `-o`, `-kf`, `-jpg`, `-nomp4` | Output folder; keep optional PNG still frames; keep JPEG quality-80 still frames (implies `-kf`); or skip MP4 and save still frames. Default MP4 runs write **no still frames**. |
| `-q` | no live progress line (the log is the same) |
| `-to` | total timeout in seconds (default 900) |
| `-lg` | log file path |
| `--sampler`, `-neg`, `-spec` | Select an existing sampler, negative prompt, or exact-match model-description JSON (not a saved generation preset). |
| `--hires-fix [FACTOR]` | Upscale factor: bare flag = 2×, `1` disables, values up to 4× (including fractions). `-sz` is **final** output size; derived/overridden first-pass dimensions and second-pass signposts are logged. |
| `--set FIELD=VALUE` / advanced `--field-name` | Override serializable fields from pinned `drawthings-py 0.4.0` using snake_case, camelCase or kebab-case aliases. Validations check the effective serialized FlatBuffer **before** a potentially paid request; supported *serialization* does not prove cloud/model behavior. Use `--help` for available field names. |

While DT+ works the console shows one updating progress line (stage, sampling step, frames and MB received). DT+ may send periodic heartbeat-like messages without meaningful sampling progress; the console can remain at "waiting for DT+" before the first sampling signpost. Exit codes: 0 ok, 1 failed, 2 invalid arguments or missing key, 3 tool missing.

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

## Follow-up experiments and API findings (PR #538, 2026-11-21)

### H3 Ref2VA availability versus output quality

- **MiniMax H3 Ref2VA i4x:** The exact `minimax_h3_ref2va_i4x.ckpt` model description was already in DTFlow's catalogue. Owner Windows DT+ Cloud runs returned **22/22 still frames**, **100/100 frames** (24 FPS MP4 + 32 kHz stereo AAC), and **124/124 frames** (5.167 s MP4 + matching-length AAC). These are successful transport and packaging results, **not acceptable visual quality**: the owner reported unnatural fast motion and fuzzy detail despite a recognizable reference and prompt. Those runs used 256×384, TCD Trailing, six steps, guidance 1 and shift 4; the best quality settings for this checkpoint are **not established**. The earlier assumption that H3 universally requires `17k+5` frames was disproved for this cloud POC by complete 100- and 124-frame returns.
- **Pika H3 Flex Ref2VA i8x:** Three owner DT+ experiments returned **one image instead of the requested multi-frame clip**, without audio, even after changing sampler/resolution. The checkpoint was not advertised by the cloud Echo call, but that alone does not prove unsupported execution: MiniMax i4x also lacked Echo advertisement and nevertheless returned complete clips. **Do not admit Pika in production** based on these probes. A local Draw Things report for Pika does not establish DT+ availability.
- A failed MiniMax 124-frame request ended in `Status.UNAVAILABLE (14): Transport became inactive` with no output, followed by a **successful owner-initiated** 124-frame retry. This is an **unknown cloud-side outcome**, not proof of a frame limit, refusal or quota exhaustion; never auto-repeat uncertain paid submissions.

### LTX model choices, duration and size

- A pair of **121-frame / 256×384 owner runs** produced complete 25-FPS clips: **LTX-2.3 22B Distilled 1.1 Q6P** took **116.3 s** total with 48 kHz audio; **LTX-2 19B Distilled Q6P** took **70.3 s** with 24 kHz audio. A separate LTX-2.3 four-step run took **88.8 s** versus an earlier eight-step run at **116.3 s**. These are **uncontrolled, different-seed/settings/queue-time comparisons**, not performance benchmarks or proof of equivalent video quality.
- LTX uses **`8k+1` frames**. The POC now normalizes a requested nonconforming count **before submission**, with an explicit notice. Before that fix, the owner requested **242** and received **241**, resulting in a valid MP4 but an incomplete-request verdict. A `-f 300` request now sends **297** frames.
- Owner **uploaded logs** verify LTX-2.3 **297/297 at 192×384** (25 FPS, **11.88 s**, AAC 48 kHz, **118.0 s** total) and **401/401 at 192×384** (25 FPS, **16.04 s**, AAC 48 kHz, **137.8 s** total). Owner subsequently **reported** successful `-f 501` (normalized to 497), **521** and **761**; these later clips have **no supplied logs** verifying exact received counts/settings/audio. At 25 FPS, 761 would mean **30.44 s**. The practical upper limit and higher-resolution reliability remain **unknown**. The production Generate GUI's then-current 361-frame cap is tracked for review in **#540**, not changed by this POC.
- Reference sizing now supports `-sz auto` and one inferred side (`512xauto` / `autox832`). Explicit sizes are authoritative; with an image and no explicit size, the POC approximates its aspect ratio at the model default pixel budget. Sides snap to multiples of 64; inputs are cover-fitted/center-cropped. This is **not exact aspect-ratio preservation**.

### Hires Fix, advanced flags and the meaning of FPS

- `--hires-fix 1` disables Hires Fix; a bare flag means 2×, up to 4× with fractional factors. The POC treats `-sz` as **final** size, derives a smaller first pass and logs both. An owner LTX-2.3 121-frame **3× Hires Fix** run reached final **448×704** from a **128×256** first pass and reported **`second_pass_sampling` signposts**, with a complete audio/video result. This proves a second pass was reported in that run, **not universal provider support**.
- Pinned SDK `GenConfig.fps` is **not a general playback-FPS control**. A run with `--fps 10` still produced a locally encoded **25 FPS** LTX MP4 (121 frames = 4.84 s), because playback FPS comes from the POC's model-family table. Do not confuse frame rate conditioning, encoded playback rate and audio sample rate.
- `--compression-artifacts h264` is model **compression-artifacts conditioning**, **not** FFmpeg H.264 selection or transport compression. The POC accepts model-specific advanced field overrides and verifies serialized values, but neither request serialization nor an apparently successful run proves the cloud honored every optional field. SDK `shiftForAudio` is absent in pinned 0.4.0, so the owner's H3 iOS audio shift setting was not sent.

### Frame transfer and encoding boundary

- The owner reports about **300 MB of downloaded frame data for a final ~35 MB MP4** on the 761-frame run. The [public Draw Things gRPC protocol](https://github.com/drawthingsai/draw-things-community/blob/main/Libraries/GRPC/Models/Sources/imageService/imageService.proto) uses `generatedImages` frame tensors and `generatedAudio`, not a verified MP4 response. Its [server source](https://github.com/drawthingsai/draw-things-community/blob/main/Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift) shows ZIP/FPZIP **per-tensor** response compression and optional chunking, **not** inter-frame H.264 transport. This public source does not prove the absence of any private DT+ endpoint.
- Default frame handling already streams directly to FFmpeg with **no PNG disk writes**, reducing local I/O without reducing network data. `-kf`, `-jpg` or `-nomp4` explicitly request still-frame output. No speculative MP4-download mode or new compression subsystem is justified by the inspected API. Product-facing download progress and longer-video UX belong to **#540**.

**Evidence boundaries:** Owner Windows/provider runs and subjective playback reports are distinct from static upstream/POC source inspection. No development-agent paid generation, visual review, local integration test, model inference or benchmarking was performed to produce this documentation. PR #538 is experimental and does **not** authorize adding MiniMax, Pika or other video checkpoints to the production application (#532); the shared settings work is #539.

## Decision

Storage option B (owner, 2026-11-21): one MP4 per attempt plus a poster image; PNG versus JPG for the poster is left open for the real feature. Option C (animated WebP/GIF) has no audio.

## Not run

Owner did not deliberately smoke-test missing `ffmpeg` on Windows or timeout/error injection. `-nomp4` was exercised for MiniMax's 22-frame PNG smoke. Optional `-kf` retained frames and `-jpg` JPEG output have no confirmed Windows smoke here; neither do a 1280×768 LTX run, native-size Wan run or local Draw Things run. Wan 2.2 refiner wiring is outside this POC.

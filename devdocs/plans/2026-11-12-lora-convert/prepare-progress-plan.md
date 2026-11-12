# Plan: Prepare overlay close rule and real progress (with the LoRA convert feature)

Plan only. No code, no PR. Read against djebaz/dtflow `main` 23f1254 (static reading, nothing executed). Companion to `architecture.md` (converter, PR 1 and PR 2) and `ranking.md`. Files read: `ui/lora_panel.py`, `ui/prepare_lora_editor.py`, `ui/inline_feedback.py`, `ui/activity_strip.py`, `ui/activity_bar.py`, `ui/model_download.py`, `ui/runner.py`, `services/assets.py`, `services/session.py` (`prepare_asset`), `services/lora_store.py`, `backends/drawthings/cloud.py`, `backends/drawthings/cloud_assets.py`, `devdocs/design-system/lora-ux.md`, `pr160-review/progress-bar-spec-review.md`.

## 1. Problem 1: the Prepare overlay never closes

### Root cause (verified in code)

It is a **missing close condition**, not a state bug and not documented intent.

- `LoraPanel.prepare()` creates `PrepareBatchEditor`, which calls `feedback(parent).editor(...)`. That sets `InlineFeedback._editor`, turns on the scrim (`set_editor_dimming(True)`) and enables Escape.
- The only things that remove an editor are `InlineFeedback.reset()/dismiss()`. Their callers are: Escape / outside click, `main_window._workspace_changed` (leaving a page) and `_selection_changed` (backend or credential change). **Nothing in `lora_panel.py` calls dismiss.** `_prepared()`, `_finish_prepare()` and `_present_prepared()` only update the status label, the activity strip and the rows (`editor.completed(...)` writes "Prepared"/"Failed" into the row). `grep` finds ten uses of `_prepare_editor` and none closes it. It is not even set back to `None`.
- After "Prepare N ready LoRAs" every admitted row is locked (`admitted()`), the footer button reads "Prepare 0 ready LoRAs" and is disabled, and the scrim keeps dimming the app. It looks stuck.
- Why it was probably left open: PR #382 was a design PR. The editor keeps outcomes visible per row, and a failed row has Re-Prepare (`_reprepare`), which only works while the editor exists. So "stay open" is right when something failed or was not admitted. Nothing handles the all-ok case, and `lora-ux.md` only states the close rule for the legacy metadata editor ("Close returns to Connections"), not for Prepare.

### Proposed rule

On the end of a run (`_finish_prepare`, and the single-file path in `_present_prepared`):

| Situation | Editor |
|---|---|
| Every row of the batch admitted **and** every outcome is Prepared | **Close it** (dismiss, scrim off) and report in the bar: success result "N LoRAs prepared" (6 s, existing bar result). Clear `_prepare_editor` and `_prepare_editor_context`. |
| Any row Failed or Outcome unknown | **Stay open.** Failed rows show Re-Prepare. Footer button re-enabled by the existing logic. The overlay summary comes from the existing `strip.complete(..., "partial"/"error")` row. |
| Some rows were never admitted (Needs architecture, Name conflict) | **Stay open**; those rows are still the user's to finish. Status line: "3 prepared, 2 need attention". |

Rules for the close itself:
- Do it through one new method on the editor (`close()` → `panel.dismiss()` only if `editor.active()`), never a blind `inline.dismiss()`, so an unrelated armed consent or newer notice is never destroyed. `notice()` already routes to strip rows while an editor is open, so those survive.
- Not while more rows are queued (`_prepare_index < len(_prepare_files)`); only at the real finish.
- No delay or confirmation: the result is already visible in the bar and the Custom LoRAs list refreshes (`refresh_assets()`), which is the thing the user wants to see.

### Second decision for Djé (see open questions)

While a long upload runs the editor sits over the app with a scrim, so Djé cannot see the bar's progress without it competing with the overlay (the bar is under the scrim). Option: **release the overlay as soon as Prepare is admitted** (editor becomes a non-modal result list or closes) and reopen only if something failed. I recommend the simple rule above first; the overlay-release is a follow-up decision because rows' live status is useful for batches.

## 2. Problem 2: the bar has one state

### What exists already (important)

The status-bar `ActivityBar` already renders everything Djé asks for. `BarModel` has `count`, `stage`, `wide`, `fraction`, `elapsed`, `eta`, `stopping`, stop button and tooltip. `ActivityStrip.update(key, done, total, detail=, elapsed=, eta=, stage=, wide=, count=)` feeds it; `model_download.py` and the queue already use `count`/`eta`. **Prepare just never feeds it**: `_prepare_next` calls `update("asset", 0, 0, detail=...)`, which means "unknown total", so the bar is striped with no count, no ETA, and the start call has no stop. The work is therefore a data model and plumbing, not a new widget (so no design-system change beyond bar text).

Two behaviours to respect in `_sync_bar`:
- If the job passes `elapsed` or `eta` text, the strip uses those and **skips its own linear ETA** (`done/elapsed`, shown after 3 s). The generic linear ETA is wrong for phased work (fast convert, slow upload), so Prepare must always pass `elapsed`, and pass `eta=""` when it wants no estimate.
- Success results go to the bar; `partial`/`error` results stay overlay (spec). Prepare keeps that.

### Data available vs missing

| Item | Today | Needed |
|---|---|---|
| Files in batch, index | yes (`_prepare_files`, `_prepare_index`, counts) | none |
| Bytes per file | `os.stat` size, also in `AssetContent.size` | none |
| Per-phase stage text | only the detail line "Preparing 2 of 5. May upload this file" | stage events |
| Measure/hash pass (`_observe`, 1 MiB chunks) | reads the whole file, no callback | optional `on_progress(done,total)`; skipping it in the UI is acceptable (seconds) |
| Retain copy (`LoraStore.retain`, 1 MiB chunks, rehash) | no callback; skipped when the hash is already retained | same, optional |
| Negotiate (`negotiate_asset`): "exists" vs "upload_required" | result only inside `_prepare` | a stage event; decides whether an upload phase exists for this file |
| **Upload bytes** | `upload_signed` is a plain streamed `httpx` PUT. `body()` already keeps a local `yielded` byte counter, 1 MiB chunks (`AssetUploadPolicy.chunk`). **Nothing is exposed** | one optional `on_progress(sent: int, total: int)` argument through `upload_signed` → `backend.upload_asset` → `AssetService._prepare` → `prepare` → `prepare_asset` |
| Upload percent accuracy | n/a | `yielded` counts bytes handed to httpx, not server-acked bytes. It runs ahead by the socket/TLS buffer (about 0.1–4 MiB). On a 200 MB file that is under 2 %. Cap the displayed upload at 99 % until the 2xx response and the receipt confirmation. gRPC is not involved in the upload (HTTP PUT to a signed URL); gRPC DT+ is only the negotiation. |
| Post-upload confirmation (`negotiate_asset` again) | no event | stage event "Confirming on DT+" |
| Convert progress | converter API in `architecture.md`: `ConversionProgress(stage, done, total, message)`, `cancel: threading.Event` | nothing new; just consume it |
| Stop | Prepare has none. `runner.submit` returns a future; cancelling it works (`_prepare` already has a `CancelledError` path that settles the registry as unknown/cancelled) | wire a stop |
| Rate / ETA | none | computed client-side from byte callbacks |

Missing from the provider side: no server-side progress, no remaining-time info. Everything is client-measured.

Threading: `upload_signed` runs on the runner's loop thread, hash/copy/convert on `to_thread` workers. Reuse `LatestRelay` from `ui/model_download.py` (thread-safe, keeps only the newest value, drains on the GUI thread). Promote it to its own module (`ui/latest_relay.py`) in the first PR and import it from both places. The 1 MiB chunk loop makes at most about 100 callbacks a second on a fast link; the relay coalesces them; the model additionally ignores updates closer than 100 ms except at phase changes.

## 3. Progress model

One headless class, `services/prepare_progress.py` (Qt-free, pure, no I/O), owned by `LoraPanel` for the length of one run. The panel feeds it events and renders its snapshot into `activity_strip.update(...)`.

### Phases per file

`checking` (measure, validate, retain copy, negotiate) → `converting` (safetensors rows only, PR 2) → `uploading` (only if DT+ says `upload_required`) → `confirming` → settled (`prepared` / `exists` / `failed` / `unknown`).

### Ordering in a batch (recommendation)

Run **convert for all safetensors rows first, then prepare/upload file by file**, instead of convert-upload per file:
- Gives the counters Djé asked for: "Converting 2/3", then "Uploading 1/5".
- A bad safetensors fails in seconds, before any cloud time is spent; the failed row is marked and the rest continue.
- Keeps the CPU work cancellable by the `threading.Event`, one place, and no overlap of Python conversion and the runner loop. Overlapping convert i+1 with upload i is faster but adds concurrency and ownership questions; later optimisation, not v1.

### Weights (global bar fill)

Fraction is work-weighted by bytes so large files count more. Each file contributes `size` bytes to each phase it has, scaled by a relative cost per phase. Constants live in one table so they can be tuned from the first smoke logs:

| Phase | Work unit | Relative cost per byte (initial) | Notes |
|---|---|---|---|
| checking | file bytes | 0.05 | hash plus retain copy; no per-byte callback needed, jump at the end of the phase |
| converting | tensors from `ConversionProgress`, mapped to bytes of the source | 0.4 | within one file: reading+converting up to 90 %, writing 90–99 %, finishing = 100 % when `convert_lora` returns |
| uploading | `sent / total` from the callback | 1.0 | capped at 99 % until the PUT returns 2xx |
| confirming | one step | 0.02 | |

The initial costs are guesses (nothing measured here). PR A logs measured seconds per MiB per phase (existing `diagnostic_event` pipeline) so the weights can be set from real data after Djé's first runs. A wrong constant only makes the bar less linear; it never makes it lie about completion.

Rules:
- **Unknown at the start:** whether a file will upload. Before negotiation each file is assumed to upload. When negotiation says `exists` the file's upload and confirm weights are removed from the total and the file is settled. The bar is **clamped to never move backwards** (displayed fraction = max(previous, new)); a drop in total only makes the next step bigger.
- Failed or unknown files are removed from the remaining total the same way (they are finished).
- Fraction is `None` (striped bar) only during the very first `checking` of file 1 when no sizes are known yet, normally a fraction of a second.

### Counters shown

Bar `count` is **files settled / files total** ("2/5"). Phase counters live in the stage text, not in `count`, so the bar never has two competing x/y:

- short `stage` (S/M tiers): `Converting 2/3`, `Uploading 1/5 · 60%`, `Confirming`
- `wide` (L tier ≥ 760 px): `Uploading Portrait_Kr2 · 120 / 200 MiB · 11.8 MiB/s`, `Converting Krea 2 · 140 / 420 tensors`
- tooltip (existing): title + detail, so the long sentence stays available

### ETA method

Estimate remaining time from **measured throughput per phase kind**, not a linear extrapolation of the global fraction:

```
eta = Σ over remaining work in phase p of (remaining_bytes_p × weighted_seconds_per_byte_p)
```
- `seconds_per_byte_p` is an exponentially weighted moving average of observed rates (window about 10 s, so one stalled second does not swing it). Upload rate comes from the byte callbacks; convert rate from tensors/second times average bytes per tensor; checking is negligible and estimated from the file size and the first completed file.
- Remaining work includes the current file's remainder plus every later file that has not been found as `exists`. Later files are assumed to upload (pessimistic), so ETA can only shrink when a file turns out to exist. This avoids "5 s left" followed by a surprise.
- Use the upload rate measured in this run for later files; the first file has no history, so no ETA until it has data.

### When to hide the ETA

Show nothing (empty `eta`, but still pass `elapsed`) when any of these holds:
- fewer than 3 s of the dominant remaining phase, or less than 5 % of that phase, or under 8 MiB uploaded of the current file, has been measured (rates are noise);
- the current step is checking, negotiating or confirming and no byte-based phase has data yet;
- no bytes for 10 s: stage becomes `Waiting for DT+ · no data for 12 s` and ETA is hidden until bytes flow again;
- the run is stopping, or the work set just changed by more than 25 % (a file turned out to exist): hide for 2 s, then resume.

Format: `~1:20 left` (existing `clock`), rounded to 5 s above one minute so it does not flicker; once per second via the existing timer. Never show an ETA under `~0:05`; show the stage instead.

## 4. UI states

All inside the existing bar and overlay, no new widget:

| State | Bar |
|---|---|
| Starting, first file | Title `Preparing LoRAs`, striped, stage `Checking files` |
| Converting | `2/5`, stage `Converting 2/3`, fill, elapsed, ETA hidden until the first conversion has rate |
| Checking one file | stage `Checking Portrait_Kr2` (a fraction of a second, mostly invisible) |
| Uploading | `1/5`, `Uploading 1/5 · 60%`, wide `120 / 200 MiB · 11.8 MiB/s`, ETA `~1:20 left` |
| Already on DT+ | file settles at once, count advances, stage `Already on DT+` (this is a normal path, not an error) |
| Waiting/stalled | stage `Waiting for DT+`, ETA hidden |
| Confirming | stage `Confirming on DT+` |
| Stopping | existing stopping look; stage `Stopping after the current file` |
| Done, all ok | bar success result `5 LoRAs prepared · 3:12 total`, 6 s; **editor closes** (section 1) |
| Done, any failure | overlay `partial`/`error` (existing), editor stays open, Re-Prepare on failed rows |

Stop: a Stop button with action text `Stop` and tooltip `Stop after the current file`. During conversion it can stop right away (event, partial output deleted by the converter's cancel contract). During an upload, v1 finishes the current file (no half-upload left in unknown state). Cancelling the in-flight upload is possible (future cancel, registry settles unknown) but produces "Outcome unknown" rows, so it is not offered in v1.

## 5. Plugging into the converter API

`convert_lora(request, progress=cb, cancel=event)` already matches this with no changes:
- `cb(ConversionProgress)` is called on the converting thread, throttled by the converter (about 10 per second plus every stage change). Prepare's `convert_for_prepare` (PR 2) forwards it to the progress model through the same `LatestRelay`. Model maps `stage`/`done`/`total` to the converting phase fraction as in the table.
- `total == 0` (before the key scan) means unknown, shown as striped. `stage` strings (`reading`/`converting`/`writing`/`finishing`) drive the stage text.
- `cancel` is the one `threading.Event` owned by the run; the Stop button sets it and also stops advancing to the next file. The runner shutdown signal sets it too, as `model_download` does.
- `ConversionResult.destination_size` and `destination_sha256` tell the model the true byte size of the converted file for the checking/upload phases of that row (the safetensors size is only a guess for the converted size; the F16 ckpt can differ). Re-baseline that row's weights at that moment.
- A convert failure marks its row Failed with `user_message` + `hint`, takes the row out of the totals, and the run continues.

The converter needs nothing extra for this plan, except confirming in PR 1 that `ConversionProgress.total` is set as soon as the key scan ends and that callbacks keep coming during `writing`.

## 6. PR split

| PR | Scope | Belongs to |
|---|---|---|
| **A. Prepare editor closes when done** | New `PrepareBatchEditor.close()`; close rule in `_finish_prepare` / `_present_prepared`; clear `_prepare_editor`; bar success result; docs (`lora-ux.md` close rule), release note. Small, no backend change. | Separate, no dependency, merge first, can go before convert PR 2 |
| **B. Real Prepare progress** | `on_progress` plumbing (`upload_signed` → `upload_asset` protocol in `core/models.py` → `AssetService` → `prepare_asset`), stage events, `services/prepare_progress.py` model (phases extensible), `LatestRelay` promoted to `ui/latest_relay.py`, panel feeding `activity_strip.update(count, stage, wide, elapsed, eta)`, Stop after current file, phase timing diagnostics for calibration, docs. Convert phase is declared in the model but unused. | Separate; touches `_prepare_next`/`_authorize_prepare`, so it must land **before** convert PR 2 to avoid conflicts |
| Convert PR 1 (refactor) | Unchanged. Only note: keep `ConversionProgress.total` and `writing`-stage callbacks as described. | Convert |
| **Convert PR 2** | `convert_for_prepare` feeds the existing model's converting phase, convert-first ordering, cancel wiring, per-row convert failure, `Converting x/y` states. Uses A and B. | Convert |
| Later | Skip the redundant `_observe` hash pass when the converter already hashed the output; overlap convert and upload; overlay release during run (if Djé wants it). | Separate |

Split rationale: A and B are valuable today for ckpt Prepare (including the 218 MB test files), are reviewable on their own, and they remove the riskiest UI change from the convert PR. A is tiny, so it can be squash-merged ahead of the rest after Djé's smoke.

Conventions: Windows-only (no behaviour here is platform-specific; `httpx`/`to_thread`), no new tests, no local test runs, ruff format and lint, drafts first, PR body `Refs #N`, Development link by Djé, squash subject ends with PR number. A and B each want a Djé smoke on Windows with real DT+ (an upload with a real percent and a stall; an all-ok batch closing; a batch with one failure staying open). Design-system: no new widgets or tokens; bar texts only; any new editor strings go into the `.ui` forms.

## 7. Risks

- Percent is "bytes handed to httpx", slightly ahead of the server. Mitigated by the 99 % cap and the stage text; stated in docs.
- Wrong initial weights make the bar uneven. Mitigated by clamped monotonic fill and logged timings.
- Closing the editor on all-ok removes the place where Re-Prepare lives. Only after full success, when nothing is left to re-prepare.
- Cancel in the middle of upload leaves an "Outcome unknown" receipt; hence Stop-after-current-file in v1.

## 8. Open questions for Djé

1. **Close rule:** close the editor only when every row is Prepared and nothing is left (recommended), or always close after the run and show failures in the overlay?
2. **During the run:** keep the editor open (rows show live status, but the scrim covers the app and the bar), or release the overlay as soon as Prepare is admitted and reopen only on failure?
3. **Batch ordering:** convert all safetensors first, then upload one by one (recommended), or per file convert-then-upload?
4. **Stop:** is "Stop after the current file" enough for v1, with no mid-upload cancel?
5. **ETA:** hide it for the first seconds and when stalled as described (recommended), or show a linear estimate from the start?
6. **Order of work:** do A (close rule) and B (progress) as separate PRs before convert PR 2 (recommended), or fold them into convert PR 2?
7. **Weights:** accept initial guesses plus logged timings, or do you prefer I wait for a real measured run (one Prepare of the Krea 2 and Z Image test files with timing logs) before fixing the table?

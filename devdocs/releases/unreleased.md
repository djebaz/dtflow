# Unreleased

Changes through the 5.1.0 preparation baseline are consolidated in [DTFlow 5.1.0](v5.1.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Generate checkpoint **Details** inspector combines exact-file Draw Things community technical descriptions, independent DTFlow classification and model-profile suggestions, and last-observed provider availability with per-field provenance. Purely local inspection; no generation behavior change.
- Generate on both Local Draw Things and direct DT+ Cloud shows an offline Compute Units meter for supported text-to-image architectures; Checkpoint details lists the CU value and supported models. Unsupported modifiers, selected unknown LoRAs and missing exact metadata use a base checkpoint estimate when its architecture is known. Architecture constants are now pinned in a versioned JSON registry separate from the community catalogue. Unknown/unsupported checkpoint families and image edits show unavailable. This is not verified server billing, quota or authorization; Windows/Draw Things comparison remains pending.
- The Compute Units architecture registry additionally includes all six video families represented in the bundled exact-file catalogue (WAN, Hunyuan Video, SVD, LTX 2.3 and MiniMax H3 variants). A separate, read-only Python video estimator requires explicit output frames and relevant conditioning; it is not displayed in the current image-only Generate UI. Source mapping alone does not establish numeric parity with Draw Things or DT+ billing.

- Queue item table with saved-result thumbnails, prompt/model/LoRA/parameter columns, observed durations and an item inspector with Open in Images. No dispatch or database-schema change.
- Development-mode diagnostics summarize queue thumbnail outcomes/timings and record Open in Images handoff outcomes without prompts, image identities, paths or raw error text.
- Cloud LoRA Prepare remembers its picker folder per runtime and accepts multiple converted `.ckpt` files for sequential preparation. Count-based progress and final summaries distinguish confirmed, failed, unresolved and unstarted files; selected weights are preserved, uncertain files are never automatically retried, and failures emit privacy-safe diagnostics.

## Changed

- Generate Compute Units meter uses a non-linear scale (linear to 100 = 15 %, then logarithmic through 3 000 = 50 % to 40 000+ = full; only 40 000+ fills the bar) so typical estimates are readable; the red threshold is unchanged.
- Generate Checkpoint selector gains small previous/next buttons that step through the current filtered list without opening the editor; they stop at either end (with nothing selected, Next takes the first and Previous the last), keep selected LoRAs and the sampler while browsing, and prune incompatible LoRAs only once stepping pauses.
- In the Checkpoint search editor and composer choice editors, double-clicking a name or pressing Enter applies it like **Apply**, including with a search filter active; Enter with no match moves the text to the identifier field instead of re-applying the old choice.
- The Queues composer's LoRA sets use the same checkbox and weight-slider editor: each Add creates one set from the checked LoRAs (starting from Generate's LoRAs, none checked is a set without LoRAs); clicking a set chip edits it in the same editor. Both Queues LoRA editors list every LoRA the backend reports (not filtered by Generate's checkpoint, since a queue may cross several).
- The Queues composer's LoRA weights row becomes chips with the same checkbox and weight-slider editor, offering any LoRA (not only those on the Generate form): check a LoRA and press + (or click anywhere on its slider) to collect several weights for it, and Edit… reopens the editor on the current weights, where Apply replaces the chips with one `IDENTIFIER: weight` chip per LoRA and weight. The row lists one line per LoRA with its weights as small chips, and the planner input is unchanged.
- **Add LoRA** becomes **Edit LoRAs**: a taller editor to check or uncheck LoRAs and set each weight with a 0.0–3.0 slider in 0.05 steps (default 1.0; one mouse-wheel notch moves it by 0.05); Apply or Enter replaces the selection once. Every LoRA starts at 1.0; catalogue weight suggestions now appear only in the tooltip. The mouse wheel over a slider adjusts its weight.
- **Edit as new queue…** (a button beside Relaunch, also in the list's context menu) loads a text-to-image queue into Generate and the composer (the dimensions that differ between its items become Cross, Pair or Repeat chips), so it can be changed and saved as a new queue.

## Fixed

- BYOM consumes usable known response branches independently instead of rejecting a successful response because an unused branch is malformed or both known branches are present. Log sanitized response structure before interpretation; distinguish local HTTP 200 interpretation warnings from API rejection. Unknown metadata lookup entries stay unknown; exact-reference/checksum and safe upload checks remain mandatory. Source only, runtime acceptance pending.
- API failures now retain bounded, sanitized server explanations in WARNING logs and shared connection/generation/asset presentation, including HTTP error envelopes and gRPC messages such as throttle-policy rejection. Preserve statuses and uncertainty instead of implying every permission denial is a bad key; never log tokens, prompts, signed URLs or image payloads. No retry/billing behavior changes; source-only delivery, acceptance pending.
- Prepared-asset dispatch checks preserve the underlying API explanation in the generation warning and inline failure instead of reporting only `prepared_asset_unknown`; local validation reasons remain distinct, and blocked generation is not submitted.
- Prepared-asset failures identify the affected LoRA by sanitized filename in the inline message and generation WARNING, without paths or private cloud references.

- The Queues composer's preview table shows every row in full instead of a short window with its own scrolling, so every item's values are reached by scrolling the composer. Its LoRA weight columns are titled with the LoRA name only (no `lora_weight.` prefix or file extension; the full name is the header tooltip), and the columns share the composer's width with elided header and cell text instead of scrolling sideways.
- Krea 2 checkpoints (`krea_2_raw` and `krea_2_turbo`, q6p and q8p) move from the Niche to the Prime catalogue, so the Krea 2 family shows in the Generate Base model list without Include niche models.

- Restore the status-bar About icon to 16 px within its existing 20 px button, matching the compact checkpoint-details control without padding that squeezes the glyph.

- Queue previews now require live committed gallery records, revalidate cached availability and use owned shutdown-safe callbacks. Open in Images uses mapped availability, clears conflicting browse filters and reports missing results; composing a queue hides old item evidence.

## Release audit

- PRs: #150, #151, #152, #153, #154, #156, #157.
- Scope: Post-5.1.0 Queue item-table/inspector and Prepare delivery (#150/#151), checkpoint metadata inspection (#152), local CU estimation (#153), Generate/Queues refinements (#154), and bounded API response diagnostics (#156) are merged source. PR #157 adds BYOM branch tolerance and pre-interpretation observations, pending merge. Scoped acceptance remains separate from source presence; not a published feature release.

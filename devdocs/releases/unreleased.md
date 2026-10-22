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
- **Add LoRA** becomes **Edit LoRAs**: a taller editor to check or uncheck LoRAs and set each weight with a 0.0–3.0 slider (default 1.0); Apply or Enter replaces the selection once. Every LoRA starts at 1.0; catalogue weight suggestions now appear only in the tooltip. The mouse wheel no longer changes sliders while scrolling a panel.

## Fixed

- Restore the status-bar About icon to 16 px within its existing 20 px button, matching the compact checkpoint-details control without padding that squeezes the glyph.

- Queue previews now require live committed gallery records, revalidate cached availability and use owned shutdown-safe callbacks. Open in Images uses mapped availability, clears conflicting browse filters and reports missing results; composing a queue hides old item evidence.

## Release audit

- PRs: #150, #151, #152, #153.
- Scope: Post-5.1.0 Queue item-table/inspector delivery and review corrections (#150) and Prepare folder persistence/sequential multi-file preparation (#151) are merged; #152 adds checkpoint metadata inspection and compact About sizing correction; #153 adds local Compute Units estimation and its meter for both Local and direct DT+ Cloud, pending merge. Scoped acceptance remains separate from source presence; not a published feature release.

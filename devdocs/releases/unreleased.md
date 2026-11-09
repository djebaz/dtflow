# Unreleased

Changes through 6.2.0 are consolidated in [DTFlow 6.2.0](v6.2.0.md); its published GitHub release body records final acceptance. This file tracks subsequent changes.

## Highlights

## Added

- Connection > Custom LoRAs now shows the locally retained converted LoRA files and total disk usage. Select a copy and click **Remove selected** or press **Delete** to remove it immediately; only the managed local file is removed. There is no opt-in preference, deletion confirmation, or automatic expiry. Source-reviewed only; Windows acceptance pending.

- F10 opens the Logs workspace from any page and, pressed again, returns to the previous page (same as the Logs status button). It follows the other workspace function keys, never starts generation, and the button tooltip now names it. Source-reviewed only; no local tests or renders.
- Ctrl+Enter (including keypad Enter) launches the enabled action from anywhere in Generate, Workflows or the Queues composer—not just a focused prompt field. It uses the current Generate/Edit image, Run workflow, Run Script or Start now/Add to queue button, including Start/Start next for a selected queue, without bypassing disabled states, confirmations or popups; Setup, Continue, Pause, Resume and Save paused are excluded.
- Images slideshow: F6 or the Slideshow toggle beside the buffered count opens fullscreen Detail, looping through the filtered gallery at 0.5–8 seconds per image. The right-hand thumbnail carousel is replaced by a scrollable grey prompt/LoRA panel reusing the info card, and restored on exit. Immersive playback hides the first header and floats the pager plus top-right Forward/Backward toggle controls over the picture; both auto-hide after 2.5 seconds of inactivity. A numbered photo slider seeks on release, and F6 works across sibling shell focus. Rate and direction persist in the existing viewer preferences, never playback resume. Drag/wheel/held-arrow positioning pauses new image admission and commits only the final target, without old-result slider snapback. Controls become opaque on hover/focus; wheel over the position slider moves 10 photos per step, wheel over other controls adjusts rate, and Left/Right navigate manually. Images remain visible during decoding and use a short crossfade after successful loading. Playback stops on exit/navigation/shutdown and never starts generation. Source-reviewed only; native acceptance pending. See [plan](../plans/2026-11-08-images-slideshow.md).
- F5 opens Images from any workspace and enables Live + Follow with newest-first sorting, returning to the newest matching image without changing filters or the selected view. Repeating F5 resumes following; it never submits generation or starts a queue.
- Ctrl+Tab cycles Generate → Workflows → Queues → Images → Generate, entering at Generate from Connection or Logs without changing Images Live/Follow settings or submitting work.

## Changed

- DT+ LoRA resolution now emits one diagnostic summary per scoped operation, counting local reuse, verified matches, relinks, restores and failures without repeating per-LoRA success records. The Generate/Queue feedback overlay names restored LoRAs and reports affected names on resolution errors. Routine reuse/match summaries are verbose-only; relinking/restoration remains visible in normal logs. Safe pinned DT+ key attribution now accompanies key switches, relevant generation/Queue/provider diagnostics and asset summaries (opaque digest and `key_alias`: an explicit saved name, safe shortcode or digest-based label; never raw key material). Asset-row states once left at `intent` by per-key Prepare are now settled without overwriting other keys' confirmed receipts, and Logs gives Search less room to prevent filter overlap at narrow widths. Source-reviewed; owner-reported CI and native/DT+ acceptance require revalidation.
- Logs can select a bundled, source-generated Component before its first logged event; Event/characteristic discovery happens on explicit Refresh, not startup, Logs open, or Follow. Component/Event selectors are wider, Search is narrower, and filter settings are durable across restarts. The new persisted Compact toggle switches resident rows between one terminal-like line and the wrapped view without requerying logs.
- Queue polls skip full panel rendering only for unchanged *idle* results; active runs always redraw their Queue status/items so live progress remains visible even when persisted snapshots are unchanged. Returning from Logs to Queues now forces a fresh read/render, including completed History entries and the selected Queue detail. Thumbnails revalidate less frequently; Open in Images no longer blinks during background checks because the Images workspace validates the saved identity on click.

- Restyle the shared Images gallery link above ordinary right inspectors with selection-blue navigation states, a 14 px existing image icon and keyboard focus, rather than run-action accent blue. Keep navigation and responsive placement unchanged; align the web reference and document Qt styling differences. Source-reviewed only; no local tests or renders.

## Fixed

- The local DT+ completed-image counter no longer opens the unrelated image-recycling ledger and avoids a duplicate startup read. On read failure, Connection shows a visible unavailable/stale notice even when key rows exist, and Logs provides the sanitized persistence stage and reason instead of only `PersistenceError`. Counts still come solely from saved attempt history; no provider calls, extra store or speculative retries. Source-reviewed; the original Windows failure code and recovery remain unverified.

- DT+ custom LoRA dispatch now trusts confirmed receipts per API key, avoiding routine BYOM checks. A schema-3 side table preserves independent remote references for different keys and for different filenames sharing bytes without attributing legacy receipts to an unverified account. First use under another key batch-checks the affected LoRAs; queued parallel generations, scripts and workflow branches share in-flight checks. A relevant asset-related provider rejection triggers one coordinated recovery per distinct LoRA/key episode, holding new queue/workflow admission while restoration proceeds; unrelated later failures remain eligible without a permanent session-wide recovery lock. Restored assets can adopt a new server reference when verified content is identical, and an in-progress restoration is cancelled when its last consumer stops. Ambiguous transfers retain their unknown outcome rather than being replayed.
- Retained LoRA file deduplication now verifies existing bytes, repairs a damaged indexed copy from verified source bytes on a later Prepare, and avoids overwriting a different file on name collisions. User data, outputs and historic receipts remain separate.

- Repair the offline Connection startup render fixture to supply the current credential-status shape (`keys` and `environment`), avoiding `KeyError: 'environment'` during the full UI render. Runtime credential behavior is unchanged. No local tests or renders were run; render completion remains unverified.
- Register the existing hidden-Connection counter and WARNING-and-worse Logs render states. Counter fixtures clear the earlier synthetic saved key and wait for credential identity rather than obsolete labels; the synthetic mapped-image presentation decodes its known local image without claiming cross-runtime managed-original verification. Decode failure now fails that fixture rather than saving a blank decoded state.

- Slideshow transition follow-up: keep Detail image scaling consistent across fade boundaries and target a fixed 60 updates/second. At owner request, the outgoing photo fades out in half of the incoming fade-in duration, with eased opacity on both. Owner accepted the asymmetric fade at 60 FPS; the subsequent slightly faster fade-out remains pending owner smoke.

## Release audit

- PRs: #321, #322, #323
- Scope: Repair offline render fixtures/catalogue, restyle the shared Images navigation link, add F5/Ctrl+Tab navigation and the Images slideshow.

- Keyboard navigation: F1 Generate, F2 Workflows, F3 Queues, F4 Images, F6 slideshow and F12 Connection. Keep F5 Live+Follow, F11 fullscreen, Escape and Ctrl+Tab unchanged. Slideshow rate adjusts in 0.1-second steps; restore the preferred half-duration fade-out at 60 Hz. Latest changes await owner smoke; no tests or renders run.

- Slideshow photo-position slider: 10-photo wheel steps, a 200x36 px minimum hitbox plus the adjacent photo-number label, retaining the original small handle; coalesced final-only image loading remains unchanged. Native acceptance pending.

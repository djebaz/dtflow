# Images — Option 2a hybrid pagination and lazy scrolling

Status: **planned; visual reference not yet integrated**  
Base: `main` (2026-10-17, fetched head `9e908cd9b6c2df498b70969e86d1145f98335fe5`)  
Scope: Images Grid, List, Detail, and the existing fullscreen/focus mode.

## Approved visual reference

Implement **Option 2a: Hybrid — infinite scroll + position scrubber + jump chips** from the user-supplied `Images Pager Mockup.dc.html`. Commit its standalone HTML reference under `devdocs/mockups/images_hybrid_pager_2a.html` in the next checkpoint. This is the selected option, **not** variants 1a, 1b or 1c, nor a new fullscreen layout.

The 2a pager is a compact **46 px toolbar** using existing DTFlow dark tokens: left current *visible* image range and total (`81–104 of 342` example), center loaded/loading status (`160 loaded`), right 26 px high page-jump chips corresponding to 80-image data batches. The active chip tracks the batch of the first visible image. It is a single persistent Qt control mounted immediately above the gallery viewport, with **identical position, dimensions, styling and semantics in normal and fullscreen/focus modes**. Fullscreen may hide secondary header, filters, selection bar and detail metadata, **but never the pager**. Do not create a floating, auto-hiding, or separately implemented fullscreen pager.

The HTML mockup is illustrative: synthetic images/counts, not a runtime contract or proof of visual parity. Keep the production selection semantics: DTFlow's current **Select all** means all matching outputs, subject to the existing 10,000-ID limit; it must **not** be silently replaced by the mockup's `Select all N loaded` text.

## Current source contracts to preserve

- `src/dtflow/ui/gallery_page.py` currently uses `PAGE_SIZE = 80`, `GalleryQuery.offset`, a bounded read service, asynchronous worker delivery, six concurrent thumbnail decodes, refresh anchors and durable viewer preferences.
- Grid/List wheel-at-edge changes the current page, replacing its widgets; `Ctrl+PageUp/PageDown` also moves pages. Detail previous/next crosses page boundaries. The displayed range is currently in the Refresh tooltip.
- `src/dtflow/ui/gallery_detail_preview.py` implements fullscreen/focus mode and hides surrounding widgets. Do not accidentally hide the new pager.
- Gallery state can be read from a browsing-only snapshot; all existing prohibitions on snapshot-backed mutations remain.
- Selection, export, deletion consent, auto-refresh, sorting/filtering, zoom, selected Detail image and native file leases must preserve their existing authority and behavior.
- Qt Designer `.ui` forms, QSS, `tokens.json`, `DESIGN.md`, and `tools/ui/render_states.py` are canonical. Do not edit generated `Ui_*` code.

## Implementation checkpoints — push each coherent phase immediately

### 0. Plan and reference (first commits)

1. **This plan first**: push plan as first branch commit and open draft PR immediately, before editing runtime files.
2. **Approved mockup**: commit a readable, locally openable standalone HTML rendering of *2a only* at `devdocs/mockups/images_hybrid_pager_2a.html`. Preserve visual measurements and interactive scrolling/chips; document any deliberate differences from the original `.dc.html`. No unused mockup variants or bundled third-party JS.

### 1. Canonical Qt component, layout and offline previews

3. Add the single shared Qt pager widget and canonical Designer slot. Keep consistent 46 px toolbar placement above gallery content in both normal and fullscreen modes. Use shared QSS/token styles and accessible labels, focus/disabled/loading states. Keep navigation presentation separated from actual query service/worker logic.
4. Extend `tools/ui/render_states.py` with offline gallery fixture renders: empty/one batch, multiple batches, active chip, loading, direct jump, narrow 800×680, Grid/List/Detail, and all three fullscreen modes. Use fake fixtures only; no network/provider calls.
5. **Render and review snapshots before live wiring**. Review visual alignment against the approved HTML and DTFlow native design at normal/narrow/fullscreen sizes. Adjust only the presentation/layout until consistent. Commit these visual adjustments separately. A screenshot from offscreen Linux does not establish native Windows/DPI acceptance.

### 2. Live hybrid loading and navigation

6. Extend gallery browsing to maintain a **bounded loaded batch window** rather than replacing the page on each wheel transition. Prefetch the next/previous 80 records near viewport edges without a jump, append/prepend with stable scroll anchor and identity, and keep thumbnail decode concurrency bounded. Allow targeted chip jumps without iterating through thousands of intervening Qt cards. The small synthetic HTML reference grows all batches up to a chip; for large libraries, direct target retrieval with bounded content is the necessary performance-safe interpretation. Never allocate an unbounded all-results widget list.
7. Derive the active chip and visible range from the **first visible item**, not the most recently fetched batch; respect variable Grid columns/thumbnail size, List row heights, and Detail focus. Reconcile out-of-order/stale async responses, zero results, last partial batch, filters, sorting, and deletions.
8. Preserve checked IDs across page jumps and lazy-load eviction, but never count a merely loaded image as checked. Preserve Detail previous/next navigation and fullscreen consistency. Keep snapshot browsing read-only; refresh anchoring should not yank the visible position after new outputs.
9. Persist a stable browsing anchor and batch/offset as appropriate via existing viewer state, with backward-compatible reading; never persist bulk thumbnails or page-record caches.

### 3. Verification and handoff

10. Extend relevant focused deterministic tests for page-boundary prefetch, chip jumps, first-visible active page, stale replies, scroll anchoring, total changes, selected Detail crossing, fullscreen survival, and snapshot fallback. Use existing test conventions; no billable inference or deletion.
11. Re-run focused offline UI renderer, inspect all specified screenshots, check Qt/Designer loading, lint/format/syntax and targeted tests where environment permits. Update `README.md`, desktop guide, `DESIGN.md` and `devdocs/releases/unreleased.md` for behavior changes.
12. Push each completed phase to this PR as a coherent commit as soon as it is ready, recording what actually rendered/was tested and what remains pending. No merge until the user's visual review.

## Acceptance criteria

- One visual and functional **Option 2a** pager at the **same location in normal and fullscreen**, with 46 px toolbar, visible range, loaded/loading status and page chips.
- Grid/List keep smooth continuous scroll across 80-image boundaries; Detail works across pages. Chip navigation supports nonadjacent batches.
- Visible position determines active page; neither asynchronous loads nor auto-refresh cause unintended scroll jumps.
- Memory, decode work and mounted widgets stay bounded on very large libraries.
- Checked images, saved viewer state, active confirmations, snapshots' read-only authority and original-file safety are preserved.
- Renderer covers normal, fullscreen, narrow, selected, loading and empty cases; real Qt snapshots are reviewed against committed standalone mockup.
- Source/live-runtime validation is reported honestly and separately from render evidence and Windows owner acceptance.

## Non-goals

No new gallery backend protocol, no provider generation, no expensive inference, no redesign of selection/export/removal, no new separate fullscreen pager, and no committed generated image artifacts.

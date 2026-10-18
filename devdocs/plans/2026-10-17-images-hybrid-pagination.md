# Images Option 2a — hybrid pagination and lazy scroll

Date: 2026-10-17  
Status: Qt presentation and bounded hybrid scrolling integrated; native owner acceptance pending  
Base: latest `main` when branch was created

## Approved UX contract

Implement **Option 2a** from the user-provided `Images Pager Mockup.dc.html`: **Hybrid — infinite scroll + position scrubber + jump chips**. The compact pager displays a visible range (e.g. `81–104 of 342`), a loaded count, and numbered jump chips. A chip indicates the batch currently in view, not just the most recently requested batch. Existing `PAGE_SIZE = 80` remains the query batch size. Additional batches appear during normal vertical scrolling, prefetched before the edge; users can also jump to an arbitrary batch without manually scrolling through preceding batches. Keep the mockup's visual treatment and do not substitute a conventional Prev/Next-only footer.

**Consistency:** One canonical Qt pager widget remains **visible in the same relative position with the same geometry, controls, states and behavior in both normal and fullscreen Images**. No floating, auto-hiding, repositioned or separate fullscreen pager. Grid, List and Detail share this state; Detail's image Previous/Next continues to work across batch boundaries. Preserve the existing full-screen collapse of secondary chrome without hiding the pager.

**Safety:** Keep gallery access read-only; never authorize mutations from browsing snapshots. Selection checks remain based on stable image IDs, persist across loading and page jumps, and retain DTFlow's existing `Select all matching` across-pages semantics (up to 10,000), rather than changing to the prototype's `Select all N loaded`. No provider calls.

## Canonical sources

Follow `AGENTS.md`, `DESIGN.md`, `README.md`, `devdocs/current_dev_status.md`, `devdocs/plans/initial.md`, `devdocs/plans/releases.md` and `devdocs/releases/unreleased.md`. Main UI sources: `src/dtflow/ui/forms/gallery_detail_page.ui`, `src/dtflow/ui/gallery_detail_preview.py`, `src/dtflow/ui/gallery_page.py`, shared QSS/tokens, gallery query services and `tools/ui/render_states.py`. The extended offline rendering script merged into `main` on October 17 is the primary iterative visual evidence; it cannot establish native Windows/DPI acceptance. Designer owns layout; controller owns state; shared QSS owns visual styles. Avoid new dependencies.

## Incremental review workflow — push each phase as its own commit

### 0. Plan and PR — **first commit, immediately**
- Publish this plan on a dedicated branch from latest `main`, and open a **draft PR before any implementation**.
- Push each subsequent coherent phase as soon as it is complete, letting CI and reviewers inspect it incrementally. Keep the PR draft until the desired checks and owner review.

### 1. Canonical pager presentation (commit 2)
- Add the compact shared pager to the tracked Designer Images form and minimal presentation wiring. Use existing design tokens/QSS and accessible names/keyboard focus.
- Place the component adjacent to the browsing viewport; ensure fullscreen visibility and the exact same widget in every mode.
- Add states for zero results, one batch, multiple batches, loading/error and narrow width.
- Push immediately; no live query model redesign in this phase.

### 2. Offline renderer and snapshots (commit 3)
- Extend `tools/ui/render_states.py` using fake offline Images projections, never a live backend. Render pager in normal and fullscreen Grid/List/Detail, at multiple active batches, during loading and at empty/boundary states.
- Capture state names and visual output; compare geometry, contrast, truncation and fullscreen placement with Option 2a. Repair visual issues before advancing.
- Push immediately. Renders/CI must report only checks actually run.

**Artifact layout:** Approved pager captures are collected under `image-navigation/`, shown as **Image Navigation** in the Actions summary (not a slash-separated category). Other preview names and locations remain unchanged.

### 3. Live hybrid browsing (commit 4)
- Make Grid/List vertical scroll load further 80-record batches seamlessly; prefetch before reaching the bottom. Expose direct jump-chip navigation, correct visible-range/active-chip tracking, and a bounded window for widgets and decode work so gallery growth does not exhaust memory.
- Retain stable ordering, filter/sort state, update-safe anchor retention during auto-refresh, existing checked identity semantics and snapshot read-only restrictions; don't submit redundant parallel queries.
- Detail Previous/Next traverses batch edges; preserve one shared pager state and scroll continuity when entering/leaving fullscreen.
- Push immediately.

### 4. Alignment, regression and docs (commit 5)
- Re-render all added normal/fullscreen views and compare screenshots. Fix discrepancies in the same branch, documenting remaining acceptance gaps.
- Update `DESIGN.md`, developer/desktop guide and Unreleased changes. Run relevant source checks and offline tests according to the canonical agent environment prerequisites. Do not run billable inference/training.
- Push immediately and leave PR ready for owner visual review; no automatic merge.

## Acceptance checks

1. Identical pager position, dimensions and operation with fullscreen toggled; no image overlap or hidden pager.
2. Wheel scroll keeps flowing across batch boundaries; explicit chip navigation works without manual traversal.
3. Visible position, total and loaded count are accurate, including after filters, sorting, refresh and jumps.
4. No unbounded gallery widgets, duplicate requests, stale thumbnail assignments, or regression in six-task decode bound.
5. Checked IDs and detail focus survive navigation; `Select all` remains across matching outputs, not merely loaded items.
6. Read-only snapshot browsing supports pager, but source-authorized actions remain unavailable.
7. Offline rendering remains provider-free; normal/fullscreen captures expose design mismatch before live integration.

Record what was actually verified and what still needs owner smoke testing.

## Progress and current evidence (2026-10-17)

- **Completed:** approved [standalone 2a HTML reference](../../devdocs/mockups/images_hybrid_pager_2a.html); canonical 46 px Designer pager and bounded jump chips; shared position normal/fullscreen; corrected chip clipping; Qt offscreen fullscreen virtual display at 1600×1000; 19 multipage/navigation screenshots organized in **Image Navigation**. Preview now simulates 80-image batches, direct jump to the partial last page, loading and large-library ellipsis.
- **Completed:** modern 36 px centered Qt ring in its own Designer form. During first entry, live DB or snapshot fallback with no committed cards, appear after 180 ms; loaded cards remain usable during refresh. Empty-read failure becomes a Retry state. Additional offline loader screenshots included. Do not confuse it with generation or remote progress.
- **Evidence:** run [39040948400](https://github.com/djebaz/dtflow/actions/runs/39040948400) passed UI renders; CI [39040948377 attempt 2](https://github.com/djebaz/dtflow/actions/runs/39040948377) passed **612 tests, 3 skipped** after an intermittent unrelated queue reservation test timeout on attempt 1. This is not native Windows/DPI acceptance.
- **Pending production phase:** live contiguous append/prepend, bounded Qt window, active chip from visible viewport, jumped batch fetching and snapshot fallback. Existing per-80 replacement navigation is a transitional implementation, not yet hybrid.
- **Safety gates:** keep cursor/anchor while clipping the loaded window, six decoder concurrency, mutation authority and snapshot warnings. Never infer that screenshot mocks prove the background paging service.

## Live browsing flicker and instrumentation follow-up

The owner reports working pagination but excessive repaint/blinking in source smoke. Reconciliation now avoids repeat density sizing of retained cards; the Grid layout skips unchanged cell placements instead of removing/reinserting every card. Auto-refresh (five seconds) skips concurrent batch reads and filter debounce; while existing images remain displayed it no longer toggles the pager into a disabled/loading state. Source/snapshot changes still reconcile and the read-only snapshot recovery warning remains authoritative. User validation of smoothness is pending.

Explicit `-x --dev` diagnostics use existing rotating sanitized logs, with `gallery_perf` query/batch/Qt layout/thumbnail timings. These are elapsed application measurements only; no benchmark or performance improvement is claimed without field observations. See [development guide](../guides/development.md#images-performance-diagnostics--x---dev).

## Owner-reported incorrect navigation (log 17:09–17:10)

The attached development log shows a direct read of the final 78-image batch at offset 720, followed by backward adjacent batch reads. All recorded operations succeeded; the log does **not** disclose the exact user action or resulting viewport, so this is not proof of the reported symptom's cause. Source inspection independently found a navigation issue: every `QScrollBar.valueChanged`, including Qt reflow, jump and anchor restoration, could schedule an adjacent prefetch. The controller now schedules boundary reads only on user Wheel events, scrollbar actions and slider drags. Programmatic scrollbar changes still update the visible-range chips but cannot start another batch read. A provider-free renderer regression covers that distinction; `-x --dev` now reports `phase=page_jump` with batch/page offsets for further diagnosis. **Pending:** latest CI/render checks and owner Windows smoke; do not declare navigation fixed solely from a code change.

## Confirmed navigation regression: downward scroll fetched previous batches (owner 17:18–17:19)

The owner explicitly confirmed that they scrolled **down** after a jump to page 9, yet the gallery moved **backward**. The development log records `page_jump page=9 target_offset=640` followed by `adjacent_start direction=backward offset=560`, then offset 480 and 400. The previous `_maybe_load_boundary()` made its backward/forward decision only from which loaded-window edge was nearby. The top of a newly jumped page is near the *backward* threshold; an input that scrolled down could therefore fetch earlier batches and move the visible window backwards. The earlier guard against Qt programmatic `valueChanged` events was necessary but insufficient.

**Correction:** Pass an explicit direction (+1 forward/down, -1 backward/up) from Wheel events, scrollbar actions, and user slider drags into `_maybe_load_boundary(direction)`. Downward input checks only the forward edge; upward input checks only the backward edge. Retain suppression of programmatic scrollbar-change prefetch and leave auto-refresh/snapshot read authority unchanged. Debug mode records `gallery_perf phase=scroll_intent direction=forward|backward source=wheel|scrollbar|drag` with numeric offsets and counts. The provider-free renderer asserts no backward read on downward scrolling near the start of a jumped batch, and the symmetric upper/lower boundary cases. This is **source-verified and synthetic-fixture-covered**, not yet confirmed in a new native Windows smoke test.

## Grid/List thumbnail zoom and paging (2026-10-17)

Source review found that changing the 25–250% thumbnail scale rebuilt Grid column positions and List row heights while retaining an absolute scrollbar value. With an 80/160-image mounted window, this can move the top visible record and activate an unrelated numbered page without the user navigating. The correction captures the first visible saved-output identity and its viewport offset before the first slider change, coalesces rapid slider events, and restores that identity after Qt layout/reflow settles. Grid adjusts the scroll position using the reflowed card location; List scrolls its retained row back to the top (and retains a partial-row offset in pixel-scrolling mode). Intermediate scrollbar events cannot trigger adjacent reads or transient pager-chip updates. A view switch at the same scale no longer re-sizes every existing card, while the first real query still sizes all newly created Grid/filmstrip entries.

**Evidence:** provider-free offline `images-89-pager-resized-grid.png` and `images-90-pager-resized-list.png` assert anchor retention, page-2 chip consistency and no adjacent-read submission. The initial implementation at `4d1c14e` passed [CI (612 tests, 3 skips)](https://github.com/djebaz/dtflow/actions/runs/39047170941) and [226 UI renders](https://github.com/djebaz/dtflow/actions/runs/39047170955). The intermediate-pager-update guard passed subsequent CI and offline UI renders; actual Windows scrolling/zoom acceptance remains owner-pending. No generation or billable inference performed.

## Upward infinite-scroll continuity at batch boundaries (2026-10-18)

**Owner-reported defect:** Scrolling *up* from the top of an 80-record batch jumped to the **first card of the preceding batch** instead of showing its **last card directly above the current card**. This breaks the approved infinite-scroll contract even though the preceding batch is queried successfully.

**Implementation:** The existing append/prepend API remains bounded to 160 mounted cards. For backward prefetch before reaching the viewport's top edge, maintain the first-visible identity and its original viewport offset without changing the visual position. If the user has reached the top of the mounted window and an earlier batch arrives, position the viewport on the **last row of that earlier batch**, with the previously visible cards immediately below. Grid waits one Qt event-loop turn after reflow before applying the scrollbar position, because early restoration was reproducibly clamped to zero while Grid cards' Y coordinates were still pending. List positions its preceding final row with the normal row-aware scrolling API. Programmatic geometry movement must never submit adjacent requests. Retain all-matching selection, snapshot authority, and selected Detail state.

**Verified evidence:** The offline renderer uses **real** `_adjacent_done` with synthetic 80-record page data to assert page-2 → page-1 prepend shows image **80**, not image 1, in both Grid and List; the List retains image 81 below; early prefetch retains its anchor instead of jumping backwards. [CI run 39092328625](https://github.com/djebaz/dtflow/actions/runs/39092328625) completed with **612 passed, 3 skipped**; [UI renders 39092328607](https://github.com/djebaz/dtflow/actions/runs/39092328607) produced **228 states** including `images-91-pager-prepend-previous-last-grid.png` and `images-92-pager-prepend-previous-last-list.png`, both under **Image Navigation**. Source/fixture checks are not a native Windows scroll, high-DPI, or trackpad acceptance. No real inference, training or billable provider operations were executed.

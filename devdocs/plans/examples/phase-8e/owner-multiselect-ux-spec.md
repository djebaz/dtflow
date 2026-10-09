> Owner-provided reference, imported 2026-10-09. The source specification is preserved below; it contains proposals and historical layout/API observations, not verified current implementation. The [8E implementation subplan](../../2026-10-09-phase-8e-implementation.md) records approved overrides: export remains in Connection, checked-image export is added there, deletion uses Windows Recycle Bin only, and all-filtered selection resolves a bounded explicit-ID snapshot. Current canonical source and DESIGN.md govern layout. Do not reproduce the old sidebar, Masonry, negative-prompt desktop filter or List-side preview.
>
> Tracked visual references: [Grid checkboxes](grid-multiselect-reference.png) and [List checkbox column](list-multiselect-reference.png). These owner mockups establish selection affordances, not a replacement app shell.
# VERSION$00001$ | Edited: 09/10 | TIME: 17:37

# DTFlow — Gallery Multi-Selection and Bulk Actions

**Status:** Proposed implementation specification; not implemented or verified.  
**Scope:** Phase 8E, Images workspace (Grid, List, Detail).  
**References:** Two project Library mockups (`DTFlow Dark Mode Image Gallery.png`, `image-gen-3(8).png`); `AGENTS.md`; `DESIGN.md`; `devdocs/current_dev_status.md`; `devdocs/plans/2026-10-05-phase-8a-viewer.md`; `src/dtflow/ui/gallery_page.py` on repository `djebaz/dtflow` main, inspected October 9, 2026.  
**Status of UX decisions:** This document proposes new choices where the references do not establish agreed behavior. These should be owner-approved before implementation.

## 1. Goal and non-goals

Add predictable multiple-image selection and contextual bulk actions without rebuilding DTFlow's existing Grid/List/Detail layouts, filters, sorting, bounded loading, asynchronous decoding, or Detail viewer. The experience should be useful for hundreds or thousands of saved images, accessible from mouse and keyboard, and safe when files are missing or state changes while a user is selecting.

**In scope:** selection state and gestures, contextual action bar, selection count, cross-view continuity, selective export, copy paths, folder access for a focused item, and explicit destructive-action UX for bulk deletion if backed by an authorized safe service.

**Out of scope:** masonry (withdrawn from Phase 8A), new negative-prompt desktop filter, gallery reindexing, tagging, moving source files, direct generation, remote/provider deletion, and a new layout system. Selection must not trigger generation, changes to original images, or billable operations.

## 2. UX principles

1. **Separate focus from selection.** The focused item is the one currently previewed in Detail. The checked set is the collection to act on. Opening an image must not clear or replace that set.
2. **Make actions explicit.** Selecting is non-destructive. Dangerous operations require explicit confirmation and describe their exact scope.
3. **Preserve orientation.** Filters, sorting, view changes and refresh must not unexpectedly discard checked items. A selection outside the current filter should never silently enter an action's scope.
4. **Use progressive disclosure.** Show bulk actions only when selection exists; do not permanently crowd the compact gallery header.
5. **Respect DTFlow visual language.** Follow `DESIGN.md` and runtime style tokens. Selection blue differs from danger red; color is never the only selection cue.
6. **Keep operations honest.** Do not imply that a missing file was deleted or exported, or that an entire query was selected when only loaded items were selected.

## 3. State model and terminology

Maintain distinct state:

- `focused_id: str | None`: image used by Detail preview, next/previous navigation, metadata and single-image actions. Existing `selected_id` currently performs this role and should be renamed or carefully adapted without breaking persistence.
- `checked_ids: set[str]`: explicit checked saved-output identities (stable output IDs, **not** row numbers, file paths or thumbnails).
- `range_anchor_id: str | None`: anchor for Shift selection in the current sorted/filter result order.
- `selection_scope`: the current explicit list of IDs for a bulk operation, frozen when that operation begins. No implicit “all results” backend mode in the first increment.

**Recommended persistence policy:** preserve `checked_ids` during tab/view switching, filter edits, paging and refresh *within the current app session*. Persist the focused image and regular viewer preferences as before. Do **not** silently restore a prior session's actionable bulk selection after application restart; if persistent selection is desired later, add a separate explicit opt-in design and state migration. This differs intentionally from the existing single focused-item persistence.

**Filter policy:** retain checked IDs that are filtered out, but make the count of hidden checked items visible. The action bar must say, for example, **“12 selected · 4 outside current results”**. Bulk actions operate on all 12 only after clearly indicating their scope; users can choose **Clear hidden selection** or **Clear all**. Alternatively, a product decision may restrict actions to visible checked IDs, but never switch policies silently. **Recommended default: all explicitly checked IDs, with an explicit hidden-items warning and confirmation for destructive operations.**

**Refresh policy:** retain stable checked identities when outputs reorder. Remove identities only when the authoritative saved-output record is confirmed gone; do not treat temporary missing files, unloaded pages or changed filters as deletion. Show a small non-blocking count change when genuine removals occur.

## 4. Interaction behavior by view

### 4.1 Grid

- Show a compact checkbox at the **top-left** of every thumbnail, as in the Grid reference. It remains discoverable without requiring hover. Use an adequate hit target independent of checkbox graphic size (recommend at least 28 × 28 logical px, validate native DPI and density).
- **Checkbox click:** toggle checked state only. Do not open Detail.
- **Thumbnail/body click:** preserve Phase 8A's current behavior: open Detail and set focus, but do not change checked IDs.
- **Ctrl+click on thumbnail/body:** toggle its checked state without opening Detail (when supported by the event handler).
- **Shift+click on checkbox or thumbnail:** select the inclusive range from the last anchor through the clicked item in the *current result order*. If no valid anchor exists, select the clicked item only. For an unloaded cross-page range, do not pretend it is complete: either resolve IDs through a bounded selection service with progress/cancel, or disable cross-page Shift with clear explanation in the first version.
- Selected card: token-based muted blue surface/border **plus** visible checked checkbox. Focus uses a separate accessible focus outline. Do not use a thick accent border that changes tile geometry.
- Provide meaningful accessible name (e.g., “Select image image_0123.png”) and checked state.

### 4.2 List

- Add a leading selection checkbox column and a select-visible-page control in its header, without changing existing information columns more than necessary.
- **Checkbox click:** toggle one row only. **Row click:** focus/open Detail, respecting current Phase 8A behavior; **Ctrl+click/Shift+click:** selection gestures analogous to Grid where available.
- Maintain a lightweight hover treatment and a selected-row indication distinct from keyboard focus. Avoid relying on Qt's native row highlight as the authoritative checked state.
- Header checkbox has unchecked / partially checked / checked states **for the current loaded page only**. Label or tooltip: “Select all 80 images on this page” (actual count dynamically derived).
- Sort changes may reorder the list; selected IDs remain stable. Do not store checkbox state in table row indexes or `QTableWidgetItem` objects.

### 4.3 Detail

- Preserve the large preview and right-side filmstrip, image fit/zoom/pan, and previous/next navigation.
- The focused image remains a single target; show a discreet **Select image** checkbox/toggle near the image header or filmstrip's active entry, not over the central image content.
- If bulk selection exists, keep the contextual action bar visible while in Detail. “Back to Grid/List” must preserve all checked IDs.
- Previous/Next always follows current filtered results, not the checked subset, unless the user explicitly activates a future “Review selected” mode (not in this scope).
- Single-item actions such as “Use settings” and “Regenerate” continue acting on the focused item and clearly state this in tooltips; they are not implicitly bulk actions.

## 5. Contextual bulk action bar

**Placement:** a slim, non-modal bar directly below the shared gallery toolbar/filter rows, or anchored just above gallery content if this avoids a jump in the existing compact layout. Prefer reserving its row height so selecting does not cause the grid to jump. It should not cover thumbnails or block the Detail controls.

**Zero checked:** hide the bar's action contents; ordinary browsing is unaffected.

**One or more checked:** present, from left to right:

`[✓ 12 selected]   [Select page] [Clear]   [Export…] [Copy paths]   [Delete…]`

The exact button count may collapse under window-width constraints into `Actions ▾`; keep **Export…** available as the most common primary action and keep **Delete…** visually separated, never adjacent to “Select page” without spacing.

- Count must include all checked IDs; identify hidden checked IDs in supporting text or tooltip.
- **Select page:** adds all currently loaded page IDs, not all database matches. A future **Select all N filtered results** must use a separately designed, bounded, explicit global selection service and truthful result count.
- **Clear:** clears all checked IDs without affecting focused image, filters or scroll.
- **Export…:** opens the existing export workflow with a frozen explicit ID set, provided the export service supports it. Do not substitute the current `export_query()` (filter scope) or `export_identity()` (one image) and falsely claim it exports the checked set. Integration must add a safe explicit-IDs service contract if absent.
- **Copy paths:** copies newline-separated file paths for validated checked outputs; report skipped/missing paths. Preserve deterministic selection order by current query order, with a stable fallback for off-filter IDs.
- **Delete…:** shown only when the deletion service and its safety contract exist. It must never be a direct `os.unlink` from a click handler.
- **Open containing folder:** keep this as a single-focused-item action; for multiple directories, do not launch N explorer windows. If a bulk folder action is added, limit it to one common folder or open a dedicated report/list.

## 6. Keyboard and accessibility contract

| Input | Behavior |
| --- | --- |
| Tab / Shift+Tab | Navigate controls in visual order, including checkbox, action bar and filters. |
| Space on focused checkbox | Toggle checked state. |
| Enter on focused thumbnail/row | Open focused image in Detail. |
| Ctrl+A when gallery content has focus | Select only all loaded items on the current page; do not intercept Ctrl+A inside search/text inputs. |
| Ctrl+click | Toggle one item without opening it. |
| Shift+click | Select a contiguous range according to current sort/filter order, honoring the bounded cross-page rule. |
| Esc | If a dialog/menu is open, dismiss it first; otherwise clear checked selection when nonempty; do not reset filters or Detail zoom. |
| Delete | Do not immediately delete files. Open the same explicit confirmation as Delete… only when gallery content owns keyboard focus, never from editable text. |

Use Qt accessible names/roles for checkboxes, selection count changes and dialogs. Keep visible keyboard focus in Grid, List and Detail. All actions require accessible text labels or tooltips, not color-only meaning. Avoid hijacking the global Run/Queue keyboard controls.

## 7. Bulk deletion: explicit high-risk workflow

**Requirement:** deletion is *not* merely a visual action. Before enabling it, establish an authoritative service that understands committed output records, source file ownership, metadata/provenance, repeated references, and safe failure modes. Current Phase 8A is deliberately read-only; no deletion contract is established by the viewed code.

1. Freeze the exact checked ID list at request start, then resolve each ID again on the service side. Never build deletion scope from visible row indices or an unbounded filter query.
2. Classify targets: deletable local saved output; missing file; external/custom-path ownership uncertain; referenced by other saved attempts; permission/error; otherwise ineligible. Do not delete paths outside authorized managed roots without separately approved policy.
3. Show a confirmation dialog that names the **exact number**, whether hidden-filter selections are included, expected treatment of source files and metadata, and the number of skipped/ineligible targets. Example: **“Delete 12 selected saved images?”** with `Cancel` (default) and **Delete 12 images** (danger role).
4. Prefer moving eligible local files to OS Recycle Bin when supported, with explicit limitations; otherwise irreversible deletion requires stronger warning and should be a separately approved product decision. No silent permanent removal.
5. Perform filesystem work off the GUI thread with bounded concurrency and cancellation between items. Preserve honest per-ID progress and a final summary: deleted/recycled, skipped, failed. Do not silently remove durable provenance or shared artifacts.
6. Remove successfully deleted identities from the checked set and reconcile the gallery on completion. Retain failed IDs checked and explain recovery options. Revalidation avoids race conditions with refresh, export or concurrent jobs.
7. Do not claim undo unless actual recoverability exists and has been verified.

**Suggested first implementation slice:** ship multi-selection + explicit export/copy, with Delete disabled/absent until the authoritative deletion service and recovery semantics are separately approved.

## 8. Performance, pagination and consistency

- Keep existing **80-item Qt pages**, **200-item shared query ceiling**, **six concurrent thumbnail decodes**, and **25–250% thumbnail scaling** unless separately changed with evidence.
- Checking a box must be O(1) in selection-state updates and must not decode images or requery the entire gallery.
- Reconcile checked state against stable identities during existing incremental refresh; do not rebuild every tile or reset scroll/zoom.
- Treat paginated “Select page” as a bounded operation on known IDs. Never imply hidden/unloaded images were selected.
- Freeze operation identities, show counts derived from resolved service results, handle disappearance between selection and execution, and avoid duplicate processing of IDs.
- Disable conflicting destructive actions during an ongoing bulk operation; permit ordinary viewing when safe.
- Do not store thousands of images or thumbnails to represent selection. A set of identities is sufficient.

## 9. Architecture and candidate files

- `src/dtflow/ui/gallery_page.py`: translate Grid/List/Detail gestures into state updates; preserve existing read-only browsing behavior and separation between focus and checked IDs.
- `src/dtflow/ui/forms/gallery_detail_page.ui`: Designer-managed checkbox column, per-card affordance slots, contextual bar, and confirmation presentation hooks; inspect current object names before editing.
- `src/dtflow/ui/gallery_detail_preview.py`: fixture parity for new states, without conflating illustrative fixtures and committed output data.
- `src/dtflow/resources/styles/tokens.json` and `dtflow_dark.qss`: reuse selection, checkbox, focus and danger tokens; introduce new tokens only if truly necessary, keeping `DESIGN.md` synchronized.
- `src/dtflow/core/` or `services/`: backend-neutral explicit-ID operations and validated bulk outcome model; service decides eligibility, not Qt.
- `src/dtflow/persistence/viewer.py`: retain backward compatibility for single-focused-item preferences; avoid default persistence of actionable bulk selections.
- Relevant CLI/service contracts: if export/delete are exposed to Qt, give the same safety and exact-ID semantics to the shared service rather than building a separate UI-only deletion path.

**Suggested controller interface (illustrative, not existing API):** `toggle_checked(identity)`, `select_loaded_page()`, `clear_checked()`, `set_focus(identity)`, `selected_snapshot()`, `reconcile_checked(valid_ids)`. Use one state update/paint synchronization path to prevent per-view divergence.

## 10. Empty, error and edge states

- No gallery results: selection controls unavailable; explain filters may exclude results; preserve hidden checked count and offer Clear.
- Checked item no longer found: do not dereference stale row widgets; reconcile after authoritative refresh.
- Missing/corrupt image: selection remains possible if committed metadata exists; explain which actions are unavailable.
- One checked image vs one focused image: clear labels, never imply they are automatically the same.
- Selected items across different folders: export to a chosen destination remains valid; avoid multiple folder windows.
- Filter changes during selection: keep IDs, show hidden count and avoid accidental destructive scope expansion.
- Very narrow window/high DPI: bar collapses gracefully with no overlap, clipped count or inaccessible Delete/Cancel.
- Mid-operation failure: show individual failures and preserve untouched originals, provenance and selection for retry.

## 11. Implementation sequence

1. **State foundation:** introduce independent focus/checked/anchor identities; preserve viewer preference compatibility and existing Detail behavior.
2. **Grid/List controls:** checkbox UI, selected styling, Ctrl/Shift, page-select and tri-state header. No destructive operations.
3. **Contextual bar + Detail parity:** count, clear, focus/selection separation and hidden-selected reporting; keyboard/accessibility.
4. **Read-only bulk action:** copy paths and selected-ID export through shared services, with eligibility checks and result summaries.
5. **Durable deletion design review:** establish ownership, metadata policy, Recycle Bin, partial failures and concurrency rules before any Delete control becomes operative.
6. **Approved deletion implementation:** service first, confirmation UX second, then completion reconciliation.

## 12. Acceptance criteria

- Clicking a checkbox never opens Detail; clicking an image opens Detail without clearing the checked set.
- Grid ↔ List ↔ Detail maintains identical checked IDs and distinct focus; navigation is unchanged.
- Ctrl/Shift and keyboard actions operate only in the gallery context; search-field Ctrl+A remains text selection.
- Page select never claims to select unloaded results; tri-state reflects checked IDs on the loaded page.
- Sorting, filtering, pagination and incremental refresh retain valid checked IDs, show hidden selections, and avoid thumbnail flicker where already addressed.
- Export receives exactly the intended selected identities, not the full filtered query or focused image alone.
- Canceling bulk actions leaves originals and selection unchanged; partial completion produces an accurate per-item summary.
- No path is destructively changed by checking, refreshing, viewing, copying paths or opening a confirmation dialog.
- Deletion cannot occur without explicit approved service rules, accurate resolved scope and confirmation; unsafe or unknown ownership fails closed.
- Layout uses shared DTFlow tokens, supports keyboard focus and native Windows DPI, and keeps the existing compact toolbar, Detail canvas and filmstrip stable.

## 13. Verification ownership

Implementer: inspect latest canonical repository files and scoped instructions again immediately before editing. Perform focused static checks and service-level tests if authorized. Owner: Windows visual/keyboard smoke at standard and high DPI; multiple selected images across filters/views/pages; accidental-click and destructive-confirmation review. **No tests, Qt runtime smoke, file deletion, model inference, training or billable actions were performed when drafting this document.**

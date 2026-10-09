# Subplan: Phase 8E multi-selection, checked-image export and Recycle Bin

**Date:** 2026-10-09
**Status:** In-progress draft implementation PR. Session-only selection, Grid/List/Detail controls, one-row toolbar and Connection checked-ID export handoff are implemented in source but not agent/owner runtime-verified. Recycle Bin deletion remains disabled pending durable safety service and owner acceptance.
**Parent:** [8E image management](2026-10-09-phase-8e-image-management.md).
**Reference:** [Owner UX specification and mockups](examples/phase-8e/owner-multiselect-ux-spec.md).

## Scope and approved decisions
- Extend the existing Images Grid/List/Detail workspace, not its app shell. Always-visible top-left Grid checkboxes, leading List checkbox column, and a discreet Detail selection toggle. No separate Select mode, Masonry, new sidebar, List-side preview or new negative-prompt desktop filter.
- Focus/preview and checked set are independent stable output identities. Ordinary image activation still opens Detail; checkbox/Ctrl-click toggles checks without opening it. Preview navigation/reuse/regeneration keep their single-focused-image behavior.
- Keep checked IDs for the current app session across views, pages, sorting, filters, tabs and incremental refresh. Do not restore actionable bulk selection after restart. Display hidden-selection counts and offer Clear hidden/Clear all; no silently omitted or included hidden targets.
- Selection commands: Select page, Clear, and explicit Select all filtered results across pages. Select all filtered **adds** matching IDs to existing checked IDs, including checks hidden by the active filter. Resolve it off-thread in deterministic current query/sort order (stable identity tie-break), with count/progress/cancel, and fill only the remaining capacity up to the **10,000 total checked-image limit**. Keep already checked IDs, stop adding at the limit, and explicitly report how many matches were not added; never claim all results are selected after a partial fill. Cancellation before commit leaves the previous checked set unchanged. Later matching generations are not automatically selected.
- Export is available from **both Images and Connection**. Keep authoritative export settings, destination, preferences and progress in Connection; add a Checked images in Images scope there. The gallery's contextual **Export** action opens/activates that existing Connection export workflow with the checked-image scope selected, rather than creating a second exporter or settings dialog. Both entry points use the same frozen exact-ID shared exporter and its privacy/metadata/skip/sorting rules.
- Delete means **Move to Windows Recycle Bin** only. Every operation opens a clear confirmation showing the eligible image count, hidden checks, ineligible count and destination (Windows Recycle Bin); explain that users can normally restore recycled files using Windows, without promising DTFlow Undo or guaranteed restoration. Cancel is the default. No permanent-delete fallback; unsupported or failed recycling must fail safely and report affected items.
- Preserve exported copies, unrelated outputs, execution audit history, shared metadata, cache ownership and provider behavior. Do not regenerate or replay attempts.

## UI/UX contract
- Reuse canonical Designer forms, handwritten presentation seams, shared QSS/tokens and incremental identity reconciliation. Checked state is a visible checkmark plus restrained selection color; focus remains independently visible. No tile-geometry changes or thumbnail reload on toggling.
- List header checkbox is tri-state for the loaded page only; its tooltip names the current page count. Select page does not imply all filtered results. Shift ranges are bounded to the loaded page in the first slice; invalid/off-page anchors select only the clicked item, with the limit explained in help/tooltips.
- Reserve **exactly one compact, single-line selection/status bar** below the existing shared header/filter rows so action visibility does not shift gallery content; never wrap it into two stacked rows. Even with **zero** checked images, expose Select page and Select all filtered; disable checked-item actions until selection exists. With checks, show total/hidden counts, **Export**, **Delete**, Clear and Copy paths alongside those selection commands. Keep the short **Export** and **Delete** button labels in Grid, List and Detail; use accessible tooltips/names to clarify their full actions. At narrower widths, move secondary controls into an accessible overflow menu rather than wrapping the toolbar or truncating action labels. **Delete** only opens the explicit **Move N images to Windows Recycle Bin** confirmation and never deletes on first click. All bulk actions cover explicitly checked IDs, including hidden checks, with the scope/count visibly disclosed. No controls cover thumbnails or the Detail canvas.
- Space toggles focused checkbox, Enter activates preview, Ctrl+A selects the loaded page only when gallery content owns focus, Esc clears checks after dismissing menus/dialogs, and Delete opens confirmation only in gallery content. Text fields retain native shortcuts. Preserve existing pan/zoom and Run/Queue controls.
- Freeze/revalidate exact targets before destructive confirmation. Show eligible count, hidden count and ineligible/missing count; explicitly say eligible originals move to Windows Recycle Bin and can normally be restored there. **Cancel** is the default; the confirm button reads **Move N images to Recycle Bin** using the actual eligible count. Source paths/metadata are not dumped into normal logs.
- Run recycling off the Qt thread. Show processed/total and Stop after current image; preserve completed outcomes. Remove successful targets from checks; retain failed/uncertain targets with actionable results, and reconcile only affected cards/preview.

## Ordered work packages and task list

### 8E-1 — Selection model and query snapshots
- [ ] Audit canonical GalleryItem/identity/query/paging and viewer preference contracts; preserve existing focused-item persistence compatibility.
- [x] Add backend-neutral checked/focused/anchor state and a bounded selected-ID snapshot path. Checkbox toggling remains local, without image decoding or requery on ordinary clicks; source presence only, Windows acceptance pending.
- [ ] Define authoritative removal versus unloaded/filtered/missing-file state; retain missing/unloaded checks and prune only confirmed unavailable records with visible feedback.
- [x] Resolve all-filtered selection and hidden counts off-thread without materializing image/metadata payloads; merge checked IDs in deterministic filter/sort order until the **combined** set reaches 10,000. Report added and omitted-at-limit counts; cancellation leaves prior checks unchanged. Source-only; runtime acceptance pending.

### 8E-2 — Images presentation and read-only actions
- [x] Implement Grid/List/Detail checkbox parity and separate focus/check styling through shared widgets/forms; offline sample fixture remains illustrative and its destructive/export controls are disabled. Source-only; visual acceptance pending.
- [x] Add a one-row selection/status bar with zero-selection page/all-filtered commands, visible total/hidden checked counts, clear-hidden/clear-all, **Export**, disabled **Delete**, copy paths, keyboard and accessible labels. Secondary controls collapse into More at narrower widths. Source-only; DPI and Windows smoke remain open.
- [ ] Preserve paging, view/tab/filter/sort/refresh continuity and existing thumbnail/preview state.
- [ ] Verify narrow-window/high-DPI action accessibility without changing global shell geometry or tokens unnecessarily.

### 8E-3 — Shared checked-image export and both entry points
- [x] Expose a read-only checked-ID snapshot seam; keep the existing single-focused-image export scope.
- [x] Add Checked images in Images to Connection's export scope; empty selection produces an honest message. The Images contextual **Export** action activates the existing Connection workflow with this scope selected (no duplicate configuration/dialog). Source-only; owner export smoke pending.
- [ ] Freeze exact identities at export start from either entry point; explicitly include/count hidden checks, and reuse services/image_export.py without duplicating export rules. Preserve the other Connection scopes and destination/privacy preferences.
- [ ] Keep export and recycle operations coordinated; checking or copying paths must never trigger generation or mutation.

### 8E-4 — Durable availability and recycle service (before enabling deletion)
- [ ] Choose/version the durable availability/recycle ledger and migration policy after auditing existing attempt storage; it is not disposable cache. Keep request/attempt/queue audit and original artifact identity history intact.
- [ ] Define per-ID eligibility: recorded final original under an authorized managed output root, no redirected path or conflicting/shared-file ownership; missing/external/uncertain/in-use targets fail closed or are explicitly skipped. Decide custom --out root authorization before enabling recycling there; user export destinations are never eligible roots.
- [ ] Implement a backend-neutral shared service plus isolated Windows Recycle Bin adapter. Verify actual supported OS API/library behavior with current primary documentation; any new dependency goes through uv with matching lock update.
- [ ] Record intent before OS mutation, then confirmed recycled/missing/failed/uncertain outcomes. Filesystem actions and SQLite are not one atomic transaction. Define restart inspection/reconciliation without automatic replay of uncertain recycling or generation.
- [ ] Coordinate actual original-file readers/export and original writers across processes with an ownership mechanism distinct from cache ownership; UI disabling alone is insufficient. Preserve the existing cache lease, document lock ordering and crash release, and revalidate each original immediately before recycling. Check durable artifact availability before taking the export already-present-copy fast skip.
- [ ] Report unsupported recycling/permission failures without permanent removal; preserve failed/uncertain selections and useful per-ID outcome summaries.

### 8E-5 — Confirmation, results and gallery reconciliation
- [ ] Enable the gallery **Delete** button (which opens the Move to Windows Recycle Bin confirmation) only after the shared eligibility/recovery service exists.
- [ ] Add per-operation confirmation with exact eligible/hidden/ineligible counts, clear Windows Recycle Bin destination and normal Windows restore guidance (no DTFlow Undo promise); Cancel must not mutate files or durable state.
- [ ] Add bounded progress/cancel and partial-failure summary, distinguishing recycled, already missing, ineligible, failed, uncertain and unprocessed.
- [ ] Make recycled availability durable so removed originals do not reappear as ordinary available Images results; preserve request/history references and unaffected outputs/metadata.
- [ ] Document manual Windows restore behavior and explicit availability reconciliation; do not advertise DTFlow Undo without implemented, verified recovery.

### 8E-6 — Documentation, validation and handoff
- [ ] Sync DESIGN.md, repository contract, desktop/architecture/runtime layout, current status and unreleased notes only as corresponding behavior becomes implemented.
- [ ] Perform repo-defined Ruff/syntax/import/help/static checks at coherent checkpoints. No new tests or agent tests/smoke under fast-implement unless the owner explicitly requests them.
- [ ] Treat deterministic interrupted/partial-recycle recovery acceptance as a **release gate before enabling Move to Recycle Bin**, even under fast-implement: owner-run offline/service checks with isolated fake adapters and temporary fixtures are permitted without authorizing agent-run tests. No real original deletion merely to test plumbing.
- [ ] Owner Windows keyboard/visual and Recycle Bin smoke using disposable generated fixtures, including preserved exports/history and partial-failure/restart behavior. Never use real generation for UI validation.
- [ ] Commit/push coherent implementation checkpoints and keep PR scope/acceptance ledger accurate. No merge/release without owner request.

## Initial PR preparation
- [x] Owner approved checked-image export from **Images and Connection** (gallery button label **Export**), a **single-row** selection toolbar with gallery **Delete** button opening the Windows Recycle Bin confirmation, session-only cross-filter selection with visible hidden counts, and partial all-filtered fill up to 10,000 total checks.
- [x] Import owner Markdown/reference images and record current-contract overrides.
- [x] Write this subplan/task list before runtime changes; synchronize parent scope/navigation.
- [x] Prepare plan-first commit/push and draft PR to main; implementation task checkboxes remain open.

## Implementation decisions still required
These are engineering audit tasks, not requests to reopen the approved UX: durable availability schema, managed/custom output-root eligibility, Windows recycle adapter, reader/writer coordination and interrupted-operation reconciliation. Record evidence/choices before the corresponding destructive code is enabled. No irreversible fallback or broad filesystem cleanup is permitted.

## Validation and acceptance ledger
The first incremental code slice now exists on this draft branch: selection model/query snapshots, canonical Qt Grid/List/Detail controls, single-row toolbar/icons/overflow, illustrative fixture and checked-scope export handoff to Connection. This is **source presence, not runtime verification or owner acceptance**. No new tests, agent smoke, Windows Recycle Bin calls, file deletion, inference, provider calls, packaging or billable actions were performed. 8E-4/8E-5 destructive work and the original-file concurrency/availability contracts remain open; Delete is disabled. Parent 8D acceptance does not establish 8E acceptance.

## Qt system Trash follow-up (2026-10-09)

The current adapter choice supersedes the earlier Windows-only implementation wording: see the [Qt implementation plan](2026-10-09-qt-system-trash.md) and [architecture](../architecture/image-recycling.md). Durable recycling, original-file coordination and UI wiring now exist in source; this does not settle the unchecked owner recovery/runtime acceptance gates above. The service remains Qt-free; the desktop injects the Qt adapter. Windows uses Recycle Bin wording and other platforms use Trash. Qt capability detection and per-file move results replace OS/filesystem allowlists and fixed-drive gates; managed-original validation remains. Missing/unverifiable receipts remain uncertain, with explicit Qt-reported-success wording in results. No permanent deletion, dependencies or state schema changes.

## Select all toolbar follow-up

The owner requested an independent, always-visible **Select all** action (2026-10-09). It selects the current filtered results across all pages within the existing 10,000 total limit, then becomes **Unselect all** only when every matching result is checked; Unselect all clears the entire checked set, including hidden checks. Loaded checkbox edits reconcile cached matching IDs locally; filter and refresh snapshots re-evaluate coverage off-thread. Partial fills remain Select all, and Ctrl+A keeps its existing select-only behavior. Secondary actions alone move into More. Source/static evidence only; owner reports Select all / Unselect all smoke passed on 2026-10-09 (follow-up to f3bc285); broader platform/packaged acceptance remains separate. No tests, smoke, Trash operation, inference or billable action ran for this follow-up.

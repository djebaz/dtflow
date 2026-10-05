# Phase 8A — Preview-only Designer forms and shared styling

**Status:** PR opened for planning; forms/QSS implementation not started.

**Base:** Phase 8 documentation PR #32 squash-merged on main (`5d9c476`).

**Sources:** [viewer plan](2026-10-05-phase-8a-viewer.md), [text templates](examples/phase-8a-viewer-wireframes.txt), [DESIGN.md](../../DESIGN.md), [Qt contract](../architecture/qt-design-tokens.md).

## Scope

Build an offline presentation preview of Grid, List and Detail, following the agreed compact header and stable Detail layout. Canonical Designer forms belong in `src/dtflow/ui/forms/`; necessary styling belongs in the existing shared QSS. Reuse shared tokens and handwritten preview widgets at presentation seams, not generated-Python business logic.

No gallery query/index implementation, application navigation integration, backend access, generation, preset/reuse, cache-management controls, durable mutations or billable operations in this PR. Fixture labels must not be mistaken for real saved outputs or compatibility evidence. Unwired application actions remain visibly disabled.

## Ordered work

1. Inspect the existing shell/form and image-preview integration seams. Decide standalone preview composition without changing shipped tabs.
2. Create canonical page form(s) for the common header and Grid/List/Detail presentation; no Masonry.
3. Add only necessary shared-QSS selectors, stable object names and interaction states; avoid a second styling system.
4. Add an offline preview harness with clearly identified fixtures, including portrait, landscape, long prompts, many LoRAs and incomplete metadata.
5. Verify view changes and presentation-only detail controls without services; preserve Grid focus/position when switching to Detail and back in the preview.
6. Inspect bounded wide/narrow captures for clipping, stable geometry and accessible focus. Keep Cover/Fit and true pixel viewing semantics distinct.
7. Record checks actually run and owner Windows feedback, then update the viewer/status documentation. Do not mark Phase 8A implemented from a preview.

## Open decisions

- Form composition inside the established context/work/evidence shell versus a standalone preview host; canonical form ownership must stay clear.
- Additional-details/raw-data button presentation and bounded long-content handling.
- Placement of sorting/dimension filters, optional negative-prompt search and new-result semantics.
- Initial Cover/Fit policy and minimum useful preview size.

## Validation boundary

Compile/load canonical `.ui` files and check necessary imports/help/syntax for the preview harness. Reuse existing tokens, logging and network-guard conventions. Use offline visual fixtures; no live inference or uploads. Windows DPI, keyboard ergonomics and final layout acceptance remain owner checks. Runtime service acceptance remains a later implementation PR.

# Phase 8A — Preview-only Designer forms and shared styling

**Status:** Detail form and offline preview implemented in draft PR #33; Grid/List, shell integration and gallery services remain unimplemented.

**Base:** Phase 8 documentation PR #32 squash-merged on main (`5d9c476`).

**Sources:** [viewer plan](2026-10-05-phase-8a-viewer.md), [text templates](examples/phase-8a-viewer-wireframes.txt), [DESIGN.md](../../DESIGN.md), [Qt contract](../architecture/qt-design-tokens.md).

## Scope

Build an offline presentation preview of Grid, List and Detail, following the agreed compact header and stable Detail layout. Canonical Designer forms belong in `src/dtflow/ui/forms/`; necessary styling belongs in the existing shared QSS. Reuse shared tokens and handwritten preview widgets at presentation seams, not generated-Python business logic.

No gallery query/index implementation, new backend access, generation, preset/reuse, cache-management controls, durable mutations or billable operations in this PR. The owner subsequently approved compact Connection navigation and an Images placeholder as the first shell-integration step. Fixture labels must not be mistaken for real saved outputs or compatibility evidence. Unwired application actions remain visibly disabled.

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
- Additional-details/raw-data buttons now reveal mutually exclusive inline sections beneath the prompt, following the supplied `add_detail.png` and `raw_meta.png`. Long-content ergonomics still require owner review.
- Placement of sorting/dimension filters, optional negative-prompt search and new-result semantics.
- Initial Cover/Fit policy and minimum useful preview size.

## Validation boundary

Compile/load canonical `.ui` files and check necessary imports/help/syntax for the preview harness. Reuse existing tokens, logging and network-guard conventions. Use offline visual fixtures; no live inference or uploads. Windows DPI, keyboard ergonomics and final layout acceptance remain owner checks. Runtime service acceptance remains a later implementation PR.

## Detail checkpoint — 2026-10-06

- Canonical `src/dtflow/ui/forms/gallery_detail_page.ui` defines the compact header, preview host/overlays, right-hand filmstrip, metadata band, prompt and inline additional/raw panels.
- Shared `dtflow_dark.qss` uses existing token references and scoped gallery selectors; no palette/token values or Generate preview behaviour changed. The teal mockup is translated to DTFlow's existing charcoal/blue identity.
- `tools/ui/preview_gallery_detail.py` loads the canonical form with QUiLoader and applies the shared theme once. Uses only bundled illustrative lighthouse artwork and synthetic metadata; no owner photo/model data was copied into the repository.
- Preview enables Cover/Fit, thumbnail-size changes, fixture selection and mutually exclusive inline panels. Grid/List, real filters/refresh, zoom/fullscreen and saved-file actions are visibly unwired/disabled. This is not a working gallery.
- Reference layouts: `DTFlow Portrait Gallery Viewer_proc.png`, `add_detail.png`, `raw_meta.png`. Raw JSON is a read-only fixed-width text region, not yet a syntax-highlighted/folding tree.
- Checks: Designer compilation, Python compilation/help, Ruff and shared-token resolution. Actual closed/additional/raw and 800×680 layout captures were made; offscreen Windows font rendering produced missing glyphs, so readable captures used the native platform. This is bounded fixture inspection, not owner DPI/accessibility acceptance or service testing.
- Owner should review narrow-header scrolling, metadata scroll limits and overlay/navigation positioning. Final tests/smoke and native-DPI interaction acceptance remain pending. No inference, uploads, training or billable operations ran.

### Preview commands

```bash
uv run python tools/ui/preview_gallery_detail.py
uv run python tools/ui/preview_gallery_detail.py --state details
uv run python tools/ui/preview_gallery_detail.py --state raw
uv run python tools/ui/preview_gallery_detail.py --size 800x680
```

## Shell navigation checkpoint — 2026-10-06 (approved scope extension)

- Compact power-icon Connection tab before Generate; its dot reflects the selected provider's last explicit check. Status remains distinct from active-tab selection.
- Token-based unknown/checking/connected/warning/error states; tooltip and accessible name include textual status. No polling or extra backend requests.
- Add Images at the end of Connection / Generate / Queues / Images. Images is a disabled placeholder until the preview is mounted; no extra empty workspace.
- Preserve the existing Connection workspace and click/keyboard navigation path. Compact navigation widths keep all four controls within the permanent left rail.
- Invalidate status on provider/endpoint changes; ignore older check completion after a selection change. This is connection-evidence presentation safety, not generation/retry policy.
- Owner Windows navigation and native-DPI validation remain pending. No tests or live connection checks are authorized by the layout request.

Implemented canonical compact navigation and shared-token power/status icon. Images remains disabled. Syntax/import, Designer compilation and Ruff checks recorded; owner visual/interaction acceptance remains pending.

### Navigation polish — 2026-10-06

- Matched row heights (40 px), enlarged the power glyph/status dot and kept a 52 px Connection target. Other tabs use quiet transparent text styling, including disabled Images; selected workspace remains distinct.
- Added shared `color.successSurface` (`#203b2b`) and matching DESIGN.md snapshot for the connected control's green-tinted fill. All status/focus colors remain token-based; no glow or inline widget styles.
- Explicit vertical scrollbar page backgrounds remove the native checkerboard track seen in the owner's screenshot.
- Captured the actual shell with temporary state and network guard installed at 1182×792 and 800×680, plus fixture unknown/checking/error states. No real connection checks/generation or state writes to the user's application folder. Reviewed connected and narrow Generate captures; all four navigation targets remain available within the row (Images intentionally disabled).
- Ruff, Designer compilation and whitespace checks passed. Native DPI, hover/focus ergonomics and owner final acceptance remain pending; no automated test suite was run.

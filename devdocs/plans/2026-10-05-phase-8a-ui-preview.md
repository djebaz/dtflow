# Phase 8A — Preview-only Designer forms and shared styling

**Reviewed:** 2026-10-06, PR #33 at `e922bfd` before documentation consolidation.

**Status:** Historical PR #33 UI-presentation record. PR #34 now connects the same canonical Grid/List/Detail forms to real saved outputs; this document remains the fixture/design evidence for PR #33.

**Base:** Phase 8 documentation PR #32, `5d9c476`.

**Sources:** [viewer scope](2026-10-05-phase-8a-viewer.md), [DESIGN.md](../../DESIGN.md), [Qt token contract](../architecture/qt-design-tokens.md). Earlier text wireframes and reference crops record the design discussion; this document describes the final implementation of this preview PR.

## Implemented presentation

| Area | Current behavior |
| --- | --- |
| Shell | Images hides both content rails, mounts the existing brand/navigation widgets in two aligned header slots and hides the Inspector toggle while retaining its layout space. Leaving Images restores parents, splitter sizes and Inspector preference. |
| Header | Logo beside view controls; navigation beside filters. Header controls scroll independently of the chrome slots. No extra outer/header inset, sample-count pill or redundant search/filter labels. Explicit defaults/placeholders, accessible names and tooltips remain. |
| Modes | Grid, List and Detail share one header above `galleryContentStack`. Grid uses uniform aspect-fit tiles; List has metadata columns, row-wide hover and no selection/bulk controls. Tile/row activation opens its fixture in Detail. Mounted browse views retain scroll positions. |
| Density | The shared slider changes Grid thumbnail size, List row density or Detail filmstrip size. |
| Detail | Flexible Cover/Fit preview with a right filmstrip spanning the entire content height. Metadata, prompt and mutually exclusive Additional/Raw panels sit below the main image, to the filmstrip's left. Overlay spacers inset badges, navigation and zoom controls by 10px. |
| Inspection | Presentation-only Cover/Fit, 100%, zoom/pan, previous/next, keyboard navigation and fullscreen/Escape are enabled. |
| Metadata | Accent model glyph, emphasized name and secondary identifier; neutral LoRA rows separate name, weight and explicit Unknown badges. The 108px metadata band scrolls horizontally. No redundant Positive prompt caption. Expanded disclosure buttons use shared accent states; Additional values have subtle separators. Raw JSON remains a read-only monospace text region, not a folding/syntax-highlighted tree. |
| Shared styling | Charcoal surfaces, blue accent and muted navy selection use `color.*`; no `gallery.*` token family remains. Typography roles and section cards apply across Connection, Generate, Queues and Images. Unknown/availability text is neutral gray with explicit wording. |
| Connection navigation | 58×34px power-icon tab before Generate, with a 12px arc and 6px status dot. Shared mint/teal connection tokens are distinct from general success semantics. Dot/border report the last explicit check; tooltip/accessibility provide text. No polling; provider/endpoint changes invalidate status and obsolete completions are ignored. |
| Batch-plan card | A visible header checkbox synchronizes with the existing checkable `planBox`; the unchecked header remains operable and the full contents wrapper hides. This restores expansion without changing planning/services. |
| Lifecycle | The gallery controller owns deferred layout timers, caches viewports and guards deleted Qt objects during event-filter teardown. The owner confirmed the shutdown error is resolved. |

Canonical structure is `src/dtflow/ui/forms/gallery_detail_page.ui`. Shared QSS/tokens live in `src/dtflow/resources/styles/`. `ui/gallery_detail_preview.py` owns offline presentation for both shell and launcher; it does not own application services or provider protocols. Section cards retain their QGroupBox identities, accessible names and enable/disable behavior. Stable business-control names remain intact.

## Fixture and service boundary

- Bundled generated illustrative portrait, reference thumbnail crops and synthetic metadata are explicitly fixtures. No owner source photo, real model weights or inference evidence is claimed.
- LoRA compatibility remains Unknown. Detail's displayed date and prompt are illustrative; browse views show no recorded generation date. Bundled dimensions/file sizes describe fixture assets, not saved outputs.
- Search/filters, refresh and saved-file actions remain visibly disabled. No saved-output query/index, gallery preferences persistence, provider access, presets, reuse, regeneration or cache-management service is added.
- Cover and Fit remain distinct: Cover crops proportionally, Fit shows the complete image. The selected read-only viewer capabilities remain in the [viewer plan](2026-10-05-phase-8a-viewer.md); this preview does not reduce or complete them.

## Preview commands

```bash
uv run dtflow gui
uv run python tools/ui/preview_gallery_detail.py
uv run python tools/ui/preview_gallery_detail.py --state details
uv run python tools/ui/preview_gallery_detail.py --state raw
uv run python tools/ui/preview_gallery_detail.py --view grid
uv run python tools/ui/preview_gallery_detail.py --view list
uv run python tools/ui/preview_gallery_detail.py --size 800x680
```

## Validation and acceptance evidence

Before the owner's instruction to commit without tests, recorded validation included locked uv synchronization, Ruff/import/Designer checks, actual Qt captures and focused shell/theme checks (9 passed; one original-logo lifecycle check excluded because the connector could not materialize that existing binary resource locally). Existing branch logo assets were preserved. A drafted test was removed at the owner's request; this PR adds no tests.

Subsequent refinements used source inspection and Python formatting. The final import fix at `e922bfd` passed the targeted Ruff import-order check and format check. On 2026-10-06 the owner reported tests passed; no command, count or CI run was supplied in that message, and the assistant did not rerun tests. The owner also confirmed the Windows close error no longer occurs.

This documentation review checks tracked token values/references, documented roles and canonical composition only. It does not establish exact pixel equality, complete Windows DPI/accessibility acceptance or packaged visual acceptance. No training, inference, uploads, live connection checks or billable operations ran during these styling/documentation follow-ups.

## Remaining acceptance and next work

Review native Windows font metrics, long-content clipping, keyboard focus, narrow-header scrolling and high-DPI behavior separately. Retain the approved logo/navigation coordinates, compact gutters, full-height filmstrip and overlay inset during further refinement.

Implement saved-output services through the ordered checkpoints in the viewer plan: source/provenance discovery, stable bounded query/loading, browsing state, search/filter/sort, inspection and recorded metadata. Missing/corrupt outputs and unknown fields require honest handling; fixture UI success is not service acceptance.

## Superseded design checkpoints

Earlier PR commits used a lighthouse, a separate teal gallery palette, disabled Images/Grid/List controls and information panels spanning underneath the filmstrip. Those are historical iterations, superseded by the current presentation above. The earlier 52×40 and 74×44 Connection approximations are also superseded by the 58×34 control. Use DESIGN, current canonical forms/QSS and this implementation snapshot when extending the UI rather than copying an old checkpoint.

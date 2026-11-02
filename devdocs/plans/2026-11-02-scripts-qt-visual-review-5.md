# Scripts UI: fifth native Qt screenshot review (PR #201)

**Evidence:** Owner-supplied `ui-renders-scripts5.zip`, 24 PNG screenshots (21 at 1600×1000; three at 800×680), reviewed against the approved mockups in `Downloads.zip` and the latest Scripts design documentation. The captured screenshots show the **pre-correction** UI; code changes listed here have not yet been rerendered on Windows.

## Improvements observed since the earlier render

- The center canvas now has a native Scripts empty state. Run and Results have real Qt presentation widgets rather than the prior one-line placeholder.
- Desktop forms have an independent content scroller, and the editor has syntax colors and a line-number gutter.
- The narrow details drawer contains Compatibility, Starting settings, Source and History sections.
- All 24 state names are present and three narrow captures are 800×680.

These improvements do **not** establish visual acceptance.

## Visible defects and follow-up commits

| States | Fifth-render mismatch | Corrective change (not yet rerendered) |
| --- | --- | --- |
| Inspector-bearing states | Duplicate/overprinted hook verdicts such as `supportedsupported` | Immediately hide and detach obsolete hook widgets before `deleteLater()` |
| `scripts_form_long_values`, `scripts_form_after_unknown`, narrow forms | Old labels, controls, sections and sliders visually overlap newly populated form | Hide and detach obsolete form controls before `deleteLater()`; improve compact form control alignment |
| `scripts_narrow`, `scripts_editor_dirty`, related states | Previous state's source and `Start new run` text persist into later screenshots | Reset source, Start label, status and captions for every screenshot |
| `scripts_empty`, `scripts_imported_supported`, other desktop states | New script, Import, Reload and library storage caption disappear below the oversized library list | Constrain library-list height from window geometry to reserve footer actions |
| `scripts_narrow_details` | Wrong supported Wildcard configuration where the reference shows Hires fix loop with incomplete preflight and a required source | Correct offline state identity, source requirement and form presentation |
| `scripts_form_after_unknown` | Green script-compatibility status was replaced with a prior-attempt warning; separate amber warning card was absent | Add a dedicated amber unresolved-attempt notice; preserve compatibility status independently |

## Unresolved acceptance issues

- Native screenshot confirmation of the above changes (no claim of success until owner reruns).
- Typography, controls, card density and visual proportions remain different from the browser mockup in some states; inspect at both desktop and 800×680.
- Several hook/inspector values and all run/result images are **illustrative**, not parser-backed or generated. Avoid presenting them as live script evidence.
- Persisted source versions, script dispatch/admission, shared Stop/cancel linkage and real ScriptRun history are not connected in this UI draft.
- Full 24-state visual review, warning/error presentation, and Windows DPI acceptance remain separate gates.

No automated tests, script execution, inference or billable operations were performed for this review.

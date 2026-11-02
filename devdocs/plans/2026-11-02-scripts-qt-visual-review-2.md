# Native Scripts Qt visual audit — second owner render

**Evidence:** owner-supplied `ui-renders-scripts2.zip`, containing 24 rendered 1600×1000/800×680 PNGs, reviewed against the 25 owner-supplied reference screenshots in `Downloads.zip` and the authoritative `DTFlow Design System (8).zip` `ui_kits/scripts/` mockups. Date: 2026-11-02.

This report compares the supplied captured pixels. Source code and mockup agreements do not constitute a successful Qt rerender, and this artifact is not a visual acceptance sign-off.

## Findings by reference screen

| States | Observed in the second Qt render | Follow-up on PR #201 |
| --- | --- | --- |
| `scripts_empty` | Generic Inspect controls in center instead of centered logo and empty canvas; settings/source/history misleadingly visible; library footer below the viewport | Added dedicated branded empty canvas, state-aware inspector visibility and shared left-rail compact sizing; **not rerendered yet** |
| `scripts_imported_supported`, `scripts_imported_unsupported`, `scripts_inspect_partial` | Compatibility hooks flattened into paragraphs instead of independent status/action rows; history too terse; warning/error cards have roughly correct semantic tint but lack the reference spacing and hierarchy | Added separate semantic hook labels, detailed history rows, themed card styles; **not rerendered yet** |
| `scripts_form_default`, `scripts_form_long_values`, `scripts_narrow` | Visible overlapping/clipped labels and controls around Sweep order; lower form content is compressed into view instead of remaining scrollable; segmented aspect consumes entire field width; narrow layout text is overdrawn | Set content-derived minimum height, fixed two-column label widths, compact segmented/size controls and left-rail sizing; **not rerendered yet** |
| `scripts_editor_new`, `scripts_editor_dirty`, `scripts_editor_syntax_error`, `scripts_editor_run_active`, `scripts_editor_narrow` | Editor shell exists and syntax colors render, but fixtures show a sparse unrelated four-line example versus the mockup's starter and Wildcard script; line diagnostics/dirty banner not at reference density | Replaced illustrative source with the exact starter/Wildcard examples from the owner kit; **not rerendered yet** |
| `scripts_narrow_details` | Right drawer shows only a few status lines instead of the full Compatibility, Starting settings, Source, History stack | Populated native scrollable drawer with those sections; **not rerendered yet** |
| `scripts_queued`, `scripts_cancelled_before_start`, `scripts_running`, `scripts_waiting_input`, `scripts_stopped`, `scripts_failed`, `scripts_interrupted`, `scripts_outcome_unknown` | Run body is a large empty result rectangle with sparse static console text; overlays demonstrate common app widgets but lack the mockup's detailed run-state content/actions | **Still open.** Rework native run and results components against the reference. Do not present fixture history as actual generated output |
| `scripts_results` | Plain-text attempt list lacks the mockup's per-step result cards and Open-in-Images actions | **Still open.** Render saved-attempt identities as fixture-only cards; the real action requires a linked persisted attempt |
| `scripts_from_inpaint`, `scripts_form_after_unknown` | Handoff is only a text label; previous-unknown state lacks the reference warning card with distinct new-run choice | **Still open.** No live handoff or retry admission should occur in UI-only fixtures |

## Layout themes

1. Mockup's shell columns, card borders, label/value alignment, library footer and section headers are denser and more consistently structured than the second Qt capture. These are visual defects, not merely platform font substitution.
2. The second capture proves the 24 named Qt states can be written, not that the 24 states match the reference. Side-by-side visual comparison and owner review remain required after changes.
3. The backend and durable script lifecycle remain disconnected. Manual offline UI screenshots must never be labeled as real executions, queued submissions, billable attempts or saved result provenance.

## Next acceptance gate

Capture all 24 states from the new PR head, inspect normal and 800×680 widths with special attention to the form clipping and library footer, fix still-open run/results and lifecycle details, and compare the new captures to the original owner references. No new automated tests at owner's direction.

# Seventh owner Qt capture — Scripts visual review

**Evidence:** owner-provided `ui-renders-scripts7.zip`, 24 named PNG captures (21 desktop at 1600×1000, 3 narrow at 800×680), compared with the owner's approved `Downloads.zip` mockups and `devdocs/design-system/scripts-design.md`. This report refers to **the captured branch before the corrective commits below**; it does not claim that the corrections have been rendered or accepted.

## Confirmed improvements in this capture

- The former duplicate hook verdicts and overlapping/ghosted form controls are no longer visible in the reviewed supported, form, and narrow states.
- The empty canvas is centered and the editor now has populated JS, gutter and syntax coloring.
- The two-thumb slider, separate unresolved-run warning, per-attempt results, and right-side narrow details drawer are present.
- The lifecycle states have run identity, a committed-result placeholder, console, and shared activity presentation; those remain offline fixtures, not run evidence.

## Remaining discrepancies / findings

| Area | Owner mockup versus actual seventh capture | State |
| --- | --- | --- |
| Script library (all states) | New script is barely visible at the very bottom; Import, Reload and the storage caption are clipped off. The left heading and list take disproportionate vertical space. | **Fix committed, not yet rendered:** fixed heights for title, action controls and caption; only the library list consumes remaining stretch |
| Narrow details | The drawer works, but shows Wildcard script Source metadata while `scripts_narrow_details` is actually Hires fix loop, and the source-required text is not red. | **Fix committed, not yet rendered:** source fields now follow selected filename/SHA; required source is styled with error token |
| General form | All key controls are visible and no major overlap in `scripts_form_default`, but spacing, line-height and bottom scroller differ from mockup. Source and long-value input layout remain less compact. | Visual tuning still open |
| Run / results | Native preview is still an illustrative gray tile with text, and the console/status geometry does not exactly match the reference. No real source images or durable result identities are connected. | Still open |
| Editor | Functional-looking native controls and syntax colors are now present, but toolbar/footers and code-area heights differ from the web reference. Parse-only check and immutable Save as new version are not integrated. | Still open |
| Lifecycle overlays | Shared activity/notice components render. Temporal behavior (6 s expiry, hover pause), provider settlement and stop/cancel semantics are not validated from static PNGs. | Not established by screenshot review |

## Review boundary

The owner-provided ZIP verifies the **pre-correction** Windows Qt images only. The fixes above are source changes and must be rerendered in the same Windows environment for visual acceptance. No new automated tests were added at owner request. The UI remains disconnected from execution; no generation/inference or potentially billable provider work was performed.

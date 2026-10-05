---
name: DTFlow
description: Native Qt workspace design system for generation and queue workflows
colors:
  background: "#1f1f1f"
  surface: "#252525"
  input: "#1e1e1e"
  canvas: "#151515"
  text: "#e6e6e6"
  secondary: "#b2b2b2"
  placeholder: "#a8a8a8"
  disabled: "#777777"
  border: "#3a3a3a"
  accent: "#0068b8"
  accent-hover: "#0078d4"
  accent-pressed: "#005a9e"
  focus: "#60b6f6"
  selection: "#173852"
  selection-text: "#d5ecff"
  success: "#78c97c"
  connection-accent: "#00d8b0"
  connection-surface: "#123e36"
  connection-glyph: "#d9f7ef"
  warning: "#ffbf59"
  error: "#ff8179"
  unknown: "#b7a4e5"
typography:
  title:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "17pt"
  body:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "10pt"
  small:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "9pt"
rounded:
  input: "5px"
  button: "6px"
  panel: "8px"
spacing:
  rail-gap: "8px"
  workspace-gutter: "8px"
  panel-padding: "12px"
  canvas-overlay-inset: "10px"
---

# DTFlow design system

## Overview

DTFlow is a compact, image-first native desktop workspace. The generated image gets the largest flexible region; the controls that create and inspect it remain nearby. The interface uses charcoal surfaces, one blue action language, restrained corners and precise state text. It is a Qt Widgets application, not a web skin.

This file describes the implemented Generate design and the shared rules to use when changing Connection, Queues or a later tab. The frontmatter is a readable snapshot of the reusable tokens; the **runtime authority** is [`tokens.json`](src/dtflow/resources/styles/tokens.json). Change that file and [`dtflow_dark.qss`](src/dtflow/resources/styles/dtflow_dark.qss) as needed, then update this snapshot in the same PR. The QSS contains some local, one-off colors; do not turn each into a global token without a second use.

The canonical files and responsibilities are:

| Concern | Source |
| --- | --- |
| Structure, size policy, spacing and stable object names | [`src/dtflow/ui/forms/`](src/dtflow/ui/forms/) |
| Shared palette, type and reusable sizes | [`tokens.json`](src/dtflow/resources/styles/tokens.json) |
| Widget appearance and interaction states | [`dtflow_dark.qss`](src/dtflow/resources/styles/dtflow_dark.qss) |
| Token expansion and one-time stylesheet application | [`ui/theme.py`](src/dtflow/ui/theme.py) |
| Persistent geometry | [`ui/forms/workspace_shell.ui`](src/dtflow/ui/forms/workspace_shell.ui) |
| Shared shell, navigation and responsive placement | [`ui/main_window.py`](src/dtflow/ui/main_window.py) |
| Aspect-fit image inspection and floating controls | [`ui/image_preview.py`](src/dtflow/ui/image_preview.py) |
| Offline state capture | [`tools/ui/render_states.py`](tools/ui/render_states.py) |

The existing [Qt token and interaction contract](devdocs/architecture/qt-design-tokens.md) records the renderer command and fixture boundaries. The [Generate refinement plan](devdocs/plans/2026-10-05-preview-workspace.md) records the scope of this PR. Earlier plans are historical decisions; the current implementation and these sources govern new work.

## Colors

Blue marks primary action, focus and active selection. The four charcoal depths separate the application, panels, editable controls and image canvas. Text and status labels say what happened; color is supporting evidence, never the only signal. Use the frontmatter roles through `${color.name}` in QSS or `load_tokens()` in custom painting. Do not copy literal hex values into new Python widgets.

- **Accent:** primary actions at rest, brighter on hover and deeper while pressed. Focus is a separate lighter blue and must remain visible on both neutral and selected controls.
- **Selection:** muted navy with light text, shared by selected navigation, lists and tables. It is not the primary-action fill.
- **Status:** success, warning, error and unknown each have a distinct label and semantic color. Unknown is not treated as failure or success.
- **Disabled:** muted text and surfaces without a blue or destructive accent. An unavailable file action must look unavailable over an image as well as over an empty canvas.

Existing body, secondary and placeholder color pairs were checked for at least 4.5:1 contrast against the four primary surfaces. That check does not establish the contrast of every new overlay or future custom color; check those when added.

## Typography

The shared QSS uses Segoe UI with a sans-serif fallback. Body controls are 10pt, small evidence and preview designations 9pt, and shell titles 17pt. These are point sizes so Qt can follow DPI scaling. Some existing one-off roles, such as 8pt eyebrow labels and the 15pt product mark fallback, live in QSS; they are not a general page-heading scale.

Use short labels and specific status text. Keep long paths, model identifiers, timestamps and request evidence in scrolling or expandable regions rather than truncating the identity needed for diagnosis. Placeholders describe the expected input and never stand in for a persistent field label when the field's meaning would disappear on entry.

## Layout

All tabs share one persistent Designer shell (`src/dtflow/ui/forms/workspace_shell.ui`) with three coordinated content stacks. Branding, navigation, rail scroll areas and splitter remain mounted across tab switches. Only context, primary workspace and evidence content change. The shell owns the global Inspector toggle and responsive state; page controllers retain their content references and service wiring.

Generate has three regions: a left parameter rail, a flexible center containing prompt, optional negative prompt and the large preview, and a collapsible right inspector for LoRAs, assets and evidence. Branding lives at the top of the shared left rail; navigation stays permanently below the brand in the shared left rail at every width. A compact panel-outline toggle beside the brand shows or hides right-side details; its tooltip and accessible name explain the action. Connection uses backend/credential context, readiness in the center and safety evidence on the right. Queues uses durable queue selection/policy, items and dispatch in the center and selected-attempt evidence on the right. There is no full-width application title band or slogan on any tab. The center uses the space that band once consumed.

The shared splitter uses the `size.workspaceGutter` token for an 8 px neutral drag gutter between panels, with a subtle border-colored hover affordance. Internal panel padding is unchanged.

The shared left and right rails have 248–300 logical-pixel constraints in the Designer form. Their contents scroll independently. At a window width below 1100, the inspector initially collapses and navigation remains in the left rail. The panel icon can reopen it. If the inspector is hidden at *any* width, Connection, Generate and Queues remain reachable. The main splitter gives the center its flexible stretch; do not let long labels force a wider minimum.

The prompt starts at 76 px and expands to 180 px on request. The negative prompt stays compact. The canvas takes the remaining center height and fits the whole image, whether portrait 1:2 or landscape 16:9, without crop or distortion. Its 12 px fit inset and 10 px floating-control inset are implemented in the preview widget. At 100% or manual zoom, overflow gets scrollbars; drag pans and Control-wheel zoom anchors at the pointer. Expand temporarily gives the preview its own window and restores its original parent on Escape or close.

For a new tab, establish its primary work object first, then give its details scrolling rails or collapsible sections. Reuse shell navigation and the shared QSS; do not copy Generate's image canvas into a page where the primary object is a queue table or connection state. Check an 800×680 narrow window and a normal desktop window with representative long data before fixing widths.

## Elevation & Depth

Depth comes from adjacent charcoal tones, thin borders and small radii. The image canvas is the deepest surface, so the artifact reads as the subject. Qt floating toolbars sit inside the canvas on opaque dark surfaces and may overlap a full-bleed image. They are presentation only and never enter saved pixels. There is no shared shadow or animation token. Avoid adding decorative elevation to a new tab without a functional separation it explains.

## Shapes

Inputs use gently rounded 5 px corners, buttons 6 px, and panels 8 px. The reusable QSS minimum is 22 px of control content height **before** padding and border; it is not a 22 px final widget height. Designer layouts own the actual space and stretch. Keep the 14 px checkbox indicators and 12 px scrollbar track consistent with the current stylesheet. Use the same focus border language on keyboard-operable controls and retain native focus behavior.

## Components

### Buttons and navigation

Neutral buttons have distinct rest, hover, pressed, focus and disabled states. Primary actions use the accent family and lose that accent when disabled. Destructive actions use warning red only while actionable; disabled destructive controls return to the neutral disabled treatment. Checkable navigation shares the navy selection language with item views. Maintain stable `role` properties and object names instead of adding inline widget styles.

Connection navigation is a compact 58×34 px power-icon button before Generate, with a token-colored last-check dot and textual tooltip/accessibility status. Unknown is neutral, checking/inconclusive is warning, connected is success and failed is error. Border/dot status is independent of the checked tab; focus remains visibly distinct. It does not poll or imply continuous reachability. Images is placed after Queues and opens the explicitly labelled offline Detail preview. Its inline metadata replaces the right Inspector while Images is active; other workspaces retain their Inspector preference. Compact labels preserve the permanent left-rail navigation width.

### Fields and selection

Editable fields use the input surface and border, with a lighter hover border, visible focus border, readable placeholder, disabled treatment and an `invalid` property for validation. Lists and tables use the same selected-row colors and an explicit empty-state hint. An empty LoRA or asset list is informative rather than an unexplained blank rectangle. Preserve textual diagnostics for mismatch, unknown and upload-required states; visual color does not imply compatibility.

### Image preview

The `ImagePreview` widget keeps original pixels and handles Fit, 100%, zoom, pan, scrollbars and expand. Zoom controls float top left; Expand floats top right; saved-output file actions float at the bottom. The designation badge identifies a saved final image, an interim preview, a fixture or an illustrative sample as appropriate. The empty canvas carries the DTFlow mark and a short prompt to generate. The portrait artwork is the fake provider's generated result, labeled as a fixture and never presented as inference. Open file, Open folder, Metadata and Copy path stay disabled until a saved artifact exists. Keep this distinction when reusing the viewer elsewhere.

### Status and progress

Use a short outcome heading plus concrete evidence. A progress bar reflects only known progress. `Outcome unknown`, interruption and failure remain different states. The effective request and attempt details may collapse, but their content remains reachable for reproducibility. Do not infer remote cancellation or a finished result from a local Stop waiting action.

### Page implementation checklist

1. Keep global geometry in `workspace_shell.ui`; register the page's context/work/evidence regions with the existing three stacks. Edit the tracked Designer `.ui` file for the new page, including accessible names, stable `objectName` values, layout margins, stretch, size policy and scroll containers. Do not hand-edit generated `Ui_*` Python.
2. Use the existing stack and shell navigation in `main_window.py`. Keep its navigation reachable when a rail collapses. Introduce page-specific responsiveness only where the page's content requires it.
3. Add shared appearance in `tokens.json` and QSS selectors keyed by semantic object names or dynamic properties. Load tokens in custom painting. Do not call `setStyleSheet()` on individual widgets for ordinary states.
4. Wire presentation through a page/controller and the existing background service boundary. Do not put backend protocols, network waits or durable state decisions in a Designer file or custom visual widget.
5. Exercise empty, populated, loading/busy, invalid, disabled, selected, keyboard focus, hover, pressed, error and unknown states as applicable. Fixture imagery, models and cloud receipts must remain explicitly fake and must not contact a provider.
6. Capture the actual Qt page at normal and narrow widths with representative long text. Compare it with Generate's density and state language; correct clipping and hidden navigation. Verify on Windows for native frame, font metrics and DPI before claiming Windows visual acceptance.

## Do's and Don'ts

- **Do** let the page's primary work object take flexible space and make secondary evidence inspectable without crowding it.
- **Do** keep the runtime tokens, QSS, this guide and a changed `.ui` page in agreement within the same PR.
- **Do** label provenance and outcome in words, especially for samples, previews, fixtures, unknown attempts and saved results.
- **Don't** apply the image-preview composition to a queue or connection page merely for visual parity; reuse color, type, density, controls and states.
- **Don't** add a full-width decorative page header that reduces the primary workspace, or hide navigation with an optional inspector.
- **Don't** claim Linux offscreen screenshots prove packaged Windows behavior.


The compact Connection control follows the supplied mint/teal reference using dedicated shared connection tokens (accent, surface, glyph), without changing global success semantics. Keyboard-only focus uses the standard focus token; mouse focus does not replace the thin status outline.



## Images reference presentation

Images uses a full-width gallery with its logo aligned to the view-controls row
and navigation tabs aligned to the filter row. The
shared brand and tabs remain the same widgets; only in Images are they mounted
in the Designer-defined `galleryBrandSlot` and `galleryNavigationSlot` header slots. Both content rails and the global
Inspector toggle are hidden. Leaving Images restores the rails, chrome and
splitter widths; the Inspector preference remains independent of this page.

Images uses the same charcoal surfaces, blue accent, muted navy selection and
control states as the other tabs, through shared `color.*` tokens and QSS.
The reference guides its composition rather than introducing a separate palette.
Its Detail form has two compact header rows, an
aspect-preserving Cover image, right vertical filmstrip, full-width bottom metadata row and
three-line positive prompt with small inline disclosure buttons. Additional
and raw panels are mutually exclusive.

The shared fixture controller provides Cover/Fit, 100%, zoom, pan, previous/next,
keyboard navigation, fullscreen/Escape and thumbnail sizing. Artwork and metadata
remain explicit samples; LoRA compatibility stays Unknown. Filters, auto-refresh and file actions are disabled until services exist. The source
assets include a generated portrait and crops from the approved reference,
identified as fixtures rather than generation evidence. No saved-output query,
gallery persistence or provider execution is implemented by this presentation.


Grid, List and Detail share one header instance above `galleryContentStack`.
Grid has uniform, aspect-fit thumbnail tiles with the same height at every ratio;
List has metadata columns and a compact row-density control. Neither browsing
view exposes checkboxes, selection state or bulk actions. List hover spans the
row. Clicking a tile or row opens that fixture in Detail; returning to browsing
retains the mounted widgets and their scroll positions. The shared slider controls
Grid thumbnail size, List row density or Detail filmstrip size for the active view.
The fixture controller keeps all three presentations offline; a populated preview
does not establish saved-output discovery, query, filtering or provenance services.


Images header rows have no additional outer inset. The shared brand/navigation
slots sit outside the scrolling controls and retain the shell's chrome width and
height, with the same 8 px row gap. Hidden Inspector controls reserve their space
to keep the logo/title geometry stable. Gallery panel gaps are 8 px; the canvas
has a compact 4 px inset and metadata avoids nested padding.

---
name: DTFlow
description: Native Qt workspace design system for generation and queue workflows
colors:
  background: "#1f1f1f"
  surface: "#252525"
  editorScrim: "rgba(32, 32, 32, 128)"
  input: "#1e1e1e"
  canvas: "#151515"
  text: "#e6e6e6"
  secondary: "#b2b2b2"
  placeholder: "#a8a8a8"
  disabled: "#777777"
  border: "#3a3a3a"
  accent: "#0068b8"
  accentHover: "#0078d4"
  accentPressed: "#005a9e"
  focus: "#60b6f6"
  selection: "#173852"
  selectionText: "#d5ecff"
  success: "#78c97c"
  connectionAccent: "#00d8b0"
  runStart: "#6f3bd6"
  runEnd: "#00ffff"
  connectionSurface: "#123e36"
  connectionGlyph: "#d9f7ef"
  warning: "#ffbf59"
  error: "#ff8179"
  unknown: "#b2b2b2"
  button: "#2d2d2d"
  buttonHover: "#353535"
  buttonPressed: "#292929"
  buttonBorder: "#424242"
  buttonBorderHover: "#5a5a5a"
  borderStrong: "#4a4a4a"
  disabledBorder: "#303030"
  inputBorderHover: "#606060"
  selectionBorder: "#285d84"
  selectionHover: "#204c6e"
  selectionPressed: "#122e44"
  sectionHeader: "#2b2b2b"
  sectionBorder: "#343434"
  itemSurface: "#222222"
  title: "#f2f2f2"
  panelEdge: "#333333"
  onAccent: "#ffffff"
  eyebrow: "#8fbde3"
  badgeText: "#b9dcf7"
  menuText: "#dddddd"
  inputText: "#eaeaea"
  toolbar: "#242424"
  buttonText: "#eeeeee"
  disabledEdge: "#2f2f2f"
  accentEdge: "#1085dd"
  dangerText: "#ff9d97"
  dangerSurface: "#432323"
  dangerBorder: "#c62828"
  host: "#1c1c1c"
  glyphNeutral: "#8a8a8a"
  statusOkBorder: "#2e7d32"
  statusWarningBorder: "#9a6700"
  statusErrorBorder: "#a33a35"
  barTrack: "#2a2a2a"
  tabBorder: "#363636"
  tabText: "#a8a8a8"
  checkBorder: "#777777"
  checkDisabledBorder: "#414141"
  groove: "#505050"
  handle: "#e6e6e6"
  handleBorder: "#909090"
  handleDisabled: "#555555"
  scrollHover: "#707070"
  scrollPressed: "#909090"
  logInfo: "#8a99a6"
  logRowHover: "#2b3846"
  logCodeSurface: "#171c23"
  logPopupSurface: "#292929"
  logNoticeSurface: "#423525"
  logPill1Bg: "#263b46"
  logPill1Fg: "#a9cfe8"
  logPill2Bg: "#30394a"
  logPill2Fg: "#aebfec"
  logPill3Bg: "#283b34"
  logPill3Fg: "#a8d5bc"
  logPill4Bg: "#403629"
  logPill4Fg: "#e8c894"
  logPill5Bg: "#393044"
  logPill5Fg: "#cbb5ed"
  logPill6Bg: "#403039"
  logPill6Fg: "#e4b0c3"
  logPill7Bg: "#293d40"
  logPill7Fg: "#a5d2d4"
  logPill8Bg: "#38372c"
  logPill8Fg: "#d8d0a0"
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
  section:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "11pt"
font-weights:
  body: "400"
  label: "500"
  emphasis: "600"
  title: "650"
  strong: "700"
font-families:
  body: "Segoe UI"
  mono: "Consolas"
control-sizes:
  content-minimum: "22px"
  scrollbar: "12px"
rounded:
  input: "5px"
  button: "6px"
  panel: "8px"
  2xs: "2px"
  xs: "3px"
  sm: "4px"
  thumb: "9px"
  chip: "16px"
  pill: "17px"
  round: "21px"
spacing:
  rail-gap: "8px"
  workspace-gutter: "8px"
  log-row-action: "28px"
  log-table-min: "700px"
  panel-padding: "12px"
  canvas-overlay-inset: "10px"
---

# DTFlow design system

## Images gallery link

The shared Images entry above ordinary right inspectors is navigation, never a primary run action. Preserve its existing responsive placement and visibility rules. Use selection/selectionBorder/selectionText at rest, selectionHover/focus on hover and selectionPressed when pressed, with the standard button radius, body type and weight 600. Keep the full-width 36 px target and reuse the existing image SVG at 14 px, tinted to selectionText. Keyboard focus uses the shared keyboard-only guard. The web reference uses a 6 px content gap, selection/focus shadow rings and 120 ms transitions; native Qt uses its standard centered icon/text spacing and token-backed thicker borders for rings, with immediate state changes (QSS has no CSS box-shadow/transition support). Do not change the shared SVG stroke or other buttons to implement this link. Native Windows/DPI and visual acceptance remain separate from source review.

## Detail slideshow

The checkable Slideshow entry and 0.5–8 second rate editor follow the buffered count in the canonical gallery form. In slideshow, hide the first header row and float the existing pager as a top-of-image translucent bar. Both it and the slideshow controls auto-hide after 2.5 seconds without pointer/keyboard activity; automatic frame advances never reveal them. Dragging, position-wheel bursts and held arrows preview only an ordinal, pause new image admission, and defer in-flight display updates; release/quiet commits the final bounded seek and pins the slider until that selection settles. F6 is window-scoped, gated to active Images/playback. The floating controls sit below the pager at the top right of the picture and are canonical in `gallery_slideshow.ui`, including a checkable Forward/Backward automatic-direction button (checked = Backward); use a 35%-opaque control group at rest and full opacity on hover/keyboard focus. Wheel over the position slider moves 10 photos per step; over the other overlay controls it changes the rate, while image wheel zoom is untouched. During playback, the right-hand carousel is hidden and the same info card is docked into a canonical scrollable rail, showing its prompt/LoRA content in secondary grey without an independent metadata reader; exit restores the carousel. Explicit slideshow entry owns native fullscreen and temporarily uses optimized Detail; exit restores both independently. Ordinary optimization still never changes native window state. F6 enters/leaves, Left/Right navigate, and transient presentation/timers remain window-owned. Slideshow image changes keep the previous photo/info during decode, then crossfade for one third of the configured interval, targeting a fixed 60 updates/second to reduce CPU painting load; retain at most one outgoing pixmap and release it on completion/exit/error. Ordinary browsing is unchanged. This is source-reviewed, not visually accepted.

## Overview

DTFlow is a compact, image-first native desktop workspace. The generated image gets the largest flexible region; the controls that create and inspect it remain nearby. The interface uses charcoal surfaces, one blue action language, restrained corners and precise state text. It is a Qt Widgets application, not a web skin.

This file describes the shared design implemented across Connection, Generate, Queues and the saved-output Images viewer, and the rules to use when adding a tab. The frontmatter is a readable snapshot of the reusable tokens and selected component dimensions; color keys use the exact suffix of `color.*`, while the extra radii are `size.radius*` tokens (`rounded:` keys drop that prefix) and spacing remains Designer/QSS values. the **runtime authority** is [`tokens.json`](src/dtflow/resources/styles/tokens.json). Change that file and [`dtflow_dark.qss`](src/dtflow/resources/styles/dtflow_dark.qss) as needed, then update this snapshot in the same PR. Component-local colors repeated across QSS rules (control text, danger, slider, checkbox, scrollbar, tab, status-card borders) are named `color.*` tokens too; a few single-use literals remain in the QSS (see the token contract), and a new one should become a token on its second use.

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
| Images structure and production controller | [`ui/forms/gallery_detail_page.ui`](src/dtflow/ui/forms/gallery_detail_page.ui), [`ui/gallery_page.py`](src/dtflow/ui/gallery_page.py) |
| Images offline fixture presentation | [`ui/gallery_detail_preview.py`](src/dtflow/ui/gallery_detail_preview.py) |
| Offline state capture | [`tools/ui/preview_states.py`](tools/ui/preview_states.py) |
| Web design-system reference (prototype aid, not runtime) | [`devdocs/design-system/`](devdocs/design-system/README.md) |

The existing [Qt token and interaction contract](devdocs/architecture/qt-design-tokens.md) records the renderer command and fixture boundaries. Offline state screenshots use `<tab>-<NN>-<state>.png`, for example `images-01-grid.png` and `generate-01-empty.png`; numbers are local to each tab/group, not a global sequence. Keep existing names stable and append new states within their group. See [the screenshot-tool guide](tools/ui/README.md) for the separate screen and component-sheet capture namespaces. The [Generate refinement plan](devdocs/plans/2026-10-05-preview-workspace.md) records the earlier workspace work; the [Images preview plan](devdocs/plans/2026-10-05-phase-8a-ui-preview.md) records PR #33. Earlier plans are historical decisions; the current implementation and these sources govern new work.

## Colors

Blue marks primary action, focus and active selection. The four charcoal depths separate the application, panels, editable controls and image canvas. Text and status labels say what happened; color is supporting evidence, never the only signal. Use the frontmatter roles through `${color.name}` in QSS or `load_tokens()` in custom painting. Do not copy literal hex values into new Python widgets.

- **Accent:** primary actions at rest, brighter on hover and deeper while pressed. Focus is a separate lighter blue and must remain visible on both neutral and selected controls.
- **Selection:** muted navy with light text, shared by selected navigation, lists and tables. It is not the primary-action fill.
- **Selects:** every `QComboBox` reads as a select, not a text field: a chevron segment in the input fill, separated by a border-colored divider, on the right whose fill brightens on hover and while open, the shared focus border while open or focused, a dimmed disabled segment, and a popup list with the shared selection colors. Do not restyle a select as a bare input; the compact Images toolbar view mode is the only chevron-only exception.
- **Status:** success, warning and error have distinct semantic colors. Unknown uses neutral secondary text and an explicit label; it is not treated as failure or success.
- **Disabled:** muted text and surfaces without a blue or destructive accent. An unavailable file action must look unavailable over an image as well as over an empty canvas.

Existing body, secondary and placeholder color pairs were checked for at least 4.5:1 contrast against the four primary surfaces. That check does not establish the contrast of every new overlay or future custom color; check those when added.

## Typography

The shared QSS uses Segoe UI with a sans-serif fallback. Body controls are 10pt, small evidence and preview designations 9pt, section headings 11pt, and shell titles 17pt. These shared sizes use points so Qt can follow DPI scaling. Scoped gallery rules retain logical-pixel sizes (including the 14px prompt and 12px disclosure buttons); these are component exceptions, not an alternative global scale. Some existing one-off roles, such as 8pt eyebrow labels and the 15pt product mark fallback, live in QSS; they are not a general page-heading scale.

Use short labels and specific status text. Keep long paths, model identifiers, timestamps and request evidence in scrolling or expandable regions rather than truncating the identity needed for diagnosis. Placeholders describe the expected input and never stand in for a persistent field label when the field's meaning would disappear on entry.

On WSL 1, Qt may substitute DejaVu Sans Bold for Segoe UI semibold (600),
making selected toggles appear heavier. At startup the theme may register the
existing `segoeui.ttf`, `seguisb.ttf` and `segoeuib.ttf` faces from
`C:\Windows\Fonts` through the verified drive-mount resolver. This is
process-local font use, not installation or redistribution; all three faces must
be available/loadable or Qt retains its normal fallback. Windows and ordinary
Linux keep their existing behavior. Tokens and selected-state weights do not
change; native rasterization/DPI parity still needs owner review.

Use the same text hierarchy in Connection, Generate, Queues and Images. Designer
`textRole` properties describe typography independently of `role` and runtime
`state` properties, so presentation does not erase status or action semantics.

| Text role | Treatment | Use |
| --- | --- | --- |
| Default body | Segoe UI, regular 400 | Editable content and prose |
| `caption` | 9pt, medium 500, secondary color | Field/metadata labels |
| `value` | Semibold 600, existing component size | Metadata values and concise status |
| `supporting` / secondary | 9pt, regular 400 | Model identifiers, hints and evidence |
| `section` | 11pt, semibold 600 | Prompt and empty-state headings |
| `identifier` / `code` | Consolas, 9pt, regular 400 | Stable IDs, effective request, events and raw JSON |
| `panelHeading` | 9pt, semibold 600 | Section-card header titles |
| `reading` | Component size, medium 500 | Gallery prompt |
| `loraName` | 9pt, medium 500 | Gallery LoRA names |

Group titles, primary actions and active tabs use emphasis weight; ordinary
buttons use medium weight. Thumbnail badges use the same family, small size and
emphasis tokens in custom painting. Unknown intentionally shares secondary gray; success, warning and error remain distinct semantic outcomes.
Gallery model names and model identifiers are separate labels to preserve their
hierarchy without inline HTML styles. Gallery Additional-detail values use regular
body weight and subtle bottom separators as a scoped exception to `value`.
Windows font metrics and resulting line wraps still need target-machine review.

## Layout

The fixed-width left rail is sized by its scroll viewport, not by the widest hidden page in its stack. The canonical `leftStack` and all mounted context pages use horizontal `QSizePolicy.Ignored`; preserve each page's vertical policy and Queues/Scripts scrolling ownership. Do not widen the rail, add padding or enable horizontal scrolling to conceal oversized hidden-page hints. `connection-61-left-rail-fit` renders synthetic hidden-page pressure and logs widths without assertions; the owner confirmed the native correction on 2026-11-05.

The offline Workflows mockup uses the existing shell with compact library/node palette, canvas and inspector forms, shared section cards and token-scoped controls. Its preview-only Workflows text tab sits between Generate and Queues; Scripts remains a Generate mode. Execution and branch-local pause are fixture presentations, not runtime integration. See the [mockup plan](devdocs/plans/2026-11-05-workflows-ui-preview.md).

All tabs share one persistent Designer shell (`src/dtflow/ui/forms/workspace_shell.ui`) with three coordinated content stacks. The splitter, rails and page content remain mounted across tab switches. Connection, Generate and Queues change context, primary workspace and evidence stacks. Images reparents the same branding/navigation widgets into its two header slots and hides both content rails; leaving Images restores their parents and splitter geometry. The shell owns responsive right-rail state; page controllers retain their content references and service wiring.

Generate has three regions: a left parameter rail, a flexible center containing prompt, optional negative prompt and the large preview, and a responsive right inspector for LoRAs, assets and evidence. Branding lives at the top of the shared left rail; primary navigation (Generate and Queues) stays below the brand in the shared left rail at every width on Connection, Generate and Queues; Connection itself opens from the bottom-bar status icon and Images from the primary-action button above the inspectors. Images uses the aligned header slots described below. There is no header progress ring or Run control (removed in #564): Start, Pause and Resume live only in the Queues interface, and global progress is the aggregate Activity Bar in the status bar. A 30×30 auto-raised **Queue** tool button (stack icon) sits in the brand row with deliberate clearance from the logo and opens the one-click Queue popover (#135). The popover is a `role="card"` popup, 340 px wide: a title, *Repeat N times* and *New queue plan* (the composer's current Pair/Cross plan, disabled with its reason when there is none) as radio choices, a secondary line with the availability reason, the new queue's Concurrency and the credit disclosure, then a neutral *Add to queue* and a `role="primary"` *Start now (N)*, right-aligned; only the action that applies now is enabled. *Generate image* is a split button: a 30 px ▾ segment joined to its right (square inner corners, an `accentEdge` divider) opens the same popover right-aligned under it; its tooltip carries the current count (*Queue ×N*), and it stays enabled during a generation so Add to queue can wait behind it. Outcomes go to the shared bottom overlay. Connection uses backend/credential context, readiness in the center and safety evidence on the right. Its Custom LoRAs, Draw Things presets and Disposable cache controls use the same section-card header/content structure; destructive whole-cache cleanup uses the shared danger action role. Queues uses the grouped queue list on the left, New queue on top of the center above the selected queue's header and items (or the composer), and selected-attempt evidence on the right. There is no full-width application title band or slogan on any tab. The center uses the space that band once consumed.

The shared splitter uses the `size.workspaceGutter` token for an 8 px neutral drag gutter between panels, with a subtle border-colored hover affordance. Internal panel padding is unchanged.

The shared left and right rails have 248–300 logical-pixel constraints in the Designer form. Their contents scroll independently. For Connection, Generate and Queues, at a window width below 1100 the inspector is hidden automatically, primary navigation remains in the left rail, Connection stays at the extreme right of the bottom bar and the Images button moves above the centre; there is no manual Inspector toggle in the header. If the inspector is hidden at *any* width, Connection, Generate and Queues remain reachable. The prompt-hints toggle is a deliberate exception: when opened on a narrow Generate/Queue workspace it temporarily reveals the right inspector and keeps Images above the hints. The main splitter gives the center its flexible stretch; do not let long labels force a wider minimum.

The prompt starts at a compact 52 px (approximately two lines) and expands to 180 px on request. The prompt header holds a compact 38 px icon-only Expand/Collapse prompt toggle (with stateful icon, tooltip and accessible label) and the primary blue Generate image action with a 16 px sparkles SVG side by side; there is no duplicate action row below the preview. The negative prompt stays compact. The canvas takes the remaining center height and fits the whole image, whether portrait 1:2 or landscape 16:9, without crop or distortion. Its 12 px fit inset and 10 px floating-control inset are implemented in the preview widget. At 100% or manual zoom, overflow gets scrollbars; drag pans and the mouse wheel over the canvas zooms (Ctrl optional, 1.1× per notch, same as Images → Detail), anchored at the pointer and never scrolling the surrounding panel. Inpaint uses the same wheel policy: it has no wheel-based brush sizing, so painting, Space/middle-drag pan and Alt-erase are unaffected. Expand temporarily gives the preview its own window and restores its original parent on Escape or close.

Prompt LoRA training hints do not appear on text-input hover. A compact, **icon-only information button** next to each relevant prompt input is checkable: first click opens a full-height, scrollable hint panel, the checked state keeps it visible, and a second click closes it. Generate and the independent Queues composer overlay only the right inspector's scroll viewport, **below and never covering the Images navigation button**. Workflows keeps prompt editing in the right node inspector and overlays the **center canvas**, never the node parameter fields, for prompts and negative prompts. Changing the workspace closes any open hint panel; the content comes from local LoRA metadata, never from a provider lookup or prompt mutation.

For a new tab, establish its primary work object first, then give its details scrolling rails or collapsible sections. Reuse shell navigation and the shared QSS; do not copy Generate's image canvas into a page where the primary object is a queue table or connection state. Check a 1000×680 minimum-size window and a normal desktop window with representative long data before fixing widths.

## Elevation & Depth

Depth comes from adjacent charcoal tones, thin borders and small radii. The image canvas is the deepest surface, so the artifact reads as the subject. Qt floating toolbars sit inside the canvas on opaque dark surfaces and may overlap a full-bleed image. They are presentation only and never enter saved pixels. There is no shared shadow or animation token. Avoid adding decorative elevation to a new tab without a functional separation it explains.

## Shapes

Inputs use gently rounded 5 px corners, buttons 6 px, and panels 8 px. The reusable QSS minimum is 22 px of control content height **before** padding and border; it is not a 22 px final widget height. Designer layouts own the actual space and stretch. Keep the 14 px checkbox indicators and 12 px scrollbar track consistent with the current stylesheet. Use the same focus border language on keyboard-operable controls and retain native focus behavior.

## Components

### Buttons and navigation

Neutral buttons have distinct rest, hover, pressed, focus and disabled states. Primary actions use the accent family and lose that accent when disabled. Destructive actions use warning red only while actionable; disabled destructive controls return to the neutral disabled treatment. Checkable navigation shares the navy selection language with item views. Maintain stable `role` properties and object names instead of adding inline widget styles.

Connection navigation is the existing colored power/status icon in a compact 40×20 px button at the extreme right of the bottom bar, using `size.radiusXs` (3 px) corners. Unknown is neutral, checking/inconclusive is warning, connected is success and failed is error. Border/dot status is independent of the checked destination; focus remains visibly distinct. It does not poll or imply continuous reachability. Images opens the saved-output Grid/List/Detail viewer through one shared 36 px blue primary-action button above ordinary right inspectors, moved above the centre when the right rail collapses. Do not show this button in Images or Logs. The primary navigation remains Generate/Queues, with a Workflows text tab between them only in the offline preview. Scripts stays inside Generate. Images inline metadata replaces the right Inspector while Images is active.

### Fields and selection

Editable fields use the input surface and border, with a lighter hover border, visible focus border, readable placeholder, disabled treatment and an `invalid` property for validation. Lists and tables use the same selected-row colors and an explicit empty-state hint. An empty LoRA or asset list is informative rather than an unexplained blank rectangle. Preserve textual diagnostics for mismatch, unknown and upload-required states; visual color does not imply compatibility.

### Images navigation and loading

The owner-selected Option 2a pager lives directly above the gallery viewport in a persistent 46 px Designer bar, unchanged in Grid, List, Detail and fullscreen. It displays the **actual visible range**, total and buffered count, with bounded batch jump chips keyed to 100-item query batches (the original approved mockup used 80). A selected chip reflects the viewport rather than the last network/database request. The [interactive approved HTML reference](devdocs/mockups/images_hybrid_pager_2a.html) is a visual guide only: the runtime selection remains all matching results, up to 10,000 IDs. Offline render previews are grouped under the **Image Navigation** artifact category. During first entry or an unloaded jump with no committed cards, a delayed, animated thin ring (36 px, 3 px stroke, 180 ms delay, shared accent/track tokens) sits in the gallery canvas; populated cards never receive a blocking overlay. A failed initial read offers Retry, while the existing snapshot warning remains separate.

### Image preview

The `ImagePreview` widget keeps original pixels and handles Fit, 100%, zoom, pan, scrollbars and expand. Zoom controls float top left; Expand floats top right; saved-output file actions float at the bottom. The designation badge identifies a saved final image, an interim preview, a fixture or an illustrative sample as appropriate. The empty canvas carries the DTFlow mark and a short prompt to generate. The portrait artwork is the fake provider's generated result, labeled as a fixture and never presented as inference. Open file, Open folder, Metadata and Copy path stay disabled until a saved artifact exists. In Additional details, show the image path compactly from the `output`/`outputs` directory onward; keep the underlying full path for file actions. Place the compact Use settings and Regenerate actions at the right of that path row, while Copy path, Open folder and Open in external viewer remain a separate file-action row below. Regenerate remains a neutral secondary action because confirmation, not primary-action styling, carries its potentially billable warning. Keep this distinction when reusing the viewer elsewhere. Images → Detail also floats a 30 px rounded-square info button (`info` icon, canvas fill, border token, focus-colored border on hover) at the image's top right; hovering it for about 200 ms opens a compact overlay beneath it with the complete positive prompt and the recorded LoRA chips (the Detail chip presentation, middle-elided names, `Unknown` status). The prompt box is sized to its full text with no scrolling or clipping, starting at the Detail prompt size (10.5 pt) and shrinking to at most 8 pt only when space is short. The overlay stays open while the pointer is on the button or overlay, stops above the Next button and image toolbar, closes on image change, press or wheel on the image, or leaving Detail, and never takes keyboard focus; the Detail panels remain the keyboard path. It reads only the loaded record, with no backend or metadata calls.

### Generate video controls

The Video section stays in Generate's center workspace and uses the ordinary `sectionCard`, `sectionHeader` and `sectionContents` structure, a 16 px video glyph and `panelHeading`. Its body has explicit 12 px internal padding and one horizontal control row: Duration and its effective-duration/frame/playback/audio readout on the left, the cover-filled Start image thumbnail and its small red × clear badge pinned right. The readout may wrap within its flexible cell at narrow widths, never adding a separate normal-state control row or widening the center. The badge clears only the draft source, never saved media. The photo uses the full tile without letterboxing; that thumbnail-only crop is not the submitted crop. Source inspection still shows the full source and exact centered output crop. Keep all row items vertically centered. The thumbnail inherits ordinary button states; the badge uses shared danger/focus/disabled tokens. Duration evidence uses the existing pale-blue selectionText token for readable emphasis rather than another gray hint. Supporting warnings appear below only when needed; do not reserve blank status height. Hover or keyboard focus on the thumbnail or Width/Height controls temporarily shows the centered output-aspect crop in the existing viewer; dimension edits update it live. Restore the latest result when all those controls lose hover/focus, keeping result identity and saved pixels untouched. Crop shading uses the shared canvas color with presentation opacity; the outline uses the focus token. No new palette, mode switch, editor or provider call is introduced. Source review is not native Windows/DPI or runtime acceptance.

### Queue authoring templates

Pose-enabled Queue drafts show a compact Designer-owned ControlNet section: the guidance checkpoint/quantization, strength, control force and Apply from/to values, plus a small read-only preview fitted to the draft dimensions. Numeric text retains exact saved values instead of clamping them to a spin-box step; the shared pose validator reports invalid in-progress values and prevents queue/template capture until corrected. A saved control missing from the cached inventory remains visible, never silently replaced. Replace pose map accepts a prepared body-map image only, validates/fits locally on a worker and replaces the draft atomically; cancellation, errors or obsolete delivery preserve the previous map. It never extracts a photo, uploads, stages execution inputs or changes Generate, existing queues or saved templates. Explicit template Save/Update captures the current map/controls under existing limits. Source review does not establish native visual or backend acceptance.

The Controlnet queue popup's explicit New queue plan action refreshes only the current immutable pose map/controls through Use Generate settings' pose-only mode, keeping the Queue draft's base settings and plan variations (a missing draft captures Generate's base once). Its source disclosure states this transfer; simply opening the popup does not replace the draft. Global-header Plan and the Queues composer continue to use their captured independent draft. The composer pose summary and queued Parameters tooltip expose the exact checkpoint (including quantization suffix), strength, Control Force and apply range for inspection without a provider call.

The independent composer provides Save template and Load template actions beside a wrapping, bounded-width loaded-name/modified label. The searchable template library uses its canonical Designer form in the shared inline editor, with plain-text source/settings/dependency summaries and explicit Update, Rename and Delete actions. Dirty replacement and backend rebinding use the original Load button's shared confirmation state; Update uses Save and Delete uses Load with destructive intent. No template action submits work or modifies Generate; source reconstruction and mutable wildcard dependencies stay disclosed. Keep names/prompts from widening narrow layouts and preserve the shared Escape/navigation/editor priorities.

Pose templates use these same controls, not a second wizard or form. The library summary identifies the saved pose control/strength and retained body map. Save from the captured Controlnet draft or a verified Edit as new queue draft copies bounded map bytes into the recipe, not a source photograph or execution reference. Loading requires DT+ already selected and changes only the composer; missing/altered/oversize map data refuses without dropping the pose. The template is independent of source queue files, and deleting/updating it cannot change an already loaded draft or created queue. Queue creation and Start retain their existing fit, inventory, staging and upload/credit boundaries. Source review is not visual or owner/provider acceptance.

### Queues item evidence

Video items reuse the existing Status cell and shared Activity Bar for observed waiting, sampling, receiving-frame/MB, finalizing and saving phases. Keep one bounded latest transient event per queue/item/kind in the existing GUI relay, not one global progress observation that hides the other running clips. These labels are transient window-owned observations; durable attempt/item settlement remains outcome authority. The ordinary composer summary exposes captured video duration, frame count and one-source/text-only mode; Use Generate settings remains the explicit transfer, not a continuing binding or new multi-run Generate control.

Keep the existing Qt model/view table with painted fixed-height rows: Preview, Prompt, Model, LoRAs, Parameters, Status and a painted stopwatch header. Use shared tokens for selection and semantic status text. Owner-selected presentation uses 64 px cropped tiles, an aspect-fit hover up to 440 px, two visible prompt lines, extension-less display names and three inline LoRAs with all entries on hover. Gauges share the recorded-weight scale. These are presentation choices, not backend compatibility or performance evidence.

The inspector's item identity/actions and Additional information must disappear while composing a new queue. Open in Images uses live committed identity and shared mapped availability, resets conflicting browse filters and discloses unavailable results. Thumbnail failures never authorize a raw-path decode; visible caches are revalidated on owned timers, cleared across navigation and stopped at shutdown. Keep the accepted shared-shell inspector breakpoint without introducing a new narrow-layout fallback.

### Status and progress

Use a short outcome heading plus concrete evidence. A progress bar reflects only known progress. `Outcome unknown`, interruption and failure remain different states. The effective request and attempt details may collapse, but their content remains reachable for reproducibility. Do not infer remote cancellation or a finished result from a local Stop waiting action. The global Activity Bar is a summary, not a second execution authority: a single generation shows its latest actually reported sampler step, preserving that observation through subsequent stage-only and preview events; a new generation resets the observation. A queue with one unambiguous running item shows that item's queue position and keeps backend step detail in the tooltip; concurrent queue work does not invent a single item number. Finished and cancelled queue selections leave no persistent completion/error badge in the header.

### Page implementation checklist

1. Keep global geometry in `workspace_shell.ui`; register the page's context/work/evidence regions with the existing three stacks. Edit the tracked Designer `.ui` file for the new page, including accessible names, stable `objectName` values, layout margins, stretch, size policy and scroll containers. Do not hand-edit generated `Ui_*` Python.
2. Use the existing stack and shell navigation in `main_window.py`. Keep its navigation reachable when a rail collapses. Introduce page-specific responsiveness only where the page's content requires it.
3. Add shared appearance in `tokens.json` and QSS selectors keyed by semantic object names or dynamic properties. Load tokens in custom painting. Do not call `setStyleSheet()` on individual widgets for ordinary states.
4. Wire presentation through a page/controller and the existing background service boundary. Do not put backend protocols, network waits or durable state decisions in a Designer file or custom visual widget.
5. Exercise empty, populated, loading/busy, invalid, disabled, selected, keyboard focus, hover, pressed, error and unknown states as applicable. Fixture imagery, models and cloud receipts must remain explicitly fake and must not contact a provider.
6. Capture the actual Qt page at normal and narrow widths with representative long text. Compare it with Generate's density and state language; correct clipping and hidden navigation. Verify on Windows for native frame, font metrics and DPI before claiming Windows visual acceptance.

## Iconography

One icon per meaning. The glyphs are monochrome SVGs in `src/dtflow/resources/images/gallery/`, drawn into section, metadata and row icon labels through the `iconKey` property and a `QLabel[iconKey="…"]` QSS rule; set the key in the Designer `.ui` file, never a per-widget stylesheet. Section headers lead with a 16 px icon, additional-details rows use 14 px and metadata-band labels and prompt disclosure buttons 12 px (sizes are Designer dimensions). Strokes are light `#f2f6f8` with no fill.

- **Repository icons (use first):** `grid`, `list`, `detail` (sliders: Parameters), `refresh`, `document`, `json`, `calendar`, `fullscreen` and `chevron`: 24 px viewBox, 1.6 stroke, round caps and joins. The app mark is `resources/images/dtflow-app.png` with `dtflow.ico`; the panel and connection icons are code-drawn in `ui/brand.py`.
- **Lucide icons (where no repository icon fits):** `server`, `key-round`, `shield-check`, `flask-conical`, `settings`, `cloud`, `send`, `layers`, `list-checks`, `triangle-alert`, `image`, `box`, `repeat`, `compass`, `waypoints`, `ban`, `dices`, `file-image`, `hard-drive`, `weight` and `timer`, copied unmodified from `lucide-static` 1.52.0 (ISC, notice in `images/gallery/LICENSE-lucide.txt`) except that the stroke is `#f2f6f8` instead of `currentColor`. They keep Lucide's 2 px stroke, slightly heavier than the repository set; replace one with a repository SVG by changing its file and keeping the key.

| Area | Icons |
| --- | --- |
| Connection sections | Connection `server`, DT+ credentials `key-round`, Connection check `shield-check` |
| Generate sections | Connection `server`, Parameters `detail`, Simulation `flask-conical`, Configuration `settings`, Cloud assets `cloud` |
| Queues sections | Variation `layers`, Selected item `list-checks`, Outcome `triangle-alert` (shown only for no-result and failed items) |
| Gallery header | Grid `grid`, List `list`, Detail `image`, Refresh `refresh`, full-screen toggle `fullscreen` (every view), filter combo arrow `chevron`, fullscreen `fullscreen` |
| Metadata band | Model `box`, LoRAs `layers`, Steps `repeat`, CFG `compass`, Sampler `waypoints`, Date `calendar` |
| Prompt disclosure | Additional `document`, Raw `json` |
| Additional-details rows | Negative prompt `ban`, Resolved seed `dices`, Filename `file-image`, Source `hard-drive`, File size `weight`, Provider duration `timer`; Path keeps `document` |

Do not use emoji; Unicode is limited to the gallery's prev/next and zoom glyphs. The web reference's "Icon Usage Map" card in [`devdocs/design-system/`](devdocs/design-system/README.md) mirrors this table.

## Web design-system reference

[`devdocs/design-system/`](devdocs/design-system/README.md) mirrors these tokens as CSS custom properties, with React components, foundation cards and per-view kits for prototyping. It follows this guide and never overrides it. Token naming, the named component-local QSS tones, the extra radii, the web-only spacing scale and the font substitution are recorded in the [token contract](devdocs/architecture/qt-design-tokens.md#web-design-system-mapping). Update both in the PR that changes a token.

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
and navigation tabs aligned to the filter row. The shared brand and tabs remain
the same widgets; only in Images are they mounted in the Designer-defined
`galleryBrandSlot` and `galleryNavigationSlot` header slots. Both content rails
are hidden there, while the Queue button stays in the remounted brand row.
Leaving Images restores the rails, chrome and splitter widths; right-inspector
visibility follows the responsive shell layout rather than a saved toggle preference.

Images uses the same charcoal surfaces, blue accent, muted navy selection and
control states as the other tabs through shared `color.*` tokens and QSS. Grid,
List and Detail share one compact header above `galleryContentStack`. With no
valid saved viewer preference, Images opens in Grid. A saved view preference
restores Grid, List or Detail on later launches.

The shared thumbnail control is user-facing percentage, not pixels: 100% is now
the former 200% size, a 320 logical-pixel Grid/filmstrip baseline, with a
25–250% range. List rows scale their image *width and height* and the entire
Image column as the percentage changes; a fixed narrow column must never clip
larger thumbnails. Grid, List and the Detail filmstrip reuse one thumbnail
decode per saved output with a fixed maximum side of **600 source pixels**
(smaller originals retain their actual size). Changing zoom adjusts only
presentation geometry; it does not trigger separate 320/640/1024 px versions
or write derived thumbnails to disk. The 25–250% slider may still upscale
above the 600 px source at its highest settings. Decoding is asynchronous and
bounded to six concurrent tasks, with no unbounded full-resolution gallery
loads. The Detail preview alone reads the original image at native resolution
(`max_side=0`) under the original-file read lease. The internal pixel
conversion is a presentation detail and must not leak into the control
label. Grid keeps uniform aspect-fit tiles; List has metadata columns and
row-wide hover. Original shipped viewer behavior was single-focus browsing; Phase 8E's draft branch adds checkboxes and a checked-image selection layer without changing Detail focus or tile geometry.

Detail defaults to Fit so the complete saved image is visible on entry. The
image toolbar uses the Generate preview's controls and styling (Fit, 100%, −, zoom slider, + in the shared toolbar frame, floating top left; the
Cover/Fit select is gone) and the position/size labels sit bottom left; 100%, zoom, pan, previous/next,
independent window fullscreen (F11), optimized layout (Images icon/Escape) and the right-hand filmstrip remain available. Optimization only collapses/restores the filter row, selection bar and metadata/prompt panels; it never calls native window show/state methods. Escape toggles it outside text editors, popups and active inline feedback. The filmstrip
spans the content height beside the preview and its metadata/prompt/disclosure
panels. Image overlays have a separate 10 px interior inset.

The production `GalleryPage` queries committed saved outputs through shared
bounded read-only services. Thumbnail decoding, selected-image decoding and
metadata reads remain off the GUI thread. Auto-refresh is on by default and
anchors the selected or first visible output so newly saved results do not
unexpectedly move the browsing context. Viewer preferences are durable and
separate from disposable derived data.

Auto-refresh keeps its labelled On/Off control and existing geometry. Its On
colors reuse Connection's `color.connectionSurface`, `color.connectionAccent`
and `color.connectionGlyph`; Off uses the neutral input, border and secondary
tokens. This is a colors-only change, not a new icon or refresh behavior.
On remains mint/teal on hover and after mouse activation. Connection and
Auto-refresh share keyboard-only focus handling: Tab/Backtab/shortcut focus
shows the dashed focus-token outline, while mouse focus preserves state colors.
Auto-refresh selectors outrank the gallery's generic hover/focus rules.

Refresh reconciles the bounded page by stable output identity: keep existing
Grid cards, List cells/icons and filmstrip buttons/painters through insertions
and reordering. Update metadata in place, remove only departed entries and
decode only new or changed image references (path, size, availability). Retain
unchanged in-flight decodes and the selected Detail pixels/zoom/pan when other
outputs change; identical pages are a render no-op. Designer filmstrip slots
remain valid for shared presentation callbacks. See the
[incremental refresh plan](devdocs/plans/2026-10-06-images-incremental-refresh.md)
for implementation and the owner validation boundary.

The compact filter row omits separate visible labels. Positive-prompt search is
visible and intentionally compact; model, LoRA, date, exact dimensions,
aspect-ratio and deterministic sort controls use placeholders/defaults plus
accessible names and tooltips. Negative-prompt search remains available through
the shared query/CLI service but is intentionally absent from the visible Images
header. Detail shows recorded provenance and uses explicit `Not recorded` or
`Unknown` text when evidence is absent.

The Phase 8E gallery selection toolbar is one reserved 52 px-high row immediately below the shared header/filter rows. It uses `color.toolbar` with `color.selectionBorder`, muted checked-state blues from `color.selection`, `color.selectionText` and `color.selectionBorder`, and the existing danger treatment for Delete. **Select all** is an independent always-visible button in Grid/List/Detail, never an overflow action; it adds matching outputs across all pages up to the combined 10,000 checked-ID limit. When all current filtered results are checked it becomes **Unselect all**, clearing every checked ID including hidden checks. A partial fill must not show Unselect all. Narrow layouts collapse secondary Unselect/Copy paths controls into More rather than wrapping or truncating Select all, Export or Delete. Grid and List checks share stable output identities; Detail focus remains separate. Export is a true checkable toolbar toggle, always enabled even with no checked images: neutral when the column is closed, blue selected when open, with accessible expanded-state semantics. It toggles a scrollable right-hand column beside gallery content, using the same canonical export card and controller as Connection; no export is submitted on panel opening and Export JPEGs still requires checked identities. The close button and tab navigation must synchronize its checked visual state, while reopening Images restores an open column. Delete opens a confirmed system Trash action through the shared durable service. Owner reports smoke acceptance of deletion, checkbox appearance and Select all / Unselect all behavior (2026-10-09); broader platform/packaged acceptance remains separate.

Images removal uses a joined main action and adjacent dropdown, preserving the existing toolbar slot/danger treatment. The main label is **Move to Trash** (Recycle Bin on Windows) or **Delete permanently**; the dropdown selects a mode only and stays usable when Trash is unavailable. Keep the permanent label fully visible, with stable confirmation width. Default Trash, separate environment preferences, no saved consent or automatic fallback. Each permanent operation shows a fresh inline count and irreversible/no-Undo warning; only explicit **Delete permanently** confirms, with Cancel in the shared bottom overlay. Mode/selection/filter/navigation changes discard consent. Reuse the shared worker/progress/result pipeline; source presence does not establish new permanent-removal/platform acceptance. This supersedes the preceding historical Trash-only action description; existing owner Trash smoke is not permanent-delete acceptance.

When live gallery persistence is unavailable, the last published browsing snapshot may supply Grid/List/Detail, filtering and paging. Show a persistent word-wrapped read-only status with publication timestamp and live-updates-unavailable wording. Preserve widget ownership across failure/recovery; do not reset the first-render sentinel. Keep source-file leases/provenance and disable snapshot-derived export/edit/reuse/regeneration/removal authority until live access returns; do not dismiss unrelated active inline editors on source changes. Snapshot layout/source presence and offline fixtures are not native acceptance evidence.

Snapshot fallback also has one slim amber bottom-overlay status row: one text line plus a small dismiss control, no separate title/detail, progress bar or expiry countdown. Its height follows one text line with minimal padding, bypassing the normal overlay minimum only when compact rows are alone. Existing editors/confirmations keep their own overlay surface. Retries update the same keyed row; manual dismissal lasts through brief live/snapshot oscillation, tab navigation and newer snapshots. With Auto-refresh On, a first live success changes the same row to recovering wording; another consecutive live success at least one five-second refresh interval later ends the episode and removes the row. With Auto-refresh Off, one successful live refresh ends it immediately; turning polling off during successful live recovery also ends it, without another timer. Snapshot results or failed refreshes do not count as recovery. Fresh live authority returns immediately, independently of warning stability. Only a new fallback after recovery may warn again. The gallery's timestamp label remains available after dismissal. New overlay appearance/dismissal remains owner acceptance; preview source is not a visual smoke result.

The standalone `DetailFixture` and preview tools continue to use bundled
illustrative artwork. They are development presentation fixtures only and do not
replace or establish saved-output evidence.

Images header rows have no additional outer inset. The shared brand/navigation
slots sit outside the scrolling controls and retain the shell's chrome width and
height, with the same 8 px row gap. The Queue tool button remains part of the
brand row in this remounted chrome. Gallery panel gaps are 8 px; the canvas
has a compact 4 px inset and metadata avoids nested padding.

Reusable neutral-button, input-hover and selection states use named `color.*`
tokens. Keep intentional component geometry overrides scoped; remove obsolete
base declarations rather than layering another copy of the same selector.

## Section cards and information surfaces

Connection, Generate and Queues use shared `sectionCard` group boxes with an
internal `sectionHeader` row (16 px glyph and compact semibold heading) and a
`sectionContents` region. Native floating group titles are empty; the QGroupBox
identity, enable/disable behavior and accessible name remain intact. Shared
neutral header/border/item-surface tokens separate sections without introducing
a second tab palette. Gallery metadata uses the same leading glyph language;
its prompt uses medium reading weight and disclosure actions use semibold.
Lists use compact rounded item surfaces; table rows use fine separators and a
shared hover fill. Selected and disabled states retain the existing semantics.
Keep the approved shell chrome and gallery gutters; deeper section hierarchy
comes from headers/surfaces rather than extra external padding. These styles
replace the earlier Generate group-box treatment. Exact native-DPI and packaged Windows visual acceptance is not established by
source review or the owner-reported test pass.


Unknown/informational notices use the neutral secondary palette, small body
weight and explicit status wording. Do not tint entire LoRA names/weights or
backend-availability hints purple. Success/warning/error colors remain reserved
for their actual outcomes.

Gallery metadata separates model identity (accent icon, semibold name, secondary
identifier) from LoRA rows (neutral chip, name, numeric weight, explicit Unknown
badge). Status does not tint the full identifier. The positive prompt has no
redundant visible caption; its accessible name remains explicit. Expanded
disclosure controls use shared accent rest/hover/pressed states.

The metadata band is 108 logical pixels high, with compact LoRA row padding
and horizontal scrolling for long identifiers. This is a scoped Designer
dimension, not a new shared token. Preserve the 28px model mark, separate
name/weight/status widgets and explicit Unknown wording when adding live data.

Checkable section cards require an operable header control (a keyboard-accessible
checkbox synchronized with the native group-box state); a passive label cannot
replace a native group-box toggle. Parent activity disabling still applies.

The New queue inspector summary uses the existing narrow right rail and a two-column form. Its values and explanatory note must allow horizontal shrinkage, wrapping long checkpoint and LoRA identifiers (including unbroken names) without enlarging the card or clipping the rail. A wrapped identifier must also receive enough vertical space for every line, including the last LoRA identifier and its weight; the following Items row may not overlap or crop it. The offline `queues-52-composer-long-identifiers` and `queues-55-inspector-two-loras` renders cover text height and geometry at minimum rail width; native Windows DPI acceptance remains separate.

Queues keeps the queue header at its natural height. The items table or
empty-state region receives the flexible workspace height; empty-state title,
guidance stay together in its center.
Hide the queue header (and its progress) when no queue is selected rather than
presenting “0 of 0” as work. Avoid distributing spare height among action rows.

**New queue** opens the composer in the center instead of the selected queue
(header, items and empty state hide; Close or selecting a queue returns). It
scrolls on its own: an untitled top row, right-aligned, holds the actions
where + New queue was (Concurrency for the new queue, neutral *Save paused* and
*Add to queue*, `role="primary"` **Start now (N items)**, then Close; when the
center is too narrow, the entire action group including Close flows together
onto a wrapping row, rather than isolating Close above the remaining actions), a
secondary summary of the independent queue draft with explicit *Use Generate settings* and *Copy to Generate* actions, an editable base/source prompt, then the **Variation** section card. Editing or selecting a saved queue never overwrites Generate. A compact copy icon beside the selected queue title copies its full first-item prompt. Newly composed queues retain original authoring input separately from resolved execution items; Edit as new restores that source, while legacy reconstruction discloses its limits. A bottom spacer
keeps the cards at their natural heights. Variation's Mode is Repeat (an item
count, seeds s, s+1, …) or Cross/Pair. Pair/Cross dimensions are rows built by
selection: `ValueChips` show each value as a removable `valueChip` button
(selection-blue fill, small emphasis text) that wraps with a flow layout, then
an adder: the shared searchable inline editor for models (inventory, plus a
custom identifier) and samplers, an editable preset list for sizes (Generate's
size first), a bounded number box for steps, guidance and Inpaint
strengths, and the shared LoRA identifier+weight editor for LoRA sets (with
*None* and *Generate's LoRAs* at the top of its list). Prompts are one per line; seeds and LoRA weights stay typed. Below them,
*Draw at random* is a grid of small count boxes (*Listed* = every value). It reflows from three label/control pairs per row to two and then one according to the visible center viewport, not the main window width. *Edit prompts* keeps shuffle, drop and add controls together in responsive groups; the plan-seed field and Reroll remain visible without horizontally widening the center. The form may put a caption above a field at the narrowest widths. Hidden dimensions (notably text-to-image Strength) do not reserve an empty grid cell; the two remaining LoRA controls occupy the same row. The item-preview table collapses when the plan has no valid rows and reappears after a successful preview, while the validation message stays visible. All action buttons stay reachable, and the composer scrolls vertically without relying on a hidden horizontal scrollbar. Offline `queues-53` and `queues-54` capture the narrowed center with both outer rails visible. *Prompt
variation* a mode box with a count, and, only while a prompt variation is on,
wildcard lists, an *Edit prompts* row (shuffle, drop, add) and *Parts to add*;
the *Plan seed* field and **Reroll** show only while something is drawn. The
count updates at once and the preview table about 250 ms after the last edit,
without a Dry run button; planner errors mark the owning row (`invalid`, red
chip and Add borders) and appear in the count line. Add to queue is enabled
only while a queue runs with a live dispatch owner; Start now is disabled while
this window dispatches or generates and while another process dispatches. While
composing, the right column replaces item evidence with a *New queue* summary
card (the `itemIdentityBox` pattern: backend, model, LoRAs with weights, items,
varies, seeds, Concurrency, plus a caption that nothing is sent yet).

The queue header is the single status location for the selected queue: the
prompt title (`section` text role, wrapping), then a labelled facts grid
(`caption` / `value` pairs: Model, LoRAs one per line, Plan on the left;
Backend, Created on the right), one `statusChip` label (state-colored border and text; the
neutral selection treatment for *Up next N*), then one action row and one
progress row. The action row holds exactly one `role="primary"` button whose
text follows the state (Start, Start next, Pause, Resume), neutral-red Cancel,
Earlier/Later only for a waiting queue, the queue's own Concurrency spin box, a neutral Relaunch
that opens a menu (same or new seeds, all or only failed items; the list's
context menu offers the same submenu) and a neutral Remove whose consent uses
the shared red inline confirmation. The item panel keeps its identity grid and
puts Open folder and a neutral *Retry item* (failed, cancelled or no-result
items only) on their own row below it, so long failure codes never push them
out of the column. The
progress row is a painted segmented bar (success, error, unknown, warning and
accent runs over the bar track; pending is empty track), a finished count and
`filterChip` count buttons in their state color that turn selection-blue when
checked. Transient outcomes use the shared bottom overlay, never a sticky
message line. Long titles and subtitles wrap or elide; at a 1000 px window the
header must not clip.

The shared running-queue overlay and Activity Bar tooltip include elapsed wall time
since the latest dispatch start (including pauses and provider waits). After the
first successful item with a usable duration, append a labeled remaining-time
estimate using median successful duration and observed running-item concurrency.
Hide the estimate when paused, cancelled, finished or awaiting a new owner; mark
mixed model/size/step plans as rough. Estimates never drive the blue progress bar.
Count down visibly once a second with a separate page-parented presentation timer
and a monotonic deadline. Unchanged database polls preserve that deadline; observed
settlements, timing samples, concurrency changes and Resume rebase the estimate.
An expired estimate stays at zero until fresh evidence arrives; it never completes work.

In the left context rail the queue list takes the flexible rail height under
the QUEUES eyebrow, an icon-only Refresh and a filter field. Rows are painted
two-line items (semibold title with a status chip, secondary subtitle, then a
4 px segmented mini bar with a finished count) under eyebrow-style group headings
(Running, Up next, Paused, History). Clear history stays at its natural height
below it. A neutral **+ New queue** button (the selected queue keeps the only primary action) sits right-aligned in
a row at the top of the center workspace, so it is visible at every width
(the right column hides below 1100 px); it hides while the composer is open.

### Local completed-image counters (issue #221)

Connection hosts a `sectionCard` named **Local completed images** between the status card and the preset library. A caption, the shared `SearchableComboBox` (inline editor, fixed list, no free text) and a secondary day line sit above dynamically built rows: a compact rounded `itemSurface` row (`role="usageRow"`) with the key label in value weight, an optional `statusChip` **Active** marker and a right-aligned count whose color comes from the shared `QLabel[state=ok|warning|error]` tokens. Count text and the accessible description stay readable without color. The card always carries the estimate disclaimer and an unattributed note when needed; empty, reading and unreadable states use the secondary text role. The dropdown lists one entry per whole GMT offset (-12..+14) as `GMT+02 · 15:36 · Cairo`; Europe/Paris takes the entry matching its current offset (for example `GMT+01 · 14:36 · Lagos, Paris`) to keep daylight-saving boundaries.

The status bar places the usage pill `active/all` (`QLabel#usagePill`) after Help and Logs, followed by the Connection power/status button at the extreme right. It uses the `size.radiusXs` (3 px) corner, 8 pt strong text and the existing success/warning/error text, border and surface tokens (`connectionSurface`, `warningSurface`, `dangerSurface`), driven by the active key's count; neutral when no key is available. Its width is fixed (room for five digits each side) so the loading placeholder never shifts neighboring buttons. The grid position field to its left is a diagnostic shown only with `-x/--dev`. Thresholds are owner working-assumption guidance, not provider semantics. Offline renders `connection-45` to `connection-60` cover empty, thresholds, edges, timezones, collisions, errors, narrow width and the pill states; they are layout evidence, not Windows/DPI acceptance.

The Generate LoRA picker marks a custom LoRA the selected DT+ key is not known to hold with a small amber pill, `QLabel#loraKeyPill` (text "not on key"): the warning usage pill's recipe (`size.radiusXs`, 8 pt strong text, `color.warning` text, `color.statusWarningBorder` border, `color.warningSurface` fill, 7 px side padding), inserted after the name, before the weight slider. LoRAs the key holds carry no mark and none are hidden. No new token was added. The pill and its hover line are hints from the last stored-list read; an already open picker fills them in when that read finishes. Owner-seen on Windows 2026-11-14; no render was made.

### Inventory continuity and statistics

Provider switches restore each provider's last-successful matching observation without resetting its form. Preserve model/LoRA counts, provenance (cached, remembered or refreshed) and original observation time alongside refreshing/error status. A failed refresh says Refresh failed · showing previous models and does not rebuild/prune selections. Unknown/no matching observation is not a fabricated zero inventory. Endpoint or selected credential changes invalidate mismatched availability evidence; mere provider switching or a failed HTTP/RPC response does not. Discovery restoration itself never silently removes selected checkpoints/LoRAs; deliberate user filter changes keep existing catalogue behavior. Generation still performs fresh live validation. Use the existing inventoryEvidenceLabel and shared secondary-text styling; no new tokens or layouts.

The Images export column is page-local, not the shell inspector or a List-side preview. The canonical gallery form owns its scroll container, close action and empty card host; the Connection form owns the single shared vertical export card. Reparent the card on navigation, preserve settings/progress and never cancel merely because the panel is hidden. Keep the listing flexible beside the column; both remain keyboard-accessible and the export controls scroll at smaller heights. Owner reports column smoke passed for 5772f54. The subsequent checked-only Images context hides the scope label/dropdown, retaining Connection scope choices; that small follow-up remains owner-smoke-pending.

Grid and Detail selection indicators share List's explicit white tick/partial SVGs, 14 px indicator size and token-based rest/hover/checked/disabled states. Scope the same QSS selector groups to gallery object names; preserve the Grid checkbox wrapper and independent focus/check state. Owner reports style smoke passed for the glyph-parity fix at 8ec3a55 on 2026-10-09; broader DPI/platform/packaged coverage remains separate.


## Inline actions and shared progress

The global activity-bar Stop controls the locally owned displayed queue, not whichever queue is selected for inspection. A missing owned view cannot authorize a selection-based fallback. Stop immediately disables its action and shows the stopping caveat at both short and long widths; later progress must not replace that text. Auto-play changes run identity and gives the following queue fresh transient feedback and an enabled Stop. Per-queue Cancel and dispatcher/remote-uncertainty rules stay unchanged.

PR #160's docked activity-bar refinement uses `color.selectionBorder` for running fill and `color.selectionHover` for the alternate unknown-progress stripe, without accent mixing. Keep the queue count compact (`4/7`) and retain the `+N queued` chip before dropping ETA/elapsed as stage text clips. The status-bar slot falls back to the existing overlay below 240 px. Routine results retain the six-second hover/focus-paused lifetime and 2 px draining line, then fade for 450 ms; expiry callbacks must not fade or remove a newer task using the shared bar. These five owner-directed refinements supersede the bundled activity-bar mockup's older fill/count values; native Windows/DPI acceptance remains separate.

The standalone [inline interaction mockup](devdocs/mockups/dtflow_inline_panels.html)
is the synchronized **illustrative reference** for PR #58's owner-smoke-tested Qt
workflows (2026-10-10). It retains the approved original embedded images and
covers Recycle Bin/Delete, Cache, Export panel toggle, Generate header and
progress, editor/notification coexistence, and the teal/amber six-second notice
lifetime. Its photos, metadata, step counts and generated outcomes are **demo
fixtures**, not actual saved outputs, live service calls, or pixel-perfect proof.
Current Qt Designer forms, Python controllers and shared QSS remain authoritative.


Application-owned dialogs are replaced by the canonical `inline_feedback.ui` surface,
with `ui/inline_feedback.py` handling presentation only. Keep native file/folder pickers
and ordinary menus. Only Confirm occupies the original action slot, preserving its width;
its non-destructive styling reuses Auto-refresh ON's `color.connectionSurface`,
`color.connectionAccent` and `color.connectionGlyph` tokens; Delete/Clear override
with `color.dangerBorder` and `color.onAccent` for a red confirmation. Cancel is in the shared bottom overlay, not the original action row. No timer;
Cancel/Escape/navigation/relevant scope edits disarm pending consent. Do not count a mouse
double-click as two independent consent activations. Preserve the full destructive/credit
consent safeguards. Cloud LoRA Prepare is an explicit file-upload action but does not
need a second Confirm after the native file picker: show its possible DT+ upload and
later-generation credit implications in the asset note and Prepare tooltip instead.
Cancelling the picker does nothing; reject a changed backend or newly busy state
before starting asynchronous preparation. Service-level receipt/retry safeguards remain.

Prepare supports multiple explicitly selected converted LoRA files and remembers the picker folder per runtime. Keep a single owned activity row with count-based “Preparing N of M” detail, then one summary separating confirmed, failed, unresolved and unstarted files; do not invent elapsed progress percentages or replace selected weights. Folder-preference failures use a separate amber result row and never block explicit preparation. Navigation does not cancel admitted uploads; backend/credential changes stop only further admission after the current file settles.

Prepare rows align all six cells (file, architecture, short name, quantization, Trigger Words, status) on one top edge. A `.safetensors` row's descriptive metadata is two inline lines in the preview band, drawn by `ui/source_details_view.RunsLine` from tokens only: labels and separators `color.secondary` at `type.weightBody`, bold values `color.text` at `type.weightEmphasis`, `type.small`; each line is elided to the width and shows no panel or box. File text is plain text, never markup. One HTML-escaped tooltip, headed "Training metadata", covers the whole area (lines, gap and margins). The Trigger Words field shows a constant-height "saved" or "from file" badge; an empty saved value counts as not set. Every tooltip in the app fades in (set once at startup) instead of sliding.

Checkpoint and LoRA choices use inline substring search and custom-identifier entry.

The compact checkpoint-details and status-bar About buttons use a 20 × 20 logical-pixel button with a 16 × 16 icon and zero padding. Do not combine a fixed 20 px About button with wide text-button padding that squeezes its icon.
On Local Draw Things and direct DT+ Cloud, the Checkpoint label row also holds a 16 px Compute Units meter with 8 pt numeric text, narrow no-break thousands separators and no hover overlay. Its fixed 0–40 000 scale fills blue below the limit and stays full/red at or above it. Unsupported modifiers or missing exact metadata show a base estimate when a supported family is known; unknown/unsupported families and unsupported operations read unavailable with no fill. Fake hides the meter; Local shows it regardless of the Include DT+ Cloud catalogue checkbox. Keep the label row fixed at 20 px, the Backend fields 8 px apart, Checkpoint 12 px below the preceding field and its selector 4 px below the label row; inventory buttons and caption use 6 px gaps. The CU section of Checkpoint details shows only the value and supported models; technical explanation belongs in developer documentation. The display threshold is not quota or authorization.
LoRA identifier and 0.0–3.0 weight (0.1 steps, one decimal) share one Add step. In the
inline editor, Cancel and Add appear beneath the fields, right-aligned; Add uses the
same `role="primary"` button treatment as the Queues Start now action, while Cancel stays neutral.
Other confirmations retain their header controls. Existing saved requests are not silently clamped; if a catalogue LoRA suggests a weight outside 0.0–3.0, the Add editor visibly explains its 1.0 initial value. Preset names and explicit metadata/About inspection
use the same main-window surface, with scrollable text for long notices.
While an inline input editor is open, a 50%-alpha charcoal scrim covers the central
workspace behind the bottom editor, intercepting background mouse interactions
without resizing the preview. The editor remains fully opaque, and keyboard focus
stays inside it until Add/Apply, Cancel or Escape closes it. The scrim follows
window resizing and is removed when the editor closes or is replaced. It does
not apply to confirmation, progress or result messages.

`activity_strip.ui`, `inline_feedback.ui` and `activity_progress_row.ui` compose one
bottom feedback overlay for confirmations, editors, notices and fallback/concurrent jobs. The docked activity bar above owns the newest running job or routine result; the overlay retains the earlier HTML interaction structure. It
never joins the main content layout: showing it does not resize previews, thumbnails
or scroll containers. A normal row is compact (48 px baseline, 28 px symbol, title and
small body copy, 5 px progress track with separate count and Stop). Long essential
messages wrap directly; no Details toggle. Expanded selection/name editors and explicit
metadata inspection stay in the same overlay; long metadata remains scrollable.

Use existing selection/blue tokens for progress/info, connection/mint-teal for
success and non-destructive confirmation, danger/red for errors and Delete/Clear
confirmation, and warning/amber for partial/stopped and ordinary nonfatal warnings.
Amber outcomes use `color.warningSurface` and `color.warningGlyphSurface`
with `color.statusWarningBorder`; red is reserved for failures and destructive
consent. The outer activity-strip frame is transparent and borderless, so the
actual feedback row supplies a single semantic surface and border. Non-destructive
confirmation reuses Auto-refresh ON's connectionSurface-to-surface treatment,
connectionAccent border and connectionGlyph icon; Cancel remains neutral. The
warning text still explicitly describes possible uploads or charges; confirmation
does not imply successful completion. Repolish the inline icon as well as the form
when its feedback state changes, avoiding a stale blue icon. Reuse shared fonts,
radius and native DPI
rather than hardcoding the HTML's pixel font sizes. Confirm remains on the original
button with stable width; Cancel is in the overlay. Export has no extra confirmation.

Concurrent tasks and unrelated informational/error notices have separate labelled
rows inside this one surface and must not replace one another or dismiss pending
consent. An unrelated InlineNotice shown during an armed confirmation is a
separately dismissible row; only direct Cancel/Escape, relevant scope changes,
or another explicit editor/confirmation may disarm it. Unrelated notices
also use their own rows while an inline input editor is active, so partially
typed values remain untouched. Success and all amber (warning, partial,
stopped) overlay rows last six seconds, with a 2 px full-width lifetime
line draining along the bottom edge and a small right-aligned countdown
before Dismiss; success uses teal and amber states use warning amber.
A 450 ms fade-out follows expiry. The timer and line pause while the
overlay is hovered or keyboard-focused. The 5 px blue task progress
track is distinct and reflects actual completed work only.
Amber informational warnings use the timed row even without another active
editor/confirmation. Red errors stay until dismissed; neutral informational inspection closes with its × button, Escape or a click outside it. A click outside also dismisses finished notices (errors included) and cancels an armed confirmation or editor; running rows are never affected. Routine queue/generation success notices last three seconds; every other timed notice keeps six. Amber overlay expiry only hides the notification: it
never erases durable outcome records, uncertainty indicators, failure
details, or changes stop/retry behavior. Escape dismisses a standalone
notice (a × button replaces the text Dismiss), while Cancel/Escape disarm a pending confirmation/editor;
dismissing any row never stops work.
Recycle Bin Delete confirmation and in-progress Trash rows reuse the exact
Images Delete button SVG (\`resources/images/gallery/delete.svg\`), tinted
with the shared danger-red token; neither shows a recycling emoji. Stopped
shows a stop-square glyph; progress uses operation-specific glyphs
for Trash, Export, Cache, Queue, selection and generation. Destructive
classification is explicit at the confirmation call site (`danger=True`),
rather than inferred from button object names. Dismiss never stops work;
Stop delegates to the owning controller.
Keep the generation preview and existing uncertainty semantics. The shared activity strip routes live Generate progress and Stop waiting locally to the docked bar or its overlay fallback; never duplicate the stop button or a synthetic 0–100% bar inside the Generate workspace. Show real backend stage text and step counts when reported, not invented percentages. Keep a discreet persistent Generation status label beneath the preview for durable failures and uncertain outcomes. All filters,
slots, expiry timers and deferred positioning are Qt-owned by their overlay widgets.
No idle layout space is reserved. Native DPI, clipping/overlap, resize, focus, scroll
continuity and packaged acceptance require owner verification; static/CI checks do
not establish pixel-perfect parity with a web mockup.

### Generate image-operation mode switch (step 6, work in progress)

Place the joined Text to image | Inpaint buttons beside Prompt in the canonical Designer header, not a dropdown. The shared `segmented` frame role inherits ordinary button typography, control height and padding. Exterior corners use the button `size.radius` token (6 px), not the panel radius (8 px); interior corners are square only to join segments. Selected, selected-hover, selected-pressed and disabled states use the existing selection/button/disabled tokens. The initializer owns zero layout margins and spacing. No new palette or duplicated inline style is introduced. The manual editor default strength is 0.7, as selected by the owner; UI/runtime acceptance remains separate.


### Generate > Controlnet mode (#503)

Controlnet is a fourth Generate mode in the same joined segmented control. It reuses the Inpaint layout: a Pose source card in the left column where Inpaint has its Source image card, a Pose guidance card at the top of the right column where Inpaint has its Mask card (detector status and download, person list, control, strength, control force, range), and a read-only central canvas with a Source / Pose / Result toggle. The map facts and omitted joints sit under the canvas. The detector licence is shown beside its own Download button. Images and the shared Queue arrow are available for pose-guided Repeat and Pair/Cross queues. A queued pose keeps one staged map and control while image seeds/ordinary parameters vary; plans cannot vary size. Save configuration and Save preset stay disabled because the ordinary configuration format has no pose map. Only the owner-observed DT+ run (flux_1_dev_q5p, Union Pro 1.0 q5p at 0.7) establishes behaviour; the rest awaits owner smoke. Preview category `controlnet` (17 states).

### Generate > Scripts: mockup-led Qt views (draft PR #201)

The owner-supplied `DTFlow Design System (8).zip`, particularly `ui_kits/scripts/scripts-design.md` and its React/HTML screens, is the interaction and visual authority for Scripts. Issue #118 is historical context where it differs. Scripts is the third Generate sub-mode, not a top-level tab. Use the shared three-region shell, a script library on the left, Inspect / Configure / Code / Run / Results in the flexible work region, and compatibility/settings/source/history on the right. Narrow inspector behavior follows the mockup drawer design. Starting, queuing, stopping and unknown attempts must reuse the shared activity overlay and durable service contracts when wired.

The native Scripts UI now mounts the Designer-owned rails from `src/dtflow/ui/forms/scripts_workspace.ui` into the existing `MainWindow` shell. `src/dtflow/ui/scripts_page.py`, `scripts_form.py`, and `scripts_editor.py` implement the presentation, form controls and draft editor. The `tools/ui/preview_states.py --category scripts` fixtures use the actual Qt widgets and shared activity strip. The UI remains non-generative: production script dispatch, immutable version persistence and parser-derived forms are not wired, and no visual acceptance is established without native captures compared against all 24 owner screenshots. The editor-only VS2019 Dark palette uses `color.scriptEditor*` tokens in `tokens.json`, with QSS scoped to `scriptsCodeEditor`: background #1e1e1e, text #e1e1e1, keyword #e67ddc, type/API #65c9fe, string #f09b87, number #32eb87, comment #57a64a, operator #c0c0c0 and selection #264f78. The native editor currently provides a token-backed syntax highlighter, 40 px line-number gutter, find, undo/redo and font-size controls; parse-only Check syntax, immutable Save as new version and live execution remain disconnected. Both Scripts sub-mode and Inspect / Configure / Code use the shared `role=segmented` frame with zero gaps, joined 6 px outside corners and the existing selected-blue token rules. The editor toolbar uses compact 32 px undo/redo and font-size controls, with the usual neutral, disabled and focus states. The Scripts center layout has its own 8 px content inset; this does **not** change the shell's existing 8 px splitter handles. The Scripts right inspector must remain within the shared scroll viewport even when hook names or history rows are long: the right-rail page ignores oversized horizontal size hints, rows wrap or elide, and section cards have an 8 px internal right-edge inset. The rail itself does not add a second outer card border. Short or empty section cards take their content height with top-aligned titles, rather than vertically centering headings in stretched cards. A new unsaved draft must not retain a saved script's compatibility, Source or History evidence. All other buttons, inputs, status and overlays use the shared DTFlow QSS/token language. The fixed-width shared left rail and its 8 px splitter gutter must not be widened or padded to hide clipping. While Scripts is active, the left `QStackedWidget` and its Scripts page accept the existing `leftScroll` viewport width (`QSizePolicy.Ignored` horizontally), restoring the original stack policy on other workspaces; the horizontal scroll remains disabled. Empty inspector card titles use fixed-height top-aligned headings rather than vertically centered empty panels. These adjustments require native Windows geometry and visual verification. Mockup generation cap 8 is a UI default proposal, separate from the existing headless CLI default 4 until explicitly reconciled.

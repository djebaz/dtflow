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
  accentHover: "#0078d4"
  accentPressed: "#005a9e"
  focus: "#60b6f6"
  selection: "#173852"
  selectionText: "#d5ecff"
  success: "#78c97c"
  connectionAccent: "#00d8b0"
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
  panel-padding: "12px"
  canvas-overlay-inset: "10px"
---

# DTFlow design system

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
| Offline state capture | [`tools/ui/render_states.py`](tools/ui/render_states.py) |
| Web design-system reference (prototype aid, not runtime) | [`devdocs/design-system/`](devdocs/design-system/README.md) |

The existing [Qt token and interaction contract](devdocs/architecture/qt-design-tokens.md) records the renderer command and fixture boundaries. The [Generate refinement plan](devdocs/plans/2026-10-05-preview-workspace.md) records the earlier workspace work; the [Images preview plan](devdocs/plans/2026-10-05-phase-8a-ui-preview.md) records PR #33. Earlier plans are historical decisions; the current implementation and these sources govern new work.

## Colors

Blue marks primary action, focus and active selection. The four charcoal depths separate the application, panels, editable controls and image canvas. Text and status labels say what happened; color is supporting evidence, never the only signal. Use the frontmatter roles through `${color.name}` in QSS or `load_tokens()` in custom painting. Do not copy literal hex values into new Python widgets.

- **Accent:** primary actions at rest, brighter on hover and deeper while pressed. Focus is a separate lighter blue and must remain visible on both neutral and selected controls.
- **Selection:** muted navy with light text, shared by selected navigation, lists and tables. It is not the primary-action fill.
- **Status:** success, warning and error have distinct semantic colors. Unknown uses neutral secondary text and an explicit label; it is not treated as failure or success.
- **Disabled:** muted text and surfaces without a blue or destructive accent. An unavailable file action must look unavailable over an image as well as over an empty canvas.

Existing body, secondary and placeholder color pairs were checked for at least 4.5:1 contrast against the four primary surfaces. That check does not establish the contrast of every new overlay or future custom color; check those when added.

## Typography

The shared QSS uses Segoe UI with a sans-serif fallback. Body controls are 10pt, small evidence and preview designations 9pt, section headings 11pt, and shell titles 17pt. These shared sizes use points so Qt can follow DPI scaling. Scoped gallery rules retain logical-pixel sizes (including the 14px prompt and 12px disclosure buttons); these are component exceptions, not an alternative global scale. Some existing one-off roles, such as 8pt eyebrow labels and the 15pt product mark fallback, live in QSS; they are not a general page-heading scale.

Use short labels and specific status text. Keep long paths, model identifiers, timestamps and request evidence in scrolling or expandable regions rather than truncating the identity needed for diagnosis. Placeholders describe the expected input and never stand in for a persistent field label when the field's meaning would disappear on entry.

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

All tabs share one persistent Designer shell (`src/dtflow/ui/forms/workspace_shell.ui`) with three coordinated content stacks. The splitter, rails and page content remain mounted across tab switches. Connection, Generate and Queues change context, primary workspace and evidence stacks. Images reparents the same branding/navigation widgets into its two header slots and hides both content rails; leaving Images restores their parents and splitter geometry. The shell owns the global Inspector toggle and responsive state; page controllers retain their content references and service wiring.

Generate has three regions: a left parameter rail, a flexible center containing prompt, optional negative prompt and the large preview, and a collapsible right inspector for LoRAs, assets and evidence. Branding lives at the top of the shared left rail; navigation stays below the brand in the shared left rail at every width for Connection, Generate and Queues. Images uses the aligned header slots described below. A compact panel-outline toggle beside the brand shows or hides right-side details; its tooltip and accessible name explain the action. Connection uses backend/credential context, readiness in the center and safety evidence on the right. Queues uses durable queue selection/policy, items and dispatch in the center and selected-attempt evidence on the right. There is no full-width application title band or slogan on any tab. The center uses the space that band once consumed.

The shared splitter uses the `size.workspaceGutter` token for an 8 px neutral drag gutter between panels, with a subtle border-colored hover affordance. Internal panel padding is unchanged.

The shared left and right rails have 248–300 logical-pixel constraints in the Designer form. Their contents scroll independently. For Connection, Generate and Queues, at a window width below 1100 the inspector initially collapses and navigation remains in the left rail. The panel icon can reopen it. If the inspector is hidden at *any* width, Connection, Generate and Queues remain reachable. The main splitter gives the center its flexible stretch; do not let long labels force a wider minimum.

The prompt starts at 76 px and expands to 180 px on request. The negative prompt stays compact. The canvas takes the remaining center height and fits the whole image, whether portrait 1:2 or landscape 16:9, without crop or distortion. Its 12 px fit inset and 10 px floating-control inset are implemented in the preview widget. At 100% or manual zoom, overflow gets scrollbars; drag pans and Control-wheel zoom anchors at the pointer. Expand temporarily gives the preview its own window and restores its original parent on Escape or close.

For a new tab, establish its primary work object first, then give its details scrolling rails or collapsible sections. Reuse shell navigation and the shared QSS; do not copy Generate's image canvas into a page where the primary object is a queue table or connection state. Check an 800×680 narrow window and a normal desktop window with representative long data before fixing widths.

## Elevation & Depth

Depth comes from adjacent charcoal tones, thin borders and small radii. The image canvas is the deepest surface, so the artifact reads as the subject. Qt floating toolbars sit inside the canvas on opaque dark surfaces and may overlap a full-bleed image. They are presentation only and never enter saved pixels. There is no shared shadow or animation token. Avoid adding decorative elevation to a new tab without a functional separation it explains.

## Shapes

Inputs use gently rounded 5 px corners, buttons 6 px, and panels 8 px. The reusable QSS minimum is 22 px of control content height **before** padding and border; it is not a 22 px final widget height. Designer layouts own the actual space and stretch. Keep the 14 px checkbox indicators and 12 px scrollbar track consistent with the current stylesheet. Use the same focus border language on keyboard-operable controls and retain native focus behavior.

## Components

### Buttons and navigation

Neutral buttons have distinct rest, hover, pressed, focus and disabled states. Primary actions use the accent family and lose that accent when disabled. Destructive actions use warning red only while actionable; disabled destructive controls return to the neutral disabled treatment. Checkable navigation shares the navy selection language with item views. Maintain stable `role` properties and object names instead of adding inline widget styles.

Connection navigation is a compact 58×34 px power-icon button before Generate, with a token-colored last-check dot and textual tooltip/accessibility status. Unknown is neutral, checking/inconclusive is warning, connected is success and failed is error. Border/dot status is independent of the checked tab; focus remains visibly distinct. It does not poll or imply continuous reachability. Images is placed after Queues and opens the saved-output Grid/List/Detail viewer. Its inline metadata replaces the right Inspector while Images is active; other workspaces retain their Inspector preference. The standalone fixture launcher remains explicitly offline and illustrative. Compact labels preserve the shared navigation width, including when mounted in Images.

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

## Iconography

One icon per meaning. The glyphs are monochrome SVGs in `src/dtflow/resources/images/gallery/`, drawn into section, metadata and row icon labels through the `iconKey` property and a `QLabel[iconKey="…"]` QSS rule; set the key in the Designer `.ui` file, never a per-widget stylesheet. Section headers lead with a 16 px icon, additional-details rows use 14 px and metadata-band labels and prompt disclosure buttons 12 px (sizes are Designer dimensions). Strokes are light `#f2f6f8` with no fill.

- **Repository icons (use first):** `grid`, `list`, `detail` (sliders: Parameters), `refresh`, `document`, `json`, `calendar`, `fullscreen` and `chevron`: 24 px viewBox, 1.6 stroke, round caps and joins. The app mark is `resources/images/dtflow-app.png` with `dtflow.ico`; the panel and connection icons are code-drawn in `ui/brand.py`.
- **Lucide icons (where no repository icon fits):** `server`, `key-round`, `shield-check`, `flask-conical`, `settings`, `cloud`, `send`, `layers`, `list-checks`, `triangle-alert`, `image`, `box`, `repeat`, `compass`, `waypoints`, `ban`, `dices`, `file-image`, `hard-drive`, `weight` and `timer`, copied unmodified from `lucide-static` 1.52.0 (ISC, notice in `images/gallery/LICENSE-lucide.txt`) except that the stroke is `#f2f6f8` instead of `currentColor`. They keep Lucide's 2 px stroke, slightly heavier than the repository set; replace one with a repository SVG by changing its file and keeping the key.

| Area | Icons |
| --- | --- |
| Connection sections | Backend `server`, DT+ credentials `key-round`, Connection check `shield-check` |
| Generate sections | Backend `server`, Parameters `detail`, Simulation `flask-conical`, Configuration `settings`, Cloud assets `cloud` |
| Queues sections | Dispatch policy `send`, Plan batch `layers`, Selected item `list-checks`, Uncertainty `triangle-alert` |
| Gallery header | Grid `grid`, List `list`, Detail `image`, Refresh `refresh`, filter combo arrow `chevron`, fullscreen `fullscreen` |
| Metadata band | Model `box`, LoRAs `layers`, Steps `repeat`, CFG `compass`, Sampler `waypoints`, Date `calendar` |
| Prompt disclosure | Additional `document`, Raw `json` |
| Additional-details rows | Negative prompt `ban`, Resolved seed `dices`, Filename `file-image`, Source `hard-drive`, File size `weight`, Duration `timer`; Path keeps `document` |

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
and the global Inspector toggle are hidden. Leaving Images restores the rails,
chrome and splitter widths; the Inspector preference remains independent of this
page.

Images uses the same charcoal surfaces, blue accent, muted navy selection and
control states as the other tabs through shared `color.*` tokens and QSS. Grid,
List and Detail share one compact header above `galleryContentStack`. With no
valid saved viewer preference, Images opens in Grid. A saved view preference
restores Grid, List or Detail on later launches.

The shared thumbnail control is user-facing percentage, not pixels: 100% is the
standard 160 logical-pixel Grid/filmstrip baseline, with a 25–250% range. List
row thumbnails follow the same percentage through the full range. The internal
pixel conversion is a presentation detail and must not leak into the control
label. Grid keeps uniform aspect-fit tiles; List has metadata columns and
row-wide hover. Neither browsing view exposes bulk selection or destructive
actions.

Detail defaults to Fit so the complete saved image is visible on entry. Cover
remains available for a filled canvas; 100%, zoom, pan, previous/next,
fullscreen/Escape and the right-hand filmstrip remain available. The filmstrip
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

The standalone `DetailFixture` and preview tools continue to use bundled
illustrative artwork. They are development presentation fixtures only and do not
replace or establish saved-output evidence.

Images header rows have no additional outer inset. The shared brand/navigation
slots sit outside the scrolling controls and retain the shell's chrome width and
height, with the same 8 px row gap. Hidden Inspector controls reserve their space
to keep the logo/title geometry stable. Gallery panel gaps are 8 px; the canvas
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

Checkable section cards require an operable header control. Queues uses
`planBoxToggle` (a keyboard-accessible checkbox) synchronized with the existing
checkable `planBox`; the controller re-enables the custom header when unchecked
and hides the entire contents wrapper. A passive label cannot replace a native
group-box toggle. Parent activity disabling still applies.

Queues keeps toolbar, action hint and collapsed Plan card at their natural
heights. The items table or empty-state region receives the flexible workspace
height; empty-state title and guidance stay together in its center. Hide queue
progress when no queue is selected rather than presenting “0 of 0” as work.
The Plan header has a fixed vertical size policy, while expanded contents retain
their natural layout. Avoid distributing spare height among action rows.

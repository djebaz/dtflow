# Qt workspace design tokens

For the design rules and a page-building checklist, start with the root [`DESIGN.md`](../../DESIGN.md). This document records the implementation-specific interaction and offline fixture contract. The tracked `tokens.json` remains the runtime source for shared values; the `DESIGN.md` frontmatter is kept in sync as an agent-readable snapshot. A Claude Design web reference of the tokens, components and screens lives in [`devdocs/design-system/`](../design-system/README.md); it is a prototype aid, not runtime code.

The native Windows-oriented charcoal/blue identity is preserved. `tokens.json` beside `dtflow_dark.qss` owns the shared palette and scale. `load_theme()` resolves `${token.name}` references once at startup; the custom canvas reads that same palette. Designer `.ui` files remain canonical for geometry and layout. `workspace_shell.ui` owns the single persistent splitter, rails, scroll containers, branding and navigation. Connection, Generate and Queues page forms supply context, work and evidence content through three coordinated stacks. Images is the explicit composition exception: the same brand/navigation widgets move into gallery header slots, both rails hide, and leaving Images restores parents, chrome dimensions and splitter sizes. QSS is not generated Python and there is no separate web implementation.

## Palette and geometry

| Token or pattern | Value | Role |
| --- | --- | --- |
| Background / surface / input / canvas | `#1f1f1f` / `#252525` / `#1e1e1e` / `#151515` | Work area depth |
| Primary / hover / pressed | `#0068b8` / `#0078d4` / `#005a9e` | Main action |
| Focus | `#60b6f6` | Keyboard focus, independent of selection |
| Body / title / small | 10pt / 17pt / 9pt | Native Segoe UI scale; point sizes respect DPI |
| Control content minimum | 22px | Plus QSS padding and border; not the total widget height |
| Input / button radius | 5px / 6px | Consistent component families |
| Standard outer / rail margins | 8px / 12px | Designer layout spacing; Images outer/header inset is 0px |
| Rails | 248–300px | Independently scrolling settings and inspector; each rail is as tall as its shown page only (`CurrentPageStack`), so it scrolls only on real overflow |
| Prompt | 76px / 180px | Compact or explicitly expanded editor |
| Canvas fit inset | 12px | Entire image remains visible, no crop/stretch |
| Overlay inset | 10px | Controls stay inside canvas at any ratio |

Primary, secondary and placeholder text contrast is checked against all four surfaces (at least 4.5:1). Disabled controls remain deliberately muted and are not interpreted as enabled affordances. Native high-DPI/Windows rendering still needs target-machine verification; Linux offscreen pixels do not prove it.

## Interaction contract

- Buttons have hover, pressed, selected, disabled and keyboard-focus rules.
- Inputs expose placeholders, focus, hover, disabled and invalid states.
- Lists expose empty hints, hover and selected rows. Scrollbars and sliders have idle/hover/pressed/disabled states. Status colors preserve warning, unknown, error and success distinctions.
- At widths below 1100px the inspector initially collapses and navigation stays in the left rail for Connection, Generate and Queues; Images keeps the same widgets in its header. It can be reopened explicitly. Hiding the right details panel at any width never hides Connection/Generate/Queues.
- Fit shows complete portrait/landscape output; mouse-wheel zoom (Ctrl optional, as in Images → Detail) is cursor anchored and never scrolls the parent panel, drag pans, overflow adds scrollbars. `F`, `+`, `-` and Escape support keyboard inspection. Idle toolbars hide; focusing the canvas restores them.
- Expand restores on Escape, close, or owner-window close. Saved-output actions are disabled for empty/interim previews; no watermark is written to artifacts.
- The empty canvas offers an illustrative portrait sample. Its badge explicitly says it is a sample with no inference; file and metadata actions remain disabled because there is no saved output. The sample button must be enabled when the Generate page leaves the shell's preview-only control state.

## Offline visual checks

When explicitly authorized, `uv run python tools/ui/preview_states.py --out tmp/ui-previews` captures the actual Qt forms and shared QSS, with the existing network guard enabled. It exercises empty, portrait 1:2, landscape 16:9, zoom, hover, pressed, selected, narrow-window, invalid/focus, incompatible/unknown LoRAs, busy/disabled, unknown outcome and confirmed/unknown cloud receipts. Receipt actions use the asset tests' in-memory API and transport; they do not contact DT+ or upload real model files. See the [UI previews guide](../guides/ui-previews.md) for coverage and readiness limitations.

The regular fake backend now lists `fake-fixture`, `fake-grid`, `fake-soft`, `fake-mismatch` and `fake-unknown`. These are explicit fixture identifiers, not real models or weights. Architecture diagnostics test the common preflight path; even a match stays application-unconfirmed. Fake cloud receipts belong only to the development renderer/tests, not the production fake backend.



## Images-only full-width composition

Images hides both content rails and retains the logo in the view-controls row and
navigation in the filters row of the canonical gallery form; leaving Images restores the existing rail chrome and
splitter widths. The existing stylesheet resolves gallery SVG URLs relative to
its own tracked resource path, supporting source and packaged execution.
Gallery QSS and custom painting consume the shared `color.*` tokens, matching
the other tabs' charcoal surfaces, muted navy selection and blue accent. Sample presentation is shared by the shell and
standalone launcher, with no provider or persistence ownership.


Images header rows have no additional outer inset. The shared brand/navigation
slots sit outside the scrolling controls and retain the shell's chrome width and
height, with the same 8 px row gap. Hidden Inspector controls reserve their space
to keep the logo/title geometry stable. Gallery panel gaps are 8 px; the canvas
has a compact 4 px inset and metadata avoids nested padding.

Detail's filmstrip spans the entire content height. Metadata and prompt panels
stay beneath the main image to its left. Overlay controls use 10 px interior
spacers, separate from the compact canvas frame.

Neutral button rest/hover/pressed fills and borders, disabled borders, input
hover borders and selection hover/pressed/border colors are named shared tokens.
Gallery-specific dimensions remain scoped to their presentation selectors.


Shared typography uses token-defined families and weights (body 400, label 500,
emphasis 600, title 650, strong 700) and an 11pt section size. Designer `textRole`
is independent of semantic `role` and runtime `state`. Connection, Generate,
Queues and Images assign caption/value/section/identifier/code roles to fields,
evidence and metadata. Secondary text uses the small regular style; raw data
uses the shared monospace family. Gallery model name/identifier are separate
labels, and custom-painted thumbnail badges use shared typography tokens.


Section cards retain QGroupBox identities but replace native floating titles
with Designer header/content widgets. Shared header/border/item-surface tokens,
resource-resolved SVG glyphs, information-label glyph rows and list/table states
provide the same composition language in Connection, Generate, Queues and
Images. Forms retain business-control names and the controller boundary.

`color.unknown` now shares the neutral secondary gray. Unknown/availability
text uses small regular typography and explicit wording rather than violet
emphasis; compatibility remains Unknown until evidenced.

## Metadata and typography exceptions

`panelHeading` is small semibold text, `reading` gives the gallery prompt medium
weight, and `loraName` uses small medium text. Additional-detail `value` labels
use regular weight and subtle separators. Shared sizes are point-based; scoped
gallery prompt/disclosure and overlay dimensions remain logical-pixel rules.

The model identity uses a 28px accent glyph tile beside its name and secondary
identifier. Two neutral LoRA rows separate name, numeric weight and explicit
Unknown badge; the status does not tint the whole name. The metadata band is
108px high with horizontal overflow. The prompt has no redundant visible caption
but retains its accessible name. Expanded Additional/Raw controls use shared
accent rest/hover/pressed colors and remain mutually exclusive.

`color.unknown` and `color.secondary` intentionally have the same value: their
semantic names preserve independent future state styling. All runtime token
values are documented in the root DESIGN snapshot; component-only dimensions
stay in Designer/QSS rather than becoming duplicate tokens.

Checkable section cards require an operable header control. Queues uses
`planBoxToggle` (a keyboard-accessible checkbox) synchronized with the existing
checkable `planBox`; the controller re-enables the custom header when unchecked
and hides the entire contents wrapper. The custom header covers the native indicator, so a scoped zero-size indicator
rule suppresses it. Keep one visible toggle; retain the group's checked state. Parent activity disabling still applies.


## Web design-system mapping

[`devdocs/design-system/`](../design-system/README.md) is the Claude Design web reference. It is a prototype aid: `tokens.json` and `dtflow_dark.qss` stay the runtime authority, and a value change lands in them first, then in `design-system/tokens/*.css`, the root `DESIGN.md` snapshot and this document in the same PR.

**Naming.** Every `color.*` token maps to `--color-*` in kebab-case (`color.secondary` → `--color-secondary`, `color.disabledBorder` → `--color-disabled-border`, `color.connectionAccent` → `--color-connection-accent`). Type and size tokens map as `type.body` → `--font-size-body`, `type.weightLabel` → `--font-weight-label`, `type.family` → `--font-family-ui`, `type.monoFamily` → `--font-family-mono`, `size.controlContent` → `--size-control-content`, `size.scrollbar` → `--size-scrollbar`, `size.inputRadius`/`size.radius` → `--radius-input`/`--radius-button`. Every `color.*`, `type.*` and radius token in `tokens.json` now matches the web tokens exactly. The web reference substitutes Noto Sans / Noto Sans Mono for Segoe UI / Consolas and uses browser-native `SpinBox` arrows and `ComboBox` popup; Qt draws its own. `QComboBox` and the web select/`ComboBox` share one affordance: a `color.input` segment (field hover `color.surface`, segment hover `color.button`) separated by a `color.border` divider holding a chevron, brighter on hover/open and dimmed when disabled. Qt uses a single `resources/images/controls/chevron-down.svg`; state-specific `::down-arrow` rules made Qt draw a second, centered chevron, so state feedback lives on the segment only. No new tokens were added.

**Component-local tones.** These colors were repeated QSS literals and are now `color.*` tokens in `tokens.json`, referenced as `${color.name}` in `dtflow_dark.qss`; the web reference uses the same values as `--color-*`. The resolved stylesheet is byte-identical to the previous literal version.

| Token | Value | Role |
| --- | --- | --- |
| `color.panelEdge` | `#333333` | Header/panel edge |
| `color.host` | `#1c1c1c` | Card/preview host surface |
| `color.toolbar` | `#242424` | Floating toolbar, alternate row |
| `color.barTrack` | `#2a2a2a` | Progress track |
| `color.title` | `#f2f2f2` | Title and tooltip text |
| `color.buttonText` / `color.inputText` / `color.menuText` | `#eeeeee` / `#eaeaea` / `#dddddd` | Control text |
| `color.onAccent` | `#ffffff` | Text on accent and danger-hover fills |
| `color.eyebrow` / `color.badgeText` | `#8fbde3` / `#b9dcf7` | Eyebrow and badge text |
| `color.accentEdge` | `#1085dd` | Accent border |
| `color.dangerText` / `dangerSurface` / `dangerBorder` | `#ff9d97` / `#432323` / `#c62828` | Danger button |
| `color.statusOkBorder` / `statusWarningBorder` / `statusErrorBorder` | `#2e7d32` / `#9a6700` / `#a33a35` | Status card borders |
| `color.glyphNeutral` | `#8a8a8a` | Neutral status glyph |
| `color.groove` / `handle` / `handleBorder` / `handleDisabled` | `#505050` / `#e6e6e6` / `#909090` / `#555555` | Slider |
| `color.checkBorder` / `checkDisabledBorder` | `#777777` / `#414141` | Checkbox indicator |
| `color.disabledEdge` | `#2f2f2f` | Disabled control edge |
| `color.scrollHover` / `scrollPressed` | `#707070` / `#909090` | Scrollbar thumb |
| `color.tabBorder` / `tabText` | `#363636` / `#a8a8a8` | Tabs |

Single-use QSS literals not named by the design system remain literal: `#232323` and `#373737` (header/group surface and border), `#d7d7d7`, `#e0e0e0`, `#d8d8d8` (label/progress/menu text), `#d9b36a` (elevated spin box), `#6a6a6a` (group indicator), plus `#2b2b2b`, `#343434` in the tooltip, header and menu rules that coincide with the section tokens. `--color-statusbar-edge` (`#000000`) has no literal match in the QSS and is not a token.

**Radii.** `size.inputRadius` (5 px), `size.radius` (6 px) and the new `size.panelRadius` (8 px), `size.radius2xs` (2), `radiusXs` (3), `radiusSm` (4), `radiusThumb` (9), `radiusChip` (16), `radiusPill` (17) and `radiusRound` (21 px) replace the matching `border-radius` literals. The web reference names the same values `--radius-*`. Radii of 7 and 10 px, and the `border-top-*-radius` rules, stay literal.

**Spacing.** The web `--space-*` scale (2, 4, 6, 8, 10, 12, 14, 18 px) names gaps that the Designer forms actually use. Counting `spacing` and margin properties in `src/dtflow/ui/forms/*.ui` gives 0 (most), 1, 2, 4, 5, 6, 7, 8, 10, 12, 14, 16 and 18 px; 8, 10, 12 and 6 px dominate, and 14 and 18 px occur a handful of times. The scale omits 1, 5, 7 and 16 px, and Python layouts also use 5 px and 8 px. Like the component-only radii, these are Designer/QSS layout values rather than `tokens.json` tokens, and only the 8 px workspace gutter, 12 px panel padding and 10 px overlay inset are shared tokens. The scale is therefore a documentation aid for web mockups of the same layouts, not a separate web invention.

## Web reference captures

The [headless design-system capture tools](../guides/design-captures.md) render the exported HTML kits and compiled components without changing Qt or the reference export. Their manifests record computed styles, bounds and unsupported states. Matching token values or successful capture is not a pixel-parity claim; fixture, state and layout alignment remain explicit review steps.

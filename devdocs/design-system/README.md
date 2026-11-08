# DTFlow Design System

DTFlow is a compact, image-first native desktop workspace for AI image generation and queue workflows. Built as a Qt Widgets application, it prioritizes efficient layouts, clear interaction states, and a restrained visual language where the generated image dominates the interface.

For the planned [compact Logs workspace UX](logs-workspace.md), see the [standalone interaction study](../mockups/dtflow_logs_workspace.html), [app-shell study](../mockups/dtflow_app_shell_with_logs.html), [Logs color card](cards/colors-logs.html) and [all-components catalog](components/dtflow_all_components.html). These are PR #105 synthetic HTML design references, not runtime Qt screenshots or replacements for the canonical Designer/render-state workflow. The bundled studies embed their own asset/style snapshots and do not automatically track edits to source CSS.

## Overview

**Source materials:**
- `tokens.json` — authoritative runtime palette and typography token definitions
- `dtflow_dark.qss` — Qt stylesheet with widget appearance, states, and interaction language
- `DESIGN.md` — comprehensive design guidelines, layout rules, color usage, and component specifications

## Content fundamentals

DTFlow uses **short, specific labels** and **concrete status text**. Copy describes outcomes, never infers state; visual color supports text, never replaces it. Tone is technical but accessible—diagnostics are precise (model IDs, request evidence), placeholders describe expected input, and long paths/metadata stay in scrolling regions rather than truncating meaning away.

The interface employs:
- **Title case** for section headings and persistent labels
- **Sentence case** for button labels and actions
- **Monospace** (Consolas) for identifiers, effective requests, JSON and code
- **No emoji** — visual status uses semantic colors and icons only
- **No decoration** — every element earns its space; empty states are informative, not unexplained blanks

## Visual foundations

### Colors

A **charcoal palette** separates depth: background (darkest) → surface → input → canvas (lightest). **Blue** marks primary action, focus, and active selection across all contexts. Four semantic status colors (success green, warning amber, error red, unknown neutral gray) are never the only signal—always paired with text.

| Role | Hex | Usage |
| --- | --- | --- |
| Background | `#1f1f1f` | Lowest layer, window chrome |
| Surface | `#252525` | Panels, cards, rails |
| Input | `#1e1e1e` | Form fields, editable regions |
| Canvas | `#151515` | Image preview, deepest surface |
| Text | `#e6e6e6` | Primary body text |
| Secondary | `#b2b2b2` | Metadata, captions, hints |
| Placeholder | `#a8a8a8` | Field placeholders |
| Disabled | `#777777` | Unavailable controls |
| Accent | `#0068b8` | Primary action |
| Accent Hover | `#0078d4` | Hover state |
| Accent Pressed | `#005a9e` | Pressed state |
| Focus | `#60b6f6` | Keyboard focus indicator |
| Success | `#78c97c` | Success status |
| Warning | `#ffbf59` | Warning/caution status |
| Error | `#ff8179` | Error status |
| Unknown | `#b2b2b2` | Unknown status; neutral secondary gray with explicit label, never tinted purple |
| Section header | `#2b2b2b` | Section-card header row |
| Section border | `#343434` | Section-card separators |
| Item surface | `#222222` | Compact rounded list items |

### Typography

**Segoe UI** is the primary typeface; **Consolas** for code and identifiers. All sizes are expressed in points so Qt respects DPI scaling.

| Role | Family | Size | Weight | Use |
| --- | --- | --- | --- | --- |
| Body | Segoe UI | 10pt | 400 | Default form content |
| Small | Segoe UI | 9pt | 400 | Captions, metadata, support text |
| Section | Segoe UI | 11pt | 600 | Section headings, empty states |
| Title | Segoe UI | 17pt | 650 | Window/workspace titles |
| Caption | Segoe UI | 9pt | 500 | Field labels, badges |
| Emphasis | Segoe UI | — | 600 | Active tabs, primary buttons, status values |
| Strong | Segoe UI | — | 700 | Product mark, strong emphasis |
| Code | Consolas | 9pt | 400 | Model IDs, JSON, events |

### Spacing

- **Workspace gutter** (panel drag zone): 8px
- **Panel padding**: 12px
- **Canvas overlay inset**: 10px (preview toolbar float)
- **Control content height**: 22px minimum (before padding/border)

### Shapes

- **Inputs**: 5px corners
- **Buttons**: 6px corners
- **Panels**: 8px corners
- **Scrollbar**: 12px width

### Interaction states

All interactive elements follow a consistent language:

- **Rest**: neutral colors, no border emphasis
- **Hover**: lighter surfaces, stronger borders
- **Pressed**: darker surfaces, no color inversion
- **Focus**: distinct blue focus border, always visible on both neutral and selected controls
- **Disabled**: muted text, neutral surfaces, no blue

Primary actions use accent blue; navigation uses muted navy selection. Destructive actions use warning red only when actionable; disabled destructives revert to neutral.

### Layout & depth

Three charcoal depths create visual separation. **The image canvas is the deepest surface**, so the artifact reads as the primary subject. Floating toolbars sit inside the canvas on opaque dark overlays—presentation only, never saved.

No shared shadow system; elevation comes from color gradation and thin borders. Avoid decorative depth unless it explains functional separation.

## Components

DTFlow's components are Qt Widgets styled through:
- **QSS selectors** based on semantic object names (`role`, `state` dynamic properties)
- **Token expansion** via `${color.name}` and `${size.name}` in QSS
- **Native focus behavior** preserved across all controls

### Buttons

- **Neutral**: rest/hover/pressed/focus/disabled states with distinct borders
- **Primary**: accent blue at rest, brighter on hover, deeper when pressed
- **Navigation**: transparent at rest, selection navy when checked
- **Danger**: warning red only when actionable; disabled reverts to neutral

### Fields

- **Text inputs**: input surface, hover-lighter borders, visible focus, readable placeholders, disabled treatment, `invalid` state support
- **Lists/tables**: selection navy, explicit empty states, scrollable for long content
- **Side panels (rails)**: a rail scrolls vertically only when its own shown page overflows; the height of another tab's panel never adds a scrollbar. In Qt this is `CurrentPageStack` (`dtflow.ui.current_page_stack`) for the shell's left and right stacks; a mockup should size each rail from its own content.

### Image preview

The `ImagePreview` widget preserves original pixels with Fit/100%/zoom/pan support. Floating controls (zoom, expand, file actions) live in opaque dark overlays atop the canvas. Designation badges identify saved, preview, fixture, or sample images—never presented ambiguously.

### Status & progress

Always pair color with text. Progress bars show only known progress. Unknown outcomes, interruptions, and failures remain distinct. Effective requests and attempt details remain accessible even when collapsed. The shared 30×30 segmented Run control is the persistent compact execution summary: queue-ready Play starts through the existing queue path; owned queue work can pause/resume; external queue work and single generation are status-only unless the underlying service truthfully supports an action. A single active queue item may show its queue position in the center, while backend step detail stays in the tooltip.

### Section cards

`SectionCard` (`components/layout/`) is the web counterpart of Qt `QGroupBox[role="sectionCard"]` with a `sectionHeader` row and `sectionContents` region. Surface fill, 1px `--color-section-border`, 8px radius; header on `--color-section-header` with a bottom separator, a 16px leading glyph and a 9pt semibold `panelHeading`. Native floating group titles are empty. Checkable cards (Queues Plan) put an operable checkbox in the header and hide contents when unchecked; a passive label cannot replace it. Disabled cards use `--color-disabled-border` and muted heading. Lists use compact rounded `--color-item-surface` rows (4px radius); tables use fine `--color-section-border` row separators and a `--color-button-hover` hover fill. Depth comes from headers and surfaces, not extra outer padding.

### Unknown and informational notices

Unknown uses neutral secondary gray (`#b2b2b2`), small body weight and explicit wording. Never tint LoRA names or availability hints purple; success/warning/error stay reserved for real outcomes.

### Gallery metadata

The metadata band is 108 logical px high (a scoped Designer dimension, not a token) with horizontal scroll for long identifiers. Model identity: 28px accent mark, semibold name, secondary identifier. LoRA rows: neutral `loraChip` (input surface, section border, 5px radius), name (9pt medium), numeric weight and an explicit *Unknown* badge; status never tints the whole identifier. The positive prompt has no visible caption (accessible name stays), uses medium reading weight; disclosure buttons are semibold and accent-filled when expanded. Additional-detail values are regular weight with subtle bottom separators.

### Images layout

Images is a full-width gallery. The shared brand and navigation widgets are remounted into two header slots (260px, shell chrome width): the logo aligns with the view-controls row (Grid / List / Detail, one shared size slider, Auto-refresh, Refresh) and the navigation tabs align with the filter row (prompt search, model, LoRA, date, ratio). Both content rails are hidden while the segmented Run control stays in the shared brand row; rows share an 8px gap. Detail: aspect-preserving Cover/Fit canvas with position, dimensions and fixture overlays, round prev/next buttons, a floating pill toolbar (view mode, −, 100%, +, fullscreen), the 108px metadata band, a three-line prompt with Additional/Raw disclosure (mutually exclusive) and a full-height right filmstrip. Grid tiles are uniform aspect-fit cards; List has metadata columns and no checkboxes. Filters, auto-refresh and file actions stay disabled until services exist; artwork and metadata are labelled fixtures, LoRA compatibility is Unknown.

### Queues layout

Toolbar, action hint and the collapsed Plan card keep natural height; the items table or empty state takes the flexible height, with title and guidance centred together. Hide progress when no queue is selected rather than showing "0 of 0".

## Brand and iconography

- **Logo:** `assets/dtflow-app.png` (app mark: blue folded \"D\"/\"T\" ribbon on charcoal) and `assets/dtflow.ico`. Used as the 34px brand mark beside the \"DTFlow\" title; the old \"DTF\" text mark is only a fallback.
- **Icons:** the codebase's own monochrome SVGs in `assets/icons/` (grid, list, detail = sliders, refresh, document, json, calendar, fullscreen, chevron; 24px, 1.6 stroke, light). Where none of them fits the meaning, the kits borrow **Lucide** from the CDN (`lucide-static`, 2px stroke — flagged substitution, tinted with `filter: invert(1)`): server (Backend), key-round (credentials), shield-check (safety), flask-conical (simulation), settings, cloud, send (dispatch), layers (plan, LoRAs), list-checks (item), triangle-alert (uncertainty), image (Detail view), box (model), repeat (steps), compass (CFG), waypoints (sampler), ban, dices, file-image, hard-drive, weight, timer (additional details). Send the app's own SVGs for these and I will swap them in.
- No emoji. Unicode is limited to the gallery's \u2039 \u203A prev/next and \u2212 / + zoom glyphs.

## Components

- `components/icons/` — **Icon** (source SVGs, code-drawn connection status, Lucide substitutes; full catalogue on the "Icon Set" card)
- `components/layout/` — **SectionCard**, **Panel**, **StatusBar**
- `components/controls/` — **Button**, **ConnectionButton**, **TextField**, **TextArea**, **ComboBox**, **SpinBox**, **Checkbox**, **Slider**, **Tabs**
- `components/text/` — **Label**
- `components/data/` — **ListBox**, **ListItem**, **DataTable**, **LoraChip**
- `components/feedback/` — **ProgressBar**, **StatusCard**, **Badge**, **EmptyState**, **Tooltip**
- `components/gallery/` — **ToolbarRow**, **FloatingToolbar**, **OverlayChip**, **RoundButton**, **MetadataBlock**, **ImageCard**, **Thumbnail**
- `components/shell/` — **Brand**, **Navigation**, **MenuBar**

### API conventions

- `checked` = on/off state of a toggle (Button, Checkbox, SectionCard, ConnectionButton). `selected` / `selectedIndex` = selection inside a collection (ListItem, Thumbnail, DataTable).
- Native form controls (TextField, TextArea, ComboBox, SpinBox, Checkbox, Slider) pass DOM events to `onChange`. Composite components (Navigation, Tabs, SectionCard) call back with the value: `onChange(tab)`, `onCheckedChange(bool)`.
- Text styling is `<Label textRole="…">` (Qt `textRole`); `variant` picks a Button/Navigation style. Never pass raw hex: use `--color-*` tokens (the QSS one-offs are tokenised in `tokens/colors.css`).
- Token names follow tokens.json (`color.secondary` → `--color-secondary`, `color.disabledBorder` → `--color-disabled-border`).
- Spacing uses the `--space-*` scale (2/4/6/8/10/12/14/18px).

### Known limits

- Segoe UI and Consolas are system fonts and not bundled; **Noto Sans / Noto Sans Mono** (Google Fonts, `tokens/fonts.css`) are the flagged fallback. Send font files to replace them.
- `SpinBox` arrows and the `ComboBox` popup are browser-native; Qt draws its own.
- The legacy group-box title is replaced by `SectionCard` and not built separately.

Component CSS lives in `components/components.css` (imported by `styles.css`) and mirrors `dtflow_dark.qss`.

## Intentional additions

The [Logs token proposal](tokens/logs.css) adds documentation-only surfaces, an INFO stripe, deterministic event/key pill pairs and row-action sizing, shown in the [Logs color card](cards/colors-logs.html). It is not yet part of runtime `tokens.json` or shared QSS. Accepted additions must be promoted through the existing theme loader and documented token mapping in Phase 3; no separate native Logs stylesheet. Section-card icons use supplied `assets/icons/*.svg` via the optional `icon` slot.

## How to use this system

### For designers

Read `DESIGN.md` for the complete layout specification, component checklist, and do's/don'ts. Reference the token values in this readme or the token cards. Verify new work against the responsive breakpoints (800×680 narrow, normal desktop widths) and state coverage (empty, populated, loading, disabled, focused, hovered, pressed, invalid, unknown).

### For developers

1. Load tokens from `tokens.json` in your theme setup
2. Apply `dtflow_dark.qss` to the application stylesheet
3. Use semantic `objectName` and `role`/`state` properties instead of inline styles
4. Test keyboard focus, hover, and press states on all controls
5. Verify Windows font metrics and DPI scaling
6. Capture actual Qt rendering at narrow (800×680) and normal widths

## Files in this system

- `styles.css` — web-based token reference (mirrors `tokens.json` for documentation)
- `tokens/` — individual token CSS files (colors, typography, spacing); [logs.css](tokens/logs.css) is a separately loaded proposal, not imported globally by `styles.css`
- `cards/` — foundation specimen cards showing colors, type, and spacing in context
- `components/layout/` — `SectionCard` component + card
- `ui_kits/connection/`, `generate/`, `queues/`, `images/` — one clickable kit per view, each built only from `components/` and its own `.ui` source (shared layout in `ui_kits/shared.css`, chrome in `ui_kits/Chrome.jsx`)
- `SKILL.md` — agent skill entry
- Source: [djebaz/dtflow](https://github.com/djebaz/dtflow) — explore for fuller context
- `README.md` (this file) — design guide and manifest

---

**Built from:** DTFlow Qt workspace (`djebaz/dtflow` — see original repository for implementation details)

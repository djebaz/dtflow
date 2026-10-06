# Qt workspace design tokens

For the design rules and a page-building checklist, start with the root [`DESIGN.md`](../../DESIGN.md). This document records the implementation-specific interaction and offline fixture contract. The tracked `tokens.json` remains the runtime source for shared values; the `DESIGN.md` frontmatter is kept in sync as an agent-readable snapshot.

The native Windows-oriented charcoal/blue identity is preserved. `tokens.json` beside `dtflow_dark.qss` owns the shared palette and scale. `load_theme()` resolves `${token.name}` references once at startup; the custom canvas reads that same palette. Designer `.ui` files remain canonical for geometry and layout. `workspace_shell.ui` owns the single persistent splitter, rails, scroll containers, branding and navigation. Page forms supply only context, work and evidence content; switching tabs changes three coordinated stacks without moving chrome or resetting splitter sizes. QSS is not generated Python and there is no separate web implementation.

## Palette and geometry

| Token or pattern | Value | Role |
| --- | --- | --- |
| Background / surface / input / canvas | `#1f1f1f` / `#252525` / `#1e1e1e` / `#151515` | Work area depth |
| Primary / hover / pressed | `#0068b8` / `#0078d4` / `#005a9e` | Main action |
| Focus | `#60b6f6` | Keyboard focus, independent of selection |
| Body / title / small | 10pt / 17pt / 9pt | Native Segoe UI scale; point sizes respect DPI |
| Control content minimum | 22px | Plus QSS padding and border; not the total widget height |
| Input / button radius | 5px / 6px | Consistent component families |
| Outer / rail margins | 8px / 12px | Designer layout spacing |
| Rails | 248–300px | Independently scrolling settings and inspector |
| Prompt | 76px / 180px | Compact or explicitly expanded editor |
| Canvas fit inset | 12px | Entire image remains visible, no crop/stretch |
| Overlay inset | 10px | Controls stay inside canvas at any ratio |

Primary, secondary and placeholder text contrast is checked against all four surfaces (at least 4.5:1). Disabled controls remain deliberately muted and are not interpreted as enabled affordances. Native high-DPI/Windows rendering still needs target-machine verification; Linux offscreen pixels do not prove it.

## Interaction contract

- Buttons have hover, pressed, selected, disabled and keyboard-focus rules.
- Inputs expose placeholders, focus, hover, disabled and invalid states.
- Lists expose empty hints, hover and selected rows. Scrollbars and sliders have idle/hover/pressed/disabled states. Status colors preserve warning, unknown, error and success distinctions.
- At widths below 1100px the inspector initially collapses and navigation stays permanently in the left rail. It can be reopened explicitly. Hiding the right details panel at any width never hides Connection/Generate/Queues.
- Fit shows complete portrait/landscape output; Control-wheel zoom is cursor anchored, drag pans, overflow adds scrollbars. `F`, `+`, `-` and Escape support keyboard inspection. Idle toolbars hide; focusing the canvas restores them.
- Expand restores on Escape, close, or owner-window close. Saved-output actions are disabled for empty/interim previews; no watermark is written to artifacts.
- The empty canvas offers an illustrative portrait sample. Its badge explicitly says it is a sample with no inference; file and metadata actions remain disabled because there is no saved output. The sample button must be enabled when the Generate page leaves the shell's preview-only control state.

## Offline visual checks

`uv run python tools/ui/render_states.py --out tmp/ui-renders` renders the actual Qt forms and shared QSS, with the existing network guard enabled. It exercises empty, portrait 1:2, landscape 16:9, zoom, hover, pressed, selected, narrow-window, invalid/focus, incompatible/unknown LoRAs, busy/disabled, unknown outcome and confirmed/unknown cloud receipts. Receipt actions use the asset tests' in-memory API and transport; they do not contact DT+ or upload real model files.

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

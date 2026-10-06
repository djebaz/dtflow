# Qt style and layout centralization

Date: 2026-10-06. Status: proposed implementation plan; no runtime migration is implemented by this document. Baseline: source revision `9f7245f`, PySide6/Qt 6.11.2, six canonical Designer forms. This plan follows the render-comparison investigation and preserves existing application behavior before any intentional redesign.

## Goal

Make shared visual values authoritative in `src/dtflow/resources/styles/tokens.json`, apply supported widget styling and sizing through the shared QSS, and centralize Qt layout properties through one token-driven presentation initializer. Keep `.ui` files canonical for composition, widget identities, layout structure and structural policies, rather than duplicating reusable design constants. Capture tools must consume the actual application implementation, not reconstruct its appearance independently.

## Scope and non-goals

- In scope: audit shared dimensions, min/max constraints, icon sizes, layout margins, layout gaps, splitter handle width, repeated Python geometry constants, QSS specificity/polishing conflicts, canonical load paths and reliable visual verification.
- Preserve: object names, semantic roles, accessibility, signal wiring, form structure, backend boundaries, saved preferences, responsive composition, gallery controls, generation safety and real application behavior.
- Out of scope: rewriting Qt Widgets in QML or HTML, treating the web export as runtime code, redesigning density, changing generation/provider behavior, migrating every number indiscriminately, replacing all layout APIs with QSS adapters, release/version changes and billable validation.
- No blanket ban on `.ui` properties: structural constraints, stretch factors, policies, initial window geometry and input-value ranges can remain where justified. Shared visual dimensions need a documented authoritative owner.

## 1. Repository reference map

| Source | Responsibility / evidence |
|---|---|
| [`AGENTS.md`](../../AGENTS.md) | Canonical Designer forms, shared QSS, uv environment, safety and verification requirements. |
| [`DESIGN.md`](../../DESIGN.md) | Design rules and token snapshot; update alongside changed runtime tokens during implementation. |
| [`tokens.json`](../../src/dtflow/resources/styles/tokens.json) | Existing shared palette, typography and dimensions; includes `size.workspaceGutter = 8px`, `size.controlContent = 22px`, radii and scrollbar width. |
| [`dtflow_dark.qss`](../../src/dtflow/resources/styles/dtflow_dark.qss) | Widget appearance and state selectors; connection rules begin around line 664, broad gallery button rules around line 721, gallery mode rules around line 746, section-header rules around line 1086. Line numbers are baseline navigation aids, not stable identifiers. |
| [`theme.py`](../../src/dtflow/ui/theme.py) | `load_tokens()`, `${token.name}` resolution in `load_theme()`, shared startup stylesheet application and resource URL resolution. |
| [`main_window.py`](../../src/dtflow/ui/main_window.py) | `load_ui()` uses QUiLoader; MainWindow mounts canonical forms and moves the existing brand/navigation into Images. Inspect `_responsive_workspace()` for reparenting, rail visibility, slot dimensions and splitter-size restoration. |
| [`workspace_shell.ui`](../../src/dtflow/ui/forms/workspace_shell.ui) | Connection button at baseline line 175; minimum/maximum 58 by 34 and icon 40 by 24. Shell layout margins/spacing at lines 6–20; splitter `handleWidth` at line 23. |
| [`gallery_detail_page.ui`](../../src/dtflow/ui/forms/gallery_detail_page.ui) | Gallery layout, 106 min/max declarations, scoped header/filter ancestors, button constraints, canvas, filmstrip and detail panels. |
| [`gallery_detail_preview.py`](../../src/dtflow/ui/gallery_detail_preview.py) | Shared `apply_gallery_button_icons()` and presentation helpers. Application and icon-state captures now reuse the same icon initializer. |
| [`qt-design-tokens.md`](../architecture/qt-design-tokens.md) | Current architecture and interaction contract; amend its statement that `.ui` owns geometry/layout to distinguish structural composition from centralized shared visual values. |
| [`design-captures.md`](../guides/design-captures.md) | Capture provenance, known limitations and verified icon/image-load corrections. |
| [`render-comparison-gallery plan`](2026-10-06-render-comparison-gallery.md) | Existing matched-frame contract, gallery specimen selection and capture corrections; this plan is a follow-up, not a replacement. |

## 2. Verified baseline inventory

Counts below are explicit XML property declarations, not distinct tokens, unique widgets or automatic violations. Input ranges, default values and indexes are excluded from the visual-size inventory. Zero dimensions and Qt's unlimited-size sentinel must not be interpreted as ordinary component sizes.

| Declaration category | Count | Intended destination |
|---|---:|---|
| Explicit colors / inline stylesheets | 0 / 0 | Already absent from `.ui`; preserve that boundary. |
| Font sizes | 0 | Keep typography in shared QSS/tokens and shared painting configuration. |
| `minimumSize` / `maximumSize` | 162: 85 minimum, 77 maximum | Review each constraint; move reusable component dimensions to QSS where semantics are equivalent, otherwise one shared component initializer. |
| `iconSize` | 1 | Token-backed `qproperty-iconSize` or the shared icon initializer; do not maintain both as competing authorities. |
| Initial `geometry` | 3 | Window/page initialization; not automatically a QSS migration target. |
| Spacer `sizeHint` | 17 | Structural policy or token-driven layout configuration, not QSS styling. |
| Layout margins | 412 | Shared layout initializer using `setContentsMargins()`. |
| Layout spacing | 91 | Shared layout initializer using `setSpacing()`, `setHorizontalSpacing()` and `setVerticalSpacing()` as appropriate. |
| Splitter `handleWidth` | 1 | Token-backed `qproperty-handleWidth`, or one initializer if lifecycle validation favors it. |
| Total size/layout properties | 687 | Includes 504 layout/splitter declarations. |

These 687 properties contain 870 dimension/layout numeric values: 366 width/height values and 504 layout/splitter values. Of the 366 dimension values, 32 are zero, 19 are the Qt unlimited-size sentinel `16777215`, and 315 are other positive values. Initial geometry x/y coordinates are not included in that scalar total.

### Per-form distribution

All paths are under `src/dtflow/ui/forms/`.

| Form | Margins | Spacing | Splitter width | Min/max sizes | Spacer sizes | Geometry | Icon size |
|---|---:|---:|---:|---:|---:|---:|---:|
| `gallery_context.ui` | 4 | 1 | 0 | 0 | 1 | 0 | 0 |
| `gallery_detail_page.ui` | 164 | 38 | 0 | 106 | 6 | 1 | 0 |
| `generation_page.ui` | 84 | 16 | 0 | 20 | 3 | 0 | 0 |
| `main_window.ui` | 60 | 17 | 0 | 11 | 4 | 1 | 0 |
| `queue_page.ui` | 72 | 13 | 0 | 15 | 3 | 1 | 0 |
| `workspace_shell.ui` | 28 | 6 | 1 | 10 | 0 | 0 | 1 |
| Total | 412 | 91 | 1 | 162 | 17 | 3 | 1 |

### Repeated scalar values

There are **13**, not 12, distinct values across the 504 layout/splitter declarations. This corrects the conversational summary's distinct-value count; the individual frequencies were correct.

| Value, logical px | Margins | Spacing | Splitter | Total |
|---|---:|---:|---:|---:|
| 0 | 244 | 13 | 0 | 257 |
| 1 | 0 | 1 | 0 | 1 |
| 2 | 4 | 0 | 0 | 4 |
| 4 | 12 | 8 | 0 | 20 |
| 5 | 0 | 1 | 0 | 1 |
| 6 | 0 | 17 | 0 | 17 |
| 7 | 24 | 0 | 0 | 24 |
| 8 | 28 | 39 | 1 | 68 |
| 10 | 60 | 10 | 0 | 70 |
| 12 | 28 | 1 | 0 | 29 |
| 14 | 4 | 1 | 0 | 5 |
| 16 | 2 | 0 | 0 | 2 |
| 18 | 6 | 0 | 0 | 6 |
| Total | 412 | 91 | 1 | 504 |

### Repeated complete margin profiles

Order is left, top, right, bottom. These profiles cover 103 layouts and ten distinct profiles; missing/default properties were not guessed into the audit.

| Profile | Layout count | Distribution |
|---|---:|---|
| `0,0,0,0` | 61 | Gallery Detail 34; Generate 9; Main 4; Queues 7; Shell 7. |
| `10,7,10,7` | 12 | Section headers: Generate 5; Main 3; Queues 4. |
| `10,8,10,10` | 12 | Section contents: Generate 5; Main 3; Queues 4. |
| `12,12,12,12` | 7 | Gallery context 1; Generate/Main/Queues 2 each. |
| `8,8,8,8` | 4 | Gallery Detail 3; Main 1. |
| `4,4,4,4` | 2 | Gallery Detail. |
| `4,2,4,2` | 2 | Gallery Detail. |
| `18,18,18,18` | 1 | Main. |
| `18,16,18,16` | 1 | Main. |
| `14,14,14,14` | 1 | Queues. |

Concrete section-header owners: Generate has `backendSummaryBoxHeaderLayout`, `parametersBoxHeaderLayout`, `simulationBoxHeaderLayout`, `configurationBoxHeaderLayout` and `assetBoxHeaderLayout`; Main has `connectionBoxHeaderLayout`, `credentialBoxHeaderLayout` and `safetyBoxHeaderLayout`; Queues has `concurrencyBoxHeaderLayout`, `planBoxHeaderLayout`, `itemIdentityBoxHeaderLayout` and `uncertaintyBoxHeaderLayout`.

Their corresponding content layouts are Generate's `backendSummaryForm`, `parametersGrid`, `simulationForm`, `configurationLayout` and `assetLayout`; Main's `connectionForm`, `credentialLayout` and `safetyLayout`; Queues' `concurrencyForm`, `planBoxLayout`, `itemIdentityForm` and `uncertaintyLayout`.

Identical numeric values do not imply identical semantic roles. Preserve independent roles where future evolution differs; aliases may share a primitive value without coupling unrelated controls.

## 3. Qt capabilities and boundaries

Standard QSS styles widgets, not layout-manager objects. Its widget-box `margin`/`padding` are not equivalent to QLayout contents margins; its internal-widget `spacing` property is not a general layout gap. The 503 margin/spacing declarations therefore need layout API calls, not a mechanical translation into QSS.

QSS supports widget min/max dimensions and designable widget properties through `qproperty-...`, including documented icon-size syntax. The property's availability and behavior must be verified on the deployed Qt version. `qproperty` assignments happen during polishing and cannot be treated as hover/pressed-state property bindings. Avoid introducing custom widget adapters solely to hide every layout API behind QSS.

A local review on Qt 6.11.2 kept explicitly set layout margins at 12 px and spacing at 9 px despite widget QSS margin/padding and a QVBoxLayout selector. A QSplitter `qproperty-handleWidth` rule changed the property from 8 to 6. This proves those tested boundaries; it is not acceptance of a full layout migration.

Official references consulted during the investigation:

- [Qt stylesheet reference](https://doc.qt.io/qt-6/stylesheet-reference.html): supported widgets/properties, widget box model, internal spacing, min/max dimensions.
- [Qt stylesheet syntax](https://doc.qt.io/qt-6/stylesheet-syntax.html): specificity, ancestor styles, designable `qproperty` assignment and polish-time limitations.
- [QLayout](https://doc.qt.io/qt-6/qlayout.html): contents margins, spacing inheritance and layout management.
- [QBoxLayout](https://doc.qt.io/qt-6/qboxlayout.html): contents margins versus neighboring-box spacing and native defaults.
- [QSplitter handleWidth](https://doc.qt.io/qt-6/qsplitter.html#handleWidth-prop): splitter handle geometry.

Qt documentation was fetched with Context7 and cross-checked against official Qt documentation. Recheck version-sensitive behavior before implementation; the unversioned documentation URL can describe a later Qt release than the locked environment.

## 4. Capture evidence motivating the migration

The connection-button specimen in `tools/ui/states.py` independently constructs a QPushButton, sets its object name/icon, calls `setFixedSize(58,34)`, then `prop()` repolishes it. The real form declares the same minimum and maximum dimensions, but the capture's isolated host does not reproduce application composition.

A local probe observed: immediately after fixed sizing, minimum and maximum were both 58 by 34; after state repolishing, minimum became 58 by 2 while maximum remained 58 by 34; the isolated host then laid the button out at 58 by 26. The relevant QSS declares `min-height: 0` and a 1 px border. The sheet's nominal 58 by 34 description was therefore not reliable evidence of the rendered size.

User screenshots showed a different running-app appearance. They are observational evidence, not a controlled identical-DPI/identical-state baseline: `Capture d’écran 2026-10-06 081230.png` and `Capture d’écran 2026-10-06 081641.png` were supplied from the owner's ScreenSketch TempState directory. Do not commit personal screenshots or depend on ephemeral absolute paths for acceptance.

Additional confirmed capture defects were separate from token ownership: old ZIP gallery previews omitted shell logo/navigation; web round arrows were clipped by a zero-height absolute-position host; expanded web Detail icons raced image loading. The latter two defects and gallery icon-state coverage have been corrected in capture tooling. Qt gallery screen references still need full-shell regeneration. Do not disguise capture corrections as changes to production design values.

## 5. Target ownership and application contract

| Layer | Owns | Must not own |
|---|---|---|
| `tokens.json` | Authoritative shared color, typography, component dimensions and semantic layout values. | Widget trees, backend state or unrelated values merged only because they match numerically. |
| Shared QSS | Supported widget appearance, state selectors and migrated component sizing where Qt semantics are verified. | Unsupported QLayout margins/gaps or a second conflicting geometry authority. |
| Shared presentation initializer | Typed token conversion; layout margins/gaps; unavoidable widget geometry/icon/spacer configuration; semantic profile application. | Provider calls, application business rules, scattered numeric defaults or duplicated token tables. |
| Canonical `.ui` forms | Composition, layout types, widget names, semantic role/profile references, stretch/size policies, accessibility and approved structural exceptions. | Repeated migrated visual literals or styles overriding the shared theme. |
| Dynamic UI code | Responsive decisions, user-driven thumbnail/zoom changes and measured geometry relationships. | Re-declared base sizes and gaps already owned by tokens. |
| Capture tooling | Offline fixtures, state driving and extraction of actual mounted application widgets. | Independent copies of production widget styling, sizes or component construction. |

Keep the current flat token file authoritative; do not create a parallel hand-edited geometry-token registry. Define validated conversion for integer logical-pixel layout values, preserving existing color/font token behavior. Do not multiply logical layout values by OS scaling manually. Distinguish content-box dimensions from intended outer-widget dimensions, and treat Qt unlimited/default sentinels as constraints rather than design-scale tokens.

Proposed semantic profiles include zero-inset, section-header, section-content, panel-inset and workspace-gap. Preserve the existing verified profiles exactly in the first migration. Proposed shared dimensions include connection control outer size and glyph size, gallery mode/disclosure/icon-only controls, rails and scrollbar geometry after their existing behavior is audited. Token names are proposed, not implemented APIs.

Associate profiles with stable layout identities or validated layout dynamic properties. If QUiLoader cannot preserve a proposed layout property reliably, use a single explicit form/layout-name registry in the presentation module. That registry maps identities to token/profile names, not duplicated literal pixel values. Do not infer semantic roles through fragile name substrings or silently assign every layout the same defaults.

## 6. Ordered implementation stages

- [ ] Build a maintained XML audit under `tools/ui/` that reports file, owner type/name, property, value, explicit/default status and proposed authority. Extend it to relevant QSS and Python geometry owners before migrations. Reproduce the counts above and emit an ignored machine-readable inventory so omissions and count drift are visible.
- [ ] Establish a trustworthy Windows baseline first: capture actual MainWindow with controlled offline fixtures, record source revision/dirty state, Qt/platform plugin, font family and available weights, logical DPI, screen/widget/pixmap DPR, widget bounds and relevant parent/layout properties. Keep real-app/native-platform acceptance separate from DPR-1 offscreen rendering. Preserve the network guard before provider imports; never load owner credentials or production state for fixture captures.
- [ ] Define the token/profile schema and one presentation initialization path, integrated with `theme.py` and canonical form loading. Require missing profiles, wrong owner types and invalid token units to fail clearly in development/tests. Ensure application, standalone previews, dynamic widgets and capture tools use the same path without import cycles or separate fallback sizing.
- [ ] Pilot the connection component before bulk migration. Select exactly one sizing authority, remove competing `.ui`/QSS/capture declarations only after replacement exists, preserve the intended measured outer 58 by 34 behavior, and verify state changes and repeated polishing. Account for border/padding box semantics: do not blindly substitute outer dimensions into content-size QSS properties.
- [ ] Migrate repeated layout profiles first: all reviewed zero-inset layouts, the 12 section headers and 12 corresponding content layouts, then shared panel insets and gap roles. Apply layout token values after forms load and before first display. Reapply idempotently when required by canonical composition, without overwriting user preferences, measured slot sizes, responsive relationships or runtime-controlled values.
- [ ] Migrate reusable widget sizes, icon sizing and splitter properties in coherent component groups. Remove duplicated literals only for migrated groups, preserve approved structural constraints and review all 162 min/max declarations individually. Keep runtime geometry exceptions in a small documented allowlist with reason and authoritative owner.
- [ ] Migrate remaining unique form-specific profiles, spacer values and Python presentation literals only after classifying their semantics. Audit zero values too: removing an explicit zero can restore native nonzero margins/spacing. Preserve intended default/unlimited behavior instead of forcing all values onto a universal spacing scale.
- [ ] Replace independently reconstructed state specimens with actual mounted application widgets, including connection/navigation controls in normal-shell and Images composition. Drive presentation states offline, extract their pixels with the shared framing contract, and record actual geometry. Retain unsupported/unreachable states explicitly rather than inventing an approximation.
- [ ] Add migration-aware audit enforcement and compact contract tests at authoritative layers. Initially allow unmigrated literals through an explicit baseline; reject new uncontrolled styling/sizing in migrated roles and report removed exceptions. Update `DESIGN.md`, architecture, capture guide, status and unreleased documentation to describe actual implemented ownership and evidence.
- [ ] Deliver reviewable migration slices on feature branches/PRs, beginning with initializer plus the connection pilot, followed by repeated layout profiles and remaining components. Validate every slice before removing its previous authority. Keep rollback scoped to that slice; no bulk conversion or layout redesign in the same change.

## 7. Lifecycle, sizing and regression risks

- QSS specificity and ancestor context can change after Images reparents brand/navigation. Test Connection, Generate, Queues, Images and return transitions, not just isolated controls.
- Polishing can alter size constraints, as the connection probe demonstrated. Test actual minimum, maximum and final widget rectangles after first show, state changes, repeated repolish and layout activation. Reinitialization must be idempotent and must not create a polish/layout loop.
- QSS min/max dimensions are not guaranteed to match the outer-widget dimensions used by Designer setters. Establish and document box semantics for each migrated family, including border and padding.
- Explicit zero margins are deliberate values in 244 declarations. Removing them without applying their profile can restore platform-default margins. Default/inherited gaps and unlimited dimensions also need explicit preservation rules.
- `QFormLayout` and `QGridLayout` have separate horizontal/vertical gap settings; a single general spacing value must not erase intentional asymmetry.
- Icon sizes, real fonts/weights, disabled glyph rendering and focus states must be preserved. Offscreen fallback-font results do not establish native Windows pixel parity.
- Responsive rails, splitters, Inspector preferences, gallery header slots and filmstrip scaling are runtime relationships. Preserve measured sizes and preferences; centralize their base design values without freezing dynamic behavior.
- Raw min/max dimensions do not all represent design style. Review functional viewport constraints, wrapping, scroll behavior, accessibility, long labels, narrow windows and dialog initialization before deciding ownership.
- Standalone Designer preview may lose token-applied geometry when literal values are removed. Document that executable previews use the shared initializer; do not retain competing literals just to make Designer's static preview look authoritative. Keep Designer forms editable and structurally valid.

## 8. Verification and acceptance gates

Use the canonical locked uv environment. Planned baseline commands are `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest`; runnable tool changes additionally require import/compile checks and `--help`. Select focused Qt tests and bounded offline renderer checks for each slice, and run broader offline tests at integration checkpoints. Do not treat test counts or duplicated screenshot assertions as quality gates.

- Audit output reproduces the baseline before changes and accounts for every removed declaration as migrated or explicitly justified. Report coverage by owner/profile, not just a lower number of XML literals.
- Tokens have one authoritative definition; QSS resolves all references; the initializer validates supported units and owner types. Shared layout profiles preserve current margins/gaps, including asymmetric and zero-inset cases.
- The connection pilot renders with its intended actual outer dimensions after showing, state changes and repolishing. It uses the same mounted widget/initializer in the app and capture tool; no hand-built duplicate specimen remains.
- Before/after application captures use the same native/offscreen platform profile, fonts, logical DPI/DPR, window dimensions, fixtures, preferences and interaction states. Compare actual widget bounds and layout values before interpreting pixels. Do not normalize away geometry differences by independently resizing images.
- Full-shell Grid/List/Detail/expanded Detail captures include logo/navigation. Native running-app spot checks confirm that extracted connection and gallery controls represent the actual application. Offscreen tests and native acceptance are reported separately.
- Navigation and Images enter/leave cycles preserve layout, chrome, rail visibility and user preferences; narrow windows retain intended scroll/wrap behavior. Gallery thumbnail and image fit/zoom controls retain their dynamic ranges.
- Frame overflow, missing/undecoded icons, unsupported states, font fallback and DPI mismatch are surfaced in reports. Browser-versus-Qt engine differences remain distinct from capture defects and true design-rule divergence.
- No provider generation, model upload, training, inference or billable operation is required. Offline fixtures use temporary state and mocked backends, never the owner's saved-output/credential configuration.

## 9. Delivery and documentation

This document records a plan only. Implementation should update the relevant architecture and design references as each migration becomes real; do not prematurely claim that `.ui` no longer contains shared dimensions. Add implemented changes and actual verification to `devdocs/releases/unreleased.md`, keep `devdocs/current_dev_status.md` concise, and preserve earlier capture evidence as historical rather than rewriting it into current acceptance.

The default decision is semantic centralization with QSS where supported and a small shared Qt layout/component initializer elsewhere. A QSS-only architecture is not the objective. Pixel changes require an explicit design decision after migration fidelity is established; matching the web export is a separate review, not a reason to silently alter the app during centralization.

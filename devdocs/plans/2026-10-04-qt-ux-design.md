# Plan: Qt UX design foundation

**Status:** Design foundation implemented; interactive review pending  
**Owner:** DTFlow Phase 4/5 presentation work  
**Reference style:** the compact Windows dark theme and three-area workflow used by the local `splitvids` project

## Scope

- In:
  - define the DTFlow desktop information architecture before expanding the Qt implementation;
  - establish a centralized QSS theme derived from the Splitvids visual language;
  - create canonical, tracked Qt Designer `.ui` sources for the main shell and Phase 5 generation and queue surfaces;
  - keep generated Python disposable and free of business logic;
  - document object names, dynamic state properties, responsive behavior and presentation boundaries;
  - prepare the forms so the Phase 4 shell in PR #15 can adopt them without moving backend or service behavior into Qt.
- Out:
  - generation, queue dispatch, cloud asset upload or credential behavior changes;
  - replacing Phase 3 services with UI-owned implementations;
  - live inference, network validation or a packaged Windows build;
  - the Phase 8 gallery/preset browser and later workflow editor.

## Action items

- [x] Inspect the current DTFlow phase/release contracts and the Splitvids stylesheet/layout conventions.
- [x] Record the presentation architecture, visual tokens and canonical `.ui` workflow.
- [x] Add the centralized DTFlow dark QSS without Splitvids-specific selectors or inline runtime styling.
- [x] Add canonical Designer forms for the shell, generation workspace and queue workspace.
- [x] Define promoted-widget/model seams for custom previews, queue views and future rich editors.
- [x] Integrate the theme and relocated main form with the Phase 4 shell after PR #15 landed.
- [x] Synchronize the repository contract, README, initial plan and pending release notes without claiming runtime Phase 5 behavior.
- [x] Commit, push and maintain draft PR #16 for review.

## UX architecture

The first desktop path uses three persistent regions rather than a collection of modal workflows:

1. **Left context rail** — backend selection, connection state, model/checkpoint choice and saved configuration actions.
2. **Central workspace** — prompt and generation parameters, primary action, progress, result preview and failure/uncertainty presentation.
3. **Right inspector** — LoRA selection and compatibility, effective request details, attempt identity and reproducibility evidence.

The durable queue is a first-class workspace reachable from the same shell. It preserves stable item identity and exposes Pause, Resume and Cancel without reimplementing scheduler decisions. Narrow windows collapse the inspector before hiding primary generation controls. The minimum supported desktop layout remains usable at 800x680; normal layouts target 1280x800 and above.

## Visual language

DTFlow adapts the Splitvids dark Windows style rather than copying its programmatic UI construction:

- application background `#1f1f1f`;
- raised panel `#252525` and header `#2b2b2b`;
- input surface `#1e1e1e` with border `#3a3a3a`;
- primary blue `#0068b8`, focus/hover blue `#0078d4`;
- success green `#2e7d32`, destructive red `#c62828`, warning amber `#ff9800`;
- primary text `#e6e6e6`, secondary text `#a0a0a0`, disabled text `#777777`;
- Segoe UI on Windows, compact 6 px spacing, and 6-8 px radii;
- color is never the only status signal: state text and accessible names remain required.

Selectors use stable semantic object names and dynamic properties such as `role`, `state` and `severity`. Runtime code changes properties and requests a repolish; it does not inject ad-hoc stylesheets for ordinary states.

## Canonical Designer workflow

- Track editable `.ui` XML under `src/dtflow/ui/forms/`.
- Keep QSS under `src/dtflow/resources/styles/` and apply it once at application startup.
- Use `pyside6-uic` only as a mechanical conversion/validation step. Generated modules are not authoritative and contain no manually maintained logic.
- Wrap generated `Ui_*` classes with handwritten PySide6 view classes that connect presentation signals to controllers/services.
- Use Designer promotion for custom widgets. The promoted classes expose presentation APIs and signals only; backend types stay outside the UI package.
- Keep widget `objectName` values stable because tests, QSS selectors and accessibility wiring depend on them.

## Presentation boundaries

- Qt widgets render immutable/backend-neutral view data and emit user intent.
- Existing services own connection checks, request resolution, generation, queue state and persistence.
- Background execution uses the Phase 4 worker boundary; no network, generation or blocking persistence runs on the Qt event loop.
- Generated Designer Python is never edited by hand and is never a home for business rules.
- Error, unknown and interrupted outcomes remain distinct in text and styling.

## Decisions and design changes

- 2026-10-04: start the UX design foundation in a separate review branch while PR #15 owns the minimal Phase 4 runtime shell.
- 2026-10-04: base visual tokens on Splitvids but replace its imperative builder and inline-style patterns with canonical Designer forms and one application QSS.
- 2026-10-04: treat generation and queue forms as Phase 5 presentation contracts; adding the forms does not claim that those workflows are wired or usable.
- 2026-10-04: PR #15 was squash-merged first; rebase this branch and integrate its existing shell rather than duplicating connection or credential behavior.
- 2026-10-04: keep `previewHost` as a future custom preview promotion point and `queueItemsView` as a model/view seam; neither form imports backend types.

## Open questions

None blocking. Detailed control ordering and text can change during visual review without changing service contracts.

## Validation

- [ ] Review the forms interactively in Qt Designer.
- [x] Mechanically convert all three tracked forms with `pyside6-uic` after integration with PR #15.
- [x] Run focused Ruff lint and format checks for `src/dtflow/ui/`.
- [ ] Final tests and smoke tests deferred to the user under the fast implementation workflow.

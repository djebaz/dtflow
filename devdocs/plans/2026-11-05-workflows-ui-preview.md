# Workflows workspace: native preview phase

## Scope

- In: dedicated Workflows workspace mockup in the native shared shell; canonical Designer left/library, centre/canvas and right/inspector forms; token-scoped QSS; accepted nodeeditor-qtpy POC canvas reused only by the offline renderer; four targeted Workflows captures and representative mappings.
- Out: production navigation, workflow admission/execution, providers/models/downloads, queue schema changes, real selectors, file persistence and unit tests. The only normal-app change is the owner-reported shared left-rail sizing correction described below; the Workflows mockup is not executable.

## Decisions

- PR #228 is owner accepted and squash-merged as `814d986f`; nodeeditor-qtpy is the selected canvas direction.
- Workflows is a separate workspace, not another window or Generate mode. Current Scripts remains a Generate mode; the mockup adds a preview-only Workflows navigation entry without moving Scripts or changing shipped navigation.
- Use left library/node palette, dominant centre canvas and selected-node inspector. Run state uses the existing shared activity bar, not synthetic percentage progress.
- Show editing, running, branch paused and narrow editing. A no-detection pause affects only that image branch; Continue/Stop act only on it and sibling branches continue. Preview actions never admit work.
- Reuse real library interactions through renderer-side canvas injection. Do not import tools or nodeeditor from normal production launch paths; the library is a dev dependency only for captures.
- The owner authorized Workflows renders and visual review, then Connection/Queues captures to diagnose the reported horizontal clipping. All outputs are under ignored `tmp/`. No unit tests, EXE launches or other category captures.

## Action items

- [x] Record owner POC acceptance, squash-merge #228 and branch from verified current main.
- [x] Add canonical Designer forms and token-based Workflows styling.
- [x] Add a presentation-only binder and reuse the accepted canvas in offline fixtures.
- [x] Register `workflows` category, independent worker, levels, representative pack and file mapping.
- [x] Render `--category workflows -s` to `tmp/ui-workflows-preview`, inspect all four images and correct visible layout issues.
- [x] Reproduce the left-rail clipping, fix shared sizing, inspect Connection/Queues outputs and obtain owner native-app confirmation.
- [x] Update scope/evidence docs.
- [ ] Open a draft PR.

## Evidence and review

- Workflows: four captures saved in 11.8 s on the final mockup-layout pass, no waits timed out. Reviewed editing/running/branch-paused/narrow images; corrected clipped navigation, stretched cards/buttons, unreadable initial graph fit, a narrow hint collision and inspector values that did not match the selected fixture node. The narrow state intentionally pans to the selected node at readable scale rather than shrinking the whole graph.
- Workflows navigation is a compact selected icon between Generate and Queues, with a tooltip/accessibility name and Workflows library heading. Scripts remains inside Generate. This is a preview placement, not a production navigation change; the earlier phrase "beside Scripts" was imprecise.
- The 14 Workflows notices are expected offscreen Qt `propagateSizeHints` notices, not provider or save failures. Details and manifests are under `tmp/ui-workflows-preview/`.
- Owner reported real-app left-panel clipping. Initial four fast Connection fixtures did not reproduce it. A new `connection-61-left-rail-fit` capture injects a synthetic wide hidden page without reading owner state and logs geometry after switching Generate → Queues → Connection, with no layout assertions.
- Before correction, the stack/page was 1,537 px wide in 288/300 px viewports. The canonical left stack and its pages now ignore horizontal size hints, so hidden inventory/queue content cannot widen them. After correction, viewport/stack/page match at 288 px for Generate/Connection and 300 px for Queues (zero excess). Vertical policies, the 300 px rail, scrolling and queue execution remain unchanged.
- Reviewed the corrected 1180×790 Connection width-pressure capture and both fast Queues captures (2/2 saved). The owner confirmed the fix works in the native app on 2026-11-05. Workflows mockup approval remains separate.
- No unit tests added or run, no real providers/generation, and no EXE launches. `ruff format` was used as an editing step; no separate test/lint suite was run for this preview phase.

## Acceptance

Rendered screenshots establish offline layout evidence only. Owner mockup approval, native Windows/DPI and production workflow behavior remain separate. No automated layout assertions or unit tests are added. Static POC parameter choices are illustrative, not a production contract or live inventory.

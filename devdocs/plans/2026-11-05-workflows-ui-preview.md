# Workflows workspace: native preview phase

## Scope

- In: dedicated Workflows workspace mockup in the native shared shell; canonical Designer left/library, centre/canvas and right/inspector forms; token-scoped QSS; accepted nodeeditor-qtpy POC canvas reused only by the offline renderer; four targeted Workflows captures and representative mappings.
- Out: production Workflows navigation, workflow admission/execution, providers/models/downloads, queue schema changes, real selectors, file persistence and unit tests. Normal-app changes are the owner-reported shared left-rail sizing correction and approved utility-navigation relocation; the Workflows mockup is not executable.

## Decisions

- PR #228 is owner accepted and squash-merged as `814d986f`; nodeeditor-qtpy is the selected canvas direction.
- Workflows is a separate workspace, not another window or Generate mode. Scripts remains a Generate mode. The preview primary row reads Generate, Workflows, Queues. Connection moves to the extreme right of the bottom bar as the existing colored status icon, with the extra-small `size.radiusXs` (3 px) corner. Images becomes a 36 px shared blue primary-action button above ordinary right inspectors, moving above the centre when the inspector rail collapses. It is absent from Images and Logs views.
- Use left library/node palette, dominant centre canvas and selected-node inspector. Run state uses the existing shared activity bar, not synthetic percentage progress.
- Show editing, running, branch paused and narrow editing. A no-detection pause affects only that image branch; Continue/Stop act only on it and sibling branches continue. Preview actions never admit work.
- Reuse real library interactions through renderer-side canvas injection. Do not import tools or nodeeditor from normal production launch paths; the library is a dev dependency only for captures.
- The owner authorized scoped offline renders and visual review, including navigation regressions and the pushed render Action results. All local outputs are under ignored `tmp/`. No unit tests or EXE launches.

## Action items

- [x] Record owner POC acceptance, squash-merge #228 and branch from verified current main.
- [x] Add canonical Designer forms and token-based Workflows styling.
- [x] Add a presentation-only binder and reuse the accepted canvas in offline fixtures.
- [x] Register `workflows` category, independent worker, levels, representative pack and file mapping.
- [x] Render `--category workflows -s` to `tmp/ui-workflows-preview`, inspect all four images and correct visible layout issues.
- [x] Reproduce the left-rail clipping, fix shared sizing, inspect Connection/Queues outputs and obtain owner native-app confirmation.
- [x] Update scope/evidence docs.
- [x] Open draft [PR #229](https://github.com/djebaz/dtflow/pull/229) from `feature/workflows-ui-preview`.

## Evidence and review

- Workflows: four captures saved in 11.8 s on the final mockup-layout pass, no waits timed out. Reviewed editing/running/branch-paused/narrow images; corrected clipped navigation, stretched cards/buttons, unreadable initial graph fit, a narrow hint collision and inspector values that did not match the selected fixture node. The narrow state intentionally pans to the selected node at readable scale rather than shrinking the whole graph.
- Workflows navigation is a full text tab between Generate and Queues. Scripts remains inside Generate. The Workflows tab is preview-only, while Connection/Images utility placement changes the shared runtime shell. The earlier phrase "beside Scripts" was imprecise.
- The 14 Workflows notices are expected offscreen Qt `propagateSizeHints` notices, not provider or save failures. Details and manifests are under `tmp/ui-workflows-preview/`.
- Owner reported real-app left-panel clipping. Initial four fast Connection fixtures did not reproduce it. A new `connection-61-left-rail-fit` capture injects a synthetic wide hidden page without reading owner state and logs geometry after switching Generate → Queues → Connection, with no layout assertions.
- Before correction, the stack/page was 1,537 px wide in 288/300 px viewports. The canonical left stack and its pages now ignore horizontal size hints, so hidden inventory/queue content cannot widen them. After correction, viewport/stack/page match at 288 px for Generate/Connection and 300 px for Queues (zero excess). Vertical policies, the 300 px rail, scrolling and queue execution remain unchanged.
- Reviewed the corrected 1180×790 Connection width-pressure capture and both fast Queues captures (2/2 saved). The owner confirmed the fix works in the native app on 2026-11-05. The owner approved the Workflows mockup design on 2026-11-05.
- No unit tests added or run, no real providers/generation, and no EXE launches. `ruff format` was used as an editing step; no separate test/lint suite was run for this preview phase.

## Utility-navigation follow-up

- Owner correction: Images is absent from Images and Logs, not duplicated into those headers. Use the existing primary blue action style; Connection uses `size.radiusXs` (3 px), not the panel radius.
- Registered Workflows narrow capture now crosses the actual 1100 px shared breakpoint at 1040 px; no new catalogue IDs or broad fallback expansion.
- Local navigation captures reviewed: ordinary header placement, compact placement, footer status icon and Images/Logs absence. A packed Workflows/Logs worker initially crashed during teardown; draining queued/deferred Qt events while Python owners remain alive corrected the same packed three-state capture (3/3 saved in 14.7 s). Separate Logs capture also saved successfully.
- Pushed Actions results: the render Action for `58927c03` saved all 12 captures. CI then failed only on one stale existing shell assertion that still expected Connection and Images inside the top navigation frame; it now expects Generate/Queues there, Connection in the footer status bar and Images hidden in Logs (`53fbf97`), and CI passed. No new unit tests, no local test runs and no EXE launches.

## Acceptance

The owner approved the Workflows mockup design on 2026-11-05. Rendered screenshots establish offline layout evidence only; native Windows/DPI and production workflow behavior remain separate. No automated layout assertions or unit tests are added. Static POC parameter choices are illustrative, not a production contract or live inventory.

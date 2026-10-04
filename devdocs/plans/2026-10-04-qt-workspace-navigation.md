# Plan: Qt workspace navigation and preview composition

**Status:** Implemented; user visual review pending
**Owner:** DTFlow desktop presentation follow-up  
**Depends on:** merged PR #16 canonical Designer forms and theme

## Scope

- Add canonical shell navigation for Connection, Generate, and Queues.
- Load the existing generation and queue Designer forms into one desktop workspace.
- Keep Generate and Queues explicitly marked as preview-only and disable all actions that could imply usable Phase 5 behavior.
- Preserve the existing Phase 4 connection, credential, runner, and backend-service wiring unchanged.
- Keep form loading and page switching in the presentation layer; do not add generation, queue, persistence, or provider logic.
- Synchronize current-status and unreleased documentation without claiming Phase 5 completion.

## Out of scope

- Generation submission, configuration persistence, inventory refresh, LoRA preflight/upload, queue model binding, or queue controls.
- Live backend checks, generation, packaging, or release work.
- Phase 3B cloud LoRA upload/reuse.

## Action items

- [x] Confirm PR #16 is squash-merged and `main` is clean.
- [x] Add Designer-owned navigation and a stacked workspace host.
- [x] Compose `generation_page.ui` and `queue_page.ui` through a reusable form loader.
- [x] Apply an honest preview state and disable unwired controls.
- [x] Update README, initial plan, and unreleased notes.
- [x] Mechanically validate all tracked `.ui` files and run focused formatting checks.
- [x] Commit, push, and open the follow-up PR.

## Decisions

- 2026-10-04: make the next PR a presentation-only composition slice. This allows visual review of all canonical forms without crossing the Phase 5 service boundary or bypassing the remaining Phase 3B cloud-asset prerequisite.
- 2026-10-04: keep Connection as the initial page and preserve its existing runtime behavior.
- 2026-10-04: visible preview pages are permitted only when their action controls are disabled and the shell labels them as not yet wired.
- 2026-10-04: PR #17 records the next implementation phase in [the detailed Phase 3B cloud asset completion plan](2026-10-04-phase-3b-cloud-assets.md); the remaining pre-Qt asset gate is completed before Phase 5A service wiring.

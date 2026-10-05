# GUI-first README and usage guides

## Approved direction

The README presents DTFlow as a desktop application and guides readers through the repository. It is not a status report, CLI manual or implementation diary. The CLI is linked only as an automation/development interface.

## Scope

- Keep presentation, Python/uv requirements, setup and desktop launch, repository layout, documentation navigation, contributing and license in README.md.
- Move current desktop, CLI, queue, testing and diagnostic instructions into devdocs/guides/ with readable headings and cross-links.
- Keep implementation status and verification history in existing plans, release notes and research. Do not replace historical evidence with new claims.
- Retain safety warnings beside operations that contact backends, upload assets or submit generation. Correct the obsolete claim that only generate/check/models contact backends.
- Add a short, dated `devdocs/current_dev_status.md` mapping implemented/remaining/added scope to `initial.md`, as requested during implementation. Reference it from AGENTS.md; code presence is not acceptance.
- Preserve source execution, flags and runtime behavior. No dependency, version, UI or backend changes.

## Validation

Check local Markdown destinations and anchors, preserved guidance, canonical CLI help and diff whitespace. Do not run live checks, generation, uploads, builds or billable actions for this documentation change.

## Completion

Implemented: 54-line GUI-first README; desktop, CLI, queues, testing and development guides; initial-plan-aligned current development snapshot; AGENTS.md maintenance rules; corrected source-run documentation link and unreleased notes.

Verified: local Markdown destinations/anchors and JSON example; canonical help for top-level, gui, generate, queue add, test and assets prepare; whitespace checks. Testing guide accepted by the owner. No tests, UI launch, live checks, inference, upload or packaging were run. PR #28 carries this documentation-only change.

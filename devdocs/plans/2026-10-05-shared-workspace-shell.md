# Persistent shared workspace shell

Approved direction: one Generate-style shell stays mounted across Connection, Generate and Queues. Only left context, center work and right evidence change. No per-tab shell geometry or navigation reparenting on tab switches.

1. Establish a canonical Designer shell with shared splitter, brand/navigation slots, scrolling rails and three coordinated content stacks.
2. Reduce page forms to their existing content regions; preserve object names and controller references, services and safety gates.
3. Mount existing content once, share inspector/responsive behavior globally and preserve Generate image inspection.
4. Use existing QSS/tokens with semantic shell roles; avoid new dependencies or duplicated page-specific styling.
5. Extend authoritative Qt composition tests and offline captures, then run canonical lint/format/offline tests. Windows interactive/package acceptance remains separate.

No provider checks, uploads, live generation or queue dispatch are required. Existing data and outputs are untouched.

## Implementation and verification status

The canonical shell and three content stacks are implemented; page forms retain only content and controller object names. Navigation moves only for shared responsive behavior, never on tab switches. Existing test-facing Generate rail references point to the same shell, without duplicating controls.

User requested no added or further tests during implementation. No test files are changed. Before that instruction, initial Qt checks failed on removed Generate-specific splitter references; these references were removed. A later shell check showed seven passing cases before interruption. Earlier attempts were not successful full-suite verification. No post-fix full-suite or visual acceptance is claimed. Final static checks: `uv run ruff check .`, `uv run ruff format --check .` and `git diff --check` passed. Final tests and visual acceptance are deferred to the end-of-PR review at the user's request. No provider requests, uploads, training, inference or billable jobs were initiated.

## User screenshot follow-up

Add a shared 8 px splitter gutter using `size.workspaceGutter` and the existing background/border palette. Keep internal padding and the Inspector toggle unchanged. No tests or captures run for this follow-up; final visual acceptance remains pending.

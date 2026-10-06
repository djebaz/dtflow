# Design-system screenshot tooling

Date: 2026-10-06. Scope: development capture only; no runtime UI/theme changes.

## Contract

- Add `design_shots.py` and `design_states.py`, using the exported HTML kits and
  compiled component bundle without editing/rebuilding the export.
- Accept `--design-system` for either the tracked reference or an external export.
- Match Qt screen filenames/dimensions and component family/state labels where
  supported. Preserve actual kit defaults; disclose unmatched fixtures rather
  than silently rewriting kit behavior to make screenshots appear equivalent.
- Capture browser interaction states headlessly, one fresh component per cell.
  Record computed styles, bounds, requested/observed state and unsupported cases.
- Render at device scale 1; enlarge matrix cells 2x like `states.py`.
- Vendor only the export's pinned React/ReactDOM/Babel scripts with explicit
  `--fetch`, verifying their declared SHA-384 integrity. Ordinary captures block
  non-loopback requests and fulfill those scripts from the verified cache.
- Write images and a machine-readable manifest under ignored `tmp/`. Fail with
  nonzero status on capture errors; unsupported native states remain explicit.
- Use Playwright in the uv dev group; browser installation is an explicit setup
  step. No provider traffic, training, inference, credentials or billable work.

## Validation

Syntax, imports, CLI help, Ruff, focused offline contract tests, then a real
headless screen/matrix smoke if browser/resources are available. Report failures
and incomplete coverage honestly; this does not establish pixel parity.

## Remaining scope

Full fixture alignment, automatic Qt/web pixel diffs and shared exhaustive state
schemas are follow-ups. The current Qt scripts remain unchanged except README
command corrections/documentation. Do not commit unrelated owner changes.

## Implementation evidence

- Scripts and verified vendor cache implemented; ordinary captures block non-local traffic.
- Focused offline contracts: 3 passed. Eight updated-export screen captures and 25 button-state cells succeeded using existing Chromium 149 (explicit executable override); broader matrix validation is in progress.
- Installing the Playwright-matched Chromium hit ENOSPC on C: and was stopped. No user files were deleted. Exact browser version remains recorded in each manifest.

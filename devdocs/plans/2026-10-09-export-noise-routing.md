# Plan: export noise and blank routing

## Scope
- In: default-checked Connection export checkbox; shared pixel detector v2; noise/blank destination folders and matching sidecars; saved choice and completion counts.
- Out: source deletion/moves, generation status changes, automatic regeneration, aesthetic/prompt checks, new dependencies or provider operations.

## Action items
- [x] Extract detector into a shared runtime service and keep the developer CLI on the same path.
- [x] Add default-on option, additive schema-1 preference, canonical Designer checkbox and background routing.
- [x] Document the intentional exception to flat export; static checks passed. Delivery continues in PR #55.

## Decisions and Design Changes
- 2026-10-09: Owner overrides the earlier root-only contract specifically for detected noise/blank images. Checkbox off restores completely flat exports.
- Detector analyzes the white-composited source RGB before JPEG encoding, using the same v2 thumbnail metrics/rules. Earlier labelled evidence was JPEG-based; PNG export acceptance remains pending.
- Classification is advisory, never job failure. Unreadable source images remain export failures, not blank/noise.
- Create noise/blank folders only when used; reserve names and sidecars inside the chosen category, never also at root. Reject redirected category folders. Originals remain read-only.
- Old schema-1 preferences without the new boolean default to enabled; present non-booleans are rejected. No version/schema migration required.

## Open questions
- None blocking.

## Validation
- [x] Scoped Ruff format/lint, AST/import, detector and dtflow CLI help, Designer XML/unique widget names/default checked, and diff checks passed. No export or GUI smoke invocation.
- Tests and export/GUI smoke checks remain with the owner under fast-implement; no new tests or smoke runs.
- No inference, provider calls, billable actions or packaging required.

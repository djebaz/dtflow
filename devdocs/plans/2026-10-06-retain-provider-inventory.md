# Plan: retain provider inventory and persistent UI statistics

## Scope
- In: restore each provider's last successful display snapshot after switching, startup cache prefill, keep choices/selections and counts through failed refreshes, and always show observation provenance/time.
- Out: generation/live validation, retry/auth/provider fallback, billing, dependencies and cache schema/layout changes.

## Action items
- [x] Inspect canonical catalogue, inventory cache, session and Connection wiring; start fix/retain-provider-inventory from clean main.
- [ ] Add backend-neutral, identity-validated last-successful presentation memory with existing disk-cache fallback.
- [ ] Restore provider forms before rebuilding selectors; preserve model/LoRA selections through missing/failed discovery.
- [ ] Keep model/LoRA counts and observation time alongside cache/loading/error status; reject obsolete completions.
- [ ] Sync docs, static checks, commit/push and new PR.

## Decisions and Design Changes
- 2026-10-06: provider switch is not an invalidation of that provider's last successful observation. Local endpoint and selected cloud credential source/key remain identity boundaries.
- Last-successful in-run presentation snapshots remain observations, not generation authorization. Existing fixed disk-cache filenames, credential validation, cache version and no-cache read/write bypass remain authoritative.
- Refresh failure must not replace successful snapshots or selection state. A successful empty response remains a real observation and is not silently ignored.
- Stats must show models, LoRAs and original observed-at time even if this launch only restored cached/remembered evidence.

## Open questions
None.

## Validation
- Tests and smoke tests deferred to the owner under fast-implement; none added/run. Static checks only.
- No inference, training, provider requests, uploads, packaging or billable actions planned.

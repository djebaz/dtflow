# Plan: retain provider inventory and persistent UI statistics

## Scope
- In: restore each provider's last successful display snapshot after switching, startup cache prefill, keep choices/selections and counts through failed refreshes, and always show observation provenance/time.
- Out: generation/live validation, retry/auth/provider fallback, billing, dependencies and cache schema/layout changes.

## Action items
- [x] Inspect canonical catalogue, inventory cache, session and Connection wiring; start fix/retain-provider-inventory from clean main.
- [x] Add backend-neutral, identity-validated last-successful presentation memory with existing disk-cache fallback.
- [x] Restore provider forms before rebuilding selectors; preserve model/LoRA selections through missing/failed discovery.
- [x] Keep model/LoRA counts and observation time alongside cache/loading/error status; reject obsolete completions.
- [x] Sync docs, static checks, commit/push and new PR.

## Decisions and Design Changes
- 2026-10-06: provider switch is not an invalidation of that provider's last successful observation. Local endpoint and selected cloud credential source/key remain identity boundaries.
- Last-successful in-run presentation snapshots remain observations, not generation authorization. Existing fixed disk-cache filenames, credential validation, cache version and no-cache read/write bypass remain authoritative.
- Refresh failure must not replace successful snapshots or selection state. A successful empty response remains a real observation and is not silently ignored.
- Stats must show models, LoRAs and original observed-at time even if this launch only restored cached/remembered evidence.

## Open questions
None.

## Validation
- The owner delegates tests to GitHub Actions. No local tests added/run under fast-implement; interactive smoke remains owner-deferred. Static checks only locally.
- No inference, training, provider requests, uploads, packaging or billable actions planned.

## Implementation notes
- PR #51 targets main from fix/retain-provider-inventory; not merged.
- Shared service retains at most one last-successful snapshot per provider with a process-salted identity digest; every restoration resolves selected credentials/endpoint before reuse. No secrets enter the Qt layer, logs or new files.
- Disk fallback preserves cache version/layout checks. Presentation history is retained in-run even with no-cache, while persistent reads/writes remain disabled. Empty successful inventory is accepted, not silently replaced with old evidence.
- UI guards provider/endpoint/revision and read tokens; pending disk reads cannot replace newer live observations. Model/LoRA form selections survive restoration and missing inventory, while deliberate filter changes retain existing pruning policy.
- No dependencies, version or schema changes. Packaging is not applicable to this source fix PR.

## Evidence and handoff
- Passed targeted Ruff lint/format, Python compilation/imports, canonical UI XML parsing, shared theme-token resolution and git diff --check. No local tests ran.
- On 2026-10-06 the owner reported smoke tests OK for the in-progress source change. No exact tested source/build ID or scenario list was supplied; this is owner-reported interactive acceptance, not agent-run or packaged-executable acceptance.
- Automated tests are delegated to GitHub Actions by the owner; their outcome is not claimed here.
- No agent-run training/inference, provider/network checks, uploads or billable actions. No executable built.

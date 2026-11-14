# Prepare storage recovery (#442)

## Selection and scope

Start with #442 on merged #441 (`01d4c9e9`): recover an explicit Prepare after storage refusal without reselecting files. #443 requires unverified runtime re-attach behavior; #444 is supplementary visibility/optimization. No new provider behavior, automatic retry, tests, fixtures or renders are authorized.

## Implementation plan

- Reuse the read-only dummy-size storage probe for a best-effort key-scoped snapshot alongside the locally inspected editor. Neither a pending snapshot nor a read failure holds readiness or explicit approval.
- Show free space and a source-plus-5% conversion estimate as an advisory only. Use the latest real storage-refusal free-space observation for exact needed/free feedback.
- Keep storage-refused rows in the editor. Re-Prepare unlocks those rows without granting accepted-name replacement; another explicit Prepare action is required.
- Open the existing storage overlay within the shared center panel without navigating away or losing selected files. Keep the side panels uncovered. Revalidate backend, saved-provider identity and ownership revision; never silently switch keys.
- Prefer identifiable held rows in a new approved batch using the advisory listing, without using name/size observations as receipt or upload authority. Unknown conversion outputs retain relative order.
- Keep retry, receipt and upload services unchanged. No assumed re-attach-on-full or cross-key behavior.

## Evidence boundary

The first comment on #442 owns AC-1 through AC-5 and levels. Source/static checks do not prove recovery, UI timing or live key behavior. CI and owner smoke are separate acceptance evidence. No tests, renders, uploads, conversion or generation are run by the development agent.

## Source progress and limitations

- Source implementation complete: canonical editor storage row, shared probe helper, read-only registry composition, current-owner checks, explicit storage retry and center-panel storage overlay.
- AC-3 is best-effort prioritization only: the provider listing exposes names and sizes, not a verified content hash. Matching observations only reorder already-approved rows; they never bypass service verification or authorize an upload. New/unknown converted bytes cannot be classified in advance.
- AC-4 estimate is selected-source bytes plus 5%, without dtype analysis; it is deliberately approximate and never gates readiness. Unknown free space is shown as unknown.
- `assets.prepare_storage_unavailable` records only the selected-file count when the advisory snapshot cannot be read; arbitrary exception text is not logged.
- Review follow-up: keep the advisory network read separate from local inspection. Unknown headroom remains explicit if approval happens before the answer; stale snapshot callbacks are dropped by editor/owner checks and a read revision, including after a newer storage-refusal observation.
- Prescribed environment readiness passed (Python 3.13.13, SQLite 3.53.1), changed-module imports/AST and Designer XML syntax passed, and focused Ruff lint/format and diff checks passed. No source-only check is counted as live acceptance.

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

## Owner-authorized offline acceptance extension

The owner could not practically fill the live key (about 3 GB free), then explicitly authorized a controlled offline storage-full fixture and required execution in GitHub Actions, not locally. This overrides the implementation-time no-tests restriction only for these focused checks. The existing in-memory HTTP transport now supplies a synthetic storage refusal, confirmed deletion/free-space change, and successful ordinary Prepare after a separate explicit approval. Additional checks cover owner-change rejection and read-only registry inspection preserving an interrupted intent. No real backend, upload, deletion, generation or preview is authorized by this test request. Actions results are recorded in the PR after execution; this does not establish live DT+ quota semantics.

## Owner UX correction

The owner rejected storage appearing underneath Prepare, then hiding Prepare and exposing the side panels. Storage now opens as a smaller centered popup above the still-visible disabled Prepare editor. Its backdrop dims Prepare; the shared workspace scrim stays behind Prepare and blocks both side panels. Closing storage restores only the same draft and focus under the same owner. Replacement, navigation and shutdown must not resurrect a stale editor. Connection storage remains center-panel-sized. Estimates are blue normally and amber only for insufficient known space or an unresolved refusal; they never block approval.

## Updated evidence

[Actions Full](https://github.com/djebaz/dtflow/actions/runs/43582923995) at exact target `d4f811c9f8d254e73808efecbab2550ed4d67da1` passed **700 tests, 3 skipped in 68.84 seconds**, including lint and format. Controlled checks cover explicit recovery without reselection, visible inactive Prepare/scrim, smaller popup, focus, owner change, replacement safety, read-only registry preservation, and advisory readiness/color with insufficient and ten-times-sufficient headroom. This is in-memory/offscreen execution, not live provider or native visual acceptance.

The owner screenshot confirmed the advisory is visible but exposed low contrast and missing side insets. Later `25fc672b` uses the existing pale-blue selection-text token and 12 px horizontal padding on both introductory lines. This later presentation change has static diff checks only; owner visual checking continues. No local tests or renders ran.

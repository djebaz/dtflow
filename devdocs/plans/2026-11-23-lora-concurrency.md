# LoRA conversion and Prepare concurrency

Refs #548; implementation in PR #585. Source and local offline evidence must be
distinguished from Windows and deployed DT+ owner acceptance.

## Before and after

| Mechanism | Failure it prevented | Revised ownership and tradeoff |
| --- | --- | --- |
| Lifetime `assets.lock` | Another writer treating a live transfer as interrupted | Short schema/recovery lock; per-writer OS lease recorded with operation intent. Only an unlocked owner is interrupted. |
| Process-wide `_WRITER` | Overlapping restore/rebind under exclusive recovery assumptions | Existing shared tasks still coalesce identical work. Content/name leases protect conflicting operations; different files proceed independently. |
| Store lock across copy/hash | Lost index updates, colliding names, deletion during use | Copy and verification happen under content ownership, outside the short atomic index publication lock. Upload, rename and removal share content ownership. |
| Sequential Prepare and Generate busy flag | One mutable progress index and one owning request | Three file workers, immutable per-file callback indices and separate relays; Generate's request snapshot remains independent. |
| Refused admission on ordinary contention | Avoid uncontrolled background work | Up to 100 admitted files; two conversion slots and three Prepare slots per process. Waiting is visible, cancellable and limited to five minutes. |

## Recovery, consent and compatibility

- `asset_operation_owners` is an additive schema-6 table; no saved receipts,
  accepted names, retained bytes or execution records are reset. Close older
  DTFlow processes before using this version against the same state directory.
- Owner and resource lease files remain stable and must not be deleted to clear
  a busy owner. OS ownership ends on process exit. No heartbeat, polling daemon,
  automatic re-upload, backend/key change or new durable scheduler is introduced.
- Short SQLite metadata writes use a 30-second busy timeout. Content/name and
  conversion/Prepare admission waits use a five-minute limit outside transactions.
  Exhaustion ends with an actionable reason; provider/validation refusals are not
  contention and are never retried by these waits.
- Stop and account/backend changes cancel queued/waiting/converting work before
  upload. An upload already started settles with its captured backend/key.
  Unconfirmed transfers remain unknown; manual reconciliation/Prepare remains
  the recovery path. One consumer stopping a shared restore does not stop another.
- Auto-prepare requires the existing opted-in preference. Mixing automatic files
  into a manual batch does not select those files in Generate.
- More independent work can increase disk/network load. Bounds are process-local;
  content/name leases additionally protect cooperating processes. Windows/WSL
  simultaneous SQLite writers remain unsupported.

## Validation

The owner explicitly authorized local tests on 2026-11-23. Existing committed suite files were not changed. Temporary offline checks used in-memory DT+ transport, dummy SQLite LoRA bytes and mocked conversion/Prepare work; no trained weights, real provider requests or UI renders were used.

| Evidence | Result and scope |
| --- | --- |
| Locked runtime readiness | Python 3.13.18, SQLite 3.53.1, Qt/dev imports passed. The standalone setup script reported missing OS packages, but the actual readiness import succeeded. |
| Ruff lint, changed-file formatting, Python compilation | Passed. These are static checks. |
| Existing offline suite, excluding design-capture tools and live markers | 690 passed, 3 failed, 3 deselected in 129.57 s. Two failures explicitly require the removed global exclusion. The queue completion-label assertion also failed once. |
| Focused assets/Qt/queue recheck plus temporary issue checks | 38 passed, 2 deselected in 12.60 s. The two deselections are the obsolete global-lock assertions. The previously failing queue completion-label case passed on this recheck; this is not proof that its timing sensitivity is resolved. |
| Temporary issue-specific checks | 7 passed in 3.48 s, subsequently included in the 38-pass recheck. |

Representative checks and AC mapping:

| AC | Offline evidence |
| --- | --- |
| AC-1 | Three simultaneous managed uploads; unrelated source registration during the hold; two overlapping fake conversions and a third visibly waiting. Civitai's unchanged three-transfer cap inspected in source. |
| AC-2 | Manual batch plus four automatic admissions: three concurrent workers and two queued files, with per-file outcomes. Busy-page automatic admission separately succeeds. Existing Prepare upload/confirmation tests pass. |
| AC-3 | Source registry commit during held uploads; Prepare leaves page busy ownership unchanged, including completion during an unrelated busy call. Generate request snapshots retain their existing suite coverage. |
| AC-4 | Removal waits while an upload owns retained bytes; conflicting retained-name publications keep exactly one verified copy; per-key receipt associations survive separate preparations. Existing changed-content/origin and receipt-failure cases pass. |
| AC-5 | Another writer leaves a live transferring operation intact; simulated owner-lock loss leads to unknown on recovery. Existing interrupted-intent, unconfirmed-upload and metadata-only reconciliation cases pass without automatic re-upload. |
| AC-6 | Before/after mechanism inventory above; no lifetime global writer or process-wide restore mutex. |
| AC-7 | Queued/Waiting progress, single bounded refusal, per-file result callbacks and unchanged explicit retry/re-Prepare surfaces inspected and exercised where covered by existing Qt cases. |
| AC-8 | Local evidence exists, but CI does not yet cover the temporary checks and the unchanged suite still contains two incompatible old assertions. Leave unchecked. |
| AC-9 | Capacity waiter resumes after release; resource timeout ends with an actionable error; cancellation prevents admission; account switch stops queued files; fixture refusal occurs once. |

### Outstanding evidence and suite maintenance

The unchanged tests `test_registry_refuses_newer_schemas_second_writers_and_read_only_changes` and `test_asset_writer_contention_is_a_clear_busy_message` still assert that an unrelated registry writer must reject/block Prepare. That behavior contradicts #548. Their current failures are disclosed, not suppressed or counted as passing. They need separately authorized suite maintenance to replace global exclusion with live-owner preservation and independent-writer assertions; the schema/read-only and user-facing error protections must remain covered.

The temporary checks are local validation, not committed CI coverage. The queue completion-label failure is recorded as intermittent evidence requiring follow-up if it recurs; no unrelated queue change was made. Windows packaged and real DT+ checks remain separately unverified. No model inference, training, real upload, generation or potentially billable action was performed.

# Plan: Phase 3B cloud asset completion

**Status:** PR A–C scope implemented together in one branch and offline-validated (see [phase-3-cli.md](phase-3-cli.md#phase-3b-cloud-half-implemented-scope-and-limits)); live `3-LG4` pending. Originally: ready for implementation after PR #17  
**Milestone:** Complete Phase 3B and unlock Phase 5A desktop generation wiring  
**Authoritative acceptance:** `3B-O6` through `3B-O9` and separately authorized `3-LG4`  
**Related plans:** [Phase 3 CLI](phase-3-cli.md), [LoRA assets](lora-assets.md), [release roadmap](releases.md), [initial roadmap](initial.md)

## Intent

Complete the missing Phase 3B custom DT+ LoRA path before wiring the Qt generation page. The implementation will prepare already-converted Draw Things LoRA files through the verified DT+ BYOM flow, persist honest durable receipts, reuse only server-validated references, and place immutable prepared references into generation and queue requests. Qt remains a consumer of this shared service in Phase 5A rather than becoming the first implementation of upload behavior.

This phase extends the existing metadata-only `/get_byom_urls` lookup. It does not add conversion, training, remote account listing, deletion, storage-balance inspection, or broad compatibility claims.

## Scope

### In

- Prepare one already-converted Draw Things LoRA file using SHA-256, byte size, authenticated BYOM metadata negotiation, and a signed storage PUT when required.
- Handle both verified server branches: `alreadyExists` without transfer and `signedUrls` followed by transfer.
- Stream file hashing and upload with bounded memory on the target Windows machine.
- Persist operation intent before transfer and a versioned durable receipt after confirmed completion.
- Preserve interrupted, ambiguous, and post-transfer persistence failures as explicit unresolved records.
- Reconcile an unresolved or stale record through a fresh authenticated metadata/deduplication check before any new transfer.
- Coalesce concurrent preparation of identical content within one active cloud-auth scope.
- Inspect locally known receipts without presenting them as complete remote inventory.
- Embed immutable prepared asset identity in cloud generation and queue requests.
- Revalidate the prepared reference before dispatch without silently uploading from generation or queue execution.
- Expose the shared workflow through a source-run CLI surface and keep it reusable by Phase 5A.
- Add authoritative offline coverage for `3B-O6` through `3B-O9` and define the separately authorized `3-LG4` live gate.

### Out

- Safetensors or other model conversion, LoRA editing, training, or metadata authoring.
- Uploading arbitrary model/checkpoint types; the first supported asset kind is a converted Draw Things LoRA.
- Remote list/delete operations or authoritative storage usage, because no verified public contract exists.
- Automatic transfer retry after an uncertain PUT or failed receipt write.
- Hidden upload during `generate`, `queue add`, `queue start`, preflight, or Qt interaction.
- Treating upload success, plain generation success, or filename matching as proof that a LoRA was applied.
- Broad model/LoRA compatibility, indefinite retention, or cross-account reuse guarantees.
- Phase 5A widget wiring, Phase 5B queue presentation, or packaged Windows release work.

## Entry conditions and preserved contracts

- PR #17 is merged and the branch begins from clean, current `main`.
- Existing credential precedence remains `DRAWTHINGS_API_KEY` first, then the saved Windows credential for Qt callers; failure never switches sources.
- The API origin and selected credential stay fixed for one cloud-auth client lifetime.
- Existing `/get_byom_urls` metadata lookup and LoRA inventory evidence remain non-uploading.
- Signed URLs are temporary credentials. They must never enter logs, exceptions, receipts, generation metadata, queue records, or CLI JSON.
- Provider HTTP payloads and signed-upload details remain inside `dtflow.backends.drawthings`.
- Core, persistence, and services use backend-neutral immutable values and protocols.
- Generation and queue requests remain reproducible and never mutate a selected LoRA silently.
- Ordinary offline tests never contact DT+ or submit generation.

## Delivery sequence

### 1. Freeze the supported protocol subset

- Extend the current BYOM fixtures around the observed request shape: `file`, lowercase SHA-256, and byte `size`.
- Preserve the existing narrower deployed finding that `sha256` is required for reuse; do not generalize one observed response into a universal deduplication key.
- Accept only consistent `alreadyExists` and `signedUrls` branches. Reject missing, conflicting, malformed, or identity-mismatched responses with sanitized protocol failures.
- Record the remote file reference returned by the server, not the signed URL.
- Keep HTTPS verification enabled for production. Fixture endpoint and trust injection remain test-only seams.

### 2. Add backend-neutral asset values and service contracts

Introduce immutable values in `src/dtflow/core/models.py` for:

- local content identity: asset kind, display/local identifier, lowercase SHA-256, byte size, and optional metadata provenance;
- prepared reference: backend ID/API origin, exact remote identifier, checksum, size, stable local receipt ID, and confirmation timestamp;
- preparation state: `intent`, `transferring`, `confirmed`, or `unknown`, with a sanitized reason/evidence code;
- reconciliation result that distinguishes confirmed reuse, upload-required, unavailable, and unknown.

The values must reject empty identifiers, invalid hashes, negative or non-integer sizes, mutable metadata, and provider secrets. Provider response dictionaries do not cross the backend boundary.

Add a narrow protocol used by `src/dtflow/services/assets.py`:

- negotiate current storage state for one content identity;
- stream a required upload to the returned ephemeral destination;
- validate a prepared reference against the current authenticated scope;
- expose no remote list or delete method.

### 3. Create an independent durable asset registry

Add `src/dtflow/persistence/assets.py` backed by `<state>/assets.sqlite3`, separate from disposable inventory cache and from execution attempts.

Schema version 1 stores:

- stable local asset and operation IDs;
- asset kind, local identifier, exact checksum and size;
- backend ID and API origin;
- remote identifier/reference only after server acknowledgement;
- state, sanitized evidence code, timestamps, and metadata provenance;
- whether the latest confirmation came from transfer or already-existing reconciliation.

Persistence rules:

- enable WAL, `synchronous=FULL`, foreign keys, and short transactions;
- write intent before negotiation/transfer and commit confirmed receipt atomically afterward;
- reject unsupported/newer schemas without reset;
- never store credentials, credential-derived fingerprints, authorization headers, signed URLs, or local file contents;
- retain unknown records across restart and cache cleanup;
- expose deterministic list/show queries for local receipts and unresolved operations.

Account isolation does not rely on a persisted API-key fingerprint. A receipt is only a local candidate. Every new process or credential context must perform the authenticated metadata check and accept reuse only when the current server response validates the same checksum/reference. If the current scope cannot confirm it, the receipt is not reusable and no hidden transfer occurs.

### 4. Implement bounded preparation in the shared service

`src/dtflow/services/assets.py` owns orchestration while the adapter owns HTTP details:

1. Validate the file exists, is regular/readable, and matches the initially observed size throughout preparation.
2. Calculate SHA-256 incrementally; never load the full file into memory.
3. Persist preparation intent.
4. Negotiate with DT+ using the selected credential.
5. If `alreadyExists` validates the same checksum, persist a confirmed receipt without PUT.
6. If upload is required, stream the file once to the signed URL without DT+ authorization headers.
7. Require a successful terminal HTTP result, then persist the confirmed receipt.
8. Return an immutable prepared reference.

Safety and uncertainty rules:

- A failure before any PUT begins is definitely not transferred and may be retried explicitly.
- Cancellation, timeout, disconnect, or ambiguous response after PUT may have started produces `unknown`; no automatic re-upload.
- Successful PUT followed by failed receipt persistence also produces local unknown evidence and never claims the remote upload failed.
- A changed file during hashing/upload fails safely and cannot reuse the earlier digest.
- Reconciliation always performs a fresh metadata check first. It may confirm reuse or report upload-required/unknown, but it does not transfer bytes unless the user starts a new explicit prepare operation after acknowledging the prior uncertainty.
- In-process concurrent prepares with the same API origin, active auth client, checksum, and size join one shielded operation. Different content, API origins, or auth-client scopes never coalesce.
- Cancellation of one waiter must not cancel a shared preparation still awaited by another caller.

### 5. Extend the DT+ adapter without leaking signed credentials

Expand `src/dtflow/backends/drawthings/cloud_assets.py` and `DirectCloudBackend` to:

- request signed upload metadata with filename, SHA-256, and size;
- parse and validate `alreadyExists` and signed URL records;
- stream the file with bounded chunks and explicit connect/write/read/total deadlines;
- omit cloud authorization and unrelated headers from the signed-storage PUT;
- cap response bodies and sanitize all errors before they reach services;
- redact query strings and the complete signed URL from logs and diagnostics;
- distinguish verified authentication, storage refusal, malformed protocol, timeout, and unknown transfer outcome without inventing quota semantics;
- close upload clients through the existing bounded cleanup discipline.

Do not add upload methods to the general generation `Backend` protocol. Use a separate asset-capable protocol so fake/local generation adapters are not forced to pretend they support cloud storage.

### 6. Define explicit CLI operations

Add a top-level `assets` command in `src/dtflow/cli.py` with the current canonical option conventions:

- `dtflow assets prepare -f/--file PATH [-n/--name IDENTIFIER]`: explicit potentially network-writing preparation; cloud only.
- `dtflow assets list`: list local known receipts and unresolved operations from the selected state directory; no network.
- `dtflow assets show ASSET_ID`: inspect one credential-free local record; no network.
- `dtflow assets reconcile ASSET_ID`: perform metadata-only server validation under the currently selected credential; never PUT.

CLI output must identify `confirmed`, `upload_required`, or `unknown`, include checksum/size/reference where safe, and state that list/show are local registry views rather than remote account inventory. Help text must disclose that `prepare` may upload bytes and that reconciliation does not.

The command never accepts an API key on the command line. It uses the existing environment-only headless credential path and existing state/log options.

### 7. Bind prepared references to generation and queues

- Add an immutable backend-neutral prepared-asset reference collection to the resolved generation request while preserving the existing LoRA identifier and weight.
- Keep user configuration export credential-free and portable: it retains LoRA identifier/weight but does not silently turn an account-scoped receipt into a portable preset.
- Add an explicit CLI selection for a prepared local receipt when cloud LoRAs are requested; resolve it before creating an attempt or queue item.
- Store the exact checksum, size, remote reference, backend origin, and receipt ID in the immutable request serialized for attempts/queue items.
- `queue add` remains non-uploading. It may bind an already confirmed local receipt, but it must fail before queue creation if no matching prepared reference exists.
- Immediately before cloud dispatch, perform metadata-only validation through the asset service. A stale, inaccessible, changed, or unconfirmed reference blocks that item clearly and performs no PUT.
- Never replace the requested LoRA, remove it, alter its weight, or fall back to a different receipt.
- Keep local Draw Things generation unchanged; prepared DT+ references are rejected for local/fake backends.

### 8. Add authoritative offline evidence

Implement contract-focused tests without duplicating token, TLS, generation-wire, queue-scheduler, or persistence matrices already owned elsewhere.

#### `3B-O6` — prepare/upload/reuse

- One fixture scenario covers POST → signed PUT → confirmed durable receipt → restart → metadata-validated reuse with no second PUT.
- The already-existing response branch performs no PUT and persists the exact server reference.
- Assert streamed content checksum, size, remote identifier, bounded request/response handling, and receipt fidelity together.

#### `3B-O7` — identity and concurrency

- Concurrent identical preparation coalesces to one negotiation/transfer.
- Changed bytes under the same filename create a distinct identity.
- Same content under a different API origin or active auth scope does not share an in-flight operation.
- A cancelled waiter does not cancel the shared operation for remaining waiters.

#### `3B-O8` — uncertainty and sanitization

- Cover definitely-not-started failure, ambiguous transfer interruption, successful transfer followed by failed receipt write, and restart with unresolved intent.
- Prove no automatic second PUT occurs.
- Reconciliation confirms reuse only from a consistent authenticated response.
- Cover representative auth/storage refusal and malformed/conflicting responses at the lowest authoritative layer.
- Put conspicuous fake API keys and signed URLs into fixtures and prove they are absent from logs, durable state, CLI output, and raised public errors.

#### `3B-O9` — CLI and queue composition

- Prepare/list/show/reconcile report local registry semantics honestly.
- Generation and queue requests receive the exact immutable prepared reference.
- `queue add` and `queue start` never upload implicitly.
- Stale/inaccessible/unknown receipts fail clearly before generation submission.
- Existing queue ordering, restart, uncertainty, and scheduler tests remain authoritative and are not repeated.

Run the repository checks after implementation:

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Add focused Windows execution only where filesystem locking, SQLite durability, credential selection, or streaming behavior differs from Linux fixtures.

### 9. Run the separately authorized live gate

`3-LG4` is opt-in and may upload data and consume generation credits. Never run it as part of ordinary validation.

Required evidence for the advertised subset:

- exact clean source commit/build identity;
- exact converted LoRA filename, SHA-256, byte size, declared metadata source, model, parameters, and backend identity;
- first prepare outcome: uploaded or server-acknowledged existing;
- durable receipt and application restart;
- second preparation/reconciliation showing server-validated reuse without another transfer;
- one bounded cloud generation using the prepared reference;
- durable output and metadata;
- trustworthy evidence available from the backend/application about LoRA use, with `application_unconfirmed` retained if application cannot be proven.

A receipt, upload response, or plain txt2img success alone does not pass `3-LG4`. If trustworthy application evidence is unavailable, record that exact limitation and do not advertise confirmed custom-LoRA support.

### 10. Documentation and Phase 5A handoff

Update together with implementation:

- `README.md` current CLI and safety boundaries;
- `AGENTS.md` only if a durable asset-layout or workflow rule becomes repository-wide;
- `devdocs/architecture/cloud-integration.md` and `cloud-protocol.md` with implemented response/transfer behavior;
- `devdocs/architecture/runtime-layout.md` with `assets.sqlite3` and cleanup guarantees;
- `devdocs/plans/phase-3-cli.md`, `lora-assets.md`, and `initial.md` with actual completion/evidence;
- `devdocs/releases/unreleased.md` with verified behavior and explicit remaining limits.

Phase 5A may begin only after `3B-O6` through `3B-O9` pass offline. The Qt generation page will call the same asset service for explicit prepare/reconcile and render receipt/uncertainty states; it will not own HTTP upload, durable receipt rules, or compatibility decisions. `3-LG4` is required before custom cloud LoRA support is advertised in the desktop release, but it is not required merely to start non-billable Qt composition work.

## Pull-request sequence

Keep the phase reviewable rather than combining protocol, persistence, CLI, queue composition, and Qt wiring in one change.

1. **PR A — Asset contracts and durable registry**  
   Immutable models, `assets.sqlite3`, schema/refusal behavior, intent/receipt/unknown persistence, and focused storage tests.
2. **PR B — DT+ preparation and reconciliation**  
   BYOM negotiation, signed PUT streaming, sanitization, coalescing, shared asset service, and `3B-O6` to `3B-O8` fixture evidence.
3. **PR C — CLI and queue references**  
   `assets` commands, prepared-reference request binding, pre-dispatch validation, and `3B-O9` composition evidence.
4. **Gate — Final Phase 3B validation**  
   Full offline checks, optional separately authorized `3-LG4`, documentation/evidence closure, and explicit decision on whether custom cloud LoRA can be advertised.
5. **Next phase — Phase 5A** (planned in [2026-10-04-phase-5a-desktop-generation.md](2026-10-04-phase-5a-desktop-generation.md))  
   Wire generation/configuration/LoRA presentation to the shared services, followed by Phase 5B queue presentation.

Each PR starts from current `main`, opens as draft after its first coherent commit, updates this plan with decisions, and is squash-merged only after its owned checks and review evidence are complete.

## Risks and mitigations

- **Signed URL leakage:** keep URL parsing/transfer inside the adapter, redact query strings, and assert absence from all outputs.
- **Duplicate remote storage after ambiguity:** persist intent first, prohibit automatic transfer retry, and require metadata reconciliation.
- **False cross-account reuse:** never trust a local receipt alone; require current authenticated server validation.
- **File mutation during upload:** compare observed identity and fail rather than recording a mismatched receipt.
- **UI or queue hidden side effects:** keep upload exclusive to explicit `assets prepare`; generation and dispatch only validate.
- **SQLite coupling:** use an independently versioned asset registry so asset evolution cannot reset attempts or queues.
- **Target-machine memory pressure:** incremental hashing and streaming transfer; no full-file buffering.
- **Overstated compatibility:** preserve compatibility, availability, storage, and runtime-application evidence as separate states.

## Exit criteria

Phase 3B cloud assets are complete only when:

- `3B-O6` through `3B-O9` pass through the documented authoritative layers;
- a clean restart can inspect and safely revalidate confirmed and unknown records;
- no path blindly retries an ambiguous transfer or hides an upload in generation/queue dispatch;
- signed URLs and credentials are absent from durable state, logs, CLI output, and public errors;
- cloud generation/queue requests carry immutable prepared references and reject stale/unknown references before submission;
- docs distinguish local receipts from remote inventory and retain all unsupported operations;
- full offline repository checks pass on the implementation head;
- live `3-LG4` is either recorded with exact evidence or explicitly left unverified without support claims.

## Open questions to resolve in PR A/B

- Confirm the exact remote identifier expected by the generation wire for the supported converted LoRA and pin it with fixture plus deployed evidence before advertising use.
- Determine an evidence-based transfer-size policy/server refusal mapping without inventing a quota from unrecognized responses.
- Confirm whether the signed storage endpoint requires any non-secret content headers beyond content length/type; never forward DT+ authorization.

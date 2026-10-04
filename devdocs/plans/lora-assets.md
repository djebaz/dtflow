# LoRA compatibility and custom cloud assets

**Status:** Planned; no runtime capability or deployed API validation is introduced.  
**References:** [Phase 2](phase-2-backends.md), [Phase 3](phase-3-cli.md), [roadmap](initial.md), [parity matrix](tanquestudio-parity.md), [release gates](releases.md), [source findings](../research/dtflow-ecosystem-research-report.md#appendix-e--lora-compatibility-and-cloud-storage-follow-up).

## Delivery sequence

- Phase 2: define metadata/preflight and separate asset-storage contracts; audit converted-file metadata and generation-reference requirements; prepare offline fixtures.
- Phase 3A / proposed 0.1.0: structured local model/LoRA discovery, exact selections/weights and shared compatibility diagnostics for a bounded text-to-image subset.
- Phase 3B / proposed 0.2.0: custom direct-cloud upload/reuse through CLI/services, durable receipts and queued references; pass offline and separately authorized deployed upload/use gates before Qt.
- Phase 5A / proposed 0.3.0: Qt selection/weights, diagnostics and upload/known-receipt reuse call those existing services.
- Later: arbitrary safetensors conversion, LoRA editor/training, broad combinations and remote listing/deletion/storage inspection only when their contracts are verified.

If the agreed converted-file or deployed cloud path is blocked, record the precise missing evidence and resolve it before declaring the pre-Qt gate complete. Do not silently defer the feature to Phase 11 or claim universal LoRA support.

## Evidence distinctions

The owner reports that the iOS app usually uploads a custom LoRA once and reuses server storage. Official LoRAStore source independently implements signed upload and an already-existing branch, supporting a deduplication design. This does not establish indefinite retention, cross-account reuse or deployed DTFlow behavior.

TanqueStudio can enrich LoRA metadata from custom_lora.json, including version, prefix and default weight. Its inspected picker filters by name/filename and add action prevents duplicates; no model-family comparison was found in that selection path. DrawHeadlessThings provides the stronger design reference: installed-asset checks, architecture comparison and indexed typed diagnostics. Use its design as a reference. Its GPL source is no longer blocked by an MIT/GPL project mismatch because DTFlow is GPL-3.0-only, but any direct incorporation still needs deliberate provenance, attribution and notice review.

Official Draw Things generation uses architecture matching against base/refiner versions and an explicit exception, and can omit unmatched entries. Its LoRAZoo version helper defaults unknown entries to SD v1; DTFlow must retain unknown instead of adopting that fallback. Architecture match, file availability and confirmed runtime application are separate evidence.

## Shared metadata and compatibility service

Preserve exact provider file/asset identifiers; model-family version; relevant modifier; explicit weight; backend/server identity; source/revision and observation time. Preserve checksum, size and imported metadata provenance when available. User-supplied architecture is a declaration, not inspected tensor evidence. Trigger prefixes remain explicit request-resolution inputs; do not silently modify prompts.

Use structured gRPC Echo metadata through drawthings-py when exposed. Missing structured metadata leaves raw files unclassified or architecture unknown. Do not infer compatibility from a filename, file extension, catalog presence or upload success. Preserve unknown future version strings rather than mapping them to a known family.

Preflight should return all detected problems with code, severity, field/item identifier, reason and evidence source. Keep architecture match/mismatch/unknown separate from availability present/absent/unknown and combination-validation evidence. Reject confirmed incompatible selections before generation. Unknown selections require an explicit supported-scope policy; never silently omit a selected LoRA or label it compatible.

Pin backend-specific official rules. Base/refiner/exception logic needs focused fixtures where exposed; do not port a coarse capability grouping into LoRA compatibility. Same-family metadata does not prove tensor shapes, conversion format, quantization or every runtime combination works.

Run preflight at request creation and immediately before queued dispatch using available refreshed evidence. Revalidation may block an item but must not silently replace its model/LoRA or alter an immutable resolved request. Keep requested identifiers/weights and evidence in reproducibility metadata. Mark application as confirmed only if trustworthy backend evidence supports it; successful image generation alone is insufficient.

## Separate cloud asset service

Share the Qt-independent credential resolver and expiry-aware token provider with generation. Keep provider HTTP/upload types in the backend integration. Upload is a storage operation, not a generation RPC or resumable generation job.

Initial input is an already converted Draw Things LoRA file. The official guide demonstrates conversion separately and uploading the resulting ckpt file; LoRAStore itself does not validate its architecture or prove model compatibility. Audit the supported format and required model/LoRA metadata, including how custom identifiers reach cloud generation, before implementation acceptance. Do not claim native Python conversion or a Swift runtime requirement from the upload interface alone.

The inspected source performs:

1. SHA-256 and byte-size calculation for the file.
2. Authenticated POST to /get_byom_urls with files entries containing file, sha256 and size.
3. If signedUrls is returned, PUT the file to the supplied signed URL.
4. If alreadyExists is returned, complete without transferring file bytes.
5. Return a file identifier/checksum; the wrapper's receipt also records size and has an empty delete key.

Model fixture responses on the inspected structures, but do not inherit its generic fallback that labels every otherwise-unhandled payload as storage-limit exhaustion. Classify verified storage refusal distinctly; keep malformed/unknown responses generic and sanitized. Validate response identity/checksum consistency without inventing the exact server deduplication key or retention guarantee.

Use bounded-memory file hashing and streaming transfer suitable for the target Windows machine. Upload explicitly during asset preparation; reuse the prepared reference across generations and queue items. Coordinate simultaneous preparation of identical content within the same backend/account scope so ordinary concurrency does not trigger redundant transfers. Changed content requires fresh preparation, even under an unchanged filename.

Never send cloud authorization headers to the signed storage endpoint. Treat signed URLs as temporary credentials: keep them out of logs, durable receipts, ordinary metadata and error messages. Production TLS remains validated; injectable endpoints/trust are for local fixtures only.

## Durable receipts and uncertainty

Maintain a versioned durable registry of locally known uploads, separate from disposable cache and generation attempts. Record a stable local asset ID, backend/API origin and non-secret account scope, exact remote file identifier, local SHA-256/size, metadata provenance, upload outcome and timestamp. Define a safe account-scope mechanism without persisting an API key/token; isolate records if account identity cannot be established. Credential rotation must not imply account equivalence.

Persist operation intent before transfer and atomically save the confirmed receipt afterward. A failed receipt write after possible upload is a local persistence failure with possible remote storage, not proof the upload failed. Interrupted or ambiguous uploads remain unknown; do not automatically resend file bytes or fabricate completion.

Permit explicit reconciliation using a verified metadata/deduplication check before any further transfer. A safe repeat-check policy must be established from actual response semantics; it is not generation resubmission. Without reconciliation evidence, preserve uncertainty.

Receipts survive restart and cache cleanup. They record past server acknowledgement, not perpetual remote availability. Reuse needs a documented validation policy; stale/deleted/inaccessible assets fail clearly without silently changing the request or starting a hidden upload. Queue requests preserve immutable checksum/identifier references and cannot depend solely on a mutable local filename.

The inspected LoRAStore.list explicitly throws because no public list endpoint is exposed. Upload returns no usable delete key; deletion requires keys not obtainable from that receipt. Expose local known-upload inspection clearly, never complete account listing. Remote listing, deletion and authoritative remaining storage stay unavailable until independently verified.

## Acceptance ownership

Use the [Phase 3 scenario plan](phase-3-cli.md#testing-and-acceptance) as the authoritative acceptance mapping; do not create a second CLI/integration matrix from the behavioral requirements above.

| Phase 3 owner | LoRA/asset evidence |
| --- | --- |
| 3A-O5 | Structured compatibility/preflight: match/mismatch/unknown, bounded official exception/refiner rules, availability distinction and rejection before dispatch. Reuse existing inventory parsing/refresh evidence when unchanged. |
| 3A-O6 | Exact identifiers/weights, explicit prefix behavior, configuration round trips and resolved-request/pre-dispatch fidelity. |
| 3B-O6 | Signed upload and already-existing/no-PUT branches, bounded-memory transfer, checksum/size/identifier fidelity, durable receipt and validated restart/reuse. |
| 3B-O7 | Concurrent preparation coalescing, changed content and backend/account isolation. |
| 3B-O8 | Both transfer uncertainty and post-transfer receipt-write failure, representative storage/auth refusals, response/identity validation, secret/signed-URL redaction and explicit reconciliation without blind retry. |
| 3B-O9 | Immutable queue/CLI references without hidden repeated transfers; stale/inaccessible receipt handling and honest known-upload inspection without remote list/delete claims. |
| 3-LG4 | Separately authorized actual cloud preparation/reuse plus supported LoRA generation, individually selectable when implemented. |

Small parametrized parser/service tests own genuinely new validation logic; higher-level asset/CLI scenarios do not repeat cloud-token, TLS or shared wire matrices. Unexpected-network refusal remains shared infrastructure. All behavioral requirements above remain mandatory, but several requirements may share one scenario. These groups are planned, not implemented tests or deployed evidence.

For the advertised subset, record actual model/LoRA versions, effective request, source/build identity and trustworthy application evidence through the release implementation. A plain txt2img smoke, receipt or mocks alone cannot establish LoRA application. No generation, upload, model download or billing-affecting call is needed merely to edit or validate this plan.

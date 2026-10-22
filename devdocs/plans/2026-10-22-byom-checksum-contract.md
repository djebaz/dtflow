# Plan: correct the BYOM upload checksum contract

## Scope

Fix DTFlow's unsupported equality requirement between the locally computed SHA-256 and the server-returned `signedUrls.urls[filename].sha256`. Add safe format/comparison evidence before consuming the upload entry. Keep local content hashing, safe destinations, explicit upload consent, post-upload receipt confirmation and no automatic retry.

## Evidence

- Owner runtime warning identifies `upload_checksum_mismatch` before submission; the redacted body does not expose the returned value.
- Official [LoRAStore.swift](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/MediaGenerationKit/Sources/LoRAStore.swift#L167-L179) computes a lowercase hex SHA-256 for the request. [Response consumption](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/MediaGenerationKit/Sources/LoRAStore.swift#L263-L279) forwards the response field as `serverSha256` to upload completion without requiring equality to the request digest. [Completion](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/MediaGenerationKit/Sources/LoRAStore.swift#L286-L308) returns that server value.
- Retrieved upstream blob: `1e166763ea86049079f8c12a4e455ec445572461`. No upstream code or live provider was executed.
- This demonstrates that DTFlow's equality rejection is stricter than the official client. It does not prove the encoding or meaning of the owner's hidden returned value, successful upload, or generation compatibility.

## Action items

- [x] Accept usable server-returned upload references independently of local checksum equality in negotiation and metadata lookup.
- [x] Record bounded non-identifying type/length/format/comparison diagnostics before consuming upload evidence.
- [x] Synchronize contract documentation, preview definitions, status and release notes.
- [ ] Commit/push a new PR; do not merge.

## Decisions and acceptance

- The local SHA-256 remains local content identity and the key for subsequent metadata checks. Server-returned values stay private and are not substituted into the local digest or exposed in logs.
- Keep destination HTTPS validation, streamed local re-hashing, durable outcomes and fresh post-upload metadata confirmation. No inferred receipt from a successful PUT alone.
- No tests added or run, validation, UI renders, package builds or live calls. Existing tests that assert mismatch rejection reflect the superseded assumption; owner validation must reconcile those expectations with the official contract.

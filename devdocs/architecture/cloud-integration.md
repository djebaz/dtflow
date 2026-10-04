# Direct DT+ integration (Phase 2C)

**Status:** implemented against HTTP mocks and the existing scripted TLS/gRPC fixture. The owner reports a passing
Windows offline suite and successful deployed token/inventory and bounded generation checks through this adapter,
using `z_image_turbo_1.0_q6p.ckpt`. This is user-reported evidence for that workload, not broad model compatibility.
The latest runtime/evidence changes still need a Windows repeat; broad model/LoRA compatibility, production
cancellation latency, charging/refunds and packaged execution remain unverified.
See the [pinned protocol](cloud-protocol.md) and [Phase 2 plan](../plans/phase-2-backends.md).

## Implementation and probe reuse

`cloud_auth.py` follows the probe's `/sdk/token`, `/authenticate`, raw short-token Authorization header,
contents-free protobuf blob, `fromBridge: true`, and lowercase gRPC `bearer` metadata. Optional generation-auth
fields are omitted as specified by the official Codable path, rather than sent as null by the probe. No PAYG or
billing lookup is performed. Tokens and blobs never enter durable metadata or public errors.

The deliberate production changes are scoped async HTTPX instead of diagnostic `to_thread(urllib)`, bounded HTTP
responses/deadlines, and expiry-aware shared refresh. The API origin and selected credential are fixed per auth
client. The selected API key is resolved once and cached for that client's lifetime: changing `DRAWTHINGS_API_KEY`
does not update an existing `CloudAuth`; close it and create a new client. A valid cached token is reused; concurrent refresh callers join one shielded task. Cancellation of one waiter
does not cancel that task. Each generation obtains its own authorization; failures do not silently retry or switch
credentials. HTTP uses normal verified HTTPS; gRPC explicitly chooses system trust with h2, exactly the probe baseline.
No cloud certificate pinning or local bundled-CA exception is introduced.

`DirectCloudBackend` inherits the actual `DrawThingsBackend.generate` implementation, including channel creation,
wire configuration, codec validation, stream completion, previews, deadlines and failure evidence. Its hook authorizes
the same built message before the submission boundary and supplies its metadata to the shared stream. Echo obtains
a short token but carries no generation bearer. Health reports API authentication separately from transport and
inventory; it never requests generation entitlement. A successful token exchange is not a quota or billing check.

The cloud backend supports a configurable client cap of 1–4 (default 4), using the shared semaphore; local remains
one lane. This is client support and a product cap, not a server entitlement or the Phase 3B rolling scheduler.
Only discovered model IDs are admitted. Generation scope remains txt2img, batch 1/1, the two existing samplers, no LoRA generation or uploads.
Separate metadata-only LoRA availability enrichment uses local `FilesExist` identities and DT+ BYOM lookup. It
validates returned checksum/entry structure and retains source observation times/freshness separately from resolution
time in `lora_inventory.json`. Fresh negative file-existence evidence overrides older Echo availability.
CLI generation backend selection remains Phase 3A. The test runner adds an optional DT+ token/inventory check
under `-live` and one complete, bounded DT+ service generation under `-gen`; these are skipped by default.
Both use the [shared live-run layout](runtime-layout.md), outside `.dtflow/`, with versioned inventory snapshots
and one credential-free summary. The local live check uses the same run root.

## Cancellation, ownership and limits

The generation total deadline includes credential preparation, lane admission, connection, authorization, stream and
decode. Authorization failure is `not_submitted` / `not_started` for GenerateImage; its HTTP effects are not inferred.
A pending task cancellation is checked after authorization so even a late mock response cannot revive RPC dispatch.
After possible send, shared 2B evidence rules apply: disconnect without validated completion is unknown, no retry.
Terminal OK with valid required outputs survives later terminal-event notification failure; services still save durably.

Callers cancel and await owned operations before closing the backend. Close refuses active generations, then closes
the channel, requests refresh cancellation, and waits within its cleanup budget. A `finally` path always attempts
HTTP pool closure with a separate cleanup budget (default 5 s for each stage). A delayed/non-cooperative refresh
produces a cleanup timeout without skipping HTTP closure; repeated close is harmless once the pool is closed. Closed auth clients
cannot accept late tokens. The existing service conservatively persists potentially executing state before calling
an adapter; task cancellation without returned failure evidence may therefore leave an attempt unknown even when
an offline test proves authorization prevented dispatch. No remote cancellation confirmation is claimed.

HTTP responses are streamed with a decoded-body cap; headers, errors and response bodies are not logged. Existing
[received-tensor and worker limits](drawthings-integration.md#limits-enforced-and-not-enforced) remain unchanged,
including grpclib's whole-message receipt and fpzip allocation limitations. No new transport limit is claimed.

## Acceptance ownership

| Scenario group | Evidence in the implementation |
| --- | --- |
| Successful generation | `test_drawthings_cloud.py`: real service + SQLite + HTTP mocks + existing gRPC fixture; exact blob/headers/PAYG omission/bearer, two per-request authorizations and saved outputs |
| Token lifecycle/concurrency | `test_cloud_auth.py`: mocked time, reuse/expiry, shared refresh, cancelled waiter, failed refresh and immutable credential selection |
| Authentication failure | Representative token/auth refusal prevents dispatch; conspicuous fake secrets absent from public failures and captured logs; parser variants are parametrized unit cases |
| Submission uncertainty/cancellation | Auth cancellation with a late response cannot submit; post-send connection loss stays unknown and is not retried |
| Health/inventory | Token acquisition and Echo without `/authenticate` or GenerateImage; cloud inventory provenance |

The unchanged 2B tests own serialization, codecs, TLS policies, generic stream statuses/cleanup/deadlines and stronger
completion evidence because cloud executes those same functions. HTTP-only tests own response validation/size,
HTTP and total deadlines, shared-refresh shutdown and no automatic retry. No separate cloud simulator is added.
Token expiry/concurrency use clocks/events, not sleeps. Runtime measurements belong to the implementation PR.
Mocks prove this composition, not deployed compatibility, entitlement, cancellation latency or charging/refunds.

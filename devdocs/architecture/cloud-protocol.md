# Direct DT+ cloud protocol specification

**Status:** Pinned source contract implemented in Phase 2C with offline fixtures; deployed adapter compatibility remains unverified. See [implementation and acceptance ownership](cloud-integration.md).  
**Related:** [backend contract](backend-contract.md), [Phase 2](../plans/phase-2-backends.md), [recorded probe](../research/drawthings-grpc-proof.md), [LoRA assets](../plans/lora-assets.md).

## Evidence and scope

“Official source” below means client code inspected at draw-things-community commit b5e9fb925ca5394b747e0e98a8c293bbab5091ca, not a complete server specification. “Observed probe” means DTFlow source at d8e28fa9697b4ca251aabeef80dc8072275660d2, supported separately by user-reported controlled runs in the proof document. “DTFlow choice” is a specified implementation policy. Unknown production behavior remains unverified.

| ID | Pinned source | Contract evidence |
| --- | --- | --- |
| S1 | [CloudConfiguration](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/MediaGenerationKit/Sources/CloudConfiguration.swift) | API base URL, App Check none, reference refresh threshold 300 s and token HTTP timeout 30 s |
| S2 | [CloudAuthenticator](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/MediaGenerationKit/Sources/CloudAuthenticator.swift) | Token request/response, expiry cache and coalesced refresh |
| S3 | [Cloud authentication](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/MediaGenerationKit/Sources/MediaGenerationCloudAuthentication.swift) | Generation authentication fields, response and reference PAYG behavior |
| S4 | [Remote generator](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/RemoteImageGenerator/Sources/RemoteImageGenerator.swift) | Serialize authentication blob before attaching contents; gRPC bearer metadata |
| S5 | [Remote executor](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/MediaGenerationKit/Sources/MediaGenerationRemoteExecution.swift) | fromBridge true; authentication handler; TLS handshake and discovered model policy |
| P1 | [DTFlow probe](https://github.com/djebaz/dtflow/blob/d8e28fa9697b4ca251aabeef80dc8072275660d2/tools/drawthings/drawthings_concurrency_test.py) | DirectCloudAuth, _request_json, authenticated stub and direct TLS service; subscription-only null fields |
| H1 | [HTTPX async guide](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/docs/async.md), [timeouts](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/docs/advanced/timeouts.md) | AsyncClient lifetime/stream closure and separate connect/read/write/pool timeout APIs |

The initial implementation covers API-key authentication and controlled txt2img. It does not implement Google/account login, App Attest/Firebase/Supabase, PAYG, remote job lookup or arbitrary input/LoRA compatibility. LoRA storage is a separate HTTP service delivered in Phase 3B.

## Endpoint and transport contract

| Operation | Endpoint | Authentication | Evidence |
| --- | --- | --- | --- |
| Short-term token | POST https://api.drawthings.ai/sdk/token | API key in JSON; no generation bearer header | Official S1/S2; probe P1 |
| Generation authorization | POST https://api.drawthings.ai/authenticate | Authorization: short-term-token (raw token, without Bearer prefix) | Official S3; probe P1 |
| GenerateImage | TLS gRPC compute.drawthings.ai:443 | authorization metadata value bearer followed by a space and gRPCToken | Official S4; endpoint and direct connection observed in P1 |
| Non-generative discovery | Echo on configured gRPC server | Follow inspected Echo/shared-secret contract; do not attach a generation token without evidence | Official S5; probe P1 |

HTTP requests use Content-Type: application/json. Hostnames/certificates are verified. API origin and gRPC endpoint are independently injected for fixtures; changing one does not silently redirect the other. No signed-storage endpoint is contacted in Phase 2.

Cloud TLS follows the working probe baseline: DirectCloudService creates ssl.create_default_context() and enables h2, so the cloud adapter explicitly selects system trust with its normal certificate/hostname checks. HTTP likewise uses normal verified HTTPS. Do not add certificate pinning, extra TLS identity checks or a separate security policy beyond that baseline without an evidenced requirement. The local Draw Things bundled-CA/hostname exception is not used for DT+.

DTFlow choice: connection checks may acquire a short-term token and perform Echo, but never call /authenticate or GenerateImage merely to report health. Return separate API-key authentication, gRPC reachability and inventory status. Token acquisition proves that request was accepted; it does not establish generation entitlement or remaining quota.

## Short-term token exchange

Official S2 encodes:

| Request field | Type | Initial value/source |
| --- | --- | --- |
| apiKey | string | Selected credential; never persisted/logged |
| appCheckType | string | none for initial API-key path (S1/P1) |
| appCheckToken | optional string | nil in S1/S2; P1 sends JSON null |

Official success requires HTTP 200 and decodable fields shortTermToken (string), expiresIn (integer seconds). S2 caches expiry relative to the time the successful response is handled. Its reuse condition is remaining lifetime greater than the configured tokenRefreshThreshold; concurrent callers join the same in-flight request for the active App Check revision. Reference defaults are threshold 300 s and request timeout 30 s, not server guarantees.

DTFlow choices:

- Send appCheckToken as JSON null in the initial path, matching the successful probe; reject empty selected API key before network I/O. No fallback to another credential after failure.
- Require a nonempty token and positive integer expiresIn; reject booleans, missing/wrong fields, non-object JSON, malformed JSON and non-200 responses. Do not cache a partially valid response.
- Use monotonic expiry measured conservatively from HTTP request start plus expiresIn, not wall-clock expiry. A response already expired by that measure fails clearly.
- Default refresh margin is 300 s, capped at half the advertised lifetime so short-lived tokens are not immediately refreshed forever. Cache reuse requires remaining lifetime strictly greater than that effective margin.
- Authentication scope (selected credential, API origin and App Check configuration) is immutable for the lifetime of one client. Resolve the credential once; changing scope requires closing that client and creating a new one with an independent cache. Runtime scope switching and old-scope response reconciliation are not implemented.
- Coalesce refresh within that client and its owning loop using one lock and an in-flight task. Shield that shared task from cancellation of one waiter; another waiter may still need it. Adapter shutdown cancels and awaits it. Never persist tokens. A refresh failure produces a typed sanitized failure, no generation submission.
- Use explicit expiry refresh and no automatic HTTP retries initially. No generation resubmission follows a token failure.

The probe caches only the token string and does not consume expiresIn. It is not the production cache implementation or an expiry test.

## Authentication blob and per-generation authorization

Official S4 serializes ImageGenerationRequest while contents is empty, then attaches transfer contents for the actual RPC. S5 passes those bytes as Base64 to S3. P1 deep-copies the built request, clears contents and Base64-encodes bytes(auth_message).

DTFlow choice: resolve and validate the effective request once, build its provider message inside the integration, then create the blob from that same message with contents empty. Preserve all other fields including configuration, prompts, overrides, identifiers, seed and chunk policy. Do not authorize one configuration and submit a different one. Initial txt2img has no content tensors; advanced hashed-content inputs require a later audited contract.

| Authentication JSON field | Official S3/S5 | Initial DTFlow choice |
| --- | --- | --- |
| blob | Base64 protobuf request string | Exact blob above; never log it |
| fromBridge | Handler uses true | true, matching P1 |
| attestationSupported | false | false |
| assertionPayload | Optional nil | Omit |
| originalTransactionId | Optional nil | Omit |
| isSandbox | false | false |
| consumableType | payg when reference account policy permits and estimated amount is positive | Omit; PAYG disabled |
| amount | Positive compute estimate when supplied by reference caller | Omit; no billing estimate sent |

P1 sends the optional fields as JSON null. The official Codable path omits nil optionals; initial DTFlow chooses omission for the optional generation-auth fields. Fixture tests assert both consumableType and amount are absent, rather than accidentally inheriting account flags. This is a subscription-only request policy, not a guarantee of free generation.

Authorization header contains the short-term token directly. Official success requires HTTP 200 with gRPCToken string; DTFlow additionally requires nonempty content and valid JSON/object structure. Use that token for this GenerateImage call only. Its expiry, reuse across generations and refresh behavior are not established; do not invent a persistent generation-token cache.

Immediately before GenerateImage can send, services must have committed their potential-execution record (the durable attempt foundation already exists in Phase 2A; Phase 3 extends its use) and the adapter marks POSSIBLY_SUBMITTED. Token acquisition and authorization alone are NOT_SUBMITTED for the generation RPC, not proof of zero server-side authentication/billing effects. Cancellation after possible RPC send retains remote uncertainty. Once terminal OK and required valid outputs are established, later local notification/cleanup failure preserves that stronger completion evidence; application success still requires durable saving.

## Billing, errors and unknowns

Official S3 can query /billing/stripe/payg, read paygEnabled/paygEligible and enable payg when its estimate is positive. DTFlow's initial path does not query that endpoint or inherit those flags. It never supplies a consumable type or amount. This matches P1's intent but does not establish authoritative account balance/refund behavior.

Official code treats non-200 and decoding failures as failure, without a complete stable structured taxonomy. DTFlow maps verified credential/authentication responses to authentication failure; preserves HTTP/gRPC status separately; and leaves unverified quota/storage/billing reasons generic. Never classify every 403 as quota exhaustion or expose a raw response body. Malformed success is protocol failure. No response means no invented status.

On authentication failure, no generation RPC is submitted. After GenerateImage may have started, timeout/network/cancellation is not proof of rejection or stopped compute. An OK terminal status with empty/invalid output still fails the backend completion contract. No automatic retry of ambiguous generation.

Production cancellation latency, charging/refunds, throttle semantics, generation token lifetime, broad model/LoRA compatibility and remote recovery remain unverified. Sources expose no generation-job lookup/reattachment. Record independently authorized deployed evidence separately from mocks.

## HTTP implementation and cancellation

DTFlow choice: use a scoped httpx.AsyncClient with injectable transport and origin; asyncio remains the service runtime. Use validated TLS, follow_redirects false and trust_env false initially so auth requests do not follow implicit redirects or environment proxy settings. Phase 2C adds HTTPX >=0.28.1,<0.29.0 through uv with a synchronized lock.

Explicit HTTP limits: connect 10 s, read/write 30 s, pool wait 10 s, and outer total 30 s per token/auth operation. HTTPX inactivity limits do not implement a total deadline; use the remaining monotonic outer budget as well. Bound decoded auth response to 1 MiB via streaming/size checks; oversized responses are protocol/resource failures without body logging. Pool/client shutdown follows the backend cleanup deadline.

Cancel and close owned HTTP streams/tasks asynchronously; preserve asyncio cancellation. A cancelled request may already have reached the HTTP server. Do not start GenerateImage after cancellation, including when a mocked authorization response arrives late, or retry a cancelled authorization. Cancelling one token waiter leaves the shared refresh alive for other waiters; shutdown owns its final cleanup. A completed response does not revive a closed client or transfer credentials to its replacement.

Do not use urllib in a worker thread for production HTTP: cancelling the awaiting task would not stop the blocking call. P1's to_thread pattern is diagnostic evidence only. No automatic redirects, transport retries or credential-source switches are introduced.

## Phase 2C acceptance and test-layer mapping

Exercise actual cloud adapter/service composition against HTTP mocks and the existing scripted TLS/gRPC fixture, with external sockets denied. Five scenario groups define acceptance, not a mandatory number of test functions; one scenario may satisfy several requirements.

| Scenario group | Required evidence | Authoritative test layer |
| --- | --- | --- |
| Successful cloud generation | Selected API key → short-term token → per-generation authorization → authenticated RPC → valid final. Assert token/auth paths and headers, omitted PAYG fields, blob matching the actual submitted provider message with contents excluded, and correct gRPC bearer metadata. | Cloud adapter composition using HTTP mocks and the existing gRPC server; shared/2B tests own full wire mapping and codecs when the same code executes. |
| Token lifecycle and concurrency | Valid-token reuse, refresh under controllable time and one shared refresh. Cancelling one waiter does not disrupt another. Each generation obtains its own authorization token. | Focused cloud auth-client tests using mocked clocks/events; generation composition verifies per-request authorization. |
| Authentication failure | Representative failure prevents GenerateImage, with no silent credential fallback or automatic retry. Conspicuous fake secrets are absent from public errors and captured logs. | One or two representative adapter failures; parametrized parser/HTTP tests cover malformed, missing, wrong-type, empty and non-200 responses. Existing credential-provider tests own unchanged source precedence. |
| Submission uncertainty and cancellation | Pre-submission failure remains not submitted. Cancellation during authorization prevents RPC dispatch even after a late mocked response. Post-send loss without authoritative completion remains unknown and is not retried; validated completion retains its stronger evidence. | Cloud composition for the auth/send boundary; shared/2B tests own generic stream failure/cancellation and completion handling only when the same implementation executes. |
| Non-generative health/inventory | Required short-term token acquisition and Echo/discovery occur without /authenticate or GenerateImage. | Cloud adapter composition; shared/2B tests own unchanged Echo parsing/inventory mapping. |

Keep small focused tests for new HTTP response parsing, bounded bodies, failed refresh, HTTP deadlines/cancellation and owned-client/shared-task shutdown. These requirements remain mandatory; equivalent response-shape cases are parametrized at the parser/HTTP boundary instead of becoming independent end-to-end scenarios. Immutable client scope removes runtime scope-switch and late-old-scope cache tests.

Test common behavior once at its owning layer. Reuse seed/sampler/dimension serialization, codecs, certificate/hostname matrices, preview/final validation, generic gRPC statuses, stream cleanup and deadlines from 2B only where 2C actually executes the same code. Reuse of a dependency or intended architecture alone is insufficient. Phase 2C inherits the actual 2B channel/stream generation path; its authorization hook is the cloud-specific boundary. See the implementation ownership mapping. Record the acceptance-to-test mapping and any genuinely cloud-specific transport differences in that PR.

Reuse tests/support/dt_server.py and tests/fixtures/tls/; do not build a separate cloud simulator. Use events, mocked monotonic clocks and tiny payloads, with no sleeps for expiry/concurrency tests or repeated TLS setup when unnecessary. Measure the runtime added to the default suite and record it in the implementation PR; impose no test-count or line-count target.

Fixture schemas are derived from S1–S5/P1. Mocks validate DTFlow's specified behavior, not production quota, remote cancellation or actual model compatibility. The Phase 2C implementation and focused fixtures are recorded in the integration note. No deployed request or billable generation was used for that acceptance.

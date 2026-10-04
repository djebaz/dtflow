# Draw Things local gRPC integration (Phase 2B)

**Status:** narrow adapter implemented in `src/dtflow/backends/drawthings/` with offline scripted TLS/gRPC coverage.
Owner-run non-generative checks exposed real-server TLS incompatibilities; the local bundled-CA policy below addresses
them, but a successful live adapter check after that fix is still pending. No real generation or billable action was
performed while building it.

## Scope

Text-to-image only, no LoRA, batch 1/1, samplers `euler_a` and `dpmpp_2m_karras`, one active generation per server.
Not included: CLI exposure (3A), LoRA, img2img/inpaint/control, chunked output assembly, direct cloud (2C).

## Ledger of dependency use

| Use | Where | Note |
|---|---|---|
| `GenConfig(...).to_fbs(seed)` | `wire.py` | Public path that honors seed 0. `RequestBuilder`/`build_grpc_message` are not used: they replace seed 0 with a random seed in drawthings-py 0.4.0. The encoded FlatBuffer is decoded again and compared with the resolved request before sending. |
| `ImageGenerationRequest/Response`, service stub/base | `backend.py`, fixture | Generated bindings shipped by drawthings-py. |
| `grpclib.Channel` with per-instance `ssl` and `ssl_target_name_override` | `backend.py` | `GrpcService` and its process-global TLS context are not used. |
| `Channel.__connect__` (private) | `backend.py` | Connects before any request bytes exist so connect/TLS failures are provably pre-submission. |
| `ImageBuffer.from_tensor` | `codec.py` | Final-image decode in a worker thread. |
| `_preview_decoders.decode_preview` (private) | `codec.py` | Best-effort previews; any failure drops the preview silently. |
| `resources/root_ca.crt` | `tls.py` | Read bundled CA through importlib.resources; local policy verifies its chain with hostname checking off and VERIFY_X509_STRICT cleared. No process-global context is patched. |

Only `dtflow.backends.drawthings` imports provider libraries (checked by `tests/unit/test_contract.py`).

## TLS policy

Local endpoints default to the explicit TlsPolicy.drawthings_local() compatibility policy, matching the working
probe: trust only the bundled Draw Things Root CA, verify the certificate chain, and disable hostname checking.
Clear VERIFY_X509_STRICT only for this policy because the bundled CA lacks the key-usage extension required by
Python 3.13's default strict validation. It neither accepts arbitrary self-signed certificates nor retries with
weaker trust after a failure. Hostname checking is absent in this local mode, so it does not establish endpoint-name
identity; callers deliberately choose their local server.

Owner-reported non-generative diagnostics found code 92 (CA missing key-usage extension) with the bundled CA, then
code 64 (IP mismatch) after clearing strict validation. These findings justify this compatibility path; a successful
live connection/inventory check using the updated adapter remains pending.

Explicit system/custom-CA policies retain chain and hostname verification, optionally with a configured verified
server name. Direct DT+ remains on the probe's default system-trust context with h2; it does not inherit the local
compatibility exception or gain additional TLS requirements. Failures remain pre-submission connectivity failures.

## Evidence mapping

| Observation | Submission stage | Remote outcome |
|---|---|---|
| Connect/TLS failure, validation, lane wait cancelled/expired | not_submitted | not_started |
| Trailers received with an error status | terminal_observed (status recorded) | unknown |
| Local deadline, disconnect, protocol error, cancellation after dispatch | possibly_submitted | unknown |
| OK with the validated expected outputs | terminal_observed | succeeded (by the service) |
| OK with no or too few images, corrupt/oversize/inconsistent tensor, `MORE_CHUNKS` | typed failure, never success | unknown |

A terminal error status alone does not prove the work failed or stopped.

Once terminal OK and the expected valid finals are established, a failing terminal-event callback retains the
completed result with a sanitized warning. The service still owns validation and durable saving before application
success; notification failure does not make known remote completion unknown.

## Limits: enforced and not enforced

Enforced after receipt and before decode: compressed size against `transport_bytes` (per payload and cumulative),
final count, the 68-byte CCV header (dimensions equal the request, 3 or 4 channels), estimated decoded size against
`tensor_bytes`, and the encoded PNG size. **Not enforced:** limits during transport (grpclib receives a whole message
first) and fpzip's own allocation, which is sized from its stream header that is not inspected; a hostile server
could still request a large allocation. Mitigation is the verified, user-chosen server plus the caps above. Bounded
decode isolation or an upstream header API is a follow-up. `TimeoutPolicy.idle` is not applied yet.

## Concurrency, cancellation, shutdown

One lane (`asyncio.Lock`); the total deadline starts at entry and covers lane wait, connect, stream and decode, and is
passed to gRPC. Cancelling while waiting never dispatches. Cancelling mid-stream closes the stream (observed on the
fixture server); remote cancellation is not confirmed. `aclose()` refuses with an active generation and is idempotent.

## Partial artifacts

Decoded finals received before a failure are attached to the failure as `PARTIAL` artifacts in memory only; the
durable store does not persist their bytes.

## Fixtures

`tests/fixtures/tls/generate.sh` creates test-only EC certificates (IP/DNS SANs, DNS-only, wrong-name, untrusted CA)
valid for about 100 years; the keys are committed and labelled test-only. The scripted server returns protocol
fixtures only. One SQLite service path runs through the real adapter. The optional `live` test
(`tests/integration/test_drawthings_live.py`) is a non-generative connection and Echo check using the bundled local
policy by default; explicit CA/server-name settings select strict trust instead.

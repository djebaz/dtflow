# Draw Things local gRPC integration (Phase 2B)

**Status:** narrow adapter implemented in `src/dtflow/backends/drawthings/` and verified only against the scripted
strict-TLS fixture server in `tests/support/dt_server.py`. It has not been run against a real Draw Things server, and
no generation or billable action was performed while building it.

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

Only `dtflow.backends.drawthings` imports provider libraries (checked by `tests/unit/test_contract.py`).

## TLS policy

Certificate and hostname verification are always on. A connection may use the system trust store or one explicit CA
file, and may name an explicit verified TLS server name (connect by IP, verify a DNS name). There is no option to
disable verification and no fallback after a TLS failure. Failures are classified (`tls_untrusted_certificate`,
`tls_hostname_mismatch`, `tls_expired`, `tls_handshake_failed`) as pre-submission connectivity failures.

**Pending:** which names/CA a real Draw Things server presents is unverified. A relaxed-hostname compatibility
policy (chain still verified, clearly named) is added only if the optional live check shows it is required.

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
(`tests/integration/test_drawthings_live.py`) is a non-generative strict connection and Echo check.

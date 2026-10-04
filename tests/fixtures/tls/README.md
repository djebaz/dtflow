# Test-only TLS fixtures

These certificates and private keys exist **only** to exercise strict TLS verification in offline tests against a
local scripted server. They are intentionally committed, carry no secrets and protect nothing. Never use them for any
other purpose.

| File | Role |
| --- | --- |
| `ca.pem` | Test CA (trusted by the strict-positive tests) |
| `server.pem` / `server.key` | Signed by the test CA; SANs `IP:127.0.0.1`, `IP:::1`, `DNS:localhost` |
| `dnsonly.pem` / `dnsonly.key` | Signed by the test CA; only `DNS:dtflow-fixture.test` (connect by IP with an explicit verified server name) |
| `wrongname.pem` / `wrongname.key` | Signed by the test CA; only `DNS:other.example` (hostname mismatch) |
| `untrusted.pem` / `untrusted.key` | Same SANs as `server`, signed by a different CA that is not committed (untrusted chain) |

Regenerate with `./generate.sh` (developer OpenSSL 3.x; the script discards the CA private keys). Certificates are
valid for 100 years, so no expiry bookkeeping is needed; `generate.sh` prints each `notAfter` date. An expired or
otherwise invalid certificate surfaces through the adapter's classified TLS failure, not through a test-side parser.

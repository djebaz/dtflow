# Plan: tolerant BYOM response consumption

## Scope

- Tolerate unused, missing, null and unrelated BYOM response branches rather than rejecting a whole successful response.
- Record bounded sanitized response evidence and fixed-name structural observations before interpreting the BYOM response.
- Distinguish local response interpretation failures from provider/API rejection. Keep exact content/reference and safe upload destination checks; never invent receipts or trigger retries.
- No credential profiles, live API calls, tests, validation, renders or packaging work in this slice.

## Action items

- [x] Implement branch-local BYOM consumption and pre-interpretation observation.
- [x] Synchronize diagnostic architecture, release notes, status and offline preview definitions.
- [ ] Commit/push a new PR; do not merge.

## Decisions

- The supplied HTTP 200 warning does not reveal the original shape because its sensitive branch was redacted. No exact deployed response variant or root cause is claimed.
- Metadata-only lookup may return unknown when the response supplies no usable evidence. Explicit preparation/dispatch must still stop if no verified reference or safe checksum-matched destination exists; logging must describe this as a local interpretation issue, not server rejection.
- No desktop layout or presentation change; existing inline messages consume backend-neutral failures as before.
- Local interpretation messages are retained separately from server messages, so a server's successful `ok` cannot hide DTFlow's reason. The offline prepared-asset interpretation preview is defined but not rendered.

## Acceptance

Source-only implementation. No tests, validation, renders or additional live calls; runtime acceptance remains with the owner.

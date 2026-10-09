# Plan: repeat-export skipping and historical dates

## Scope
- In: skip previously exported saved identities with matching source checksum across root/noise/blank; skipped summary; Windows file creation time and EXIF Date taken based on recorded durable save time.
- Out: rewriting/deleting existing exports, changing prompt privacy on skipped files, generation timestamp reconstruction, runtime state changes, inference, dependencies.

## Action items
- [x] Add bounded export-metadata discovery and checksum-confirmed skips; preserve ordinary unrelated filename collision handling.
- [x] Set creation time on newly owned Windows export handles; add UTC EXIF date fields with explicit source semantics.
- [x] Sync docs and static checks; delivery continues in PR #55.

## Decisions and Design Changes
- 2026-10-09: Owner accepts noise/blank export routing and asks for repeat-export skips and historical image dates.
- Attempts persist only updated_at_utc, exposed as saved_at_utc; exact generation start/end is not durably recorded. Use successful saved time as generation-completion proxy, never claim exact inference time or derive it from elapsed duration.
- Same saved identity plus original PNG SHA256 is a duplicate regardless of changed prompt/sidecar/routing choices. Existing exports are untouched; use a fresh folder to apply new options.
- Scan only root/noise/blank, bounded to 10000 JPEG candidates; validate owned portable identity/checksum, JPEG end marker and bounded metadata including mandatory JSON fallback. Ignore unrecognized/corrupt candidates rather than infer identity from filenames.
- Timestamp failures warn but retain successfully written JPEGs. No timestamp updates to originals or skipped files.

## Open questions
- None blocking.

## Validation
- [x] Whole-repo Ruff lint and format, AST/imports, reference-PoC help, lock consistency, Windows FILETIME availability and diff checks passed. Static scan found no owner paths/defaults or ignored-helper imports in the retained PoC.
- No new test functions added and no GUI/export smoke under fast-implement. Owner explicitly requested full pytest; results are recorded below. Owner validates repeat-export skip, creation/Date taken fields, old EXIF/sidecar fallback, mismatched checksum and privacy expectations.
- No packaging, generation, inference or billable actions.

## Additional owner request
- [x] Retain tmp/export_jpeg_exif_poc.py as tools/images/export_jpeg_exif_poc.py with no owner paths or tmp-only helper dependency; require explicit source/output arguments. Preserve the reference PoC full-private-metadata behavior with a clear sharing warning; no application-export semantics changed by copying it.
- Typed nonexistent destination already uses mkdir(parents=True, exist_ok=True), before export; retain and document this behavior.

## Owner-requested validation
- Owner explicitly requests Ruff and tests, overriding earlier execution deferral for this turn. First full run: 424 passed, 3 skipped, 2 failed in 127.01s; both failures were the existing blanket Pillow-import ban against the export service introduced earlier in PR #55.
- Existing contract checks now allow only Pillow in services/image_export.py and image_quality.py, never Qt/providers or Pillow in core/other services. No new test functions added; existing boundary assertions preserve the strict default. Full suite rerun passed: 426 passed, 3 gated live tests skipped in 109.46s. Whole-repo Ruff lint/format passed. These tests do not establish direct repeat-export/Windows-date smoke acceptance; no new exporter or reference-PoC invocation performed.

## Early-skip index follow-up
Owner reports skip is slow and requests a destination index. [The index plan](2026-10-09-export-index.md) supersedes per-run source hashing on recognized hits with quick exported-file checks and durable identity lookup. Existing-source mutation detection is intentionally no longer part of the fast path.

# Plan: destination-free image computation cache

## Scope
- In: central source-only checksum/classification cache; stable full-identity filenames; one in-memory destination inventory with legacy/renamed EXIF fallback; remove destination-index persistence.
- Out: changing original/job state, deleting old user-folder indexes or renaming old exports, caching prompts/destination history, new dependencies or generation.

## Action items
- [x] Replace export-index persistence with bounded source-analysis cache under cache/derived/image_analysis.json.
- [x] Use full attempt/output identity filename marker, folder listing plus legacy EXIF fallback, and early skips.
- [x] Reuse computations across destinations with source-fingerprint and detector-version invalidation; wire no-cache and existing Derived cleanup.
- [x] Sync current docs and historical supersession notes; static checks; commit/push PR #55.

## Decisions and Design Changes
- 2026-10-09: Owner explicitly removes persistent destination records. New filenames end in _dtf-<full-attempt-id>-<one-based-output>, with numeric collision suffixes preserved. Directory membership is rebuilt once in memory per export, independent of computational cache.
- New-format filenames plus JPEG start/end markers establish the fast practical presence check; older/renamed names use bounded portable EXIF/mandatory-sidecar fallback. This is not cryptographic integrity verification. No filename-only short-ID guesses.
- Source cache stores only saved identity, source path digest/size/mtime, original PNG checksum, advisory metrics/label/reasons and algorithm version. No source paths, prompts, credentials, filenames or destinations.
- Missing/corrupt/version-mismatched cache is a miss; source fingerprints invalidate all computations, detector version invalidates classification. New destinations still decode/encode JPEGs but avoid repeated source hashing/detection when valid.
- Existing Derived/all-cache cleanup owns this flat derived JSON. --no-cache bypasses persistent reads/writes. In-run data may be reused without disk cache.
- Previously created .dtflow-export-index.json files are ignored and not deleted. Legacy names keep EXIF fallback until independently renamed/exported into a new folder.

## Open questions
- None blocking.

## Validation
- [x] Whole-repo Ruff, AST/import, help and Designer XML checks.
- No new tests or export/GUI smoke under fast-implement. Last 426-pass suite predates this change. Owner validates new names, legacy skips, cross-destination reuse, invalidation, no-cache and Derived cleanup.
- No inference, billable actions or packaging.

## Delivery
- Implemented on feature/jpeg-image-export for PR #55. Whole-repo Ruff check and format check, AST/import checks, CLI help, Designer XML, uv lock check and git diff whitespace checks passed. Tests, export/GUI smoke and packaging were not run for this change; final validation remains with the owner.

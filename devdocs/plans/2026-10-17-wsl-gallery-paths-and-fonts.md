# Plan: WSL gallery access paths and selected-toggle fonts

## Status

Source implementation checkpoint in PR #75 (`fix/wsl-gallery-paths-and-fonts`).
Owner approved the non-mutating path direction. Runtime acceptance is pending.
Fast-implement: no tests, validation, renders or builds by the agent; owner accepts
the final runtime behavior. Do not merge without a separate owner request.

## Scope

- In: read-only Images access to committed Windows image and metadata paths from
  WSL, preserving durable references and artifact identities.
- In: investigate and correct the selected-toggle font-family/weight difference
  between native Windows Qt and Linux Qt, using the shared theme contract.
- Out: database/metadata rewrites, schema migration, global path replacement,
  inferred relocation, new destructive capabilities, credential-vault support,
  dependency downgrades, bundled Windows fonts, inference and provider calls.
- Export, Trash, edit handoff and regeneration must not silently acquire new
  cross-platform permissions through a presentation-path alias.

## GUI components affected

- [x] Images thumbnails, Detail original-image loading and metadata inspection.
- [x] Shared theme/font resolution and selected toggle states.
- [x] Offline preview fixtures in `tools/ui/render_states.py`.
- No Designer layout, title-bar/DWM settings or PyInstaller changes planned.

## Decisions

1. Keep the original stored path and local filesystem access path distinct.
   Preserve exact stored paths for provenance, metadata artifact matching and
   durable safety checks; resolve only where a local file read is required.
2. Use one backend-neutral/headless resolver. Do not duplicate Windows/WSL
   conversion in widgets, thumbnail workers and metadata readers.
3. Windows drive paths map only through verified WSL drive mounts. Discover actual
   mount roots rather than assuming `/mnt/<drive>`; native Linux and Windows paths
   keep their own semantics. Detect WSL, not generic Linux.
4. Reject unsupported foreign path forms (UNC/device/drive-relative), ambiguous
   mappings and traversal. Never search by basename or select the first existing
   candidate. An unavailable mount/file remains explicitly unavailable.
5. Cache mount discovery only within a bounded operation/run policy with explicit
   invalidation; filesystem I/O and discovery stay off the GUI thread. Do not run
   a subprocess for each thumbnail or every gallery row.
6. Read resolution grants no ownership. Audit action call sites so export/Trash/
   edit/reuse cannot mistake a mapped presentation path for the stored reference.
   Existing identity, per-file leases, root restrictions, path-reference counts,
   receipt verification and no-replay rules remain authoritative.
7. Long-term: managed outputs should have state-root-relative artifact references,
   with explicit root mappings for external outputs. That needs a separate versioned
   migration design; do not migrate history in this PR.
8. Font work starts with evidence: compare actual resolved family/style/weight for
   ordinary and selected toggles. Retain Windows typography and shared tokens;
   provide a deliberate Linux fallback or weight fix only after identifying the
   mismatch. Do not claim pixel-identical native rendering or silently install,
   copy or redistribute proprietary fonts.

## Implementation checklist (same PR)

- [x] Inspect current gallery service, persistence references and shared theme.
- [x] Agree non-mutating path resolution versus portable-reference migration.
- [x] Audit GalleryItem path consumers, metadata matching and original leases.
- [x] Define resolver result/interface and verified mount-discovery behavior.
- [x] Implement bounded read-only image/sidecar access preserving stored identity.
- [x] Keep unsupported and unsafe paths visibly unavailable without retries/search.
- [x] Audit and explicitly retain operation restrictions at non-read boundaries.
- [x] Inspect actual Linux font resolution, then apply a scoped shared fix.
- [x] Extend offline preview states for mapped images/metadata and toggle typography.
- [x] Update gallery/runtime architecture, desktop/development guide, DESIGN.md if
  typography policy changes, and unreleased notes to describe implemented scope.
- [x] Update PR body with implementation, evidence and remaining limitations.

## Owner acceptance (not agent-run tests)

- [ ] Existing Windows history: Grid/List thumbnails, Detail zoom/pan, metadata,
  Copy path and Open folder behavior from WSL with the expected mounted drive.
- [ ] Non-default WSL mount root; unmapped drive; missing/corrupt file; malformed,
  device, UNC, drive-relative and traversal paths stay safely unavailable.
- [ ] Windows-native and Linux-native outputs retain existing read behavior.
- [ ] Database, metadata and recorded Windows references remain byte-for-byte
  unchanged by browsing; original identity and sidecar checks remain effective.
- [ ] No mapped read enables an unreviewed Trash/export/edit/reuse operation.
- [ ] Selected/unselected navigation, view-mode and segmented toggles retain
  coherent family and intended weight on Linux and Windows; no clipping/focus
  regression. Owner decides whether the resulting Linux appearance is acceptable.
- [ ] GUI remains responsive during thumbnail/page changes. No inference needed.

## Resolved implementation decisions and evidence

- WSL 1 inspection showed `drvfs` root sources `C:\134`, `D:\134`, `E:\134`
  in `/proc/self/mountinfo`; mount escapes are decoded before matching whole drives.
  Discovery is bounded to 1 MiB, one snapshot per gallery query, refreshed for
  foreign image/sidecar reads. Ambiguous mount roots and symlink/traversal/ADS
  paths are refused. No global cache, directory search or subprocess conversion.
- `GalleryItem.path`/`metadata_path` remain authoritative. Optional access aliases
  support availability, image cache keys and local shell/clipboard actions.
  Exact metadata matching still uses the original reference. Copy checked paths
  accepts an injected read resolver at the existing persistence read boundary.
- Foreign Windows records cannot use edit/reuse/regenerate callbacks; export
  rejects foreign sources before reading/encoding. Trash's existing native-path,
  ownership and capability checks remain unchanged. The resolver is not called
  by destructive file operations. Export skip-only membership behavior is unchanged.
- Linux font inspection (Qt 6.8.3): requested Segoe UI 400 resolved to DejaVu Sans
  Book; 600 and 700 both resolved to DejaVu Sans Bold. Existing Windows regular,
  semibold and bold font files were present. The theme registers those three
  faces from verified drive C once per application before entering its event loop,
  or rolls back partial registration. No fonts copied, installed or bundled.
  Exact Windows/Linux rendering parity has not been validated.
- Preview states 31/32/33 use a synthetic mounted-drive record, calibration image
  and matching sidecar, with no owner database/images or providers. They list
  Grid/List toggle typography and Detail/metadata; no screenshots rendered.
- Windows/Linux original-file lock equivalence is not established. Concurrent
  cross-platform mutations are unsupported; no ownership equivalence is implied.

## Remaining work

- Owner runtime/visual acceptance and all checks listed above. No agent tests,
  syntax/import checks, lint, rendering, build or other validation were run for
  this implementation, per the selected workflow. No training, inference or
  potentially billable action was performed.

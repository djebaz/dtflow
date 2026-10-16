# Plan: WSL gallery access paths and selected-toggle fonts

## Status

Planning checkpoint. Owner approved the path direction; implementation will be
pushed to this same PR. No runtime fix is implemented by this document.
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

- [ ] Images thumbnails, Detail original-image loading and metadata inspection.
- [ ] Shared theme/font resolution and selected toggle states.
- [ ] Offline preview fixtures in `tools/ui/render_states.py`.
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
- [ ] Audit all GalleryItem path consumers, metadata matching and original leases.
- [ ] Define resolver result/interface and verified mount-discovery behavior.
- [ ] Implement bounded read-only image/sidecar access preserving stored identity.
- [ ] Keep unsupported and unsafe paths visibly unavailable without retries/search.
- [ ] Audit and explicitly retain operation restrictions at non-read boundaries.
- [ ] Inspect actual Linux/Windows font resolution, then apply a scoped shared fix.
- [ ] Extend offline preview states for mapped images/metadata and toggle typography.
- [ ] Update gallery/runtime architecture, desktop/development guide, DESIGN.md if
  typography policy changes, and unreleased notes to describe implemented scope.
- [ ] Update PR body with implementation, evidence and remaining limitations.

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

## Open implementation questions

- Final mount-discovery source and how to reject ambiguous/redirected mappings
  without relying solely on existence or lexical `/mnt/c` assumptions.
- Whether access-path information should be carried separately in gallery models
  or retained entirely in the shared I/O boundary. Decide after the consumer audit.
- Exact selected-toggle font mismatch: missing family, synthesized weight or Qt
  style resolution. Do not apply a cosmetic guess before inspecting it.

These are implementation decisions, not reasons to rewrite stored paths or widen
the agreed scope. Resolve and record them before the corresponding code changes.

# Workflow dimension editing

## Scope
- In: Generate width/height editor snapping, existing 64-pixel arrow steps, pending-edit commit before Run, offline editing preview definition.
- Out: Request/import validation changes, other numeric controls, tests, renders and provider work.

## Action items
- [x] Add dimension-only text conversion: nearest multiple of 64, ties upward, existing bounds.
- [x] Commit pending dimension text before taking the Run graph snapshot.
- [x] Extend the existing offline editing fixture with 800 → 832; update guide and unreleased notes.
- [ ] Owner native smoke: focus loss, arrows, Run with focused input and unchanged imported-invalid behavior.

## Decisions
- Owner selected nearest/ties-up. Retain canonical request validation and never normalize imported invalid values.
- No tests added or run, no smoke tests or renders run. Final runtime validation remains with the owner. PR stays draft until review/CI is requested.

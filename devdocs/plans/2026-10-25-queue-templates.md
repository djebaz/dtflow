# Queue authoring templates and admission-order coverage

**Scope:** issues #142 and #163 in one feature PR. Local text-to-image authoring recipes only; no automatic dispatch, uploads, provider calls, import/export, image-input portability or release changes.

## Delivery slices

1. Assert FIFO at durable `claim_next` admission in the ten-slot test; separately verify the backend-start set, concurrency and completion.
2. Extract the existing Qt-free composer-source projection/validation into a shared backend-neutral module. Keep current imports compatible and preserve historical source handling.
3. Add a bounded, independently versioned durable template library with transactional writes, named save/update/rename/delete, corruption/newer-schema refusal and stale-edit protection. Templates exclude execution identities, prepared receipts, resolution evidence, endpoints, image paths and consent.
4. Add canonical Designer template controls and inline searchable library/name/confirmation UI. Capture and restore the independent draft, plan, count and per-queue cap without changing Generate or shared cloud defaults. Dirty replacement and backend rebinding need explicit consent; no template action creates a queue or prepares assets.
5. Reuse existing planner/preflight validation at preview/create time; preserve unavailable choices visibly, disclose mutable/missing wildcard dependencies and reject unsupported/clamping inputs before changing the draft. Keep loaded-template identity/baseline separate from edited drafts and persisted recipes.
6. Extend authoritative offline checks and preview definitions, synchronize guides/status/release notes, run the full offline suite and repository checks, and review the complete PR. Render only if requested. Native/DPI, packaged and provider acceptance remain separate.

## Decisions

- The template library is durable state separate from queue history and cache, with its own schema. No public sharing format is introduced.
- Preserve original authoring prompt, ordered dimensions, explicit plan seed and existing image-seed semantics. Loading does not reroll; external wildcard names use current local files, not archived contents.
- Different backend requirements must not switch the connection. Rebinding is explicit and removes account-bound hints; dispatch continues through existing dependency and safety checks.
- Baseline source tree `d9f2651` has the previously completed full offline suite: 646 passed, 3 skipped. New-change evidence will be recorded below and in the PR; GitHub Actions currently has an account billing restriction.

## Progress

- Initial inspection complete; implementation and validation pending.

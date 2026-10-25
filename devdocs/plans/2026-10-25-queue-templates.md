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

- PR [#167](https://github.com/djebaz/dtflow/pull/167) contains both issues. The ten-slot check now observes FIFO through `claim_next`, independently of concurrent backend-start arrival order.
- Shared authoring types/source validation are extracted with compatible presenter imports; the template boundary separately requires complete version-1 base/form fields, keeping historical queue-source compatibility unchanged.
- Source implements the independent transactional library, canonical template controls/library form, inline name/search/confirmation flows and loaded-name/modified tracking. Core recipe validation and SQLite mutation checks run before replacement/write; background callbacks are navigation-scoped and owned by the existing runner.
- The first independent review found three source defects: deterministic enumeration incorrectly requiring an RNG seed, a dormant Repeat count not restored in Pair/Cross, and truncated forms acquiring defaults. All three were corrected and the spec reviewer confirmed them addressed. Standards review found no actionable violations. These are static reviews, not native acceptance.
- Scoped offline validation passed **73 tests in 49.10 s** across queue integration, template storage/validation, Qt composition, planning presentation and import boundaries. The full suite will be recorded against the committed implementation head. Repository-wide Ruff lint/format, changed Python syntax and both canonical Designer XML parses pass.
- Preview definitions `queues-90`–`queues-102` are added with synthetic entries and intercepted workers; not rendered. No real inference, provider calls, uploads, model downloads, destructive user-file actions or billable operations. Native Windows/DPI, complex real-use and packaged acceptance remain unverified.

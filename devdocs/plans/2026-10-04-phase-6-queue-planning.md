# Plan: Phase 6 queue planning and batch expansion

**Status:** Planned; entry condition is Phase 5B merged (see [the 5B plan](2026-10-04-phase-5b-desktop-queue.md))
**Milestone:** First half of the 0.4.0 daily-use refinement (Phase 6 plus Phase 8, see [the release roadmap](releases.md))
**Authoritative acceptance:** `6-O1` to `6-O4` from [the initial plan](initial.md#phase-6--queue-planning-and-batch-expansion); Phase 3B stays authoritative for scheduling, restart and no-replay semantics, Phase 5B for the Queues page
**Related plans:** [Phase 3 CLI and queues](phase-3-cli.md), [Phase 5B desktop queue](2026-10-04-phase-5b-desktop-queue.md), [LoRA assets](lora-assets.md)

## Intent

Today a queue is one resolved request repeated N times with seeds `seed, seed+1, ...` (`expand_items`). Phase 6 replaces that single rule with a bounded, deterministic planner that turns a small plan (a base request plus varied dimensions) into an explicit ordered list of fully resolved requests, shows that list before anything is persisted (dry run), and hands it to the existing queue unchanged. Nothing about scheduling, reservations, restart or no-replay changes: the planner produces `ResolvedGenerationRequest` values and `create_queue` persists them exactly as it does now.

## What already exists

| Already delivered | Where | Consequence for Phase 6 |
| --- | --- | --- |
| Resolved, immutable requests with per-field resolution evidence | `core/models.py`, `services/config.py` (`resolve_request`) | The planner emits these; it never invents a second request type or a second resolver. |
| Item expansion for one request (seed increments, fresh request id, `queue_position` evidence, count 1..1000, 32-bit seed bound) | `services/queue_session.py` (`expand_items`) | Becomes the degenerate "seed sweep" case of the planner and keeps its exact outputs for `-k/--count`. |
| Queue creation from a list of requests; cap; per-queue backend record | `services/queue_session.py` (`create_queue`), `persistence/queues.py` | Unchanged. The planner feeds it. |
| CLI `queue add` and the Queues page both call the shared functions | `cli.py`, `ui/queue_page.py` | One planner is reached from both; neither owns planning rules. |
| Versioned, credential-free configuration contract | `services/config.py` (`export_config`, `load_config`) | A plan file reuses the same field names and validation; it does not duplicate them. |

## Findings that shape the design

1. **Combinatorics must be bounded before enumeration.** A cross product's size is the product of its dimensions; the planner computes the cardinality arithmetically and rejects anything above the queue maximum (`MAX_ITEMS`, 1000) before generating a single request, so an adversarial plan cannot allocate a large list.
2. **Every item must be reproducible from its own record.** The queue stores the full resolved request per item; evidence entries (`plan.index`, `plan.dimension.<name>`) record why the item differs, so a queue can be understood without the plan file that produced it.
3. **Seeds are part of the plan, not a hidden side effect.** Existing behaviour (seed + N - 1) is kept only as the default for a plain count. In a planned batch the seed is either fixed (controlled comparison), swept as an explicit dimension, or incremented per item, and which one applies is stated in the dry-run output. AGENTS.md requires controlled parameters unless intentionally varied.
4. **LoRA dimensions need care.** Varying LoRA identifiers or weights changes compatibility; the planner varies only identifiers and weights the user listed, does not infer compatibility, and leaves preflight and prepared-receipt binding to the existing services at creation. Receipts bind by identifier exactly as today.
5. **No remote effect.** Planning, dry run and queue creation contact no backend and upload nothing; only Start (unchanged) dispatches.

## Entry conditions and preserved contracts

- Phase 5B is merged; the branch starts from current `main`.
- The planner lives in `src/dtflow/services/` and imports no Qt, SQLite, gRPC or HTTP types; UI and CLI call it.
- Output is deterministic: the same plan and base request yield the same ordered requests (request ids excepted, which are fresh per creation and never part of the ordering).
- Existing `queue add -k N` behaviour and outputs are unchanged.
- No live generation, upload or billable action is run by an agent.

## Scope

### In

- A plan model with three combination modes over named dimensions:
  - **seed sweep** (today's behaviour): count items, seed incrementing from the base seed;
  - **pair** (zip): dimensions of equal length matched position by position; unequal lengths are a diagnostic, never silent truncation;
  - **cross** (product): every combination in a stated, stable order (the last listed dimension varies fastest).
- Plannable dimensions (initial set): prompt (from a list), model, seed (list or range), steps, guidance, sampler, size (width and height as a pair), and LoRA weight per listed LoRA. Anything else stays fixed from the base request.
- Dry run: print or show the exact item list (index, varied values, resulting seed) and the total count, without persisting anything.
- A plan file (versioned JSON, credential-free, reusing the configuration field vocabulary) and CLI/desktop entry points that call the same planner.
- Failure before enqueueing for: oversize cardinality, empty or duplicate-valued dimensions where duplicates would create identical requests, unequal pair lengths, values rejected by the shared request validators, and seed overflow.

### Out

- Per-item dependencies, conditional items, templates or scripting (workflow territory, Phase 10).
- Priorities, fairness or adaptive concurrency (Phase 7).
- Gallery, presets and result comparison views (Phase 8); a plan file is not a preset.
- Inferring LoRA/model compatibility or choosing values automatically.
- Editing a persisted queue; a changed plan creates a new queue.

## Pull-request sequence

Each step starts from current `main` unless the owner folds steps into one pull request; each opens as a draft after its first coherent commit, updates this plan with decisions and is merged only by the owner.

### PR 1: Pure planner and CLI

1. **Planner** `src/dtflow/services/planning.py`:
   - `Plan` (frozen dataclass): `mode` (`seed`, `pair`, `cross`), ordered `dimensions` (name to tuple of values), `count` for the seed mode.
   - `plan_size(plan)`: arithmetic cardinality, evaluated before any expansion.
   - `expand_plan(base, plan)`: returns the ordered `ResolvedGenerationRequest` list through the same `resolve_request`/`replace` path used for single requests, with `plan.index` and `plan.dimension.*` evidence appended; raises `ValidationError` with a precise message for each failure above.
   - `describe_plan(base, plan)`: the dry-run table (Qt-free, plain rows).
   - `expand_items` delegates to the seed mode so there is one code path for count-based queues.
2. **Plan file** `parse_plan(document)`: a versioned JSON document (unsupported versions rejected clearly, size bounded like configuration files); field names match `services/config.py` so validation is shared.
3. **CLI**: `dtflow queue add` gains `-l/--plan FILE` (planned items replace `-k/--count`; the two are mutually exclusive) and `queue add --dry` (prints the plan table and the count, creates nothing). Canonical flag pairs follow the repository CLI rules and `--help` is updated in the same change.

Owned checks: `6-O1`, `6-O2`, `6-O3` as parametrized pure tests on `planning.py` (representative matched and mismatched pair inputs, small cross products with exact cardinality and order, bounds and invalid values that fail before enqueueing), plus `6-O4`: one composition scenario from plan to persisted queue with the fake backend asserting the items equal the planner output unchanged. The scheduler is not retested.

### PR 2: Desktop planning surface

1. **Presenter** `ui/planning_presenter.py` (Qt-free): maps form inputs to a `Plan`, summarises the dry-run table and cardinality, and maps planner errors to the owning widget with the existing error-field approach.
2. **Queues page**: a compact "Plan" area next to the item count with mode selection, per-dimension value entry, a live count readout and a dry-run table; Create calls `expand_plan` then `create_queue`. The existing count spin box remains the seed-sweep shortcut. Large cardinalities show a clear refusal, not a truncated list.
3. **Wiring check**: one focused `pytest-qt` scenario proves widget values reach the shared planner and the created queue matches the dry run; planner mathematics are not repeated through widgets.

### Gate and documentation

- `uv lock --check`, `ruff check`, `ruff format --check`, `pytest` on the final head; Windows visual review of the Plan area and dry-run table.
- Update README (queue planning usage and `--help` flags), `initial.md` status, `releases.md` 0.4.0 notes, `runtime-layout.md` only if plan files are stored (default: they are user-supplied files, not state), and `unreleased.md`.
- No live workflow is required; batch planning adds no new composition boundary.

## Decisions to take in PR 1 (defaults proposed)

- **Mode names and CLI spelling**: `seed`, `pair`, `cross`; `-l/--plan FILE` and `--dry`. Alternatives: separate `queue plan` subcommand. Default keeps one entry point (`queue add`).
- **Cross ordering**: last listed dimension varies fastest, so results read like nested loops in the order written.
- **Seed policy in pair/cross mode**: the base seed is kept fixed for every item unless a seed dimension is listed or `seed: increment` is stated in the plan, so comparisons stay controlled by default. Needs the owner's confirmation because it differs from `queue add -k`, which increments.
- **Size as one dimension**: width and height vary together as listed pairs, never as independent axes, to avoid unintended aspect-ratio matrices.
- **Item ceiling**: reuse `MAX_ITEMS` (1000); no separate planner limit.
- **Plan file location**: user-supplied path, never copied into state; the queue records the resolved requests and evidence, so no plan file is needed to understand or restore a queue.

## Risks and mitigations

- **Accidentally huge or costly batches:** cardinality is checked before enumeration, the dry run is the default review step on the desktop (Create shows the count first), and the plan is bounded by the queue maximum. Creating a queue never dispatches.
- **Silent changes to existing `-k` behaviour:** `expand_items` is kept as the seed mode and covered by the existing queue tests.
- **Uncontrolled comparisons:** the seed default and the dry-run table state exactly what varies.
- **Mistaken compatibility claims for LoRA dimensions:** the planner forwards only user-listed identifiers and weights; compatibility stays with preflight and the documented evidence limits.
- **Plan file drift from the configuration contract:** shared field names and validators, versioned document, tests assert round-trip of the common fields.

## Exit criteria for Phase 6

- `6-O1` to `6-O4` pass at their authoritative layers.
- A planned batch can be dry-run and enqueued from both the CLI and the desktop through the same planner functions, with no planning rules in `ui/` or `cli.py`.
- Every queued item is a fully resolved request carrying plan evidence; scheduling, restart and no-replay behaviour are untouched.
- Docs distinguish what is wired from what is not; Windows visual review and packaged-executable checks are stated as unverified until run.

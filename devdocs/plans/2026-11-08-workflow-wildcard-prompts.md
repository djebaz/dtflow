# Wildcard prompts in workflows (#317)

Status: plan. No code yet. Decisions by Djé on 2026-11-08 are marked (D).

## Goal

A Generate, Inpaint or Img2img node can turn its prompt into several prompts with `{a|b|c}` choices and `__name__`
wildcard lists, resolved offline and deterministically, per image/branch.

## Reuse

Queues already do this (#129): `services/dynamic_prompts.py` (syntax, seeded sampling, every combination, highlight
runs) and `services/wildcards.py` (`NAME.txt` lists, SHA-256 per list). Workflows add no grammar. Both modules are pure
or read-only I/O and do not depend on Qt; the workflow layer calls them.

## Node setting (D)

Optional node properties, additive like `DTFlow outputs` (#276); the contract version stays 1 and `widgets_values` is
untouched:

- `"DTFlow prompt mode"`: `"sample"` or `"all"`; missing means off (prompt is literal, so every existing file is
  unchanged). Rejected on node types other than Generate, Inpaint and Img2img.
- `"DTFlow prompt count"`: integer N for `"sample"` (random distinct prompts); not allowed with `"all"` (the count is
  the number of combinations).

## How a mode multiplies images

The mode produces several prompts for the same source, so it uses the branching that already exists:

- **Img2img / Inpaint:** the node splits each incoming image into K branches (K = N, or the number of combinations),
  like `DTFlow outputs` (#276, #302). Branch k uses prompt k and seed = node seed + k, so wildcard mode and
  `DTFlow outputs` exclude each other on a node (a wildcard node's K replaces the outputs count).
- **Generate:** image k (of `batch_size * batch_count`) uses prompt k. Random sample draws one prompt per image;
  Every combination sets the image count to the number of combinations.
- **Ceilings (D, 2026-11-08):** one Inpaint/Img2img node may make up to 16 prompts per incoming image (Generate keeps
  its 16 images). A larger K is refused before the run with a clear message. `DTFlow outputs` itself stays 1..4.
- **Wide branch encoding:** 16 outputs do not fit the current digits (plain radix 5, node-qualified radix 65). Rule: an
  integer keeps its present meaning wherever it is decoded today, so every stored run, label, seed and file is
  unchanged. A workflow whose plan needs more than 4 outputs on a node is planned with a third, **wide** form kept in
  a disjoint integer range (offset above the largest old value, digits radix 17; a node-qualified variant for parallel
  splitters with radix `slots * 16 + 1`). `child_branch`, `parent_branch` and the label decoder get the form from the
  integer's range, never from a stored flag. This lands as its own PR before the executor work (step 2a), since it
  touches branch identity, Retry scope and result keys.
- **Plan counts:** K enters the planned generations before branches are built (#278), against the 256 limit. Counting
  "all" never expands the combinations: `dynamic_prompts.count` gives the total first.

## Determinism and Retry

- The draw RNG is seeded from node seed + node id; a branch takes entry k of the sorted/sampled prompt list, so the
  result does not depend on finish order.
- The resolved prompt is stored in the step `settings.prompt` on the first try. Retry reuses the stored prompt and the
  same seed; a later list edit never changes a Retry. A new run (not a Retry) resolves again from the current lists.
- The run snapshots the list files once at the start (name and SHA-256) so every branch of one run sees the same
  lists.

## Lists (D)

Plain `.txt` files in one folder edited in Finder/Explorer; no in-app editor. The folder is the Queues folder
(`STATE/wildcards` by default) with a Connection setting using the remembered-folder picker (`ui/file_dialogs.py`) and
a "Show in folder" button. Lists are never stored in the workflow file; the file carries only the prompt and the mode.

## Recording

- Sidecar and step `settings.prompt`: the resolved prompt.
- Run evidence: `plan.prompt_template`, the mode and count, `plan.wildcard.<name>` = SHA-256 of each list used, and
  `prompt.variation_runs` for highlights. Prompt text is never written to logs or file names.

## Checks before a run

`dtflow workflow check` and Run planning validate the syntax and the existence of every list, naming the list; a missing
list blocks the run. Messages never include the prompt text.

## UI (D: highlights)

Node inspector: mode select (Off / Every combination / Random sample), count field for sample, a preview-draws button,
"Show in folder". The preview, the finished step and the Queues row highlight the varied parts using the
`prompt.variation_runs` evidence, as Queues does (`ui/prompt_tags.py`).

## Steps

1. This plan and the contract text in `devdocs/architecture/workflows.md`.
2a. Wide branch encoding for up to 16 outputs on one node (`core/workflows.py`), old encodings untouched.
2. Core: properties, validation, planning counts (`core/workflows.py`).
3. Executor: resolve in `_produce` and the image-operation step, stored prompt, evidence (`services/workflow_item.py`).
4. UI: inspector, preview with highlights, Connection folder setting.
5. Guide and release note.

Per repo conventions: no new tests, `Refs #317`, Djé runs the real-backend smoke.

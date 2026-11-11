# Issue #373 — LoRA preparation and selection design

**Status:** proposed UI design and visual reference; not a Qt implementation or Windows acceptance.  
**Source of decisions:** [issue #373](https://github.com/djebaz/dtflow/issues/373), finalized on 2026-11-11.  
**Implementation foundation:** [PR #381](https://github.com/djebaz/dtflow/pull/381), based on #378.  
**Renders:** [LoRA UI kit](ui_kits/lora/index.html) · [wide design render](ui_kits/lora/prepare-wide.svg) · [compact render](ui_kits/lora/prepare-compact.svg).

## Before → intended after

- **Before:** selecting converted files starts the existing cloud preparation immediately; there is no unified metadata editor, human-readable name or partial-batch readiness view.
- **After:** one editor for all selected files, with suggested editable short names, an eligible exact architecture selector, F16/Q8P selection, optional triggers, safe managed-filename preview and **one explicit Prepare N ready LoRAs** action. Incomplete rows stay editable while already-authorized rows run and require a later explicit Prepare.
- **Benefit:** no repeated dialogs, fewer unreadable filenames and one coherent editing flow.
- **Tradeoff:** Prepare authorization moves from the file picker to the editor. This is an intentional behavior change and requires a reviewed root AGENTS.md amendment **before runtime implementation**.

## Design rules

- **Wide:** table columns Source file / Architecture / Short name / Quantization / Trigger words / Status. Source filenames middle-elide, but the full value must be available for inspection and Copy original. Do not truncate editable short names.
- **Narrow:** the *same* batch editor becomes a scrollable stack of cards, not multiple dialogs. Keep field labels, inline errors, managed-filename preview, batch counts and footer action visible.
- **Readiness:** architecture and 1–20-character ASCII \`[A-Za-z0-9_]\` short name are required. A usable filename-derived whole-word suggestion can prefill a new short name, otherwise show an empty required field; never overwrite manual or legacy edits. Only confidently detected **and provider-verified LoRA-supported** exact architectures may prefill. No checkpoint-family fallback.
- **Format:** editable F16 default; recognizable terminal \`_lora_q8p.ckpt\` may prefill Q8P. Selection is metadata, not byte verification.
- **Identity:** display \`PortraitV2[Kr2]\`; new managed filename \`PortraitV2_Kr2_LoRA_F16.ckpt\`. Preserve original source and existing prepared identifiers. Existing metadata edits never rename/reupload assets.
- **Collision:** names are case-insensitively unique only within the exact architecture + F16/Q8P scope. Untouched suggestions can get \`_2\`, \`_3\` within 20 chars; unresolved conflicts block *only* that row. Same-content assets can reuse an existing receipt.
- **Provenance:** detected and user-declared architectures are separate. Explicit user selection wins for dispatch but must not be styled as file-verified evidence. Status is always text + color: Ready, Needs architecture, Name conflict, Preparing, Prepared, Already exists, Failed, Outcome unknown.
- **Picker:** selected entries first; case-insensitive short-name A–Z *inside* selected and unselected groups. Display sorting never changes effective LoRA execution order, weights, queue variants or persisted IDs.
- **Legacy editor:** reuse fields with **Save metadata** and explicit “Existing prepared files will not be uploaded or renamed.”
- **Triggers:** persistent application-wide default seeds new configurations. Generate, Queues, Workflows and Scripts each have independent saved Use LoRA trigger prefixes controls, no duplicate prompt phrases. Local canonical triggers must persist. DT+ Cloud Asset metadata-write support is unverified; no design preview claims it succeeds and optional sync does not interrupt successful asset preparation.

## Token and render ownership

[LoRA prototype tokens](tokens/lora.css) alias the **existing** shared charcoal/blue/color/typography values and add proposed UI measurements. They are prototype-scoped, **not** additions to runtime \`tokens.json\`. Promote only values needed by a later native Qt implementation; native authority remains \`DESIGN.md\`, Designer forms and shared QSS.

The HTML kit and SVG graphics are **illustrative offline design renders**, not actual captured native Qt screenshots. The kit cannot hash, upload, persist, call a provider or dispatch generation. Architecture options in fixtures are examples, **not** a verified active-backend support list. Native Windows, backend/DT+ and accessibility acceptance are separate implementation gates. No tests or provider requests are part of the design deliverable.

## Handoff

Implement the Qt Designer editor after amending the root AGENTS.md Prepare contract; wire it to PR #381's content-keyed metadata and naming resolver. Reuse shared picker patterns without mutating saved ordering, and verify provider architecture support/trigger-prefix behavior independently. Keep #373 as the product authority and #372 as the owner of Queue-specific effective LoRA stacks.

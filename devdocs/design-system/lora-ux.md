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
- **Density:** compact desktop fields and row spacing are the default, not a second density mode. Prefer bounded architecture/short-name/format/trigger widths, let source filenames use remaining space, and avoid repetitive helper text when it adds no information. Keep architecture provenance, required-field errors and text statuses visible. Prototype measurements: 30px controls, 52px minimum table rows, 8px grid gaps; these are design proposals, not native Qt tokens.
- **Compact width and narrow viewports:** keep the **same horizontal table with one LoRA per row** and its six labeled columns; do not transform the editor into stacked cards or multi-line rows. Reduce column widths/gaps and helper text instead. Source filenames middle-elide, while editable short names stay readable. At widths smaller than the compact table's minimum (720px), allow horizontal scrolling of the table only; keep the batch footer, preview and action outside that scroll area. Preserve architecture provenance, inline errors and text statuses.
- **Readiness:** architecture and 1–20-character ASCII \`[A-Za-z0-9_]\` short name are required. A usable filename-derived whole-word suggestion can prefill a new short name, otherwise show an empty required field; never overwrite manual or legacy edits. Only confidently detected **and provider-verified LoRA-supported** exact architectures may prefill. No checkpoint-family fallback.
- **Format:** editable F16 default; recognizable terminal \`_lora_q8p.ckpt\` may prefill Q8P. Selection is metadata, not byte verification.
- **Identity:** display \`PortraitV2[Kr2]\`; new managed filename \`PortraitV2_Kr2_LoRA_F16.ckpt\`. Preserve original source and existing prepared identifiers. Existing metadata edits never rename/reupload assets.
- **Collision:** names are case-insensitively unique only within the exact architecture + F16/Q8P scope. Untouched suggestions can get \`_2\`, \`_3\` within 20 chars; unresolved conflicts block *only* that row. Same-content assets can reuse an existing receipt.
- **Provenance:** detected and user-declared architectures are separate. Explicit user selection wins for dispatch but must not be styled as file-verified evidence. Status is always text + color: Ready, Needs architecture, Name conflict, Preparing, Prepared, Already exists, Failed, Outcome unknown.
- **Picker:** selected entries first; case-insensitive short-name A–Z *inside* selected and unselected groups. The entire item row is the selection click target (native checkbox wrapped in a label); keep the checkbox visible and keyboard-operable, with a clear focus outline. No separate row-click listener or double-toggle path. Display sorting never changes effective LoRA execution order, weights, queue variants or persisted IDs.
- **Legacy editor:** open from the expanded Connections → Custom LoRAs section; reuse fields with **Save metadata** and explicit “Existing prepared files will not be uploaded or renamed.”
- **Triggers:** the global default lives in Connections' right settings panel and seeds new configurations. Generate, Queues, Workflows and Scripts each have independent saved Use LoRA trigger prefixes controls, no duplicate prompt phrases. Local canonical triggers must persist. DT+ Cloud Asset metadata-write support is unverified; no design preview claims it succeeds and optional sync does not interrupt successful asset preparation.

## Approved placement within existing workspaces (2026-11-11 owner review)

- **Connections / left rail:** preserve backend, account and connection controls. Move the existing **Custom LoRAs** management section out of this rail; do not duplicate its list elsewhere.
- **Connections / center workspace:** keep **Connection readiness**, **Draw Things presets** and **Disposable cache**. Place **Custom LoRAs immediately after Connection readiness** as one compact, **collapsed-by-default, expandable section**, not a full-width permanent library and not a new navigation tab. The collapsed header contains the title, an illustrative local LoRA count and an accessible disclosure chevron; no table is visible until opened. When expanded, show a dense table with short name, architecture, format, trigger words and status, plus **Edit metadata**, existing import and retained-copy management. Bound the table height and scroll within it for many records. Full technical identities, hashes and receipt state remain inspectable; retained-copy removal must be labeled local-only.
- **Connections / right inspector:** keep the app-wide settings in their existing home. Add the persisted **Use LoRA trigger prefixes** default for *new* Generate, Queue, Workflow and Script configurations; do not treat it as a live master switch or retroactively overwrite saved values. Other global controls remain.
- **Generate / right inspector:** show **only selected LoRAs with their weights**, an **Add / Edit LoRAs** picker action and Generate's own saved **Use LoRA trigger prefixes** control. Do not duplicate the canonical metadata manager here. The picker still offers the complete eligible list on demand, preserving selected-first A–Z visual sorting and independent execution order.
- **Queues, Workflows and Scripts:** retain their existing screen structure and independent per-execution-path trigger-prefix settings; their LoRA pickers reuse the same canonical labels and selection behavior.
- **Metadata edit route:** Connections → expand Custom LoRAs → select record(s) → **Edit metadata**. Reuse the Prepare fields in metadata-only mode, without uploads, renaming prepared assets or changing identifiers; support batch edits where practical. Existing local records are the single authority.
- **Prototype interactions:** the HTML kit has dedicated **Connections placement** and **Generate placement** views, an accessible working expand/collapse button (initially closed), a route to the metadata editor and a Generate link into the existing picker. Its example counts and values are illustrative. No data is saved or removed by the kit. The separate trigger-reference view is a contract overview, *not* a new Settings page.

**Before → after:** two permanent LoRA-looking lists and a management section squeezed into a side rail become one on-demand management section in Connections' center, plus a focused selection summary in Generate. The extra disclosure click is intentional for infrequent metadata edits; it keeps everyday connection/readiness controls visible without allocating a large permanent LoRA surface.

## Token and render ownership

[LoRA prototype tokens](tokens/lora.css) alias the **existing** shared charcoal/blue/color/typography values and add proposed UI measurements. They are prototype-scoped, **not** additions to runtime \`tokens.json\`. Promote only values needed by a later native Qt implementation; native authority remains \`DESIGN.md\`, Designer forms and shared QSS.

The HTML kit and SVG graphics are **illustrative offline design renders**, not actual captured native Qt screenshots. The kit cannot hash, upload, persist, call a provider or dispatch generation. Architecture options in fixtures are examples, **not** a verified active-backend support list. Native Windows, backend/DT+ and accessibility acceptance are separate implementation gates. No tests or provider requests are part of the design deliverable.

## Handoff

Implement the Qt Designer editor after amending the root AGENTS.md Prepare contract; wire it to PR #381's content-keyed metadata and naming resolver. Reuse shared picker patterns without mutating saved ordering, and verify provider architecture support/trigger-prefix behavior independently. Keep #373 as the product authority and #372 as the owner of Queue-specific effective LoRA stacks.

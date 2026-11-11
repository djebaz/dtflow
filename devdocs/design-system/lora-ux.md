# Issue #373 — LoRA preparation and selection design

**Status:** design reference with a Qt implementation in PR #382: the batch Prepare editor, the Connections library table with the single-record metadata overlay, and the five trigger-prefix switches. Native Windows and provider acceptance are still pending.  
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
- **Identity:** display \`PortraitV2[Kr2]\`; new managed filename \`PortraitV2_Kr2_lora_f16.ckpt\`. Preserve original source and existing prepared identifiers. Existing metadata edits never rename/reupload assets.
- **Collision:** names are case-insensitively unique only within the exact architecture + F16/Q8P scope. Untouched suggestions can get \`_2\`, \`_3\` within 20 chars; unresolved conflicts block *only* that row. Same-content assets can reuse an existing receipt.
- **Provenance:** detected and user-declared architectures are separate. Explicit user selection wins for dispatch but must not be styled as file-verified evidence. Status is always text + color: Ready, Needs architecture, Name conflict, Preparing, Prepared, Already exists, Failed, Outcome unknown.
- **Picker:** selected entries first; case-insensitive short-name A–Z *inside* selected and unselected groups. The entire item row is the selection click target (native checkbox wrapped in a label); keep the checkbox visible and keyboard-operable, with a clear focus outline. No separate row-click listener or double-toggle path. Display sorting never changes effective LoRA execution order, weights, queue variants or persisted IDs.
- **Legacy editor (owner correction, 2026-11-11):** metadata editing is **single-LoRA only**. From expanded Connections → Custom LoRAs, choose exactly one row and open an **in-window overlay** over Connections. Reuse Prepare fields with **Cancel** and **Save metadata** and explicit “Existing prepared files will not be uploaded or renamed.” Close returns to Connections with the library expanded and the same row selected. There is no multi-record editor or batch metadata Save.
- **Names everywhere (implemented in PR #382):** every surface that shows a LoRA uses `ShortName[Tag]` (tables, tooltips, filters, chips, notices, accessible names). A tagless legacy `Name_lora_f16.ckpt` reads `Name (legacy name)`; anything else unmanaged stays raw. Stored text keeps the filename and is relabelled only at display time. When two LoRAs share a label the filename is appended; filters keep the identifier as item data.
- **Triggers (owner placement correction, 2026-11-11):** Connections' right settings panel shows **all five independently operable switches together**: one persistent application default for new configurations plus Generate, Queues, Workflows and Scripts. Each of the four execution switches represents its own saved choice (also accessible in its existing execution context), not a global override or a synced master flag. The default only seeds new configurations; changing it must never rewrite already-saved per-path choices. No duplicate prompt phrases. Local canonical triggers must persist. DT+ Cloud Asset metadata-write support is unverified; no design preview claims it succeeds and optional sync does not interrupt successful asset preparation.

## Approved placement within existing workspaces (2026-11-11 owner review)

- **Connections / left rail:** preserve backend, account and connection controls. Move the existing **Custom LoRAs** management section out of this rail; do not duplicate its list elsewhere.
- **Connections / center workspace:** keep **Connection readiness**, **Draw Things presets** and **Disposable cache**. Place **Custom LoRAs immediately after Connection readiness** as one compact, **collapsed-by-default, expandable section**, not a full-width permanent library and not a new navigation tab. The collapsed header contains the title, an illustrative local LoRA count and an accessible disclosure chevron; no table is visible until opened. When expanded, show a dense table with short name, architecture, format, trigger words and status, plus **Edit metadata** for one selected row, existing import and retained-copy management. Bound the table height and scroll within it for many records. Full technical identities, hashes and receipt state remain inspectable; retained-copy removal must be labeled local-only.
- **Connections / right inspector:** keep app-wide settings in their existing home. The LoRA trigger panel is deliberately **dense and proportional to neighboring settings**: one section title, five single-line switch rows, no explanatory paragraph, subgroup heading, repeated per-row description or footer. The application default is separated by a small gap and has a tooltip explaining that it only seeds new configurations. Show the **complete LoRA trigger control group**: the persisted global default for new configurations, followed by four independently saved switches labeled **Generate**, **Queues**, **Workflows** and **Scripts**. Changing one execution switch does not change any other switch; the global default never retroactively rewrites saved values. The same per-path setting may remain accessible in its own execution context. Other global controls remain.
- **Generate / right inspector:** show **only selected LoRAs with their weights**, an **Add / Edit LoRAs** picker action and Generate's own saved **Use LoRA trigger prefixes** control. Do not duplicate the canonical metadata manager here. The picker still offers the complete eligible list on demand, preserving selected-first A–Z visual sorting and independent execution order.
- **Queues, Workflows and Scripts:** retain their existing screen structure and independent per-execution-path trigger-prefix settings; their LoRA pickers reuse the same canonical labels and selection behavior.
- **Metadata edit route:** Connections → expand Custom LoRAs → **select one row** → **Edit metadata**. An in-window overlay sits over Connections with the selected record's fields, **Cancel** and **Save metadata**. It is intentionally **single-record only**, not batch editing. Closing returns to the expanded section and preserves the selection. Editing never uploads, renames prepared assets or changes identifiers; existing local canonical records are the single authority.
- **Prototype interactions:** the HTML kit has dedicated **Connections placement** and **Generate placement** views, an accessible working expand/collapse button (initially closed), **single-row library selection**, and a keyboard-dismissable **metadata overlay** instead of a separate metadata screen. The five Connections switches are interactive and mirror Generate’s corresponding switch for illustration only; changes are temporary and do not persist. Generate's picker link remains. Example counts and values are illustrative; no data is saved or removed.

**Before → after:** two permanent LoRA-looking lists and a management section squeezed into a side rail become one on-demand management section in Connections' center, plus a focused selection summary in Generate. The extra disclosure click is intentional for infrequent metadata edits; it keeps everyday connection/readiness controls visible without allocating a large permanent LoRA surface.

## Qt Designer design references (2026-11-11)

The [five editable `.ui` design forms](ui_kits/lora/qt/README.md) now supplement the
interactive HTML mockup: Connections, Generate, Batch Prepare, selected-first picker,
and a single-record metadata editor. They use standard Qt Widgets and the existing
DTFlow stylesheet when rendered, with a local-only capture command documented alongside
the forms. Connections defaults to the compact collapsed library, with an optional
expanded capture; Prepare retains its six-column horizontal table at compact widths.
They are **standalone design references**, not a change to canonical runtime
`src/dtflow/ui/forms/` files and not evidence of a working Qt feature.
The owner explicitly authorized Designer/render work on 2026-11-11; native Windows
and provider checks still remain separate.

## Token and render ownership

[LoRA prototype tokens](tokens/lora.css) alias the **existing** shared charcoal/blue/color/typography values and add proposed UI measurements. They are prototype-scoped, **not** additions to runtime \`tokens.json\`. Promote only values needed by a later native Qt implementation; native authority remains \`DESIGN.md\`, Designer forms and shared QSS.

The HTML kit and SVG graphics are **illustrative offline design renders**, not actual captured native Qt screenshots. The kit cannot hash, upload, persist, call a provider or dispatch generation. Architecture options in fixtures are examples, **not** a verified active-backend support list. Native Windows, backend/DT+ and accessibility acceptance are separate implementation gates. No tests or provider requests are part of the design deliverable.

## Handoff

Implement the Qt Designer editor after amending the root AGENTS.md Prepare contract; wire it to PR #381's content-keyed metadata and naming resolver. Reuse shared picker patterns without mutating saved ordering, and verify provider architecture support/trigger-prefix behavior independently. Keep #373 as the product authority and #372 as the owner of Queue-specific effective LoRA stacks.

# Where to put the LoRA convert feature in the dtFlow UX (issue #357)

Neutral option map. Not ranked, no recommendation: a second agent sorts these afterwards. Read-only study of `djebaz/dtflow` main at 23f1254 (after #397). Platform: Windows only.

## Facts every option shares

**What the Python converter gives** (`tools/drawthings/lora_to_ckpt.py`, notes in `devdocs/guides/lora-ckpt-format.md`, issue #357 newest comment)
- `convert(source, destination, family, scale, log, ignore_unmatched, allow_experimental)`; numpy + stdlib only, native Windows, pure function, never exits the process, never overwrites the output, closes the source after reading. `ConversionError` carries a user readable message that already points to the Colab notebook.
- Verified (0 differing tensors vs the Swift reference): Krea 2, Z Image Turbo, SD 1.x (one reference file), Flux 1. SDXL is experimental (needs `allow_experimental`, no oracle, the reference crashes on every SDXL LoRA).
- Unsupported, stops with an error: LoHa, LoKr, Tucker, diff keys, embeddings, DoRA, bias, quantized output, unmapped layers.
- Unverified arithmetic: alpha != rank, scale != 1, F32 sources, 4-D conv flag, Z Image refiner layers, Flux layers outside blocks.
- Converted file has **never been run on real DT+** (the compares prove byte identity with the Swift file, not DT+ acceptance). Issue step 2 asks for a Krea 2 and Flux 1 end-to-end run first.

**What it does not give**
- No provenance JSON (the Colab notebook writes `model_family`, name, trigger word, hashes). dtFlow computes hashes and records the converter version and Draw Things revision (db37e65) itself.
- **The family must be supplied.** `family` is mandatory. `lora_family.detect_lora_family` detects only from *converted* `.ckpt` SQLite headers, not from safetensors tensor names. So either the user picks it, or a new safetensors name detector is built (small, but new code, and it must refuse ambiguity).
- Only 5 families, so every option needs the Colab fallback for the rest.

**What the app already has that options can reuse** (verified in code)
- `PrepareBatchEditor` (`ui/prepare_lora_editor.py`, rows from `forms/prepare_lora_row.ui`): per-file row with managed short name, family dropdown (suggested/detected/manual), format (F16/Q8P), per-row authorization. Opened from the Generate page LoRA panel `LoraPanel.prepare()` (`ui/lora_panel.py`) after a file picker; **cloud only**. Today it accepts already converted Draw Things files.
- `AssetService.prepare` (`services/assets.py`): measure, retain a copy in `LoraStore`, record canonical record, negotiate and upload to DT+. CLI `assets prepare -f FILE` (documented as "already-converted Draw Things LoRA file").
- `LoraStore` (`services/lora_store.py`): retained copies in application data, keyed by sha256+size, `record_origin`/`changed_origin` for source tracking. Connection page "retained LoRAs" table (`main_window.py`, `retainedLorasTable`, Remove and Edit metadata buttons) with local metadata columns.
- Canonical record (#370 plan `devdocs/plans/2026-11-11-canonical-lora-record.md`): `lora_records` keyed by (sha256,size) with `detected_version`, `declared_version`, `short_name`, `format_code`, trigger words; managed names like `PortraitV2_Kr2_lora_f16.ckpt`. Source stamp/changed-source refresh path exists (`AssetService._refresh`, `_refreshed_from_source`), which is where "restore from saved source" (#327) hooks in.
- Background work: `ui/runner.py` + activity bar/strip + `inline_feedback` for progress and errors.
- Workflows: node types in `core/workflows.py` are stock ComfyUI types (LoadImage etc.); LoRAs are a `loras` param on generative nodes (name + weight), resolved by the shared metadata resolver and receipts at preflight.

**Common constraints**
- Convention: no new tests locally, ruff format/lint before commit, Djé smokes on real DT+ (Windows), docs and release note after smoke. All findings of reviews get solved.
- Converter must run off the GUI thread (a 200+ MB LoRA means seconds of numpy work and disk writes).
- Output of conversion is a new local file; it must flow into the same Prepare/retain/receipt path so the canonical record, naming and preflight stay single-sourced (no parallel logic, per #370).
- DT+ users only matter for upload; local Draw Things (macOS/Linux backends) is not the Windows target here, so conversion is only needed when the cloud provider lacks the LoRA.

---

## Note from Djé (2026-11-12): family detection is expected to extend to safetensors

Djé expects the existing auto family detection (today only for converted `.ckpt` files) to be extended to safetensors later. It is easier on the original format, which is widely used and has existing solutions. So "the user must pick the family" is a **temporary** con, not a permanent one. It matters most for options 1, 2, 3 and 8 (and eases option 4's "family without a user at hand" risk). Until the detector exists, the user-picked family stays the fallback, and ambiguous detection should still refuse.

## Option 1: Inside the existing LoRA Prepare editor (Generate page)

- **Where:** `LoraPanel.prepare()` file picker accepts `.safetensors` as well as `.ckpt`; each safetensors row in `PrepareBatchEditor` gets a "Convert then prepare" state.
- **Flow:** pick files, rows appear; for a safetensors row the family dropdown becomes mandatory (no detector) or prefilled by a new tensor-name detector; "experimental" badge on SDXL, "unsupported" with Colab hint for other families; Authorize runs convert in background (progress in activity strip), then the converted `.ckpt` goes straight into `AssetService.prepare` using the managed name already chosen in the row.
- **Reuses:** the editor, naming service, format column, authorization, asset controller, runner, `inline_feedback`.
- **Pros:** one place for "get a LoRA to DT+"; family/name/format already collected in the same row; no new page; batch conversion falls out for free; matches the #370/#373 design authority.
- **Cons:** (family-by-user is temporary, see Djé's note above) the editor is cloud-gated and tied to the Generate page; Q8P format choice makes no sense for converter output (f16 only) and needs row logic; two long operations (convert, upload) in one authorization; a failed conversion needs a per-row outcome state; where the intermediate `.ckpt` is kept must be decided.
- **Effort:** medium (editor states + conversion step in `_authorize_prepare`/`_prepare_next`, background wiring, family detector optional).
- **Risks:** family given by user can be wrong and the converter will not notice (shape checks only); no provenance JSON so hashes/versions must be stored by dtFlow; experimental SDXL could be uploaded and fail on DT+ later; editor complexity grows.

## Option 2: Connection page retained LoRAs table

- **Where:** Connection page, retained LoRAs table, new "Convert safetensors…" button beside Remove and Edit metadata (or per-row action).
- **Flow:** user picks a safetensors file, chooses a family in a small dialog, conversion runs in background, the `.ckpt` is retained locally in `LoraStore` and appears as a row; Prepare to DT+ stays a separate later step (Generate page).
- **Reuses:** `LoraStore.retain`, table rendering, metadata edit overlay, runner.
- **Pros:** the table already lists local files and disk usage; conversion becomes a pure local operation with no network and no cost; separates "convert" from "upload"; no paid action involved.
- **Cons:** Connection is the wrong mental place for a task (it is configuration/inventory); the user then has to go to Generate to Prepare, two-step; the table is keyed by retained copies of converted files, so adding unconverted sources blurs its meaning; extra dialog needed for family.
- **Effort:** small to medium.
- **Risks:** a retained `.ckpt` with no canonical record or family evidence until Prepare; users may not discover it; duplicates the family picking UI that already exists in the Prepare editor.

## Option 3: Dedicated "LoRA tools" page or tab

- **Where:** new side-rail page (or a tab inside Connection) with a converter workspace.
- **Flow:** drop or pick files, table with source, detected/selected family, status (verified/experimental/unsupported), scale, Convert button, per-row logs, results listed with Prepare, Open folder, Remove actions.
- **Reuses:** `Prepare` editor row pattern, `LoraStore`, runner, activity bar, design-system tokens (`devdocs/design-system`).
- **Pros:** room for status, logs, scale option, verification notes, converter version and DT revision, batch results; can host later tools (inspect, family report); no interference with Generate.
- **Cons:** largest UI surface and maintenance; yet another page to learn; duplicates Prepare/Connection concepts; navigation changes in `main_window.py` and `main_window.ui`; needs design mockup per the design-system rules.
- **Effort:** large.
- **Risks:** scope creep; page rarely used; inconsistent with the epic's goal of restoring LoRAs automatically.

## Option 4: Automatic step at run time (restore path, #326/#327)

- **Where:** no new control. When a run needs a LoRA that DT+ lacks and the only local source is a `.safetensors`, `AssetService.ensure_many` converts then uploads, as part of the existing restore.
- **Flow:** user selects a LoRA by name in Generate/Queues; at run time preflight finds it missing, converts in background with stage text in the run's progress, uploads, runs. Failures surface as the run's clear failure reason with the Colab fallback.
- **Reuses:** `ensure_many`, `_refresh` and source tracking, run progress stages, receipts.
- **Pros:** zero extra user steps; matches decisions already made (#326: no consent setting, always automatic, restore at run time); works for Queues, Workflows and Scripts through the shared preflight.
- **Cons:** needs the family without a user at hand (a stored declared family from an earlier step, or a detector); silent conversion of an experimental/unverified family is hard to accept; a long convert plus upload inside a paid run; a wrong conversion is only found when the image is bad; CI/queue runs have nobody to answer a prompt.
- **Effort:** medium to large (record family decision persistence, detector or earlier selection, error mapping, queue behavior for refusals).
- **Risks:** highest of all: family must be given, no provenance JSON, experimental SDXL, wrong-LoRA images that cost credits; cannot be smoke tested without real DT+ conversions accepted first.

## Option 5: CLI-only path

- **Where:** `dtflow assets convert -f LORA.safetensors --family krea2 [--scale] [--experimental] -o OUT.ckpt` (optionally `assets prepare --convert`), no GUI change. `tools/drawthings/lora_to_ckpt.py` is already runnable by hand.
- **Flow:** user (or script) converts at the command line, then prepares the `.ckpt` in the GUI or CLI as today; JSON output for scripting.
- **Reuses:** `cli.py` assets action group, the converter as is.
- **Pros:** smallest and safest; no UI design, no threading concerns; explicit family flag is natural; good first step for the smoke on real DT+.
- **Cons:** does not satisfy the acceptance "LoRA reaches DT+ without a Mac" for GUI users; Windows users need a terminal and Python on PATH (check for packaged app); no discoverability.
- **Effort:** small.
- **Risks:** low; mostly a documentation and packaging question (is `tools/` shipped in the Windows build, is the CLI available to end users).

## Option 6: Workflow node

- **Where:** a new node type in `core/workflows.py` ("Convert LoRA") on the Workflows canvas.
- **Flow:** node with a file param and family; its output feeds the `loras` param of generative nodes; runs once per workflow run before generation.
- **Reuses:** node spec framework, `workflow_inputs` (local file copy, hash check, snapshots), per-node retry.
- **Pros:** fits "everything is a node" for people already building workflows; conversion visible in the run graph.
- **Cons:** LoRA selection today is a name/weight param, not a data socket, so a new socket type and executor rules are needed; stock ComfyUI node types are used elsewhere and a custom one breaks workflow file interchange; conversion is a one-time asset job, not a per-run step; no Generate/Queues/Scripts coverage.
- **Effort:** large.
- **Risks:** model mismatch between asset lifecycle and node graph; per-run reconversion cost or complicated caching.

## Option 7: Windows shell and drag-and-drop entry (Explorer or file-drop on the window)

- **Where:** drop a `.safetensors` on the app window or the LoRA panel; opens the Prepare editor (Option 1) or a minimal convert dialog.
- **Flow:** same as Option 1 or 2 but triggered by drop (an entry point, not a destination).
- **Reuses:** whichever destination it feeds.
- **Pros:** very natural for file-based work; discoverability.
- **Cons:** cannot stand alone; needs a destination option; drop handling across pages and the existing wheel/focus guards.
- **Effort:** small on top of another option.
- **Risks:** accidental drops start long work; no family decision in a drop.

## Option 8: Converter section in the LoRA metadata editor / canonical record view

- **Where:** the "Edit LoRA metadata" overlay (`lora_metadata_overlay.py`, `lora_metadata_edit.py`) reached from the Connection table; a record with a known safetensors origin offers "Convert from source".
- **Flow:** record already has `declared_version`; the overlay offers Convert using the stored origin path (`LoraStore.record_origin`), producing a new `.ckpt` retained under the same record.
- **Reuses:** canonical record, declared family, origin tracking (the same pieces as #327 "download from saved source").
- **Pros:** family is already declared there; ties to #327 naturally; no new page.
- **Cons:** only works for LoRAs dtFlow has already seen; first-time files still need another entry; overlay is metadata-only today.
- **Effort:** medium.
- **Risks:** record semantics (a content record for the source safetensors vs converted ckpt have different sha256); needs #370 canonical changes.

## Option 9: Keep Colab as the only route, with dtFlow guidance and import

- **Where:** a help link and message in Prepare editor (refusal state for safetensors), and registration of the notebook's `.ckpt` plus provenance JSON through existing Prepare.
- **Flow:** user converts in Colab, brings `.ckpt` (and JSON) back, dtFlow reads `model_family`, name, trigger word and hashes.
- **Reuses:** Prepare editor, notebook in `tools/colab`.
- **Pros:** supports every Draw Things family; provenance JSON gives family for free; verified Swift output; very little code (JSON reader).
- **Cons:** needs a browser and ~14 min first build; not "without a Mac, native"; does not use the new Python converter at all, so contradicts the epic's direction.
- **Effort:** small.
- **Risks:** pinned revisions drift; Colab sessions are fragile.

## Option 10: Hybrid layers (not a separate place, a combination space)

Any of 1, 2, 3 can be combined with 4 (auto at run time for already declared families), 5 (CLI for power users) and 9 (fallback for everything unsupported). The descriptions above are kept single-place so they stay comparable; the second agent may score combinations separately.

---

## Comparison table (facts only)

| # | Place | New UI surface | Network/paid at convert | Needs family from | Covers Queues/Workflows/Scripts | Effort |
|---|-------|----------------|-------------------------|-------------------|---------------------------------|--------|
| 1 | Prepare editor | row states | no (upload after) | user row / detector | via prepared LoRA | M |
| 2 | Connection table | button + dialog | no | dialog | via prepared LoRA | S-M |
| 3 | Own page | full page | no | page row | via prepared LoRA | L |
| 4 | Run-time auto | none (status text) | yes, inside run | stored record / detector | yes | M-L |
| 5 | CLI | none | no | flag | CLI only | S |
| 6 | Workflow node | node + socket | per run | node param | workflows only | L |
| 7 | Drop entry | none | no | depends on target | depends | S add-on |
| 8 | Metadata overlay | button | no | declared version | via record | M |
| 9 | Colab only | message | no | provenance JSON | via prepared LoRA | S |

## Open questions that affect every option

1. Family source: user choice only, or build a safetensors tensor-name detector (the Swift importer detects, the port does not)? Ambiguous detection must refuse.
2. Where is the converted `.ckpt` stored and under which canonical record (source sha256 vs converted sha256), and does re-conversion after a converter or revision bump overwrite? (The converter never overwrites; dtFlow must pick a new name.)
3. Is the first real DT+ acceptance run (Krea 2, Flux 1) a precondition for any GUI work? The issue lists it before smoke.
4. How does SDXL "experimental" appear: blocked behind a confirmation, badge, or hidden?
5. Packaging: is `tools/drawthings/lora_to_ckpt.py` importable from the Windows build (it lives under `tools/`, not `src/dtflow/`)? Any GUI or run-time option needs it moved or exposed as a service module.
6. Q8P: converter writes f16 only, but the Prepare editor offers F16/Q8P.

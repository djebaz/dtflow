# Ranking: where the LoRA convert feature goes (issue #357)

Independent judge of `options.md`. Read-only. Checked against djebaz/dtflow main (23f1254) and issue #357 plus its newest comment.

## Claims verified in the repo
- Converter lives in `tools/drawthings/lora_to_ckpt.py`. `pyproject.toml` builds only `packages = ["src/dtflow"]`, and a comment calls `tools/` a "source-only root namespace". So the converter is NOT importable from a packaged Windows build. Any GUI or run-time option first needs it moved into `src/dtflow/services/` (or exposed as a service module). This is a hard prerequisite, not just an open question. (The CLI option 5 has the same problem.)
- `LoraPanel.prepare()` picker is `"LoRA files (*.ckpt)"` titled "Prepare converted LoRA files", and `prepare` returns early when backend is not cloud. Confirmed: the editor is cloud-only and ckpt-only today.
- `detect_lora_family` (`services/lora_family.py`) reads only SQLite tensor tables of converted files. Confirmed. A safetensors detector is new code, but the tensor-name signals already exist in `lora_mappings.json` and `inspect_lora_families.py`, so it is a modest job. Djé's note that it will arrive is credible.
- Issue #357 step 1 asks for exactly: call the converter for the five known families in the background, Colab fallback for the rest, family from user or detector, "experimental/unverified" labels. Step 2 asks for a Krea 2 and Flux 1 DT+ run first. Djé's Windows smoke of Krea 2 and Z Image Turbo has now satisfied most of that for two families. Flux 1 and SD 1.x are still not run on DT+.

## Ranking (best to worst)

1. **Option 1: Prepare editor.** Best fit. It is where users already turn a local file into a DT+ LoRA, and it already holds family, managed name, authorization and the cloud gate. Batch comes free, and it keeps one path into `AssetService.prepare` and the canonical record (#370). The family dropdown is a fine temporary fallback until the detector lands, and the detector slots into the existing "suggested/detected/manual" column. Cons are manageable: force f16 for converted rows, show per-row convert-failure state, keep the intermediate ckpt in `LoraStore`.
2. **Option 8: metadata editor "convert from source".** Good second layer, not an entry point. It is the natural home for "reconvert after converter/revision bump" and ties to #327 saved-source. It only works for LoRAs dtFlow already knows, so it follows Option 1.
3. **Option 7: drag and drop.** Cheap discoverability add-on that opens Option 1. Only worth it if it pre-fills the editor and never starts work by itself. Cannot stand alone.
4. **Option 4: automatic at run time.** The right long-term destination (matches #326: automatic, no consent), but wrong as a first step. It needs a stored, trusted family and a mature detector, converts inside a paid run, and cannot ask anything. Do it later, restricted to LoRAs that went through Option 1 once, so the family decision and conversion already exist and the run time step is only "upload the retained converted ckpt" (which #326/#327 already do). In that form most of Option 4 is free.
5. **Option 5: CLI.** Cheapest and a fine developer/smoke tool, but does not meet the acceptance for GUI users, and needs the packaging move anyway. Reasonable as a by-product, not a deliverable.
6. **Option 2: Connection table button.** Splits convert from upload across two pages, blurs the retained-LoRA inventory, duplicates the family picker. Mildly useful only as a "local only, no cloud" convert for users on a non-cloud backend, which is not the Windows target.
7. **Option 9: Colab only.** Keep it as the fallback message for unsupported families (a required part of every option), but not as the feature. Provenance JSON import is a small optional extra.
8. **Option 3: dedicated page.** Biggest surface for a feature that is a one-time step per LoRA. Needs design mockups, nav changes. Not justified.
9. **Option 6: workflow node.** Worst. LoRAs are name/weight params, not sockets, a custom node type breaks ComfyUI interchange, and conversion is an asset job, not a per-run step.

## Combinations
- **Best: 1 primary + 8 later + (7 drop) + 4 later in restricted form.** Option 9 stays as the refusal/fallback text, and 5 comes free if wanted.
- Avoid pairing 1 with 2 or 3: three places that pick a family and name a LoRA is exactly the parallel logic #370 tries to remove.

## Recommendation and first PR scope
Make the Prepare editor accept `.safetensors` (Option 1). First PR, kept small:
1. Move the converter into `src/dtflow/services/` (thin wrapper or move, keep `tools/` script importing it) so it ships. Record converter version and DT revision db37e65 as constants.
2. Picker accepts `*.safetensors` too. Safetensors rows get a mandatory family dropdown limited to the verified families (Krea 2, Z Image Turbo, Flux 1, SD 1.x), plus SDXL behind an explicit "experimental" choice that needs a confirmation. Anything else gets the converter's own error and the Colab hint.
3. On authorize: convert on the runner thread, retain the ckpt in `LoraStore` with converter version and source sha, hash it, then continue into the normal `AssetService.prepare`. Hide Q8P for converted rows (f16 only). Never overwrite: new name on reconversion.
4. Per-row failure state with the converter message shown as is.
Leave out: detector, drop-zone, run-time auto, metadata overlay, CLI. Ship the detector as PR 2 (refuse ambiguity, prefill the dropdown, keep it editable), then 8/7, then restricted 4.
Gate for Djé's smoke: Flux 1 and SD 1.x are not DT+ verified yet. Mark them "unverified on DT+" until he runs them, as he did for Krea 2 and Z Image Turbo.

## Assumptions and questions for Djé
1. OK to move the converter under `src/dtflow/` (needed for packaging)? Assumed yes.
2. Family list in the first PR: only Krea 2 and Z Image Turbo (smoked), or also Flux 1 and SD 1.x with an "unverified" badge? I suggest the latter, badge removed after your smoke.
3. SDXL: hidden, or visible with a confirmation? I suggest confirmation since you called it experimental and a bad upload costs credits.
4. Should the source safetensors be kept (origin path recorded) or can dtFlow forget it after conversion? Option 8 and #327 need the origin path recorded.
5. Which record owns the file: converted ckpt sha (my assumption, so Prepare/preflight stay unchanged) with the source sha stored as origin metadata.
6. Non-cloud backends: assumed out of scope (Windows/DT+ only), so the editor's cloud gate stays.

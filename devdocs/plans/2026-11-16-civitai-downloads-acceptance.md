# Civitai downloads: acceptance record and residual findings (epic #327, close-out #469)

Recorded 2026-11-16 from the merged PRs and their issue comments, checked against `main` at `c75d9902`. It adds no new smoke result: a scenario that the sources below do not report as observed is listed as not observed. GitHub Actions credits were exhausted for the whole epic, so no GitHub CI run backs any child; the checks that ran are the ones named in each PR.

## Delivered

| Child | PR | What |
| --- | --- | --- |
| #459 design note | #472 | Contract, storage, family alias table, mockups ([design note](2026-11-15-civitai-downloads-design.md)) |
| #460 credentials card | #471 | Download credentials card (token in Credential Manager) |
| #464 source registry | #474 | Additive `lora_sources` table, `services/lora_sources.py` |
| #462 Civitai client | #473 | Metadata, safe download, connection dot, `dtflow assets download` |
| #463 download and convert | #475 | `lora_convert/data/families.json`, `lora_download.py` |
| #465 Downloads view | #476 | Status-bar pill, F9, Links / IDs tab |
| #466 Browse Civitai | #477 | Gallery tab, multi-family filter, auto-prepare |
| #479 follow-up | #480 | Tag pills, search filters, seven sort choices |

Not delivered, by owner decision: Hugging Face (#468, closed), portable source list (#467) and the standalone metadata client (#461, merged into #462). Released in [6.4.0](../releases/v6.4.0.md).

## Owner Windows smoke observed

- A real Civitai token saved in the card gave a green connection dot (#473).
- Downloads and Auto-prepare reported to work "very well"; after a restart a previously auto-prepared download is restored as **Prepared** (#477, 2026-11-15).
- Browse Civitai on 2026-11-16 (#480): seven sort choices without Oldest and a sort change updating results; empty prompt, **No results** and **Search further**; persistence of keyword, sort, families, tags and both AND/OR modes; tag pills, hover Download button, direct download for one file and the chooser for several; reasonable request volume while scrolling.
- Prepare reuse, DT+ storage and q8p smokes belong to other epics and are recorded in the [6.4.0 audit](2026-11-16-release-6.4.0.md).

## Executed by the agents on Linux (not Windows)

- Public SD 1.5 LoRA (version 87153) through `dtflow assets download` without a token: SHA-256 verified, converted, source deleted (#475). A public Qwen LoRA (2585269) was refused by the family gate with the state directory left empty.
- A real Krea 2 LoRA with a faked download converted; the same file labelled SD 1.5 ended as not convertible with the detector note (#475).
- Mocked transport (#473): redirect to a second host with no `Authorization` sent there, filename sanitising, hash mismatch and HTML bodies rejected, 404 reported as missing. A made-up token gave a live 401.

## Not observed (no claim made)

- **Gated-file redirect and token test (step 0, #462 AC-14).** No result is recorded in #459, #462 or the design note, which still marks it untested: whether the download hop redirects for a gated file, to which host, and whether that host needs the token; whether civitai.red resolves gated ids; the `availability` values and `trainedWords` shape of a gated version. The engine sends the token only to civitai.com and drops it on any other host, so a second host that needs the token would fail with a plain Civitai-style error until an allowlist is added. This is the one open test the epic asked for.
- `dtflow assets download` with the saved token and with `CIVITAI_TOKEN` (#462 AC-13): not run by the owner; the output field `token_source` was not observed against a real token.
- Red, amber and grey connection dots: seen only with mocked responses, a live 401 for a made-up token and an offscreen start (#473).
- 429 handling and `Retry-After`; early-access, private and removed files on Windows.
- Real downloads through the app for Z Image, Flux 1 and SDXL. Their converters are verified separately on local files ([lora-ckpt-format.md](../guides/lora-ckpt-format.md)); the download-then-convert path itself ran only for SD 1.x (CLI) and Krea 2 (faked download).
- LoKr, LoHa and mislabeled-file refusal on a real file (the refusal path ran only on the mislabeled Krea 2 file).
- Switching DT+ key or backend after a download was prepared: source-reviewed only (#477).
- Behaviour of Civitai's full-text `query` for tag-name-only matches, the NSFW "may be withheld" note in a gated region, and global ordering across Browse streams: documented limits, not verified.
- No GitHub CI run exists for any child (credits exhausted). The owner's local full suite on #480 head `3f01a935` was 700 passed, 3 skipped.

## Residual findings

1. **Orphan sweeps did not run in the app (fixed in #497).** `civitai.cleanup_staging` was called only by the CLI download and `lora_sources.sweep_orphans` nowhere; #473 AC-8 and #474 AC-11 had left the startup call to #465. The app now runs both once at startup, off the GUI thread (`services.downloads.startup_cleanup`).
2. **Stale SDXL comments in code.** `services/lora_convert/families/sdxl.py` still says "Experimental" and "requires allow_experimental" while `families.json` and `lora-ckpt-format.md` say verified (owner promotion 2026-11-15) and the Downloads path no longer needs consent. Behaviour follows `families.json`; only the comments disagree.
3. **Release note gaps.** [6.4.0](../releases/v6.4.0.md) does not say that a downloaded `.safetensors` is permanently deleted after a successful conversion (and kept when refused), nor that gated files need the token. Both are in the desktop guide now.
4. Some SDXL LoRAs still fail to convert, so a "downloaded, not convertible" SDXL row is expected for them.
5. Hugging Face is not available; tokens and downloads are Civitai only.

## Documentation checked in this PR

The desktop guide covered Browse and Auto-prepare but not Links / IDs, the downloads list, file retention, the token or the Prepare entry; the CLI guide had no `assets download`; the credentials guide had no Download credentials card; the architecture note had no families file or source registry and listed SDXL as experimental; the runtime layout had no `lora_sources/`. All five are updated to match the merged code.

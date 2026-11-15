# Design note: LoRA download sources, Civitai contract and Downloads UI

Issue #459, child of epic #327. Date: 2026-11-15. Mockups: [`devdocs/mockups/civitai_downloads_view.html`](../mockups/civitai_downloads_view.html).

This note settles only what the other children (#460, #464, #462, #463, #465, #466, #468, #469) depend on. Every decision below was made by the owner on 2026-11-15 unless marked otherwise. Contract points are marked **documented** (stated by Civitai's developer pages), **observed** (seen in a live request on the date given) or **untested** (needs the owner's real token; listed as steps for #462).

## 1. Storage

- `assets.sqlite3` is at `SCHEMA_VERSION = 6`; `_check_version` refuses any other `user_version`, with no migration (the owner resets `.dtflow` by hand, as in #370). It also holds DT+ receipts and `lora_records`.
- `custom_loras.json` is replaced by Connection's Import JSON and deleted by Clear, so it cannot hold provenance.
- `LoraStore` is `.ckpt`-only: `retain()` accepts only `.ckpt` managed names and copies bytes again, `retain_converted()` adopts a staged `.ckpt` only.
- **Decision:** source metadata is an **additive table in `assets.sqlite3`** beside `lora_records`, with **no schema bump**. Schema statements run only when a writer opens the database; a read-only open creates nothing, writers hold an exclusive lock, and a second writer gets `AssetsBusyError`. Readers therefore tolerate a missing table, and the download's completion write uses a short scoped writer (details and ACs in #464).
- A version bump stays allowed if ever needed. It means a manual `.dtflow` reset and the loss of DT+ receipts and retained copies, so that PR must say so.
- Downloaded `.safetensors` bytes live in a sibling `lora_sources/` directory; the staged file is adopted in place (bytes written once).
- After a **successful conversion the `.safetensors` is permanently deleted** (not moved to the trash). The source row stays as provenance, marked "source file deleted". A source whose conversion failed or was refused is kept so it can be retried or removed. Consequence: re-converting after the first `CONVERTER_VERSION` bump (#403) means downloading the file again.

## 2. Families and supported models

- The list of supported families is **global data**, not Civitai-specific: one JSON file in `lora_convert/data/`, read by the converter registry (today the list exists only in `registry.py`).
- Each family carries **provider label aliases**; Hugging Face adds its own later. The gallery, the Links/IDs tab and the CLI share the file, so a new family is a JSON entry plus converter support, not a view change.
- Only models of a listed family are downloadable. The gallery lists only mapped base models; a pasted link with an unmapped label has Download disabled with the reason.
- All listed families convert automatically after download. SDXL passes the experimental consent automatically (owner: "sdxl works well now") and its row keeps the experimental status.
- The converter's own tensor matching is the safety net: a mislabeled upload ends as "downloaded, not convertible". The file detector is an optional cross-check.
- One **Download** action; no second "Download and convert" button.

### Alias table (checked against the live enum; owner approval pending)

Source: `GET https://civitai.com/api/v1/enums`, field `ActiveBaseModel` (82 labels), read 2026-11-15. The developer docs only give examples. Civitai publishes no official compatibility matrix, so architecture relations are community or vendor statements.

| Family | Civitai `baseModel` labels | Basis |
|---|---|---|
| Krea 2 | `Krea 2`, `Krea 2 Turbo` | owner |
| Z Image | `ZImageTurbo`, `ZImageBase` | owner's families list |
| Flux 1 | `Flux.1 D`, `Flux.1 S`, `Flux.1 Kontext`, `Flux.1 Krea` | Krea is built on the FLUX.1 [dev] architecture (some dev LoRAs transfer, some not); Kontext runs on the dev inference code, but no source states its LoRA layout equals dev: included on the owner's word |
| SD 1.x | `SD 1.4`, `SD 1.5` | live enum |
| SDXL | `SDXL 1.0`, `Pony`, `Illustrious`, `NoobAI`, `SDXL Lightning` | Pony, Illustrious and NoobAI are SDXL-derived (community sources); SDXL-Lightning is distilled from SDXL and ships as LoRA (arXiv 2402.13929); Pony and Illustrious also on the owner's word |

Left out: `Pony V7` (AuraFlow-based, not SDXL), `SDXL Turbo`, `SDXL Hyper`, `SDXL 1.0 LCM`, `SDXL 0.9`, `SDXL Distilled`, `SD 1.5 LCM`, `SD 1.5 Hyper` (no confirming source), `Krea 3` (no converter evidence), and every label without a converter (`Flux.2 D`, `Qwen`, `SD 2.x`, `Chroma`, video and audio families). There is no plain "SD 1" label. Caveat: NoobAI has epsilon and v-prediction variants that the label does not tell apart. Adding a label later is a JSON edit.

## 3. Explicit download, then convert and Prepare

- Every download is an explicit action. No saved-source "Download missing" and no automatic-download switch in this epic; a downloaded file records its source as provenance only.
- After download the app converts. The row offers **Prepare…**, which opens the existing Prepare editor (a new entry point, built in #465), disabled with the reason "Prepare needs DT+ Cloud" when the backend is not cloud. DT+ upload never happens automatically.
- Civitai trigger words are stored as provenance and prefill the Prepare trigger-words field for the user to confirm; the default short name comes from the existing filename helper.
- Revisit auto-fetch when workflows or queues can reference a missing LoRA.

## 4. CLI token

`dtflow assets download` reads `CIVITAI_TOKEN` from the environment (CLI only). The app uses the Windows Credential Manager (#460).

## 5. Civitai contract

Sources: the official developer pages (models, model versions, errors, enums), compared with the owner's capture scripts and PowerShell downloader.

| Point | Status |
|---|---|
| Token goes as `Authorization: Bearer`, or `?token=`. DTFlow uses the header only (a query token would leak into URLs and logs). | documented |
| `files[].downloadUrl` is `https://civitai.com/api/download/models/{modelVersionId}`, the endpoint the PowerShell script uses. | documented |
| Endpoints `GET /api/v1/models`, `/models/{id}`, `/model-versions/{id}`, `/model-versions/by-hash/{hash}`; models and versions are public or mixed, `favorites` and `hidden` need a token. | documented |
| `files[].hashes` include SHA256 (also AutoV1/V2/V3, CRC32, BLAKE3), so a published SHA-256 can be verified. `files[].primary` and `primaryFileOnly=true` match the owner's scripts. | documented |
| 401 missing or invalid token, 403 valid token without permission, 404 unknown or unpublished. | documented |
| Rate limits: no published per-endpoint limit; edge limits are "operational, not a published SLA"; most failures expose no `Retry-After`. DTFlow follows #220: at most one automatic retry, for connectivity or timeout failures only, none on 429. | documented (retry policy is DTFlow's) |
| `nsfw=true` includes mature content but is ignored in SFW-gated regions, so "always shown" can fail; the gallery shows a note when content is withheld. | documented |
| `GET /api/v1/enums` is reachable without a token on civitai.com and civitai.red and returns `ModelType`, `ModelFileType` and `ActiveBaseModel`. | observed 2026-11-15 |
| `GET /api/v1/model-versions/{id}` for one public version returns the same record (same `modelId`, same fields) on civitai.com and civitai.red. | observed 2026-11-15, public version only |
| A public file's `GET /api/download/models/{id}` without a token answers 307 to a signed `civitai-delivery-worker-prod.<id>.r2.cloudflarestorage.com` URL (`X-Amz-Expires=86400`) on both hosts. That is a different host from the API host. | observed 2026-11-15, public file, no token |
| Whether the token is required or harmful on the redirect hop, and the redirect host for a **gated** file. | **untested**, see step 0 |
| `Retry-After` on 429, the full set of `availability` values, what `trainedWords` contains, whether civitai.red resolves gated ids. | **untested**, see step 0 |

### Exact gallery filters (AC-3)

- `types=LORA` is an exact filter against the live `ModelType` list. Workflows and the other non-LoRA types are excluded exactly. LoKr and LoHa are **not** types and nothing in `files[].metadata` (only `format`, `size`, `fp`) exposes the algorithm, so the converter's refusal after download is the real check.
- `baseModels=` is an exact filter against `ActiveBaseModel`. The gallery sends only the labels mapped in the families file; the app does not depend on the live list at runtime.
- Paging: `limit` 1 to 100, `cursor` from `metadata.nextCursor`. `page × limit > 1000` returns 429, so the gallery uses cursor paging. `query` with `page` returns 400.

### Metadata host (AC-4)

**Decision: civitai.com** for metadata and download (the host the developer pages document and the PowerShell script uses). civitai.red is not used. Version ids resolve identically on both hosts for the public version checked; gated ids on civitai.red are untested and stay so unless the owner asks for that host.

### Redirect and token (decided)

The engine is safe by default: the token goes only to the civitai.com download host and is dropped on any other host (curl's default behaviour, no `--location-trusted`, which is what the owner's working script does). The first hop works with the token. The R2 host observed for public files suggests the second host needs no token; for a gated file that is an inference until step 0 runs. If the second host turns out to need the token, #462 adds an allowlist for that CDN host. Signed redirect URLs are never persisted or logged (the observed URL is deliberately not copied here).

## 6. Steps for #462 (need the owner's real token)

**Step 0, one verbose download of a gated file** (`curl.exe --location --verbose`, Bearer header, output discarded). Record, without the token or the signed query string:
1. Each hop's status, host and whether the `Authorization` header was sent.
2. The final host, and whether that hop succeeded without the token.
3. Whether the same id downloads from civitai.red.
4. `Retry-After` on a 429 if one can be provoked cheaply (otherwise leave untested).
5. The `availability` values and the shape of `trainedWords` on a real gated version's JSON.

Results go into #462's description; if step 2 fails without the token, add the CDN allowlist before the engine ships.

## 7. Downloads UI

Mockups cover every item below (AC-6).

- **One full Downloads view**, a full workspace page like the other tabs (not a Logs-style overlay), reached from a pill in the right side of the status bar. Shell, pill and F9 are built in #465. The view has two tabs, **Links / IDs** and **Browse Civitai**, a header showing "token saved" or "token missing" (linking to the credentials card), and the downloads list below both tabs.
- **Pill:** the usage-pill shape (`QLabel#usagePill` in `dtflow_dark.qss`: small `size.radiusXs` radius, 1 px border, 8 pt strong text, 0 × 7 px padding, status-bar height), but a clickable control. Dimmed navigation blue (`color.selection`, `color.selectionBorder`, `color.selectionText`), lighter on hover with the existing `color.selectionHover`; no new token. Label "Downloads", or `⬇ 2` while transfers run; success and failure use the existing status colours. Placement: after the usage pill, before the Connection dock. Tooltip "Open Downloads (F9)".
- **Shortcut F9.** In use: F1 Generate, F2 Workflows, F3 Queues, F4 Images, F5 follow, F6 slideshow, F10 Logs, F11 fullscreen, F12 Connection. F7, F8 and F9 are free; F9 sits next to Logs and Connection.
- **Concurrency:** at most 3 transfers run at once; the rest show "Queued". The shared activity bar shows one aggregate line ("Downloading 2 of 3"); the per-file list lives in the view.
- **Row states:** Queued, Downloading (progress, Cancel), Converting, Converted (Prepare…, with the cloud-only disabled state), Downloaded not convertible (file kept, Remove), Failed (Retry, always manual), Cancelled. Failure text keeps Civitai's wording and never contains a token, for example "Download failed: Civitai says this model needs a login. Add your token in Download credentials."
- **Connection card (AC-5):** in `src/dtflow/ui/forms/main_window.ui` (read 2026-11-15) the Connection page's group boxes are, in order, `connectionBox`, `credentialBox` (DT+ credentials), `rememberedFoldersBox`. The **Download credentials** card goes between DT+ credentials and Remembered folders; Remembered folders then stays last without being moved. It holds the masked Civitai token field, Save, Remove, a status, the line "Tokens stay in Windows Credential Manager." and an "Open Downloads" link. The Hugging Face field is added with #468. The Custom LoRAs panel stays small, shows source and state per downloaded entry and gets a "Downloads…" link. Generate's LoRA editor gets no download controls.

### Gallery card (owner feedback, 2026-11-15)

Cards are **square covers** (CSS `object-fit: cover`, 1:1) with overlays, in the spirit of the owner's gallery generator (`build_civitai_gallery.py`, which shows name, creator, base model, downloads, thumbs up, NSFW level, tags and trigger words). Every overlay field below was read from a live `GET /api/v1/models?types=LORA&...&nsfw=true` response on 2026-11-15 (observed); fields the API did not return are not used.

| Overlay | Shown | API field |
|---|---|---|
| Cover image | always | First still image of the representative supported version; video covers fall back to the first still image, else a placeholder |
| Name | bottom | `name` |
| Creator | bottom | `creator.username` |
| Downloads | bottom | Representative version's `stats.downloadCount` (model-level `stats.downloadCount` also exists) |
| Thumbs up | bottom | Representative version's `stats.thumbsUpCount` |
| Version count | bottom | Distinct downloadable Civitai version IDs from the listed model versions |
| Family chips, top right | always | Every distinct supported DTFlow family across the model's listed versions, including when filtering by one family |
| Civitai base model | in explicit version selectors | Each version's own `baseModel` (e.g. Pony, Flux.1 D), shown with its family and version ID |
| NSFW badge, top left | when the cover is above the lowest level | cover image `nsfwLevel` (the level scale is checked in #466) |
| Early-access flag, top left | when set | `modelVersions[0].availability` (observed value `Public`; other values untested) |
| Hover panel | on hover | version `name`, `trainedWords` (empty for many LoRAs, so shown only when present), model `tags`, `stats.thumbsUpCount` and `thumbsDownCount`, `publishedAt` |

**Browse Civitai interaction (owner revision, 2026-11-15):** One card represents a Civitai model, not one of its versions. The compact square card shows all distinct supported families. Its Download dropdown has checkboxes for each exact supported version/file, with name, Civitai base model, DTFlow family and version ID; its **Download selected (N)** immediately downloads those checked versions with no review or confirmation step. Clicking that Download button must not select the card. A selected set of cards reveals a compact version/file table just above the gallery Download selected button, with the same exact identities and checkboxes. Bulk selection defaults to no checked versions; **Select all** is explicit, and N counts files, not cards. Existing queued or converted files are not implicitly re-downloaded. All paths reuse DownloadsPage's existing enqueue and conversion pipeline.

**Continuous scroll:** The gallery requests 20 Civitai models per cursor batch (below the API maximum of 100, to avoid oversized or slow multi-version responses), appends supported cards on scroll, and has no visible page numbers or Prev/Next controls. Fetched card metadata persists in memory during the browse session; encoded previews use the bounded memory cache and decoded pixmaps use a separate bounded LRU. Covers are loaded near the viewport instead of scheduling all at once. A rare succession of entirely filtered-out batches pauses automatic requests and offers a manual **Search further** recovery action, preventing an uncontrolled fetch loop. No previews are persisted to disk.

Thumbnails use the image `url`; `.../width=320/<id>.jpeg` returned a 301 to a 200 `image/jpeg` of about 27 kB, whereas the `original=true` path also redirects (observed, public images, no token). The client downscales and caches; #466 decides the exact size parameter.

## 8. Open points

None block #460. The alias table awaits the owner's approval; the gated-file checks above are #462 step 0.

# Desktop guide

DTFlow's desktop application has Connection, Generate, Queues and Images pages. Connection, Generate and Queues share navigation on the left, the workspace in the center and an Inspector on the right. The panel icon beside the brand toggles details. Images uses the same logo/navigation in two aligned header rows and hides the content rails; returning restores splitter geometry and the shared Inspector choice.

## Inline actions and progress

DTFlow-owned confirmations, errors, results, About, metadata inspection, preset naming and
checkpoint/LoRA editors stay inside the main window. Native file/folder pickers and ordinary
menus remain available. No confirmation opens a separate window or nested dialog event loop.

A guarded action first displays its explanation and changes only its original button to
**Confirm**, red for Delete/Clear and mint/teal (matching Auto-refresh ON) otherwise, without adding Cancel to the action row. **Cancel** is in the shared
bottom feedback overlay. Nothing executes on the first activation. Cancel, Escape, navigation,
provider changes and relevant selection/filter changes discard pending consent; there is no
timer. Confirm keeps frozen file/queue/Trash scope, with busy checks and destructive-service
revalidation still applied. A double-click does not count as two consent activations. Unrelated notices
do not discard an armed Confirm; they appear as separately dismissible
feedback rows. Queue and Generate activity subscriptions are released when
their Qt pages are destroyed.
Closing during queue dispatch uses an inline **Confirm close** action because the native
window close button cannot change its label; closing during recycling requests a safe stop
and waits for the result instead of exiting mid-file. Programmatic closing follows the same
consent; a refused close does not shut down the runner. Once shutdown begins, polling timers
stop and already-queued UI results cannot restart background work.

Prepare a converted cloud LoRA by choosing the file; preparation begins immediately
after selection, with no second Confirm. Cancelling the picker does nothing.
The asset note and Prepare tooltip warn that DT+ upload may occur without upload
credits, while subsequent generation may consume credits. The existing backend
identity/busy checks, receipt verification and uncertain-upload no-retry policy remain.

Checkpoint search and exact custom identifiers use one expanded editor. **Add LoRA** shows
search, identifier and weight together and commits once with **Add**. New UI selections use
0.0–3.0 weights in 0.1 steps (one decimal); 0.0 is preserved. A catalogue default outside
this range starts the editor at 1.0, not a silently clipped default. Existing imported/saved
requests retain their recorded weights and existing validation; this does not change the
CLI or provider weight contract.

Confirmations, editors, progress and results use one thin bottom overlay above content, across tabs;
it is hidden when idle and reserves no empty layout space. Labelled rows distinguish
concurrent export, Trash, gallery selection and generation/queue work. Unknown totals are
indeterminate, not guessed percentages; queue counts mean settled items, not sampler steps.
Stop/Cancel delegates to the original controller, preserving completed copies/moves and
existing uncertainty semantics. Generation preview and the compact Run control remain.
Essential information is visible directly; long messages wrap without a Details button. Export starts directly without confirmation. Successful operation results show a teal 2 px draining lifetime line, an optional seconds label and a 450 ms fade after six seconds, paused while any overlay row is hovered or focused. Blue 5 px task progress remains a separate measure of actual completed work; amber warning/partial/stopped overlays use the same six-second countdown, amber 2 px draining line and fade; red errors and informational inspections remain persistent. Dismissing an amber notification does not modify an uncertain attempt, its recorded evidence or its recovery/retry rules. Unrelated notices cannot discard an active editor; Escape dismisses a plain notice. Informational inspections (for example About DTFlow) also close with their × button or a click anywhere outside them; errors and confirmations ignore outside clicks and keep Cancel/Dismiss semantics. Dismissing a result never cancels work. Showing/hiding the overlay does not resize the main content or reserve idle space.
This implementation still requires owner runtime/visual and packaged acceptance.

## Launch

From the repository root after `uv sync`:

```bash
uv run dtflow gui
uv run dtflow -x gui
uv run dtflow gui -nc
```

There is no menu bar: About DTFlow is the small help icon at the right of the bottom status bar. The Draw Things provider library loads in the background after the window first paints; with `-x` the log also records one `dtflow.startup` line of per-phase startup timings (Python and Qt imports, forms, pages, session restore, window shown, first paint, background library load, and first font/text-field costs).

`-x/--dev` adds sanitized diagnostic detail only; for image removal it adds the preflight counts, operation start, per-image refusal stages, Windows helper refusal phases, original-file readiness reasons and contended or timed-out leases, while normal mode keeps one finish line per removal and reconciliation pass plus per-item failure warnings; `-nc/--no-cache` disables persistent disposable-cache reads and writes for that run. Logs use repository `logs/` in development. See [development tools](development.md) and [runtime layout](../architecture/runtime-layout.md).

For a portable Windows executable that launches the GUI directly, see [the build guide](building.md).

## Connect

Select the fake, local Draw Things or DT+ cloud provider on Connection. The local provider needs its server endpoint. Connection checks run in the background and never generate. The Generate page uses the selected provider and endpoint; switching provider resets connection results.

On Windows, save or remove a DT+ API key through Credential Manager. `DRAWTHINGS_API_KEY` in the environment takes precedence over the saved key; an empty or invalid selected credential fails without switching sources. Other platforms do not offer the Windows secure-storage path; use the environment. Keep credentials out of configurations, logs and diagnostic reports.

Refresh inventory to populate model and LoRA choices. Startup and provider returns restore the last successful matching observation: first in-run memory, then the existing disk cache. The UI always shows model and LoRA counts, cached/remembered/refreshed provenance and the original observation time whenever an observation is known—even if no live refresh happened during this launch. Refreshing or a failed response adds status alongside those stats; a failure keeps the previous choices and selections with "Refresh failed · showing previous inventory". Switching providers preserves each provider's form before rebuilding discovery. Local endpoint or selected cloud credential changes invalidate mismatched evidence; saving the same key (or changing an unused saved key when the environment key is selected) does not erase it. Generation always refreshes live. Inventory listing alone does not prove workload compatibility. When the server supplies no model metadata, listed files are offered as model candidates, using the shared request gate.

## Export saved images

In Images, click **Export** to toggle the export panel beside the listing on the right, even if no images are checked. The button is neutral when closed and blue/selected when open; it is always available for opening settings. Before starting **Export JPEGs**, check at least one image. Exports include checks hidden by filters; there is no scope dropdown in Images. Connection retains the scope dropdown. No tab switch or export happens until you click **Export JPEGs**. You can also use **Export saved images** in Connection's middle panel: these are the same controls, settings and running operation. Other scopes are all saved images, the current Images filter across every page, the single image selected in Images, or the selected PNG result in Generate. Selecting Generate's metadata entry is not selecting an image. Export freezes identities when started; new outputs or changed checks afterward do not expand that operation.

Click **Export** again or **Close export panel** to hide the Images column. Hiding it does not cancel export; use **Cancel export** to stop after the current image. Switching to Connection shows the same progress and controls; returning to Images remembers its open column. Destination/privacy settings and active progress are preserved across navigation.

The export folder is remembered separately for Windows and WSL, so installations that share one state directory keep their own folders; the privacy, JSON and noise/blank choices are common. A folder remembered by the other environment is not shown or converted. You can type an absolute folder path that does not yet exist; export creates it and missing parents before writing. Permission/path failures are reported, not redirected.

Click **Export JPEGs**. Normal images go directly into the chosen folder: **no
attempt, provider or date subfolders**. Filenames use saved-at UTC time, a short
prompt label, request seed, full attempt ID marker and output index, for example:
`20261009-143025Z_red-fox-in-snow_s12345_dtf-0123456789abcdef0123456789abcdef-01.jpg`.
Existing files are never overwritten; collisions add a suffix. The timestamp is
saved time, not a claimed generation start time. The seed is the recorded request
seed, not an independently verified per-image seed.

**Separate detected noise and blank images into subfolders** is checked by default and remembered. Flagged copies go only into `noise/` or `blank/`, with any JSON metadata beside them; they are not also written at root. Folders are created only when used. Uncheck for a completely flat export and no detector analysis. The shared v2 detector uses pixel statistics on the white-composited source before JPEG encoding; flags are provisional, not aesthetics/prompt checks or job-status changes. Review them manually. The completion summary includes routed counts.

Re-exporting lists root, `noise/` and `blank/` once. New full-ID filenames skip matching copies before processing; older/renamed files use EXIF fallback. No destination index is written. Missing copies are exported again; existing files are never rewritten, so changed privacy/routing/sidecar choices require a new folder. The fast presence check checks JPEG framing, not every pixel or sidecar. Old `.dtflow-export-index.json` files are unused and left untouched; you can delete them manually.

Source checksums and advisory detection results are centrally cached at `<state>/cache/derived/image_analysis.json`, independent of export folder. New destinations reuse these computations when source fingerprints/detector version match, but still need JPEG conversion. Cache contains no prompts or destination records; **Clear derived cache**, **Clear all cache** and `--no-cache` apply to it. Cache misses recompute locally without provider calls.
New Windows copies have **Date created** set to the recorded successful saved-output time; EXIF **Date taken** uses the same UTC date with its offset. This is a generation-completion proxy: exact generation start/end was not recorded, so export cannot recreate it. Filename dates keep the same saved-time basis. Skipped/existing files are not retimestamped. On WSL and other non-Windows runtimes the file creation time cannot be set; the summary shows an informational note that EXIF keeps the saved date, and a completed export stays green. Failed items, cache fallback, unsaved preferences, unavailable dates and other genuine warnings still make the result amber. Timestamp-setting failures warn without discarding written copies; JSON sidecars receive the same creation time when written.

JPEG quality 95 / 4:4:4 is **lossy**; transparency is composited on white. Original
PNGs, metadata and history stay untouched. Right-click an exported JPG in Windows
Explorer → Properties → Details for Title, Subject, Comments and Tags. With prompts enabled, EXIF ImageDescription also includes a short ASCII-compatible prompt summary; full Unicode prompt text remains in the embedded JSON. EXIF also
carries rich portable JSON generation metadata: requested/effective settings,
model/LoRA identifiers and weights, provenance, dimensions, recorded timing and build
identity when available. Some viewers/sharing services may hide or strip metadata.

Prompts are included by default. Uncheck **Include prompts in filenames and
metadata** before sharing if they are private; this removes prompts from all export
fields, including JSON. Private paths, server identities, credentials and prepared
cloud-asset references are never deliberately copied into export metadata.
**Also save JSON metadata beside each image** is optional; a JSON sidecar becomes
mandatory if full portable metadata exceeds EXIF capacity, with a reported warning.

The folder and privacy/sidecar/routing choices are remembered independently of cache.
**Open folder** opens the last successful destination. **Cancel export** stops after
the current image and keeps completed exports. The final summary reports exported, skipped,
failed and unprocessed images; hover over it for the first 100 failure details.
Missing/corrupt images fail individually rather than hiding other saved outputs.
A single operation is capped at 10,000 images, 64 MiB encoded input per image and
32 million decoded pixels; narrow the Images filter to export larger libraries in
separate operations. Export is local-only and never generates or contacts a provider.

## Generate

Set prompt, negative prompt, exact model, seed, dimensions, steps, guidance and sampler. After inventory refresh, use All/Image/Video to narrow classified models, choose a base-model family, then choose the exact available checkpoint/quantization. Prime checkpoints are the default; `Include niche models` adds available Niche entries without replacing Prime. If the provider exposes a checkpoint DTFlow has not classified, the page reports it as unclassified, lists it only under the separate "Unclassified" base-model entry and keeps the editable exact-ID field as the explicit escape hatch rather than guessing its family or mode. Companion/support files (encoders, VAEs, upscalers, preprocessors) are hidden from model lists and counted as other files. For checkpoints with no recorded profile, an unobtrusive hint suggests steps and guidance for their family with an Apply button; it never changes values by itself. Mouse-wheel scrolling over dropdowns in scrollable panels scrolls the panel instead of changing the selection. Width and height snap to the nearest multiple of 64 with an adjustment message. The CLI and loaded configurations remain strict.

**Desktop seed 0 means random on submission or queue creation.** The effective request and metadata retain the resolved seed. Saving a configuration, checking LoRAs and the CLI preserve 0 literally.

Local/cloud Generate submits real work and may consume subscription credits. Initial DT+ generation does not enable PAYG, and failures never switch provider or credential automatically. Use fake for an offline tour.

The primary blue Generate image button includes a sparkles icon and sits in the Prompt header beside a compact icon-only Expand/Collapse toggle; the prompt defaults to a compact two-line, 52 px field and can still expand to 180 px. The shared bottom overlay shows actual backend stages and step counts and owns the single Stop waiting locally action. The preview has no duplicate stop button or synthetic percentage bar; a subdued status line preserves the last outcome. Progress and previews are not final results. A result is successful only after terminal success, final-output validation and durable saving. Stop waiting interrupts local waiting; it does not confirm remote cancellation. An uncertain remote attempt remains unknown and is never automatically replayed.

The sampler list is filtered to the samplers suited to the selected model and switches an unsuitable choice to the model's recommended one; the suitability is inferred from the sampler family, not Draw Things evidence. The configuration menu also lists Draw Things' official community presets read-only as `DT · <name>` (a user preset of the same name wins). On direct DT+ cloud, checkpoints the cloud does not describe are sent with a bundled model description; see [cloud integration](../architecture/cloud-integration.md#model-descriptions-for-unlisted-checkpoints).

With `-x`, the Attempt details box adds development evidence: on failure the error code, category, message, submission stage, remote outcome and sanitized diagnostics; on success and failure the stage timings (when each stage, first preview and event was first reported since submit, and the total settle time); and after a success the export noise/blank detector's advisory verdict, signals and metrics for each saved output (the log carries the verdict and signals only). This changes diagnostic detail only. Outputs with smooth, saturated colour blobs can still be labelled `no_obvious_failure`.

## LoRAs and cloud assets

For a classified checkpoint, Add LoRA offers the active provider's available bundled/user-defined LoRAs whose known base family and mode metadata are compatible with that checkpoint. User custom metadata may be stored in `.dtflow/catalogue/custom_loras.json`; it is durable user data and does not make an absent provider artifact available. Unknown/raw checkpoints do not receive guessed compatible LoRAs. Choose the exact inventory identifier and a weight together in the one-step inline Add editor (0.0–3.0, 0.1 steps). Shared preflight keeps architecture match/mismatch/unknown, availability and application evidence separate. A confirmed mismatch or missing LoRA stops the request; unknown architecture is reported and allowed, never silently dropped. Generation success does not prove LoRA application. Only whole-model LoRAs on a base model are supported; broader exception rules are not pinned.

Trigger prefixes are added only when you explicitly choose that option; otherwise the prompt is unchanged.

The cloud asset list contains this machine's local receipts, not complete DT+ account inventory. Prepare may upload a LoRA after explicit file selection (without another confirmation). Reconcile is metadata-only. A confirmed receipt may be bound to a selected LoRA, then revalidated before dispatch; receipt bindings are not exported in configurations. See the [CLI asset reference](cli.md#cloud-asset-receipts) and [cloud integration](../architecture/cloud-integration.md).

## Configurations and outputs

Save and load the shared versioned, credential-free DTFlow generation configuration. Named presets are separate durable user data under `.dtflow/presets/custom_configs.json` and use Draw Things' native `custom_configs.json` array envelope (maximum 4 MiB; names must be unique and non-empty; importing replaces the whole library). Connection owns global Import/Export all actions; Generate can save a named preset and load presets alongside DTFlow configurations. Imported unknown fields are preserved for re-export. DTFlow applies only supported mappings: `model`, `width`, `height`, `steps`, `guidanceScale`, `seed` (0–4294967295), `sampler` (only Draw Things values 0 = DPM++ 2M Karras and 1 = Euler A, a conservative verified subset; saving requires a mapped sampler) and `loras` (applied only when every entry has `file`, numeric `weight` and mode `all`). Everything else is preserved for re-export but not applied, and a value of the wrong type is ignored rather than coerced; prompts are not part of the native preset and are left unchanged.

The image workspace supports fit, zoom and pan; 100% means device pixels. Saved-output details expose output/metadata paths, Open folder and Copy path; multiple saved outputs offer a chooser. Previews and illustrative empty-canvas images never enable saved-result actions.

Development attempts use `.dtflow/attempts.sqlite3`; final outputs and reproducibility metadata use `.dtflow/outputs/<attempt-id>/`. Store state on a local filesystem with the patched SQLite runtime described in the [backend contract](../architecture/backend-contract.md#durable-state-and-layer-enforcement).

## Queues

The Queues page creates paused seed sweeps or Pair/Cross batches from the Generate form. Review the count and Dry run table before creation. With Generate in Inpaint mode and a source plus mask ready, **Create queue** stages the edit once (durable under `<state>/inputs/`) and queues seed, Pair or Cross items over it; the Plan area shows a **Strengths** field (for example `0.5; 0.7; 1`), and the item table an Edit column. Size cannot vary. Nothing is uploaded until Start, and cloud runs may use credits. See [queues and batch plans](queues.md) for controls, concurrency, restart and unknown-outcome handling.

During generation or queue dispatch, the window owns the state directory. Generate is disabled during dispatch and competing CLI owners fail clearly as busy; another CLI operation can own generation between desktop operations. Reading and posting queue controls do not require ownership. Closing during dispatch asks first and does not imply remote cancellation.

## Images

While browsing a snapshot, a slim one-line amber bottom-overlay warning stays until live access returns. Close it with × to hide it for the current fallback episode; retries, changing snapshot timestamps and tab navigation do not bring it back. A later new fallback after live recovery can warn again. The in-gallery snapshot timestamp remains visible. This condition row never replaces an active confirmation/editor and has no six-second countdown; ordinary outcome warnings still expire normally.

If live database reads fail while the other system is generating, Images tries the last published read-only snapshot and shows its UTC timestamp plus **Live updates unavailable**. It supports browsing, filters, paging and sidecar inspection while new results may be absent; original-file reads still require shared locking. Export, edit/reuse/regeneration and removal cannot use stale snapshot authority. The app retries live access automatically. A snapshot is published after a generation/queue session and on clean app close if committed gallery state changed, or if no snapshot exists (even for an empty library). Failed publication keeps the previous snapshot; none means the ordinary unavailable message remains. Derived-cache cleanup can remove it and `--no-cache` bypasses it. This is not simultaneous cross-runtime generation/database-writer support. See [snapshot architecture and pending owner acceptance](../architecture/gallery-snapshot.md).

Snapshot logs distinguish newly added/removed visible images from the total included images and committed attempts. Saved and unchanged messages include the trigger and last-update UTC timestamp. Clean close reports this app process's earlier publications and cumulative image additions/removals even if nothing changed at close; these are publication activity counts, not net changes or a claim about other processes. Unreadable baselines show unknown counts. A failed close check labels a retained session timestamp as last known rather than freshly verified. No prompts or private paths are recorded.

#### Windows history viewed in WSL 1

The Images reader can resolve saved Windows drive paths through the current WSL
`drvfs` mount table, including non-default mount roots, without changing database
or metadata paths. Thumbnails, Detail and sidecars use local read aliases; Copy
path(s), Open folder and external opening use the local location. Unmapped or
unsupported paths remain unavailable; UNC/device paths and guessed file searches
are not supported. Native Linux/Windows paths retain their existing behavior.

Trash and edit/reuse/regeneration of Windows-recorded outputs remain restricted to Windows; JPEG export of them goes through the verified drive mapping under the original-file lease. Explicit **Delete permanently** can remove eligible managed originals from WSL through verified drive mapping and a native Windows helper. Source-run WSL requires this checkout's existing Windows `.venv/Scripts/python.exe`; missing helper, unsupported/ambiguous mounts or identity mismatches refuse shared access. No raw mapped-path deletion fallback exists. All original-file readers, saves, exports and removals require compatible leases at I/O time; generation submission first performs a cheap static readiness check and refuses unsupported mounts without contacting a provider. A remote-completed generation can still fail local saving due to bridge unavailability, lock contention or disk I/O; files may be absent or partial, and generating again may be billed. No automatic output recovery is provided. Unverified WSL2 Windows-drive mounts (including 9p `/mnt/c`) refuse original-touching operations; move state to the WSL-native ext4 filesystem as a workaround. Existing state/output references are not automatically migrated; [WSL2 support is deferred](https://github.com/djebaz/dtflow/issues/77). Participating original readers/removers now share native Windows leases, but this does not establish general simultaneous Windows/WSL generation or SQLite/state-owner safety; do not run concurrent cross-platform generation/state writes. Qt 6.8.3 still disables Trash when its capability API is absent; the mode dropdown remains usable. New permanent-removal runtime acceptance is pending.

When the Windows Segoe UI regular/semibold/bold files are accessible on drive C,
the WSL theme registers them only for this application process. Nothing is
installed or bundled; unavailable fonts keep Qt's normal fallback. This avoids
the observed DejaVu Sans Bold substitution for selected-toggle semibold, but
exact appearance and DPI behavior remain owner checks.

Images browses committed final outputs recorded in the selected state directory.
With no saved viewer preference it opens in Grid; later launches restore the
saved Grid/List/Detail mode, filters, sort, selection, scroll positions,
thumbnail scale and auto-refresh choice.

Grid, List and Detail share the same bounded query. Positive-prompt search is
case-insensitive; model and LoRA filters use exact recorded identifiers. Date,
size-class (Small under 400 px, Medium under 600, Large under 1200, Big 1200 and up, by the longer side), exact-dimension and aspect-ratio filters compose with AND, and sorting supports
newest/oldest, model, pixel area and canonical LoRA ordering. Negative-prompt
search remains available through the shared CLI/service query but is not shown
in the compact desktop header.

The thumbnail control is 25–250%, with 100% as the standard size. It scales Grid
tiles, List row thumbnails and the Detail filmstrip. Grid/List loading is paged
and thumbnail decoding is bounded/asynchronous so the Qt event loop is not used
to decode the whole library at once. Auto-refresh is on for a first-run viewer
and preserves the selected or visible browsing anchor. Refresh reconciles Grid
cards, List rows and the Detail filmstrip by output identity: additions insert
only new entries, removals discard departed entries and changed metadata updates
in place. Retained thumbnails and their in-flight decodes survive insertions and
reordering; only new images or changed path/size/availability require a thumbnail
decode. The selected Detail image and zoom/pan stay intact when other outputs
change. Fully unchanged pages also preserve exact scroll and focus while
counts/navigation can still update.

The labelled Auto-refresh toggle uses Connection's mint/teal colors when On and
neutral colors when Off; its size, label and refresh behavior are unchanged.
Hover and mouse activation keep the On colors; the blue dashed focus outline is
reserved for keyboard navigation, matching Connection.

Click a Grid tile or List row to open Detail. Detail defaults to Fit, with Cover
available when you prefer a filled canvas. It also provides true 100%, zoom/pan,
previous/next through the current filtered order, keyboard navigation and
full screen. The full-screen toggle in the header (beside Refresh, in every view; also the Detail image toolbar button) fullscreens the window and collapses the filter row, selection bar and the metadata/prompt panels below the image; the toggle or Escape (even after changing view) restores them. Additional details include the negative prompt, resolved
seed when recorded, provider, file size/path and file actions. Raw metadata is
bounded and sanitized; unavailable evidence is shown as `Not recorded` or
`Unknown`, not inferred.

Use settings populates Generate from the recorded request without dispatching. Regenerate asks for confirmation because it may consume provider credits, then creates a fresh request through the normal generation path and records the source output identity as lineage; the original output and attempt remain untouched.

Copy path, Open folder and external-viewer actions operate on the actual saved
artifact. Missing or corrupt files remain represented without preventing other
outputs from being browsed. Browsing does not generate, modify or delete saved
outputs.


The Images action bar keeps **Select all** visible at every width. It selects matching results across all pages, up to 10,000 checked images in total. When every current matching result is checked, the same button becomes **Unselect all** and clears all checks, including hidden ones. Partial selections at the limit do not show Unselect all. Selection coverage is refreshed after checkbox edits and gallery refresh; owner reports this Select all / Unselect all smoke passed on 2026-10-09 (follow-up to f3bc285).

### Remove checked originals: Trash or permanent deletion

Use the arrow next to **Trash** / **Delete** to choose the main button's action. Choosing a mode does not remove anything. Trash is the default; Windows and WSL remember separate choices, as does native Linux. A disabled Trash main button leaves the menu usable, and never automatically selects permanent deletion. For an image edit (img2img/inpaint) the attempt's declared `raw-1.png` provider output is removed with it, in the same mode, before the final; if it cannot be removed the final is left in place and the result says so. Only managed originals qualify; custom output roots, exported copies, redirected/hardlinked or multiply referenced files are excluded. Hidden checked images are included in the reported eligible/missing/ineligible counts.

**Trash** shows inline details and turns only the main button into a red **Trash** confirmation. Destination is Recycle Bin on Windows and Trash on Linux/macOS; Qt decides per-file availability. **Delete** always shows a fresh inline image count with a permanent, irreversible, no-Undo warning; press the red **Delete** confirmation to authorize destruction outside system Trash. The saved mode is not saved consent. Cancel in the bottom overlay, Escape, changing mode/scope/filters or navigation discards pending consent. Metadata, exported copies and generation history are preserved in both modes.

A card whose original is gone shows a **File missing** pictogram in Grid, List and Detail; this needs the file to be absent while its attempt folder or the outputs folder is still readable. Anything else that cannot be read (an unmapped drive, a busy or unsupported location, a corrupt file) shows **Image unavailable**, with the reason on hover. The pictogram is only presentation: **Delete** independently re-proves the absence before it clears the card, including when the whole attempt folder was deleted in a file manager, and Trash never claims to have moved a missing file.

Removal runs off-thread one image at a time. A confirmed unchanged Windows pre-disposition refusal settles as `failed`; an unconfirmed mutation, close or acknowledgment is `uncertain` and must not be replayed. New permanent-delete uncertainties raise a one-time amber alert, including after a successful reconciliation pass, but older uncertain rows and interrupted Trash rows do not raise that alert. Reconciliation postponed by unavailable shared locks leaves interrupted intent blocked. Stop after current image preserves completed removals. Results distinguish confirmed moves/deletions, failures and uncertain outcomes; Trash can report Qt success with an unverifiable destination. Failed and uncertain selections remain checked for manual review; pending/uncertain/removed outcomes never automatically replay. Manually recovering a file to its recorded path does **not** make it available again: path, size and timestamps do not prove content identity. Recycled/deleted identities remain hidden until a future explicit recovery workflow. There is no DTFlow Undo or guaranteed restoration, and Trash failure never falls back to permanent deletion. The removal ledger upgrades to schema 2 while preserving old records; older applications refuse the upgraded ledger. See [architecture and platform limitations](../architecture/image-recycling.md). Owner permanent-deletion/platform/packaged acceptance is pending.

## Remembered settings and diagnostics

`.dtflow/last_session.json` remembers the provider, local endpoint and Generate form on submission, a successful connection check and quit. Prompts are shared; generation domain, base-model family, Niche preference, exact model, LoRAs/weights and bound receipts, seed, dimensions, steps, guidance and sampler are remembered separately per provider. It is durable state, not cache, and cache bypass does not remove it.

Connection reports disposable inventory, thumbnail and derived-cache sizes/file counts and offers confirmed category-specific or all-cache cleanup. Sizes refresh after inventory/export operations and when returning to Connection. Inventory snapshots and source image computations use disk cache; gallery thumbnails remain in memory, so the disk thumbnail category may be empty. Cleanup is blocked during active generation, queue dispatch or image export in this window. Shared nonblocking cache coordination also prevents cleanup racing actual cache I/O across threads/processes; a busy cache reports retry rather than waiting. If export starts while cache is unavailable, it continues without persistent cache and reports that fallback. New inventory observations can recreate cache after cleanup; cleanup is not a permanent caching disable. Redirected cache paths (including Windows junctions) are refused, and each category is bounded to 100,000 inspected entries. Failures can leave partial cleanup: refresh sizes, resolve permissions and retry. Cleanup never owns outputs, attempts, queues, presets, settings, credentials, assets, custom catalogue data, model files or user export folders.

Raw local/cloud inventory snapshots live under `.dtflow/cache/inventory/`. Cache reads reject incompatible versions, changed endpoints and changed cloud credential identity; no account-dependent evidence is reused blindly. See [runtime layout](../architecture/runtime-layout.md) for the exact ownership contract.

The status bar displays `DTFlow <version> | revision <value> | source: <status>`. Source runs show `dev` with the newest package file modification time as revision; packaged builds use their stamped commit and clean/dirty status. Diagnostic logs do not substitute for durable results.

## Offline simulation

For fake, `fake-fixture` produces the bundled illustrative lighthouse at the requested aspect and size, while `fake-grid` produces a calibration grid. No model inference or network request occurs. The fake-only Simulation group selects outcome, duration and previews so progress and Stop waiting can be exercised; queues created from the form record those settings.

## Previewing UI states

Only when requested, run the offline renderer from the repository root:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QT_QPA_FONTDIR = 'C:\Windows\Fonts'
uv run python tools/ui/render_states.py --out tmp/ui-renders
```

On Windows, offscreen rendering needs `QT_QPA_FONTDIR=C:\Windows\Fonts` for fonts;
alternatively use `QT_QPA_PLATFORM=windows` (visible windows). The renderer blocks
provider network traffic and uses temporary state, fixture images/masks and fake
providers. It renders only explicitly listed states; an unlisted view has no preview.

Besides the existing Generate/Queues/Images states (01–14), the editor list is:
15 Inpaint empty, 16 source loaded, 17 mask painted, 18 Source view, 19 Mask view,
20 Result with no result yet (current Source fallback), 21 Clear confirmation,
22 Find people overlay and 23 Edit image overlay. The last two are explicitly
labelled presentation fixtures, not functioning analysis or image dispatch.
The manual editor remains work in progress; its default strength is 0.7.

Screenshots are layout evidence only, not Windows/native-DPI, interaction,
generation, ONNX or packaged acceptance. Extending the script does not mean it
has been rendered; rendering remains owner-requested.

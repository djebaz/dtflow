# Desktop guide

DTFlow's desktop application has Connection, Generate, Queues and Images pages. Generate and Queues use the primary left navigation. The colored Connection power icon sits at the extreme right of the bottom bar; Images opens through the blue primary-action button above ordinary right-hand inspectors (above the centre at narrow widths). That Images button is absent from Images and Logs. The panel icon beside the brand toggles details. Images uses the same logo/primary navigation in two aligned header rows and hides the content rails; returning restores splitter geometry and the shared Inspector choice. Workflows is currently an offline mockup, not a normal-launch tab.

## Inline actions and progress

The global activity-bar **Stop** applies to the queue currently dispatched by this window, even when another queue is selected for inspection. The selected queue's **Cancel** remains a separate action. Stop immediately disables its button and keeps the stopping explanation visible as progress updates; when auto-play advances, the next queue gets its own enabled Stop. This changes targeting and presentation, not remote-cancellation certainty or the safety rule that waiting queues become paused if dispatch ends.

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

Select one or several converted `.ckpt` LoRAs to open the in-window **batch Prepare editor**. File selection itself does **not** upload anything: review architecture, short name, format, triggers and readiness, then click **Prepare N ready LoRAs** to authorize ready rows. Background import inspection detects invalid/non-LoRA files and name conflicts before preparation; incomplete rows remain editable. **Re-Prepare** is available only for an accepted-name refusal, not every failure.

Authorized files are handled sequentially. The bottom activity bar shows checking, upload bytes/percentage, throughput, elapsed time, a throughput-based ETA when meaningful, and confirming or already-on-DT+ results. A stalled upload shows **Waiting for DT+** without an unreliable ETA. **Stop** completes the current file and prevents subsequent files from starting; this does not guarantee mid-upload server cancellation. The editor closes once admitted rows have passed the stage that can require Re-Prepare and no rows need user attention. While it is open, failures are shown on their rows; when closed, the bar displays one final result summary, without duplicated failure surfaces. No file is automatically retried. Source implementation is [#407](https://github.com/djebaz/dtflow/pull/407) and [#408](https://github.com/djebaz/dtflow/pull/408); Windows smoke has not separately covered every stall, Stop or name-conflict scenario.

With `-x/--dev`, each `ui.asset_prepare_incomplete` log record additionally includes the selected file basename, the user-facing error explanation, and a sanitized traceback for local validation failures. Logs never include the selected directory or full path. Normal-mode logs retain the batch position and safe failure code, without verbose per-file details in the UI. Each Prepare also retains a verified durable local copy, without an opt-in setting, so DTFlow can restore it if DT+ later reports it missing. Before retention or DT+ negotiation, malformed converted SQLite LoRAs are rejected locally, including files whose hash DT+ might already know. When trustworthy detected architecture (or a saved explicit declaration) and a usable naming-service short name are available, an ordinary explicit Prepare automatically accepts a canonical technical basename such as `Portrait_Kr2_lora_f16.ckpt`; the retained copy is written directly under that name, with no intermediate original-named managed copy. On Connection/asset refresh, eligible previously retained copies are also locally normalized without a second approval, provider contact or upload. Content hashes, the original source files and historical/account-specific receipt identifiers are unchanged. Unknown-architecture or unnameable files remain under their original retained names until the metadata editor can resolve them. Confirmed LoRAs are added/bound without replacing existing selected weights. A backend or saved-credential change stops admission after the current file settles; changing tabs does not stop it.

The picker remembers the accepted selection's folder across restarts, separately for Windows, WSL and other runtimes, in the shared remembered-folder memory (`dialog_paths` in `settings.json`; an older `prepare_preferences.json` is read once as the starting value and never rewritten). Cancelling the picker changes nothing. A missing or inaccessible folder falls back to the nearest existing parent, then the state directory; unreadable/newer settings remain unchanged, and a folder-save warning never prevents explicit file preparation. This preference is not cache and is unaffected by cache cleanup or `--no-cache`. Preference failures, incomplete per-file preparation and registry read failures emit privacy-safe warning diagnostics, including outside dev mode; they do not log paths, filenames or raw exception text.
The asset note and Prepare tooltip warn that DT+ upload may occur without upload
credits, while subsequent generation may consume credits. The existing backend
identity/busy checks, receipt verification and uncertain-upload no-retry policy remain.

Checkpoint search and exact custom identifiers use one expanded editor. In it, double-clicking a
name or pressing Enter applies it like **Apply**; Enter in the search applies the highlighted
match (the current checkpoint when the filter still shows it), or the first match when the
filter hides it. With no match, Enter moves the search text into the exact-identifier field
instead of closing. The small previous/next buttons beside the Checkpoint selector step through
the current filtered list without opening the editor and stop at either end; with no checkpoint selected, Next takes the first entry and
Previous the last. A custom
identifier you applied stays at the end of that list until the next catalogue rebuild, so it is
also a stepping target. While you step, selected LoRAs are kept; once stepping pauses (about
0.6 s) LoRAs incompatible with the model you stopped on are removed, as when picking it in the
editor. The sampler chosen before stepping returns on any model that allows it. **Edit LoRAs** opens a
taller editor that lists the selected LoRAs first (checked, at their weights) and then every
offered LoRA, each with a checkbox and a 0.0–3.0 weight slider in 0.05 steps (default 1.0); one mouse-wheel notch over a slider moves it by 0.05.
Moving a slider checks its LoRA; search filters the list; an exact identifier can be added
with **Add custom**; text left in that field is added when you apply. **Apply** (or Enter) replaces the whole selection once; unchecking a
LoRA removes it. New UI selections use weights in 0.05 steps; 0.0 is preserved. A weight loaded
from a saved request outside the slider range is kept exactly until its slider is moved. The mouse wheel over a slider adjusts its weight (and checks the LoRA). Existing imported/saved
requests retain their recorded weights and existing validation; this does not change the
CLI or provider weight contract.

Running jobs and routine endings (finished, stopped) use one 20 px activity bar in the middle of the status bar, between the version text and the right-hand controls; it adapts to its own width (short title below 760 px, stage text clips first while retaining the `+N queued` chip, then ETA drops, then elapsed; title, compact `4/7` count and Stop never drop), shows ✓/■ results with × for six seconds (paused while hovered or focused) followed by a 450 ms fade, and renders nothing when idle. Only one job shows in the bar (the newest running one, else the newest ending); other jobs, and every job when the available bar slot is below 240 px, keep the overlay row with its own Stop. Confirmations, editors, errors, warnings, partial results, other notices and snapshot conditions use one thin bottom overlay above content, across tabs;
it is hidden when idle and reserves no empty layout space. Labelled rows distinguish
concurrent export, Trash, gallery selection and generation/queue work. Unknown totals are
indeterminate, not guessed percentages; queue counts mean finished items, not sampler steps.
Stop/Cancel delegates to the original controller, preserving completed copies/moves and
existing uncertainty semantics. Generation preview and the compact Run control remain.
Essential information is visible directly; long messages wrap without a Details button. Export starts directly without confirmation. Successful operation results show a teal 2 px draining lifetime line, an optional seconds label and a 450 ms fade after six seconds, paused while any overlay row is hovered or focused. Blue 5 px task progress remains a separate measure of actual completed work; amber warning/partial/stopped overlays use the same six-second countdown, amber 2 px draining line and fade; red errors and informational inspections remain persistent. Dismissing an amber notification does not modify an uncertain attempt, its recorded evidence or its recovery/retry rules. Unrelated notices cannot discard an active editor; Escape dismisses a plain notice. Informational inspections (for example About DTFlow) also close with their × button or a click anywhere outside them; errors and confirmations ignore outside clicks and keep Cancel/Dismiss semantics. Dismissing a result never cancels work. Showing/hiding the overlay does not resize the main content or reserve idle space.
This implementation still requires owner runtime/visual and packaged acceptance.

The [inline interaction mockup](../mockups/dtflow_inline_panels.html) illustrates main-window confirmations, activity overlays, the Generate prompt and Images selection/export behavior. Its example images and state transitions are illustrative only: the mockup cannot read saved outputs, export files, delete originals or contact Draw Things.


## Launch

From the repository root after `uv sync`:

```bash
uv run dtflow gui
uv run dtflow -x gui
uv run dtflow gui -nc
```

There is no menu bar: About DTFlow is the small help icon at the right of the bottom status bar. The Draw Things provider library loads in the background after the window first paints; with `-x`, structured `app.startup_timings` records describe startup phases and a separate `app.provider_preloaded` measurement records the background import. These observations do not imply provider connectivity or successful generation. In the packaged desktop, unavailable/incomplete diagnostic logging uses a once-only shared inline warning, never a native dialog from the writer thread. Warnings before window creation wait for the window; shutdown does not reopen a window to display them. Native acceptance remains pending.

`-x/--dev` adds sanitized diagnostic detail only; for image removal it adds the preflight counts, operation start, per-image refusal stages, Windows helper refusal phases, original-file readiness reasons and contended or timed-out leases, while normal mode keeps one finish line per removal and reconciliation pass plus per-item failure warnings; `-nc/--no-cache` disables persistent disposable-cache reads and writes for that run. For gallery performance testing from source, `dtflow gui -ml/--load N` sets the Images records per batch and page size (default 20; each batch is one read up to 1000 rows, larger batches are read in consecutive 1000-row chunks), and `-mc/--cards N` sets the most Grid/List cards kept mounted (default maximum 100 buffered/mounted cards, rounded down to whole batches with at least one). With a single-batch window, neighbouring batches are not preloaded. Each mounted card keeps a decoded thumbnail of up to 600×600 pixels (about 1.4 MB), so very large `-mc` values use several GB of memory. Logs use repository `logs/` in development. See [development tools](development.md) and [runtime layout](../architecture/runtime-layout.md).

For a portable Windows executable that launches the GUI directly, see [the build guide](building.md).

## Logs

The **Component** selector contains the DTFlow logger catalogue bundled with the installed build, so you can select a component *before* it emits any records in the current session. The list is generated from logger declarations in the source (run `python tools/generate_diagnostic_components.py` when adding one; packaging runs the generator automatically). **Event** begins at **All events**, or the last saved Event; use **Refresh** when you want to discover all events and numeric characteristic keys from the selected session in a background read. **Follow** updates log rows without rescanning for choices or rebuilding filter dropdowns. The text Search field is narrower so the Component and Event selections remain readable; full labels are available on hover/in the popup.

Select **Compact: On** in the Logs header to render one terminal-style line per record, with long values truncated in the table. Record details, copy actions and matching exports are unchanged; **Compact: Off** restores the wrapped, multi-line view. Changing density only resizes resident rows, never scans logs. Compact is **Off** by default.

The selected search/severity/Component/Event/time/characteristic filters, sort, Follow and Compact are remembered between app restarts in the durable `settings.json` file. They are not disposable cache and are retained under `--no-cache`. Values for components and events remain selected even if no matching log has been emitted yet. **Reset filters** clears the filters but not the preferred density; the app still opens the newest/current diagnostic session by default rather than restoring an obsolete session identity. Saved Search and Value expressions are user-entered text and may be sensitive; the preferences are local and are not written to diagnostic logs. A newer/unreadable settings file is left unchanged.

## Connect

Select the fake, local Draw Things or DT+ cloud provider on Connection. The local provider needs its server endpoint. Connection checks run in the background and never generate. The Generate page uses the selected provider and endpoint; switching provider resets connection results.

On Windows, save, switch and remove DT+ API keys on Connection (stored in Credential Manager); the selected saved key is used before `DRAWTHINGS_API_KEY`, unless the environment key is selected explicitly. An empty or invalid selected credential fails without switching sources. See [credentials](credentials.md). Other platforms do not offer the Windows secure-storage path; use the environment. Keep credentials out of configurations, logs and diagnostic reports.

The desktop schedules one asynchronous connection check after restoring the session and entering the event loop. The Connection navigation indicator is amber while checking, green after a successful check and red after failure; missing local endpoint/cloud credentials leave it explicitly unchecked. The result survives navigation and describes the last check, not continuous connectivity. Changing backend or endpoint invalidates pending evidence; use Check to refresh it. Selecting another saved or environment DT+ key automatically starts a new Check for that selection. Startup does not generate, prepare/upload assets or resume queues. Optional credential, preset and custom-LoRA summaries load independently, and Logs widgets are created only when first requested.

Changing the connection configuration or saving/removing a key in DTFlow retires the pending check and immediately enables a new manual Check. Selecting a different key in the Connection dropdown retires the previous check and automatically checks the new DT+ connection; rapid switches discard stale check results. Late completion/cancellation cannot alter the newer check's status or controls. Changes made directly in Windows Credential Manager are not monitored: use Check to resolve the current saved credential again. The indicator never guarantees that credentials or connectivity remained unchanged after its recorded check.

Reload Models remains available for a manual refresh. Selecting another DT+ API key automatically reloads that key's models and LoRAs without generating or uploading anything; rapid switches coalesce into one pending refresh, and a busy Generate page waits until its current inventory/asset operation finishes. Models from the previous key are cleared immediately, then only matching cached or newly refreshed models appear. The chosen checkpoint is restored after switching keys only when the newly selected key's inventory includes it; otherwise the selection remains empty. Startup and provider returns restore the last successful matching observation: first in-run memory, then the existing disk cache. The UI always shows model and LoRA counts, cached/remembered/refreshed provenance and the original observation time whenever an observation is known—even if no live refresh happened during this launch. Refreshing or a failed response adds status alongside those stats; a failure keeps the previous choices and selections with "Refresh failed · showing previous models". Switching providers preserves each provider's form before rebuilding discovery. Local endpoint or selected cloud credential changes invalidate mismatched evidence; saving the same key (or changing an unused saved key when the environment key is selected) does not erase it. Generation always refreshes live. Inventory listing alone does not prove workload compatibility. When the server supplies no model metadata, listed files are offered as model candidates, using the shared request gate.

### Local DT+ completed-image counter

Connection's **Local completed images** card lists one row per DT+ API key that produced a saved final image on the selected day. A row shows `Key 4F8K••••` (only the first four characters of the key's variable part, never more, also in tooltips, accessible names and logs), an **Active** marker for the key cloud work would use now, and the count, colored green up to 400, amber for 401-499 and red from 500. A searchable dropdown above the rows picks the reset timezone: one entry per whole-hour GMT offset (-12 to +14) with its current local time and largest city. The default is Europe/Paris (daylight-saving aware); other entries are fixed offsets. The day runs from local midnight to the next local midnight; changing the zone recounts immediately from saved times, without contacting DT+, generating or changing saved outputs, and the choice is remembered in `usage_preferences.json`.

The bottom bar carries the same information on every view as a small pill `active/all`: the active key's count and the sum over all keys for the selected day, colored by the active key's count (neutral `–/all` when no key is available). Its tooltip names the key and zone.

These are **local estimates from saved final images**, not a DT+ balance, a confirmed 500-image quota or a remaining allowance; the 400/500 thresholds are the owner's working assumption and only visual guidance. Previews, local/fake backends, failed or quota-rejected requests do not count; an image that is later moved to the Trash or deleted still counts because it was produced. Images saved before key tracking cannot be attributed to a key and appear only as a note. If two keys share the same four characters, or the key format cannot be read safely, rows carry a harmless ordinal instead of more key characters. Nothing is blocked by these counters. If the local saved history cannot be read, the Connection card marks counts unavailable or potentially outdated, the bottom-bar pill shows dashes rather than a misleading total, and Logs records a safe failure stage and SQLite error category when available. Returning to Connection or saving another output refreshes the same history without repair, extra provider calls or a separate counter store. The Images grid position field in the status bar is shown only with `-x/--dev`.

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

The primary blue Generate image button includes a sparkles icon and sits in the Prompt header beside a compact icon-only Expand/Collapse toggle; the prompt defaults to a compact two-line, 52 px field and can still expand to 180 px. The shared bottom overlay shows actual backend stages and step counts and owns the single Stop waiting locally action. The preview has no duplicate stop button or synthetic percentage bar; a subdued status line preserves the last outcome. Progress and previews are not final results. A result is successful only after terminal success, final-output validation and durable saving. Stop waiting interrupts local waiting; it does not confirm remote cancellation. An uncertain remote attempt remains unknown and is never automatically replayed, except the one Draw Things+ case below. A failure shows a headline (for example *Provider request failed*, *Credentials problem*, *Generation failed* or *Outcome unknown*) followed by the original reason, the provider's wording kept, as Queues shows it; error codes and attempt IDs stay in the collapsed Attempt details box and the logs. A failed, stopped or uncertain generation also offers **Retry** beside that line (#240): it resends the last request exactly as recorded, same settings, same seed and same provider, as a new and possibly billable attempt; the form is not read, so later edits do not leak in. The earlier attempt stays recorded as it ended, and apart from the one automatic retry nothing retries without the click. **Automatic retry (#241):** one automatic retry per submission, a few seconds after the first try, with the same settings and seed as a new attempt; it is a real, possibly billable generation. It happens only when the first try definitely made nothing: a connection, timeout or protocol failure where nothing was started or the backend reported the failure, or the Draw Things+ case where the server itself ended the call with "unknown error processing request" and sent no image. Any other no-result outcome (lost connection, timeout after sending), credentials, quota, validation and rejections, a result that was generated but not saved, and Stop are never retried automatically; they keep the manual Retry. The rule lives in `src/dtflow/core/retry_policy.py`. Generate shows *Retrying automatically in a few seconds* while it waits; Stop during that wait sends nothing more. While a queue is dispatching, Retry adds that request behind it as a one-item up-next queue, the same way Generate does (that path uses the Queues page's current trigger-prefix setting).

The sampler list is filtered to the samplers suited to the selected model and switches an unsuitable choice to the model's recommended one; the suitability is inferred from the sampler family, not Draw Things evidence. The configuration menu also lists Draw Things' official community presets read-only as `DT · <name>` (a user preset of the same name wins). On direct DT+ cloud, checkpoints the cloud does not describe are sent with a bundled model description; see [cloud integration](../architecture/cloud-integration.md#model-descriptions-for-unlisted-checkpoints).

With `-x`, the Attempt details box adds development evidence: on failure the error code, category, message, submission stage, remote outcome and sanitized diagnostics; on success and failure the stage timings (when each stage, first preview and event was first reported since submit, and the total settle time); and after a success the export noise/blank detector's advisory verdict, signals and metrics for each saved output (the log carries the verdict and signals only). This changes diagnostic detail only. Outputs with smooth, saturated colour blobs can still be labelled `no_obvious_failure`.

## LoRAs and cloud assets

For a classified checkpoint, Edit LoRAs offers the active provider's available bundled/user-defined LoRAs whose known base family and mode metadata are compatible with that checkpoint. User custom metadata may be stored in `.dtflow/catalogue/custom_loras.json`; import it as JSON from the Connection page, where DTFlow validates it before atomically replacing that catalogue. It is durable user data and does not make an absent provider artifact available. Unknown/raw checkpoints do not receive guessed compatible LoRAs. Check the LoRAs to use and set each weight (0.0–3.0, 0.05 steps) in the Edit LoRAs editor; every LoRA starts at 1.0, and a catalogue or inventory suggestion is shown only in its tooltip. Shared preflight keeps architecture match/mismatch/unknown, availability and application evidence separate. A confirmed mismatch or missing LoRA stops the request; unknown architecture is reported and allowed, never silently dropped. Generation success does not prove LoRA application. Only whole-model LoRAs on a base model are supported; broader exception rules are not pinned.

Trigger prefixes are added only when you explicitly choose that option; otherwise the prompt is unchanged.

The cloud asset list contains this machine's local receipts, not complete DT+ account inventory. Prepare may upload a LoRA only after the batch editor's explicit **Prepare N ready LoRAs** action. It accepts one or several `.ckpt` files or `.safetensors` sources, prepared sequentially, and remembers the picker folder separately per runtime (Windows or WSL). A `.safetensors` row suggests its family from the file header, and Prepare converts it locally to an F16 `.ckpt` (reusing an earlier verified conversion of the same bytes) before the usual upload; SDXL is experimental and needs an explicit confirmation. Cloud LoRA metadata lookup tolerates unusable optional response branches and records safe response structure before interpretation; local interpretation issues are distinguished from API rejection, and unknown asset evidence never authorizes a guessed upload or receipt. Uploads keep local content checksums separate from server-returned storage values, following the official client contract: a different returned value alone does not reject preparation, and logs show format/comparison facts without exposing either value. Generation uses the already-confirmed receipt for the selected DT+ key without repeated BYOM checks. On first use with another key, DTFlow batch-checks that key's bucket and restores any positively missing file from its retained copy before submission. A relevant provider rejection may trigger one shared recovery probe; authentication failures and unknown results do not authorize an upload. If DT+ reports the same content under a different reference, DTFlow relinks the receipt without uploading. Generate and Queue feedback identifies LoRAs that were actually restored, and failure notices name affected LoRAs with the known cause. Verbose `-x` diagnostics contain one resolution summary with counts for reused, matched, relinked, restored and failed LoRAs; normal logs retain relinking/restoration summaries without enumerating routine successes. Key switches, generation outcomes and relevant DT+/LoRA events include the selected operation's stable opaque `credential_id`, optional four-character `key_hint`, and saved/environment source for tracing A → B → A sessions; these do not expose a raw API key. A four-character hint is only a display aid, never a unique identity. Stop during preparation prevents new generation admission, and a cancelled or uncertain transfer is not blindly retried. A queue at concurrency 10 coordinates first-use preparation and pauses new admissions during one failure-triggered recovery; previously submitted work is not cancelled or replayed implicitly. Reconcile is metadata-only. A confirmed receipt may be bound to a selected LoRA, then revalidated before dispatch; receipt bindings are not exported in configurations. Connection > Custom LoRAs shows retained filenames and total local usage; select a file and click Remove or press Delete to delete the managed copy immediately without confirmation. Originals, DT+ assets, receipts and saved results remain untouched. Copies are kept indefinitely until explicitly removed; after removal, future automatic restoration needs a new Prepare. This local retained-file list is separate from imported custom-LoRA definitions. See the [CLI asset reference](cli.md#cloud-asset-receipts) and [cloud integration](../architecture/cloud-integration.md).

## Configurations and outputs

Save and load the shared versioned, credential-free DTFlow generation configuration. Named presets are separate durable user data under `.dtflow/presets/custom_configs.json` and use Draw Things' native `custom_configs.json` array envelope (maximum 4 MiB; names must be unique and non-empty; importing replaces the whole library). Connection owns global Import/Export all actions; Generate can save a named preset and load presets alongside DTFlow configurations. Imported unknown fields are preserved for re-export. DTFlow applies only supported mappings: `model`, `width`, `height`, `steps`, `guidanceScale`, `seed` (0–4294967295), `sampler` (only Draw Things values 0 = DPM++ 2M Karras and 1 = Euler A, a conservative verified subset; saving requires a mapped sampler) and `loras` (applied only when every entry has `file`, numeric `weight` and mode `all`). Everything else is preserved for re-export but not applied, and a value of the wrong type is ignored rather than coerced; prompts are not part of the native preset and are left unchanged.

The image workspace supports fit, zoom and pan; 100% means device pixels. Saved-output details expose output/metadata paths, Open folder and Copy path; multiple saved outputs offer a chooser. Previews and illustrative empty-canvas images never enable saved-result actions.

Development attempts use `.dtflow/attempts.sqlite3`; final outputs and reproducibility metadata use `.dtflow/outputs/<attempt-id>/`. Store state on a local filesystem with the patched SQLite runtime described in the [backend contract](../architecture/backend-contract.md#durable-state-and-layer-enforcement).

## Queues

The Queues page composes seed sweeps (Repeat) or Pair/Cross batches from the Generate form: **+ New queue** (top of the center) opens the composer in the center, where values are picked from inventory and the count and item preview update as you edit, and the right column summarizes the new queue (backend, model, LoRAs, items, seeds, Max at once). **Start now** creates and starts it, **Add to queue** puts it up next behind a running queue and **Save paused** keeps it for a later Start. With Generate in Inpaint mode and a source plus mask ready, creating the queue stages the edit once (durable under `<state>/inputs/`) and queues seed, Pair or Cross items over it; Pair/Cross shows a **Strengths** row (for example 0.5, 0.7 and 1), and the item table an Edit column. Size cannot vary. Nothing is uploaded until Start, and cloud runs may use credits. The left rail lists queues grouped as Running, Up next, Paused and History with a filter, and the selected queue's header names its model and LoRAs, offers **Relaunch** (same or new seeds, all or only failed items) and carries its status chip, one primary action, Cancel, its own maximum and Remove. From any tab, the **Queue** button beside the header Run control (and the ▾ arrow on *Generate image*) opens a popover that queues Generate's request as Repeat N or the composer's current plan, with **Start now**, or **Add to queue** while a queue or generation runs. See [queues and batch plans](queues.md) for controls, concurrency, restart and unknown-outcome handling.

During generation or queue dispatch, the window owns the state directory. Every input stays editable and only shapes the next request; the composer's **Save paused** and **Add to queue** work, **Start next** puts a paused queue up next, and Generate during a queue run adds a one-item queue behind it that starts by itself (see [editing and up next](queues.md#editing-and-up-next-while-a-queue-runs)). Competing CLI owners fail clearly as busy; another CLI operation can own generation between desktop operations. Reading and posting queue controls do not require ownership. Closing during dispatch asks first and does not imply remote cancellation.

## Images

**Infinite-scroll boundary:** When you scroll upward from a loaded batch, the previous batch is mounted *above* the existing cards. On reaching the top edge, the preceding batch's **last card/row** appears immediately above the current one (20 before 21 with default settings); the viewport does not jump to the beginning of that previous batch. If the earlier batch prefetches before the edge, the current image stays anchored. Default residency is bounded to 100 cards in 20-image batches; explicit `-mc` changes the cap, not how many images exist in the gallery. The [Image Navigation](../plans/2026-10-17-images-hybrid-pagination.md#upward-infinite-scroll-continuity-at-batch-boundaries-2026-10-18) offline fixtures cover Grid and List; real Windows scrolling/trackpad acceptance remains separate.

While browsing Grid or List, changing **Thumbnail size** or **Row density** retains the same top visible image across Grid column and List row-height reflow; the active numbered page follows that identity, not the old scrollbar pixel value. A size change never requests another batch by itself. This behavior has offline Qt fixture coverage but still needs native owner acceptance.

Grid, List, and Detail (filmstrip) each keep their own thumbnail zoom (25–250%). The slider changes only the active view's value, switching views restores that view's value, and all three persist in `viewer.json` as `thumbnail_scale_percents`. A legacy single `thumbnail_scale_percent` seeds all three. Detail filmstrip zoom is independent of the full-size image zoom.

Current defaults: **20 images per page chip, up to 100 nearby images buffered** (five default batches). The toolbar reports “N buffered”: this is the bounded resident browsing window, not how many images were ever loaded. At image 506, you may still see “100 buffered”; earlier/later records remain in the database. `-ml/--load` changes the batch/page size and `-mc/--cards` changes this same maximum buffered count. Without `-mc`, the cap stays 100, rounded down to whole batches with at least one batch. Database reads remain capped at 1000 rows; larger launch overrides read consecutive bounded chunks. The owner preferred 20/100 over the earlier 100/300 trial; old 100/300 stress screenshots and the approved HTML's 80-image illustration are historical configurations, not the launch defaults.

The **Option 2a** pager above the gallery viewport shows the actual visible image range, total and numbered configured-size batch chips (20 images by default) (the approved historical HTML mockup still uses 80). Its position and appearance do not change in fullscreen. The **Image Navigation** artifact folder contains the dedicated offline screenshots. The live pager and loading ring are implemented under PR #89; performance follow-up and acceptance are tracked separately from [the implementation plan](../plans/2026-10-17-images-hybrid-pagination.md).

When Images first opens, its saved-output database query can be followed by a read-only
snapshot fallback. While that read is pending **and no committed image cards are on
screen**, a thin animated ring appears after 180 ms in the center of the gallery
canvas. The same presentation is used in Grid, List and Detail and is unchanged
in fullscreen. It disappears when the read settles, including when there are
zero matches; an unavailable read without cards shows an error and **Retry**.
Existing cards remain visible and usable during background refresh; the centered
overlay never interrupts them. The ring is not generation progress and does not
imply a provider request. The separate bottom status row continues to disclose
read-only snapshot fallback and recovery.

While browsing a snapshot, a slim one-line amber bottom-overlay warning stays until live access is stable. A first live result updates that same row to recovering wording; another consecutive live success at least five seconds later clears it. Snapshot results or failed refreshes restart that recovery interval. Close it with × to hide it for the whole episode, including brief live successes, renewed snapshot reads, changing timestamps and tab navigation. Only a new fallback after stable recovery can warn again. Live results and their normal actions return immediately; this delay affects the warning only. The in-gallery snapshot timestamp remains visible while using a snapshot. This condition row never replaces an active confirmation/editor and has no six-second countdown; ordinary outcome warnings still expire normally.

If live database reads fail while the other system is generating, Images tries the last published read-only snapshot and shows its UTC timestamp plus **Live updates unavailable**. It supports browsing, filters, paging and sidecar inspection while new results may be absent; original-file reads still require shared locking. Export, edit/reuse/regeneration and removal cannot use stale snapshot authority. The app retries live access automatically. A snapshot is published after a generation/queue session and on clean app close if committed gallery state changed, or if no snapshot exists (even for an empty library). Failed publication keeps the previous snapshot; none means the ordinary unavailable message remains. Derived-cache cleanup can remove it and `--no-cache` bypasses it. This is not simultaneous cross-runtime generation/database-writer support. See [snapshot architecture, owner acceptance and limitations](../architecture/gallery-snapshot.md).

With **Auto-refresh Off**, one successful live refresh clears the snapshot warning immediately; there is no second manual refresh requirement or extra timer. Turning Auto-refresh off after live access has already begun recovering also clears it. A failed refresh or snapshot fallback does not count as live recovery. The two-read stability rule described above applies while Auto-refresh is On.

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
change, except under **Follow** (below). Fully unchanged pages also preserve exact scroll and focus while
counts/navigation can still update.

Press **F5** from any workspace to open Images, enable Live + Follow and return to the newest matching image with newest-first sorting. Existing filters and Grid/List/Detail choice stay unchanged. Repeated F5 resumes following rather than toggling it off; it never starts generation or a queue.

**Ctrl+Tab** cycles Generate → Workflows → Queues → Images → Generate. From Connection or Logs it enters at Generate. It uses ordinary workspace navigation, preserving drafts and the current Images Live/Follow mode; unlike F5, it does not enable following or jump to a newer image.

**Slideshow:** while Images is open, press **F6** or the **Slideshow** toggle to the right of the buffered count. Playback uses fullscreen Detail and loops through the current filtered/sorted results, including across batches. The current photo remains visible while the next decodes, then transitions with a fade lasting one third of the interval (targeting a fixed 60 updates/second; displayed FPS depends on the machine); read failures keep the unavailable-image presentation. Set **0.5–8 seconds** per image (default 3 seconds) before entry or in the floating controls. During playback the first brand/view/tools header is hidden, and the range/buffered/batch bar becomes a translucent top-of-picture overlay without reserving layout space. Slideshow controls sit just below it at top-right; both overlays disappear after 2.5 seconds without pointer/keyboard activity and reappear on activity. The numbered photo slider seeks within the filtered/sorted gallery on release, like a video scrubber; dragging, wheel bursts on the position slider and held Left/Right keys pause auto-next and new image admission while previewing only the target number. Release (or a short wheel quiet period) loads the final target; older results do not move the slider back. Use the **Forward/Backward toggle** to choose current-sort or reverse playback, both looping; changing direction restarts the countdown. Manual arrows remain previous/next independently of automatic direction. Those controls are translucent until hovered or keyboard-focused; wheel **over the photo-position slider** moves 10 photos per step, and wheel over the other controls changes rate in 0.1-second steps; the image keeps its normal wheel zoom. **Left/Right** or the overlay arrows move manually and restart the countdown. During playback, **numpad +** speeds up (0.1 seconds less per image) and **numpad −** slows down (0.1 seconds more), within 0.5–8 seconds; holding repeats the adjustment and the interval stays persisted. The right-hand thumbnail carousel is replaced during playback by a scrollable grey panel with the same prompt and LoRAs as `(i)`, and restored on exit. **F6**, **Leave**, or **Escape** exits; navigation away, F5 and shutdown stop playback. Prior view, optimization, native window state and Live/Follow mode are restored, without forcing a newest-image jump. Rate and direction are remembered in `viewer.json` across restarts; playback never resumes automatically; loading may extend a dwell and no generation or queue is started. Native fullscreen/DPI and visual acceptance remain pending.

Images has paired **Live** and **Follow** controls. Live toggles automatic refresh; Follow enables Live and newest-image following with the newest-first sort. The remembered modes remain Off, Auto and Follow. Under Follow:
when new images arrive while the view is on the newest one (scrolled to the top in Grid/List,
showing the newest in Detail, so Detail zoom/pan resets), it moves to the new image. Selecting an
older image or scrolling away pauses it; the Follow icon dims and its tooltip/accessibility label explains the paused state until you return to the
newest; clicking the paused Follow control resumes at the newest image. Other sorts never follow. Generation or
queue progress in this window schedules a debounced metadata check (250 ms); changes made by other
processes are found by the one-second metadata poll, which costs a full read only when the
database files changed (snapshot/failure recovery keeps its five-second spacing).

The labelled Live button (Auto-refresh) uses Connection's mint/teal colors when On and
neutral colors when Off; it is 196 px wide to fit its state label.
Hover and mouse activation keep the On colors; the blue dashed focus outline is
reserved for keyboard navigation, matching Connection.

Click a Grid tile or List row to open Detail. Detail defaults to Fit, with Cover
available when you prefer a filled canvas. It also provides true 100%, zoom/pan,
previous/next through the current filtered order, keyboard navigation and
two independent viewing modes. **F11** toggles window fullscreen on any page. The **optimized Images layout** icon (beside Refresh in every view; also in the Detail image toolbar) or **Escape** hides/restores the filter row, selection bar and metadata/prompt panels without changing window fullscreen or maximization. Escape toggles optimization outside text editors, selection popups and active inline feedback, which keep their normal dismissal behavior. Additional details include the negative prompt, resolved
seed when recorded, provider, file size/path and file actions. Raw metadata is
bounded and sanitized; unavailable evidence is shown as `Not recorded` or
`Unknown`, not inferred.

Use settings populates Generate from the recorded request without dispatching. Regenerate asks for confirmation because it may consume provider credits, then creates a fresh request through the normal generation path and records the source output identity as lineage; the original output and attempt remain untouched. Both use only the recorded request, so on WSL they also work for Windows-recorded outputs. Edit image stays unavailable for those, with its own tooltip, because the editor cannot hold the shared Windows/WSL original-file lock.

Copy path, Open folder and external-viewer actions operate on the actual saved artifact. On WSL, paths are translated with `wslpath -w`: folders use Windows Explorer, while External Viewer uses Windows' registered file association through PowerShell 7 `Start-Process`. Windows `pwsh.exe` must be reachable from the WSL PATH for External Viewer; there is no legacy PowerShell or Explorer-for-images fallback. A fixed encoded script receives the path as JSON stdin, not executable command text. Missing interop/association or a failed invocation produces a sanitized notice; a timeout reports that hand-off could not be confirmed, not that nothing opened. Missing or corrupt files remain represented without preventing other outputs from being browsed. Browsing does not generate, modify or delete saved outputs.

Desktop requests validate the current target on a worker before hand-off, including whether the remembered export destination still exists, is a directory and can be accessed. WSL translation and process spawning also run off the Qt thread; native Qt desktop-service calls and error notices return on Qt. Missing/inaccessible/wrong-kind targets produce a clear notice. A successful hand-off is not proof that the external application's window appeared. See the [PR #85 review correction plan](../plans/2026-10-17-wsl-followups.md); owner Windows/WSL smoke remains pending.

When the Inpaint editor checks saved person models, the worker verifies that each path is a regular readable file with a one-byte access check; it does not load hundreds of megabytes or start inference to show readiness. Missing/unreadable models show an actionable status naming segmentation/clothes/faces without exposing private paths in logs. Full SHA verification still happens when Find people actually loads the model; readiness is not proof of model validity or inference success. The owner reports model loading, Find people and clothes segmentation worked in WSL; native Windows and the later failure-path corrections remain separate acceptance.


The Images action bar keeps **Select all** visible at every width. It selects matching results across all pages, up to 10,000 checked images in total. When every current matching result is checked, the same button becomes **Unselect all** and clears all checks, including hidden ones. Partial selections at the limit do not show Unselect all. Selection coverage is refreshed after checkbox edits and gallery refresh; owner reports this Select all / Unselect all smoke passed on 2026-10-09 (follow-up to f3bc285).

### Remove checked originals: Trash or permanent deletion

Use the arrow next to **Trash** / **Delete** to choose the main button's action. Choosing a mode does not remove anything. Trash is the default; Windows and WSL remember separate choices, as does native Linux. A disabled Trash main button leaves the menu usable, and never automatically selects permanent deletion. For an image edit (img2img/inpaint) the attempt's declared `raw-1.png` provider output is removed with it, in the same mode, before the final; if it cannot be removed the final is left in place and the result says so. Only managed originals qualify; custom output roots, exported copies, redirected/hardlinked or multiply referenced files are excluded. Hidden checked images are included in the reported eligible/missing/ineligible counts.

**Trash** shows inline details and turns only the main button into a red **Trash** confirmation. Destination is Recycle Bin on Windows and Trash on Linux/macOS; Qt decides per-file availability. **Delete** always shows a fresh inline image count with a permanent, irreversible, no-Undo warning; press the red **Delete** confirmation to authorize destruction outside system Trash. The saved mode is not saved consent. Cancel in the bottom overlay, Escape, changing mode/scope/filters or navigation discards pending consent. Metadata, exported copies and generation history are preserved in both modes.

A card whose original is gone shows a **File missing** pictogram in Grid, List and Detail; this needs the file to be absent while its attempt folder or the outputs folder is still readable. Anything else that cannot be read (an unmapped drive, a busy or unsupported location, a corrupt file) shows **Image unavailable**, with the reason on hover. The pictogram is only presentation: **Delete** independently re-proves the absence before it clears the card, including when the whole attempt folder was deleted in a file manager, and Trash never claims to have moved a missing file.

Removal runs off-thread one image at a time. A confirmed unchanged Windows pre-disposition refusal settles as `failed`; an unconfirmed mutation, close or acknowledgment is `uncertain` and must not be replayed. New permanent-delete uncertainties raise a one-time amber alert, including after a successful reconciliation pass, but older uncertain rows and interrupted Trash rows do not raise that alert. Reconciliation postponed by unavailable shared locks leaves interrupted intent blocked. Stop after current image preserves completed removals. Results distinguish confirmed moves/deletions, failures and uncertain outcomes; Trash can report Qt success with an unverifiable destination. Failed and uncertain selections remain checked for manual review; pending/uncertain/removed outcomes never automatically replay. Manually recovering a file to its recorded path does **not** make it available again: path, size and timestamps do not prove content identity. Recycled/deleted identities remain hidden until a future explicit recovery workflow. There is no DTFlow Undo or guaranteed restoration, and Trash failure never falls back to permanent deletion. The removal ledger upgrades to schema 2 while preserving old records; older applications refuse the upgraded ledger. See [architecture and platform limitations](../architecture/image-recycling.md). Owner permanent-deletion/platform/packaged acceptance is pending.

## Remembered settings and diagnostics

`.dtflow/last_session.json` remembers the provider, local endpoint and Generate form on submission, a successful connection check and quit. Prompts are shared; generation domain, base-model family, Niche preference, exact model, LoRAs/weights and bound receipts, seed, dimensions, steps, guidance and sampler are remembered separately per provider. It is durable state, not cache, and cache bypass does not remove it.

Connection reports disposable inventory, thumbnail and derived-cache sizes/file counts and offers confirmed category-specific or all-cache cleanup. Sizes refresh after inventory/export operations and when returning to Connection. Inventory snapshots and source image computations use disk cache. Grid/List/filmstrip thumbnails are also kept on disk as 600 px JPEG quality-85 files (or WebP quality 80 with source `dtflow gui -format webp`; `--format` is equivalent and the option goes after `gui`) under the thumbnails category (`cache/thumbnails/`), keyed by the saved output identity plus the original's size, modification time, file ID and selected format, with the original opened and fingerprinted under its shared file lease before a cache read. Alpha-channel thumbnails retain WebP even in JPG mode. The category is pruned toward a 20,000-entry limit (not a fixed byte budget), evicting the oldest-written; Trash and permanent Delete also remove the removed images' cached previews (best effort: cache contention or a late queued write can leave previews behind; see the developer review limits below). Clear thumbnails empties it and `-nc/--no-cache` neither reads nor writes it. Thumbnail reads take no cache lease, so they never make export or cleanup report a busy cache; a cleanup that collides with an open thumbnail read on Windows reports partial cleanup and can be retried. Cleanup is blocked during active generation, queue dispatch or image export in this window. Shared nonblocking cache coordination also prevents cleanup racing actual cache I/O across threads/processes; a busy cache reports retry rather than waiting. If export starts while cache is unavailable, it continues without persistent cache and reports that fallback. New inventory observations can recreate cache after cleanup; cleanup is not a permanent caching disable. Redirected cache paths (including Windows junctions) are refused, and each category is bounded to 100,000 inspected entries. Failures can leave partial cleanup: refresh sizes, resolve permissions and retry. Cleanup never owns outputs, attempts, queues, presets, settings, credentials, assets, custom catalogue data, model files or user export folders.

Raw local/cloud inventory snapshots live under `.dtflow/cache/inventory/`. Cache reads reject incompatible versions, changed endpoints and changed cloud credential identity; no account-dependent evidence is reused blindly. See [runtime layout](../architecture/runtime-layout.md) for the exact ownership contract.

The status bar displays `DTFlow <version> | revision <value> | source: <status>`. Source runs show `dev` with the newest package file modification time as revision; packaged builds use their stamped commit and clean/dirty status. Diagnostic logs do not substitute for durable results.

## Logs

With `-x` / `--dev`, workflow/detector RAM checkpoints and a bounded minute of post-execution memory observations join the same structured session log. Filter `workflow.memory`, `detector.memory`, `ui.memory` or `backend.preview_summary`; see [interpretation and limits](development.md#workflow-and-detector-memory-diagnostics). These are whole-process observations, not proof of a particular leak. Ordinary queued generation, workflow and script runs skip unused live-preview image decoding/forwarding in all modes, while direct Generate keeps its visible previews. Progress and saved final images are unchanged. Preview bytes can still download because the inspected API has no verified preview opt-out.

Component and Event filter dropdowns expand their popup lists to fit the entries without widening the filter row. The leading `dtflow.` is hidden in Component and exact Event labels only; filtering still uses the canonical name, available on hover. Event-family groups are unchanged. Exceptionally long names retain tooltips and non-elided scrollable overflow when screen width limits the popup.
API failures emit `backend.api_failed` at WARNING in normal and development modes. Search this event to inspect the endpoint operation, HTTP/gRPC status, sanitized server explanation, bounded error-response evidence, timing and submission/remote-outcome certainty. Successful per-call summaries remain DEBUG. A cloud `PERMISSION_DENIED` may describe throttling or request validation, not just credentials; the server's actual wording is shown when safely available. The ordinary Connection, generation and asset error presentations retain that explanation without dumping response bodies into the overlay. Unknown outcomes still warn that work may have reached the server and are never automatically replayed.

Error bodies are capped at 8 KiB, with 2,048-character text projections and explicit truncation/omission markers. JSON/text/XML/HTML errors are sanitized; oversized/incomplete bodies, binary content and unknown binary gRPC details are omitted. Tokens, authorization headers, signed URLs, request blobs, prompts and image data are excluded. No response can be reconstructed retrospectively from an older generic log entry. See the [endpoint inventory and implementation boundary](../plans/2026-10-22-api-response-diagnostics.md).

The Logs button at the right of the status bar opens the Logs workspace in place of the current page; the same button, the close button or Escape returns to the previous page without stopping running work. Escape first dismisses an active inline notice or confirmation, then the details panel, then More filters, before closing Logs.

Logs initially selects **>=WARNING**, including WARNING, ERROR and CRITICAL. Severity choices are ALL, >=DEBUG, >=INFO, >=WARNING, >=ERROR and >=CRITICAL; each threshold includes more severe records. A deliberate selection survives refresh, Follow and closing/reopening Logs in the same window; Reset filters and sort restores >=WARNING. This query default does not change what the application records or the raw files.

Logs reuses the shared brand, Queue/Run controls and navigation styling. Navigation has a separate row below the brand, with Logs filters underneath so they do not overlap at smaller window sizes.

- **Sessions.** The selector numbers diagnostic sessions newest first by last write (`#1` is the most recent), with the local last-write time, `this session` for the running app and a short id (full id in the tooltip). Numbers are positions in the current list, not permanent identities.
- **Filters and sort.** Search, severity, component, event or event family (`gallery.*`), UTC time range and one typed `data.*` comparison under More filters. Component and event lists cover the whole selected session (bounded). Click DATE / TIME, or the independent COMPONENT and EVENT targets within the same column header, to sort (↕ inactive, ↑/↓ active; a second click reverses). CHARACTERISTICS opens a numeric-characteristic sort menu; the Sort list under More filters also offers severity. The count and current order show above the table. Records without characteristics show their message instead.
- **Paging and timing display.** Previous page / Next page browse the retained session in pages of at most 500 records, with session-wide counts/search/sort and one page resident. Paging pauses Follow. Timing characteristics ending in `_ms` display rounded whole milliseconds; raw JSON/copy/exports and numeric filters/sort retain the original precision. The viewer honors the active `-q` / `--logs` directory.
- **Follow.** On by default for the newest rows, checking once per second while Logs is visible. Selecting rows, scrolling to older rows or choosing a nondefault sort pauses it. With Follow off, Refresh counts new records matching the current filters against the displayed file snapshot (for example `Refresh (3 new)`); incomplete source evidence is disclosed even if zero new records match (`Refresh (0 new · incomplete)`). If the count is unavailable, it says `Refresh (new)` without inventing a number. Turning Follow on restores newest-first order.
- **Actions.** Details and Copy raw on each row (Return / Ctrl+Shift+C); the details pane shows the event, a `LEVEL · component · seq · UTC time` line, characteristics, message, raw JSON and Copy raw JSONL record. The footer has Copy visible rows, the Save / export menu (Copy selected rows (Ctrl+C), Copy raw JSONL record, Export matching CSV or JSONL — never overwriting an existing file —, Open raw file and Open folder) and Reset filters and sort. Clicking elsewhere in Logs collapses the details pane without consuming the click; splitter, scroll-bar and painted row actions are excluded. An export runs one at a time and shows progress and Cancel in the shared bottom activity overlay. It pins the starting file sizes, excludes later appends, can follow rotation renames, and discloses incomplete source evidence without claiming a complete session. Leaving Logs does not cancel an active export or discard its outcome. Open raw file opens the session's current segment; a viewer that keeps it locked can block rotation on Windows.


The form is created at startup. Export selection has a conservative byte-admission check that reports an explicit error instead of returning a truncated result, but full worker, pending-delivery and resident byte-budget enforcement—and evaluation of lazy form creation—remain in [#106](https://github.com/djebaz/dtflow/issues/106). Windows export/cancellation faults, native keyboard/DPI/performance and packaged acceptance remain in [#107](https://github.com/djebaz/dtflow/issues/107). Offline CI and UI renders are not native Windows acceptance.

## Offline simulation

For fake, `fake-fixture` produces the bundled illustrative lighthouse at the requested aspect and size, while `fake-grid` produces a calibration grid. No model inference or network request occurs. The fake-only Simulation group selects outcome, duration and previews so progress and Stop waiting can be exercised; queues created from the form record those settings.

Combined PR #95/#102 developer review status: thumbnail removal now checks cache/category/shard/leaf boundaries for issue #103, with exact-head CI still required. Late queued writes after removal and oversized flat legacy-cache cleanup remain separate follow-ups; see the [combined landing review](../plans/2026-10-17-images-hybrid-pagination.md#evidence-and-remaining-gates).

## Previewing UI states

Only when requested, run the offline renderer from the repository root:

```powershell
uv run python tools/ui/render_states.py --out tmp/ui-renders
```

The renderer defaults to `QT_QPA_PLATFORM=offscreen`. On Windows, offscreen rendering
needs `QT_QPA_FONTDIR=C:\Windows\Fonts` for fonts (otherwise text renders as squares);
the script sets it to `%WINDIR%\Fonts` when unset, and an explicit value wins.
Alternatively use `QT_QPA_PLATFORM=windows` (visible windows). The renderer blocks
provider network traffic and uses temporary state, fixture images/masks and fake
providers. It renders only explicitly listed states; an unlisted view has no preview.

Workflow screenshots use `<tab>-<NN>-<state>.png`, with independent numbering for `generate`, `images`, `inpaint`, `queues` and `shell`. For example, the initial Generate state is `generate-01-empty.png`, the Images grid is `images-01-grid.png`, and a newly created paused queue is `queues-02-created-paused.png`. See [the screenshot-tool guide](../../tools/ui/README.md) for naming and the separate screen/component capture namespaces.

The initial editor states are `inpaint-01-empty`, `inpaint-02-source-loaded`, `inpaint-03-mask-painted`, `inpaint-04-source-view`, `inpaint-05-mask-view`, `inpaint-06-result-no-result` (current Source fallback), and `inpaint-07-clear-confirmation`. `inpaint-08-find-people-overlay` and `inpaint-09-edit-image-overlay` are explicitly labelled presentation fixtures, not functioning analysis or image dispatch. The manual editor remains work in progress; its default strength is 0.7.

Images also renders `images-28-detail-additional`, `images-29-regenerate-confirmation` and `images-30-regenerate-confirmed` (an offline fake-provider submission shown on Generate).

Screenshots are layout evidence only, not Windows/native-DPI, interaction,
generation, ONNX or packaged acceptance. Extending the script does not mean it
has been rendered; rendering remains owner-requested.

## Inspect checkpoint metadata

In **Generate**, select an exact checkpoint and click the small document icon beside the **Checkpoint** label. The icon action sits above the full-width checkpoint selector.
The read-only inline inspector combines independently maintained sources:

- **DTFlow classification** (`Models_prime.json` / `Models_niche.json`): family, tier and declared modes.
- **Draw Things community metadata** (`community_model_specs.json`): technical fields only when the exact
  checkpoint filename has a published entry; other variants are not substituted.
- **DTFlow model profiles** (`model_profiles.json`): suggested steps, guidance and sampler, clearly marked
  as exact checkpoint suggestions or family defaults.
- **Provider inventory**: last observed listing status and timestamp, not a guarantee of availability or generation.

A filename suffix, when shown, is a filename observation and does not prove weight format/runtime compatibility.
Missing descriptions are shown as unknown. Viewing details does not contact a provider, submit a generation or modify model selection. Existing cloud preflight remains authoritative.

### Local Compute Units estimate

On Local Draw Things and direct DT+ Cloud, the Checkpoint label row includes a read-only meter for one text-to-image output, refreshed from the current checkpoint, dimensions, steps, guidance and LoRA/mode selection. It shows a number with narrow no-break thousands separators, or `unavailable`; fake hides it. Local shows the meter regardless of the Include DT+ Cloud catalogue checkbox. Blue fills a fixed non-linear scale: linear up to 100 (15 %), then logarithmic through 3 000 (50 %) to 40 000 (100 %), so typical estimates are readable. At or above 40 000 the full meter becomes red. Red is a display threshold, not a quota, rejection or credit balance. There is no hover tooltip: Checkpoint details shows only the CU value and supported models in its CU section.

The Python port uses architecture-specific Draw Things instruction-count functions, selected by existing exact-file community metadata, plus current settings and DTFlow profile defaults. Supported ports cover FLUX.1, FLUX.2 Dev/Klein, Z-Image, Cosmos 2.5, Qwen-Image/2.1, Krea 2 and Ideogram 4. Unsupported modifiers use the base-architecture count without modifier costs. Missing exact metadata uses an existing catalogue family classification and local profile/default inputs for a base estimate; broad FLUX.1/FLUX.2 classifications select the base architecture, not a specific variant. No filename guessing or sibling technical-spec substitution is used. Unknown/unsupported families, selected LoRAs and image edits still show unavailable. The catalogues remain independent and unchanged; the accessibility description identifies base estimates.

In Local Draw Things mode, the number describes DT+ equivalent complexity if cloud processing is used; it does not indicate that this local request will use DT+ or incur a CU charge. This offline estimate is not verified server billing, remaining credits, request authorization or model compatibility. It does not change generation, cloud authentication, preflight, queue scheduling or saved requests, and makes no provider/quota calls. Windows interaction and comparison against Draw Things' displayed estimate remain owner acceptance gates; source presence is not parity verification. See the [implementation and source provenance](../plans/2026-10-22-compute-units-local-estimate.md).


### Workspace keyboard shortcuts

F1 opens Generate, F2 Workflows, F3 Queues, F4 Images and F12 Connection. F10 opens the Logs workspace, and pressing it again returns to the previous page, like the Logs status button. F5 keeps its Images Live+Follow behavior. F6 toggles slideshow while Images is active. F11 independently toggles window fullscreen; Escape retains its existing popup/editor, slideshow and optimized-layout behavior. Ctrl+Tab retains its workspace cycle. Navigation does not start generation.

**Ctrl+Enter** (also keypad Enter) activates the currently enabled generation action from anywhere in the active workspace: **Generate image** or **Edit image** on Generate, **Run workflow** on Workflows, and **Start now** or **Add to queue** in the Queues composer (or **Start** / **Start next** for the selected queue when the composer is closed). In Generate's Scripts mode, only an enabled **Run Script** is eligible—not Setup, Continue or Retry Setup. The shortcut does nothing if its action is hidden/disabled, a confirmation or inline editor owns the keyboard, a popup is open, or another workspace is active. It never chooses Save paused. Plain Enter retains its normal input/newline behavior.

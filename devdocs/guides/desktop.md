# Desktop guide

DTFlow's desktop application has Connection, Generate, Queues and Images pages. Connection, Generate and Queues share navigation on the left, the workspace in the center and an Inspector on the right. The panel icon beside the brand toggles details. Images uses the same logo/navigation in two aligned header rows and hides the content rails; returning restores splitter geometry and the shared Inspector choice.

## Launch

From the repository root after `uv sync`:

```bash
uv run dtflow gui
uv run dtflow -x gui
uv run dtflow gui -nc
```

`-x/--dev` adds sanitized diagnostic detail only; `-nc/--no-cache` disables inventory cache reads and writes for that run. Logs use repository `logs/` in development. See [development tools](development.md) and [runtime layout](../architecture/runtime-layout.md).

For a portable Windows executable that launches the GUI directly, see [the build guide](building.md).

## Connect

Select the fake, local Draw Things or DT+ cloud provider on Connection. The local provider needs its server endpoint. Connection checks run in the background and never generate. The Generate page uses the selected provider and endpoint; switching provider resets connection results.

On Windows, save or remove a DT+ API key through Credential Manager. `DRAWTHINGS_API_KEY` in the environment takes precedence over the saved key; an empty or invalid selected credential fails without switching sources. Other platforms do not offer the Windows secure-storage path; use the environment. Keep credentials out of configurations, logs and diagnostic reports.

Refresh inventory to populate model and LoRA choices. A cached inventory can prefill lists at startup and is marked cached; generation always refreshes live. Inventory listing alone does not prove workload compatibility. When the server supplies no model metadata, listed files are offered as model candidates, using the shared request gate.

## Generate

Set prompt, negative prompt, exact model, seed, dimensions, steps, guidance and sampler. After inventory refresh, use All/Image/Video to narrow classified models, choose a base-model family, then choose the exact available checkpoint/quantization. Prime checkpoints are the default; `Include niche models` adds available Niche entries without replacing Prime. If the provider exposes a checkpoint DTFlow has not classified, the page reports it as unclassified and keeps the editable exact-ID field as the explicit escape hatch rather than guessing its family or mode. Width and height snap to the nearest multiple of 64 with an adjustment message. The CLI and loaded configurations remain strict.

**Desktop seed 0 means random on submission or queue creation.** The effective request and metadata retain the resolved seed. Saving a configuration, checking LoRAs and the CLI preserve 0 literally.

Local/cloud Generate submits real work and may consume subscription credits. Initial DT+ generation does not enable PAYG, and failures never switch provider or credential automatically. Use fake for an offline tour.

Progress and previews are not final results. A result is successful only after terminal success, final-output validation and durable saving. Stop waiting interrupts local waiting; it does not confirm remote cancellation. An uncertain remote attempt remains unknown and is never automatically replayed.

## LoRAs and cloud assets

For a classified checkpoint, Add LoRA offers the active provider's available bundled/user-defined LoRAs whose known base family and mode metadata are compatible with that checkpoint. User custom metadata may be stored in `.dtflow/catalogue/custom_loras.json`; it is durable user data and does not make an absent provider artifact available. Unknown/raw checkpoints do not receive guessed compatible LoRAs. Choose exact inventory identifiers and explicit weights. Shared preflight keeps architecture match/mismatch/unknown, availability and application evidence separate. A confirmed mismatch or missing LoRA stops the request; unknown architecture is reported and allowed, never silently dropped. Generation success does not prove LoRA application. Only whole-model LoRAs on a base model are supported; broader exception rules are not pinned.

Trigger prefixes are added only when you explicitly choose that option; otherwise the prompt is unchanged.

The cloud asset list contains this machine's local receipts, not complete DT+ account inventory. Prepare is the only action that can upload and requires confirmation. Reconcile is metadata-only. A confirmed receipt may be bound to a selected LoRA, then revalidated before dispatch; receipt bindings are not exported in configurations. See the [CLI asset reference](cli.md#cloud-asset-receipts) and [cloud integration](../architecture/cloud-integration.md).

## Configurations and outputs

Save and load the shared versioned, credential-free generation configuration. It records generation parameters, not account credentials. This is configuration interchange, not a preset library.

The image workspace supports fit, zoom and pan; 100% means device pixels. Saved-output details expose output/metadata paths, Open folder and Copy path; multiple saved outputs offer a chooser. Previews and illustrative empty-canvas images never enable saved-result actions.

Development attempts use `.dtflow/attempts.sqlite3`; final outputs and reproducibility metadata use `.dtflow/outputs/<attempt-id>/`. Store state on a local filesystem with the patched SQLite runtime described in the [backend contract](../architecture/backend-contract.md#durable-state-and-layer-enforcement).

## Queues

The Queues page creates paused seed sweeps or Pair/Cross batches from the Generate form. Review the count and Dry run table before creation. See [queues and batch plans](queues.md) for controls, concurrency, restart and unknown-outcome handling.

During generation or queue dispatch, the window owns the state directory. Generate is disabled during dispatch and competing CLI owners fail clearly as busy; another CLI operation can own generation between desktop operations. Reading and posting queue controls do not require ownership. Closing during dispatch asks first and does not imply remote cancellation.

## Images

Images browses committed final outputs recorded in the selected state directory.
With no saved viewer preference it opens in Grid; later launches restore the
saved Grid/List/Detail mode, filters, sort, selection, scroll positions,
thumbnail scale and auto-refresh choice.

Grid, List and Detail share the same bounded query. Positive-prompt search is
case-insensitive; model and LoRA filters use exact recorded identifiers. Date,
exact dimensions and aspect-ratio filters compose with AND, and sorting supports
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
fullscreen/Escape. Additional details include the negative prompt, resolved
seed when recorded, provider, file size/path and file actions. Raw metadata is
bounded and sanitized; unavailable evidence is shown as `Not recorded` or
`Unknown`, not inferred.

Copy path, Open folder and external-viewer actions operate on the actual saved
artifact. Missing or corrupt files remain represented without preventing other
outputs from being browsed. Browsing does not generate, modify or delete saved
outputs.


## Remembered settings and diagnostics

`.dtflow/last_session.json` remembers the provider, local endpoint and Generate form on submission, a successful connection check and quit. Prompts are shared; generation domain, base-model family, Niche preference, exact model, LoRAs/weights and bound receipts, seed, dimensions, steps, guidance and sampler are remembered separately per provider. It is durable state, not cache, and cache bypass does not remove it.

Raw local/cloud inventory snapshots live under `.dtflow/cache/inventory/`. Cache reads reject incompatible versions, changed endpoints and changed cloud credential identity; no account-dependent evidence is reused blindly. See [runtime layout](../architecture/runtime-layout.md) for the exact ownership contract.

The status bar displays `DTFlow <version> | revision <value> | source: <status>`. Source runs show `dev` with the newest package file modification time as revision; packaged builds use their stamped commit and clean/dirty status. Diagnostic logs do not substitute for durable results.

## Offline simulation

For fake, `fake-fixture` produces the bundled illustrative lighthouse at the requested aspect and size, while `fake-grid` produces a calibration grid. No model inference or network request occurs. The fake-only Simulation group selects outcome, duration and previews so progress and Stop waiting can be exercised; queues created from the form record those settings.

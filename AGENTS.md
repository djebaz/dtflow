# Repository contract

These instructions apply throughout `dtflow`.

## Naming

- Use `DTFlow` for the human-facing product, `DTF` as the short product name, and `dtflow` for Python packages, commands, and machine-facing artifact names.
- Keep established external Draw Things, DT+, model, checkpoint, LoRA, scheduler, and sampler names unchanged unless a compatibility/migration reason requires a mapping.

## Before changing behavior

- Read this file, `README.md`, `devdocs/current_dev_status.md`, `devdocs/plans/initial.md`, `devdocs/plans/releases.md`, `devdocs/releases/unreleased.md`, relevant architecture/research documents, and the current canonical files before editing.
- Treat repository files and verified upstream interfaces as the source of truth. Do not invent runtime behavior, compatibility, benchmarks, test results, or verification.
- Preserve unrelated working behavior.

## Architecture

- Keep UI, orchestration, backend-neutral models, persistence, workers, and generation backends separated by clear boundaries.
- Keep protobuf/gRPC and provider-specific types inside the relevant backend integration.
- The UI must not directly own Draw Things gRPC or future DT+ protocol behavior.
- Preserve reproducibility: record prompts, seeds, model/checkpoint identifiers, LoRA settings, relevant generation parameters, outputs, and durable job state where applicable.
- Keep long-running workflows resumable and inspectable.

## Python environment and dependencies

- DTFlow uses Astral `uv` as the canonical Python project, environment, and dependency workflow.
- Keep project metadata and declared dependencies in `pyproject.toml` and commit `uv.lock` for reproducible development and builds.
- Use `uv sync` to create or synchronize the project environment.
- Run project tools and Python commands through `uv run` where practical, for example `uv run pytest`, `uv run ruff check .`, and `uv run dtflow`.
- Add, remove, and update dependencies through the uv-managed project configuration rather than ad-hoc `pip install` commands.
- When a change adds, removes, or changes a third-party dependency, use the appropriate uv workflow and keep `pyproject.toml` and `uv.lock` synchronized in the same change.
- Put development-only tools in the dev dependency group rather than runtime dependencies.
- Commit dependency metadata and lockfile changes together with the code that requires them.
- Do not manually edit `uv.lock`; regenerate it through uv.
- Do not maintain a parallel hand-edited `requirements.txt`. Add a compatibility export only if a concrete external tool or deployment requirement needs one, and treat it as generated output rather than a source of truth.
- Prefer `uv run` for validation so checks execute in the locked project environment.
- Do not mutate a supposedly reproducible environment with arbitrary package upgrades during normal validation.
- When changing `pyproject.toml` tool configuration, keep documented commands and CI behavior aligned with the canonical configuration.

### Agent environment readiness

- Apply the shell settings below and run the linked readiness check before project Python commands. If it passes, proceed without running setup.
- If prerequisites are missing, run `bash tools/setup-agent.sh` in a disposable agent environment and repeat the check. Report failures; never hide them with `|| true`. Stop only commands whose prerequisites remain unavailable.
- The script defaults to the checkout containing it. Set `DTFLOW_PROJECT_ROOT` to an absolute checkout path when another location is needed. It reads the Python version from that checkout's `.python-version` and synchronizes its committed `uv.lock` with `uv sync --frozen`.
- In every agent shell, export `UV_LINK_MODE=copy`, `QT_QPA_PLATFORM=offscreen`, `UV_SYSTEM_CERTS=true`, and `UV_PYTHON_PREFERENCE=only-managed` before `uv`/Python commands, including readiness checks. A separately executed setup script cannot retain exports in its parent or later shells. If setup bootstrapped uv, add `<checkout>/tmp/agent-bootstrap/bin` (Windows Bash: `Scripts`) to that shell's `PATH`.
- This is development/agent setup, not a production-runtime bootstrap or native desktop launch configuration. Do not use offscreen mode for interactive desktop acceptance. Setup must not run tests, render UI states, download model checkpoints, submit generation or call providers.
- See [agent setup](devdocs/guides/development.md#agent-environment-setup) for platform requirements and commands.

## Runtime state and cache

- Gallery snapshot fallback is browsing-only: publish the committed metadata/availability projection after generation/queue sessions and clean close, skipping unchanged state or creating a missing snapshot. Keep it under `cache/derived/`, honor cache coordination and `--no-cache`, preserve the prior file on failure and never use snapshot records for export/edit/removal authority. Use fresh live state for mutations; snapshot browsing does not add cross-runtime SQLite writer support. See [snapshot architecture](devdocs/architecture/gallery-snapshot.md).

- Follow [the runtime layout](devdocs/architecture/runtime-layout.md). During repository development, application state and backend-neutral outputs live under ignored `.dtflow/`; retained live-test evidence lives separately under ignored `test-runs/<run-id>/`. Offline tests use temporary directories.
- Keep cache flat within each type: inventory uses `cache/inventory/local_inventory.json` and `cloud_inventory.json`, plus `lora_inventory.json`, not nested backend/endpoint directories. Use these fixed names for the current single-local/single-cloud configuration and retained live evidence. Endpoint/server identities belong inside JSON as observations, not stable filename keys. When cache reads are introduced, refresh/invalidate on endpoint or credential changes; never reuse account-dependent evidence across changed credentials. Include integer `cache_version` from the inventory writer's `CACHE_VERSION`; future cache readers treat missing/mismatched versions as cache misses and rebuild, without migrating. Durable state has its own schema policy.
- Keep outputs grouped by attempt, not backend. Create directories only when needed; cache cleanup must preserve outputs, durable state and retained test evidence.
- Packaged Windows builds must use an appropriate per-user application-data location by default rather than writing beside the executable.
- Keep disposable cache separate from durable state. Organize cache by type so categories can be measured and purged independently.
- Use persistence/cache_coordination.py for cache file I/O and services/cache_management.py for bounded category measurement/purge. The shared nonblocking thread/process lease lives in <state>/cache.lock outside purge categories; never delete it to clear a busy owner. Reject redirected cache roots/categories/files and preflight selected category trees before purge. Export holds a lease across computation/checkpoints or falls back to no persistent cache; no-cache bypasses cache I/O. Keep owner/runtime acceptance separate from code presence.
- Provide cache cleanup early enough to prevent generated images and derived assets from growing without bounds.
- Support a cache-bypass launch option `-nc` / `--no-cache`; it must disable cache reads and writes for that run.

## Logging and development diagnostics

- Use one shared Python logging setup for CLI, Qt, services and backend adapters. Prefer the standard-library logging module; presentation layers must not maintain separate logging engines.
- Before changing logging contracts, diagnostic readers, benchmark evidence or the planned Logs workspace, read [structured diagnostics architecture](devdocs/architecture/structured-diagnostics.md) and the current [PR #105 implementation plan](devdocs/plans/2026-10-19-structured-log-viewer.md). Treat the architecture guide's target JSONL sections as **planned** until implemented; the existing text/CSV logging contracts remain canonical for current running code.
- Preserve `logging_setup.py` as the single logging configuration/lifecycle authority. Ordinary standard-library logging remains supported; structured DTFlow measurements should use the planned typed emitter rather than ad-hoc machine-parsed strings. Do not introduce competing persistent handlers, diagnostic file writers, schema/redaction/parsing rules, session or sequence counters, retention policies, or location overrides in UI/backends/benchmark tools. Explicit benchmark reports and requested exports are derived artifacts, not diagnostic sinks.
- For large Qt tables and log exploration, follow [Qt model/view engineering patterns](devdocs/guides/qt-model-view-patterns.md): the shared Qt-free reader owns whole-session filtering/sorting; worker threads never mutate `QAbstractItemModel`, and models use bounded projections, GUI-thread batched updates, stable selection and cancellation. These are design constraints, not claims of Windows performance.

- In repository development, write diagnostic files under repository-root `logs/`, which remains ignored by Git. Keep application state under `.dtflow/` and disposable scratch work under `tmp/`; logs do not change those conventions.
- Packaged Windows builds default to `%LOCALAPPDATA%\DTFlow\logs\`, never the repository, executable directory or current working directory. Allow an explicit log-location override when implemented.
- Write readable UTF-8 text logs with timestamps, severity, component and session identity. Include version/source commit and backend identity; attach local job/attempt IDs when available to correlate concurrent work.
- Normal mode records important lifecycle events and errors. Explicit development mode adds sanitized connection/auth-refresh events, request validation, state transitions, stream events, timings and exception tracebacks. It changes diagnostic detail only, never generation, retry or billing behavior.
- Bound file size and retention through rotation. Avoid rotation collisions between simultaneous processes and keep log I/O from blocking Qt interaction.
- Never log API keys, tokens, authorization headers, credentials or raw image/Base64 payloads. Exclude full prompts and other sensitive request data by default; sanitize exception details as well as ordinary messages.
- Logs are diagnostic evidence, not the durable job/attempt store or proof of successful generation. Log cleanup must not delete durable state or outputs.
- Report log-location/write failures clearly without exposing secrets or triggering generation retries. Logging setup must not consume subscription credits or enable external network access.

## CLI

- The initial CLI runs from source for development and automation; packaging initially targets the Qt application, without a separate packaged CLI.
- Provide a `dtflow` entry point for normal development/application launch rather than requiring long source paths.
- For normal user-facing options, expose a canonical pair: a stable, unique lowercase short alias and a concise long form.
- Reserve `-h` / `--help` for the standard help action. Do not use uppercase short aliases such as `-H` or `-P` as collision workarounds, and avoid repurposing widely expected flags when a clearer mnemonic is available.
- Prefer a single lowercase letter for short aliases. If the obvious letter is already taken or would be confusing, choose another clear lowercase mnemonic. Use lowercase multi-letter aliases only when needed to preserve an established contract or when a single-letter choice would be materially worse; `-nc` / `--no-cache` is an intentional existing example.
- Keep long option names short and recognizable, usually one word where that remains clear, for example `--host`, `--port`, `--cloud`, `--preset`, `--jobs`, `--full`, `--serial`, `--out`, and `--conn`. Use a multi-word long form only when the shorter form would be ambiguous; avoid verbose names such as `--full-workload` when `--full` is sufficient.
- Short and long forms of the same option must have identical semantics, defaults, validation, and side effects. `--help` should advertise only the current canonical spellings.
- When renaming an established option, preserve the old spelling as a hidden compatibility alias where practical and safe. Documentation and examples should use only the canonical spelling.
- Keep every documented flag synchronized with the implemented CLI and `--help`.

## UI and Qt

- The gallery snapshot condition is a narrow exception to transient amber expiry: use one persistent, single-line amber status row in the shared bottom overlay, without a title/detail stack, timer or lifetime bar. Update its keyed row in place, never replace active confirmations/editors, and remove it after stable live recovery (another consecutive live success at least one five-second refresh interval after the first). Brief live success uses recovering wording in that same row; this presentation grace period never delays fresh live authority or permits snapshot mutations. User dismissal stays suppressed for that whole fallback episode (including brief live successes, retries, snapshot timestamp changes and navigation); only a new fallback after stable recovery may show it again. Keep its canonical Designer form and shared token styles; other warning/result expiry rules stay unchanged.

- **UI previews.** Every change that adds or alters a desktop view, state, overlay row or dialog must extend `tools/ui/render_states.py` in the same change, using offline fixtures (fixture images and masks, fake provider, no network). The script renders only the states it lists. A view that is not in the script has no preview. On Windows, offscreen rendering has no fonts unless `QT_QPA_FONTDIR=C:\Windows\Fonts` is set (or `QT_QPA_PLATFORM=windows`); document this in `devdocs/guides/`. Render only when asked, and report screenshots as layout evidence, not as Windows or DPI acceptance.
- Use PySide6 and Qt Widgets initially.
- Read the root `DESIGN.md` before adding or changing a desktop tab; follow its shared tokens, interaction states, layout principles and page checklist. Keep its token snapshot aligned with the runtime `tokens.json` when values change.
- Keep Qt Designer `.ui` files canonical and tracked under `src/dtflow/ui/forms/`.
- Keep the shared application QSS under `src/dtflow/resources/styles/`; apply it once at startup and prefer stable object names or dynamic properties over scattered inline styles.
- Do not put business logic or backend access into generated Designer Python.
- Treat `previewHost`, `queueItemsView` and similar Designer objects as presentation seams for handwritten widgets/models; promoted/custom widgets must not own backend protocols or service rules.
- Keep DTFlow-owned confirmations, notices, result/progress presentation and text/selection editors inside the main window; native file/folder pickers remain allowed. Cloud LoRA Prepare starts directly after explicit file selection (no second confirmation); disclose potential upload and later-generation credits in the asset note/tooltip, and preserve backend/busy revalidation. Use `ui/inline_feedback.py` and its canonical Designer form. Only Confirm replaces the original action button, using shared confirmation styling (red for Delete/Clear, mint/teal Auto-refresh ON styling otherwise) and preserving its width; Cancel lives in the shared bottom overlay, not the action row. No expiration timer; Escape/navigation/relevant scope changes discard pending consent. LoRA Add combines searchable identifier and weight (0.0–3.0 in 0.1 steps) in one editor; do not silently clamp imported/saved configuration values.
- Use `ui/activity_strip.py` and canonical activity/inline forms for one thin bottom overlay covering confirmation, editors, progress and outcomes; never insert it into the main content layout. Show essential information directly (no Details button); permit wrapping/long-inspection scrolling. Export starts without confirmation. Label concurrent operations separately; expire success and all amber warning/partial/stopped overlay rows after six seconds, pausing on hover/keyboard focus across the overlay, with a 2 px state-colored draining lifetime line and fade-out distinct from blue task progress. Keep warnings amber, failures red, and active editors protected from unrelated notices. Dismiss standalone notices with Escape; set destructive confirmation intent explicitly at its call site. Keep red errors and explicit informational inspections until dismissed; amber expiry never changes saved attempt status, unresolved reservations, recorded warnings, retry rules or remote cancellation uncertainty. Reuse the canonical Images Delete SVG as a danger-red trash icon for both the Recycle Bin confirmation and running Trash progress; do not use a recycling emoji. Parent receivers/timers/deferred callbacks to their owning overlay widgets. Keep task ownership/cancel semantics in existing controllers/services; hiding notices or changing tabs must not stop work. Generate keeps its 52 px two-line resting prompt (180 px expanded), places its single Generate image button in the prompt header, and delegates Stop waiting and real reported progress to the shared overlay; do not keep duplicate center-panel action or synthetic-percentage progress controls. Retain a subdued durable status label for unknown/failed outcomes.
- With Images Auto-refresh Off, one successful live refresh ends the snapshot warning episode immediately; do not wait for another manual refresh or add a recovery timer. Turning polling off after a successful live read is already recovering also ends that presentation episode. Snapshot failures never count as recovery; live authority and unrelated confirmations remain unchanged.
- `ui/runner.AsyncRunner.closed` becomes true at shutdown start; `shutdown_started` notifies a window-parented QObject slot to stop owned polling/save/debounce timers; the receiver must be destroyed with the window, not outlive it as a plain Python controller callback. Drop queued GUI result delivery after shutdown, while worker cancellation/durable settlement still completes. Programmatic `MainWindow.close()` must request the same inline close consent before shutting down; a refused close leaves dispatch running.
- Do not block the Qt event loop with network, generation, or other long-running work.

## Saved-image export

- Keep one authoritative export card and controller: destination, metadata/privacy/routing settings, progress, cancel and Start controls remain canonical in `main_window.ui`. Connection hosts the card in its middle panel; Images **Export** is an always-available, checkable panel toggle (independent of checked-image count), with neutral Off and blue selected On styling. It opens a scrollable right-hand column beside the listing and mounts the same card there, using checked-image scope without a scope dropdown or tab switch (Connection retains all scope choices). Opening the panel must never submit work; actual Images export still requires checked identities. Navigation restores the card to Connection, and hiding the column never cancels an operation. Do not duplicate export controls, preferences, encoding or service behavior.
- Use `services/image_export.export_outputs` for single and bulk exports; UI controllers must not duplicate encoding or metadata rules. Pillow is permitted only in the headless image_export/image_quality/image_preparation services; core remains dependency-neutral and all provider/Qt boundaries remain enforced.
- Export JPGs and matching JSON sidecars at the selected root except when the default-on noise/blank checkbox routes flagged copies only into noise/ or blank/. With it off, keep exports completely flat. No attempt/provider/date subfolders, original moves or overwrites. Use services/image_quality.py as the shared advisory detector; no job-status changes.
- Keep no persistent destination/export records. New names include the full _dtf-<attempt-id>-<output> marker; list root/noise/blank once to build transient membership, with bounded EXIF fallback for legacy/renamed files. Fast skips use the full identity plus JPEG framing check, not ambiguous short prefixes or cryptographic integrity claims. Existing copies remain unchanged even when options differ. Before counting skips, check committed/non-recycled availability in consecutive batches of at most 128 identities under a short original-file lease; use the identity-only selected-attempt query, not per-skip full gallery records. Actual exports retain per-image leases.
- Cache source checksum/pixel computations independently of destination in cache/derived/image_analysis.json; invalidate source fingerprints/detector version, honor --no-cache and Derived cleanup. No source paths, prompts or destination history in this cache. Create missing destination folders and parents; Windows creation/EXIF dates use saved time as a completion proxy, never invented start/end timestamps.
- Preserve prompt privacy across filenames, visible EXIF, full portable metadata and sidecars; never copy credentials, private paths or prepared cloud-asset references. Keep request/effective settings and saved-time/per-image-seed semantics explicit.
- `export_preferences.json` is durable convenience state, not cache. Keep one destination per runtime (Windows, WSL, platform) with common privacy/routing options; never translate or invent another runtime's path, and leave unreadable or newer schemas unchanged. Follow [image export architecture](devdocs/architecture/image-export.md) and the [desktop export guide](devdocs/guides/desktop.md#export-saved-images).

## Saved-image removal

- Keep `services/image_recycle.py` and persistence Qt-free. Inject the desktop's `ui/system_trash.py` adapter and non-mutating eligibility check; use worker-local QFile instances off the GUI thread.
- The Images split control explicitly selects Trash or permanent deletion, with separate environment-specific mode preferences. Default Trash; unavailable Trash disables only the main action. Choosing a mode never submits work. Never persist consent or automatically substitute permanent deletion. Each permanent operation needs a fresh inline irreversible warning/count and explicit consent (the compact button reads Delete, while the inline warning spells out permanent deletion); mode/scope/navigation changes invalidate consent.
- Permanent mode reuses the existing managed-original service and ledger; it is not a Trash failure fallback. Keep metadata, history, exports and image-edit inputs. An image-operation attempt's sidecar-declared `raw-1.png` is disposed with its final in the same mode, raw first, as its own ledger row and helper `kind`, never by directory scan or name alone. Cross-platform managed-original removal requires freshly verified mappings, alias-aware committed ownership counts, fingerprints and native Windows lock/removal boundaries. On native Windows, WSL1-recorded originals require matching complete shared-state image and metadata paths, database identity and sidecar provenance; a read alias alone never authorizes mutation. Missing/ambiguous support fails closed. Keep the source-only Windows helper limited to shared leases and identity-checked handle deletion; do not duplicate service/ledger orchestration there. Native POSIX removal retains descriptor-relative checks and the existing leases. Never replay deleted or uncertain outcomes.
- Preserve managed-original eligibility, per-image durable intent/outcomes, original-file leases and uncertain recovery. No permanent-delete fallback or automatic replay. Never infer restored content identity from path/size/mtime; keep recycled and historical heuristic-restored records hidden until an explicit content-verified recovery workflow exists. Preflight reads selected records and committed path-reference counts in at-most-128 batches with one snapshot per batch, while per-file filesystem validation and immediate execution revalidation remain mandatory. Under the batch/original-file leases, atomically validate the latest ledger outcome and commit pending intent through `claim_recycle_intent` before calling Trash; only new identities or known failed outcomes may be claimed. Pending/uncertain/recycled/restored and unknown outcomes must remain untouched and cannot be replayed from a stale plan. Use Qt capability detection and let each move determine Trash usability; do not add OS/filesystem allowlists or fixed-drive gates. Keep missing/unverifiable receipts uncertain and distinguish Qt-reported success from confirmed availability. Follow [image recycling architecture](devdocs/architecture/image-recycling.md); capability detection and code presence do not establish Windows/Debian/macOS acceptance.

## Image-operation preparation

- Use headless services/image_preparation.py and backend-neutral core/image_inputs.py for source/mask normalization and fitting. Pillow stays out of core and Qt. Run CPU preparation on a worker; no provider or persistence behavior belongs in the pixel service.
- The current preparation slice is not dispatch support: generic CLI, core requests, queues and wire remain txt2img-only. Follow devdocs/architecture/image-operations.md and the backend-first smart-mask plan before changing request schemas, provider mapping or editor behavior.
- Owner/maintainer review the image-library boundary with each slice; update the existing import allowlist narrowly, never blanket-allow Pillow/ONNX or copy tools CLI runners into production.

## Generation safety

- Do not assume LoRA, checkpoint, quantization, scheduler, sampler, or runtime compatibility without evidence.
- Keep prompts, seeds, dimensions, and other relevant parameters controlled in comparisons unless intentionally varied.
- Do not run expensive or potentially billable training/inference merely to validate syntax, configuration, or application plumbing.
- Never expose credentials, tokens, authorization headers, or sensitive payloads in logs.
- Direct DT+ cloud model descriptions (`override.models`) and LoRA `version` strings follow [cloud integration](devdocs/architecture/cloud-integration.md#model-descriptions-for-unlisted-checkpoints): exact-file descriptions only, derived entries marked as derived, and never a family-wide guess.
- Shared cloud credential resolution selects `DRAWTHINGS_API_KEY` when present, otherwise the saved Windows credential. Empty/invalid selected credentials fail clearly; never silently switch sources after validation or authentication failure.

## Tests

- Use `pytest` as the standard test runner. Use `pytest-qt` when Qt-specific behavior needs direct coverage.
- Separate unit tests from live integration tests.
- Ordinary test execution must never submit a real Draw Things or cloud generation accidentally. Live generation tests must be explicit and separately gated.
- Prefer extending an existing contract-focused test over accumulating one-off regression tests for every implementation change.
- Test each behavior at one authoritative layer. Higher layers cover only new behavior or necessary composition of tested layers; reuse requires the same implementation path. Acceptance bullets do not require one test each.
- Live checks may replace duplicated end-to-end fixture coverage, but retain deterministic tests for scheduling, persistence, uncertainty and controlled failure boundaries. Measure added runtime; test counts and test/source line ratios are not quality gates.
- Report only checks actually run.

## Packaging

- Target a Windows single-executable distribution initially, while keeping source execution supported for development.
- The canonical `tools/packaging/build_exe.py` build targets `launch_gui.py`: open the desktop directly, use Windows windowed mode, and preserve shared logging/build identity, canonical forms/resources and secure credential dependencies. Keep source CLI execution separate. Follow [the build guide](devdocs/guides/building.md); compilation alone does not establish Windows executable acceptance.
- Treat `build/` and `dist/` as generated artifacts.
- The manual Actions `pr` input builds a same-repository PR head commit to an artifact only. Never combine PR source with release actions; reject `pr` plus `release_tag`, skip the release job for PR builds and keep their dependency-cache namespace separate.
- Do not commit model/checkpoint artifacts or duplicate large model files.
- Keep build inputs and version metadata reproducible.

## Version and build identity

- Keep one authoritative application version and one shared build-identity source; CLI, Qt, packaging and output metadata must consume them rather than maintain independent copies.
- Display the application version plus a short Git commit ID in CLI version output and a discreet Qt About/status area.
- Name distributed builds `dtflow-<version>-g<short-sha>-<platform>-<arch>.<ext>`, using the actual source revision, target architecture and artifact type. An eventual installer follows the same identity convention.
- Capture the full source commit and dirty-worktree status at build time and bundle them with the application. Packaged execution must not require Git or a repository checkout.
- Build public releases from a clean checkout. Development builds must disclose local modifications; when revision metadata is unavailable, report it as unknown rather than inventing a commit ID.
- Persist application version and full source commit in generation metadata where available, and include build identity in smoke-test reports so results identify the executable actually exercised.
- Check that packaged filenames, CLI output and Qt display agree when those delivery paths exist. Do not claim runtime or packaging verification from documentation checks alone.
- Use manual release downloads initially; an integrated updater is outside the initial scope.

## Documentation and releases

- Keep `README.md` GUI-first: product presentation, implemented feature descriptions and design principles, minimal setup/desktop launch, repository layout and documentation navigation. Link the CLI only as an automation/development reference; put detailed usage, testing, diagnostics and safety instructions in `devdocs/guides/`, not README status sections or implementation history.
- Keep `devdocs/current_dev_status.md` short and dated, mapping implemented, remaining and added scope to `devdocs/plans/initial.md`. Update it when scope changes; distinguish code presence from acceptance and link detailed evidence rather than duplicating it. Repository source and verified evidence remain authoritative.
- Follow [the release roadmap](devdocs/plans/releases.md) for usable version milestones and acceptance gates; planned numbers are not shipped releases.
- Version durable state schemas separately from application versions. Preserve state and outputs on supported migrations; reject unsupported schemas clearly and never replay ambiguous attempts during an upgrade.
- Track pending changes in `devdocs/releases/unreleased.md`.
- Follow [the version-bump and release guide](devdocs/guides/releasing.md) for uv metadata/lock updates and release sequencing. After the release PR is squash-merged, the owner builds locally or requests the manual Windows Actions build, and smoke-tests the exact final executable. The CI path may freeze an existing tag and prepare a draft before smoke acceptance; never publish or replace published assets before owner acceptance. The manual workflow is build/draft-only, not an automatic release.
- When releasing a version, move the relevant entries into `devdocs/releases/v<version>.md` and reset `unreleased.md`.
- Update the relevant plan, architecture, research, and release documentation when behavior changes.

## Workflow

- Avoid committing directly to `main`. Normal repository changes should go through a feature branch and pull request. Direct commits to `main` are reserved for exceptional, low-risk cases where a pull request would add no meaningful review or safety value.
- For non-trivial work, create the feature branch and pull request early, after an initial coherent commit, so implementation progress and review context are visible from the start.
- Commit regularly at meaningful checkpoints and group related file changes into coherent commits. Do not create one commit per file or per trivial edit unless the change is genuinely independent.
- Keep the working PR history reasonably reviewable even when the final merge will be squashed; squash merge is not a substitute for excessively fragmented development history.
- Placeholder directories are intentional in this repository; use `.gitkeep` only while a directory has no real tracked files.
- For runnable Python changes, validate syntax, imports, configuration parsing, and `--help` where applicable.
- At the end of implementation work, state what changed, what was verified, what was not verified, whether training/inference ran, and whether any potentially billable action was performed.

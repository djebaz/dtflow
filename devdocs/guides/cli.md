# CLI reference

The CLI is DTFlow's automation and development interface. For the desktop application, see the [desktop guide](desktop.md).

## Experimental LoRA q8p CLI

`lora inspect`, `lora compare` and `lora quantize` are local-only source experiments (#353). These CLI commands do not download, access credentials, upload, generate or register managed files. The desktop’s Connection → Custom LoRAs row action wraps the same CPU service, retaining a separate q8p and offering the existing Prepare approval editor; that managed-library integration is not a CLI side effect. This is F16 `.ckpt` → q8p/ezm7 `.ckpt`, not direct safetensors conversion. Valid storage and small numerical errors are not proof of DT+ compatibility or unchanged image quality.

Inspect/compare use normal dependencies. Quantization needs the optional native clustering group. Ordinary source GUI installs do not enable it by default; for the desktop q8p action start source DTFlow with `uv run --group quantization dtflow gui`. The canonical Windows executable builder includes that group:

```powershell
uv sync --frozen --group quantization
$revision = git rev-parse HEAD
uv run --frozen dtflow lora inspect "D:/references/example_f16.ckpt" -r $revision
uv run --frozen dtflow lora inspect "D:/references/example_ios_q8p.ckpt" -r $revision
uv run --frozen dtflow lora compare "D:/references/example_f16.ckpt" "D:/references/example_ios_q8p.ckpt" -r $revision
uv run --frozen --group quantization dtflow lora quantize "D:/references/example_f16.ckpt" -o "D:/experiments/example_q8p.ckpt" -e -r $revision
uv run --frozen dtflow lora compare "D:/experiments/example_q8p.ckpt" "D:/references/example_ios_q8p.ckpt" -r $revision
```

Use your own input/output paths; the output directory must already exist. `-o/--out` and explicit `-e/--experimental` are required for the writer. It accepts structurally recognized raw F16 Krea 2, Z Image and SDXL Base only; unknown, partial or already encoded inputs fail clearly. Neither source files nor existing outputs are overwritten. `.dtflow`/`lora_files` output paths and redirected directories are refused. Exclusive publication needs hard-link support (e.g. local NTFS); there is no overwriting fallback.

`-j/--jobs 0` (default) automatically selects the CPUs available to the process. Use `-j 1` for serial in-process encoding, or another positive integer for an explicit worker count. Windows limits the pool to 61 workers (an explicit larger value is refused). Independent blocks use spawned processes in a bounded queue spanning tensor boundaries, so one-block tensors can use multiple workers too. The CPU pool allows up to twice the worker count in running/queued tasks, with a bounded four-times-worker reordering window (plus the batch being emitted). Small tasks (at most 4,096 values each) share transfers in groups of up to eight tasks and 16,384 values. Completion-driven refill overlaps encoding with ordered assembly and writes; only the parent touches SQLite/files. No extra processes are selected. Parent tensor buffers and worker memory/startup overhead scale with the worker count; a speedup is not guaranteed. Progress reports completed blocks, elapsed time and approximate encoding ETA, unavailable until the first completion; validation/publication follow encoding. The report records the worker count.

The standalone [Colab q8p notebook](../../tools/colab/dtflow_lora_q8p.ipynb) downloads only the 18 files in `tools/colab/q8p_files.json`, not a repository checkout. It runs the same command/service through the lightweight `q8p_cli.py` launcher, whose uv-generated script lock installs only NumPy 2.3.5 and kmeans1d 0.5.1 into an isolated environment. Colab's NumPy/Pillow stack stays unchanged; Qt and the other desktop dependencies are not installed. Upload an F16 CKPT or select a runtime/explicitly mounted Drive path; conversion stays on local runtime storage and downloads the new CKPT/report. No Swift build or GPU is needed. Its [markerized source](../../tools/colab/dtflow_lora_q8p.py) is canonical; regenerate the notebook after edits, then run `uv run ruff format tools/colab/dtflow_lora_q8p.ipynb` for code-cell final-newline formatting. The marker format itself is not executable Python and is excluded from Ruff; the generated notebook cells are checked instead. Owner reports now cover one full Z Image conversion and corrected scheduler timing; the later locked-setup/startup-label corrections still need Colab recheck.

**Optional GPU notebook cells 4–5:** these run with the kernel's already installed CUDA/PyTorch, not the standalone CPU launcher's managed Python, and use approximate FP32 Lloyd clustering rather than the CLI's optimal native 1D clustering. Cell 4 samples blocks but writes no CKPT; cell 5 produces a separate full GPU CKPT through the shared validated/no-clobber writer. One owner NVIDIA L4 report at revision `2cdeb7f` records a complete Krea 2 result in 11.632 s (194,363,392 → 100,569,088 bytes, 48.257% smaller, 472 q8p + two ezm7 tensors; zero batches reported nonconverged). The warmed 256-block sample's 0.354 s timing is **not** a whole-file duration. An owner-supplied iOS comparison reference has matching tensor inventory but unverified original-weight provenance, so numerical-error comparisons do not establish an iOS/visual-quality ranking or DT+ acceptance. The shared service now falls back to `os.cpu_count()` when the notebook kernel uses Python 3.12; the changed GPU cell still needs an owner recheck on that kernel. See the [architecture evidence](../architecture/lora-quantization.md#owner-gpu-evidence-and-review-follow-up-2026-11-15) before selecting the experimental GPU route.

Cell 1 exposes the source revision, GitHub username and Colab secret ID. Put the token in Colab Secrets and enable notebook access; the form takes only its ID (default `GITHUB_TOKEN`). A blank secret ID selects anonymous access; a named secret that is unavailable/empty fails explicitly. GitHub API authentication uses an in-memory header, never a URL, command argument or persisted Git config; only fixed HTTP status/network errors are shown. The API resolves the branch/tag/commit once, then all manifest/content requests use that exact SHA and verify each file's Git blob hash. The auth header is removed before launching uv. Anonymous API rate limits apply; use a read-authorized secret if required. The resolved SHA is recorded; installed-package build version may be unknown in this minimal source bundle. This revised setup still needs owner execution.

The standalone script declares its uv prerelease policy explicitly so `--locked` does not depend on the repository's absent `pyproject.toml`. Setup streams uv/launcher output, including dependency/entry-point errors. Its locked help command passed in an independently copied minimal Windows bundle; this does not establish Linux/Colab conversion acceptance.

Before encoding, the CLI prints `CPUs detected: N | Workers set: M (auto/explicit)` from its actual resolved settings; the report records both counts. Owner Colab reports for one 420-tensor Z Image file show the corrected 12-worker run completed in 103.483 s versus 455.531 s with the older ineffective scheduler, producing identical reported CKPT hashes and 46.609% smaller output. This is one comparison, not a universal scaling or quality guarantee. That run used the owner's temporary `--locked` workaround; the later locked-setup correction and new startup line still need Colab recheck.

`-r/--revision` is an optional owner-recorded Git hash, important because ordinary source builds may report `source_commit=unknown`. It does not prove a clean checkout. All three commands write path/name-free schema-1 JSON to stdout; explicitly redirect it if you want to retain a report. Quantization progress goes to stderr. Exit codes: 0 success, 2 unsupported/invalid input or destination, 5 local/container/resource failure, 130 interrupted. After interruption inspect the requested destination before retrying; no provider work started.

Inputs must be closed standalone CKPT files without SQLite sidecars. Current limits are 8 GiB/file, 20,000 tensors and 64 MiB decoded bytes/tensor. Codec and fallback behavior, numerical differences and optional native dependency limitations are documented in [experimental architecture](../architecture/lora-quantization.md). Earlier serial samples matched iOS decoded values; owner reports additionally cover one whole-file Z Image publication and identical baseline/corrected output hashes. No performance or compatibility improvement is claimed.

## Backend selection and safety

`uv run dtflow` selects a backend explicitly (`-b/--backend fake|local|cloud`); there is never a fallback from one backend to another. Results are JSON on stdout; progress and diagnostics go to stderr.

Checks and inventory reads are non-generative. Generation on local or cloud, including queue dispatch, submits real work and may consume credits. Cloud asset preparation can upload files; reconciliation contacts DT+ for metadata only. There is no automatic backend or credential fallback, and initial cloud generation does not opt into PAYG (subscription quota may still be consumed).

Local/cloud generation requires an exact model identifier from the selected server's inventory, refreshed before each run. Listing alone does not prove model or LoRA compatibility.

## Examples

```bash
uv run dtflow --help
uv run dtflow check    -b fake                                   # offline fixture, no model inference
uv run dtflow check    -b local -e 192.168.1.20:7859             # Draw Things gRPC; secret from DRAWTHINGS_SHARED_SECRET
uv run dtflow check    -b cloud                                  # DT+; key from DRAWTHINGS_API_KEY
uv run dtflow models   -b local -e HOST:PORT -k lora            # inventory with provenance; -k filters by kind
uv run dtflow generate -b fake -p "Offline fixture" -s 0
uv run dtflow generate -b cloud -m EXACT_MODEL -p "a cat" -l EXACT_LORA:0.8 -r -u cat.json
uv run dtflow generate -b cloud -c cat.json -s 7                  # reuse a config; explicit flags override it
uv run dtflow attempts                                            # inspect durable attempts; never resubmits
uv run dtflow gallery list -k 50                                  # bounded read-only saved-output page
uv run dtflow gallery list -p "portrait" -o newest                # positive-prompt search and deterministic sort
uv run dtflow gallery show OUTPUT_ID                              # inspect one output's safe recorded metadata
uv run dtflow settings                                            # show the DT+ concurrency default (set on the Connection page)
```

See [queues and batch plans](queues.md) for queue commands and JSON plans. These examples include real-backend operations: select them deliberately, not as setup checks.

## Options

Generation: `-p/--prompt`, `-n/--negative`, `-m/--model`, `-s/--seed`, `-w/--width`, `-y/--height`, `-i/--steps`, `-g/--guidance`, `-a/--sampler`, `-l/--lora ID:WEIGHT` (repeatable, explicit weight required), `-r/--prefix`, `-o/--out`, `-t/--timeout`.

Connection: `-b/--backend`, `-e/--endpoint HOST:PORT` (local), `-d/--state`, `-nc/--no-cache` (bypasses persistent disposable-cache reads and writes; also on `dtflow gui`). `dtflow gui` also accepts the gallery test limits `-ml/--load N` and `-mc/--cards N` described in the [desktop guide](desktop.md), before or after `gui` (for example `dtflow -x -ml 200 -mc 2000 gui`); other commands ignore them.

Gallery launch defaults are `-ml 20 -mc 100`: 20 records per batch/page chip and a maximum of 100 buffered cards. Both arguments accept positive integers; the card cap rounds down to whole batches, with at least one batch. Without `-mc`, the cap remains 100 before that rounding; there is no separate maximum-buffered setting. These source CLI options are not parsed by the packaged desktop launcher, which uses the application defaults.

For developer thumbnail-cache comparisons, use `dtflow gui -format jpg` (default, JPEG quality 85) or `dtflow gui -format webp` (WebP quality 80); `--format` is equivalent. This GUI-only argument goes after `gui`, is not persisted, and does not change original images or JPEG export. Alpha-channel thumbnails retain WebP even in JPG mode. The selected format has a separate cache fingerprint; switching rebuilds entries as they are viewed, with old entries removed on successful replacement. Clear thumbnails removes both formats.

Top level: `-v/--version`, `-x/--dev` (more log detail only), `-perf/--perf` (with `-x`, also write performance measurements), `-q/--logs DIR`.

Fake-only: `-f/--scenario`.

## Saved-output gallery

`dtflow gallery list` and the desktop Images tab use the same read-only query
service. Gallery commands never generate or submit provider work and do not take
the dispatch ownership lock.

`gallery list` supports `-p/--prompt`, `-n/--negative`,
`-m/--model`, `-l/--lora`, `-s/--since`, `-u/--until`,
`-r/--ratio`, `-w/--width`, `-y/--height`, `-o/--sort`,
`-k/--limit` (1–1000) and `-f/--offset`. Prompt matching is a
case-insensitive literal substring; model and LoRA matching use exact recorded
identifiers. Filters compose with AND. `since` is inclusive and `until` is
exclusive; CLI timestamps are UTC dates or offset-aware ISO values.

Sort values are `newest`, `oldest`, `model`, `size` and `loras`.
`size` means pixel area with deterministic dimension/tie ordering; multiple
LoRAs use their canonical identifier/weight sequence.

`gallery show OUTPUT_ID` returns the selected output plus bounded safe metadata.
Credential-like values are redacted and unavailable recorded fields stay
unknown rather than being reconstructed. Missing/corrupt artifacts are reported
as issues; browsing does not repair, modify or delete originals.

## Configuration

`-c/--config` reads a versioned, credential-free generation config (`dtflow.generation-config`, version 1) or a previous run's `metadata.json`; `-u/--export` writes the effective config of the current request before dispatch. Precedence is default < file < explicit flag, and the metadata records where each field came from. Seed `0` is preserved. Files containing credential-like keys or a newer version are rejected.

## LoRAs

Each selection needs an exact inventory identifier and weight.

Preflight compares the LoRA's reported architecture with the model's: a confirmed mismatch or a LoRA missing from the inventory stops the request before any attempt is recorded; unknown architecture is reported and allowed, never silently dropped. `-r/--prefix` explicitly prepends the server-supplied trigger prefixes; the prompt is never changed otherwise.

Preflight is metadata evidence only: successful generation does not prove a LoRA was applied.

The official base/refiner and Kandinsky/SD-v1 exception rules are not pinned yet, so only whole-model LoRAs on a base model are supported.

### Convert safetensors into Draw Things CKPT (source-tree utility)

The integrated converter is currently a **module command**, not a `dtflow lora convert` subcommand. It runs locally and does not upload to DT+. Pass the source family with `--family`, or omit it to have each file's family detected from its header:

```powershell
uv run python -m dtflow.services.lora_convert "C:/LoRAs/example.safetensors" "C:/Converted/example.ckpt" --family krea2
uv run python -m dtflow.services.lora_convert "C:/LoRAs/*BALL*" "C:/Converted" --family krea2 -w -a
uv run python -m dtflow.services.lora_convert "C:/LoRAs" "C:/Converted"
uv run python -m dtflow.services.lora_convert "C:/LoRAs" "C:/Reports" --metadata
```

`--metadata` (`-m`) converts nothing: it reads the descriptive header metadata of every selected file (same SOURCE forms as above: one file, a folder or a case-insensitive glob; DESTINATION is the report folder, default the current folder) and writes one JSON report, `reports/lora-metadata-v1-<timestamp>.json`, never overwriting. Per file: `status`, `fields` (title, output name, author, description, date, license, declared architecture and base model, base model file name, network type, rank, alpha, resolution, epochs, class tokens, trigger phrase, usage hint), `top_tags` (top 50 training tags with counts), `trigger_suggestion` and its `trigger_source`, and `declared_family` (an exact converter family or null). `-a/--analyze` adds the structurally detected family and whether the declared one agrees. It is best effort: a file with no metadata is reported as such, an unreadable file is a failed entry and the batch continues (exit 1 when any file failed, 2 for a single failed file). Options that only apply to conversion (`--family`, `--scale`, `-w`, `--ignore-unmatched`, `-xp`) are refused with `--metadata`. The values are what the files declare, never evidence for a family; dataset folders and training comments are not reported.

A quoted filename glob searches one directory, ignoring case and admitting only `.safetensors` files; the suffix can be omitted from the pattern. The second command stages explicit replacement of existing outputs and writes a version 2 source/output analysis report in `C:/Converted/reports/`. Without `-a`, batches write compact version 1 reports. Other explicit family choices are `zimage`, `flux1`, `sd1` and `sdxl` (SDXL is experimental and additionally requires `-xp`/`--experimental`). The third command detects each file's family; a file that cannot be identified fails with the reason (a batch continues and exits 1, a single file exits 2) and `--family` overrides detection. See [the converter guide](lora-ckpt-format.md#integrated-windows-converter-409) for supported limits. The desktop Prepare editor also accepts `.safetensors` files and converts them before upload.

## Cloud asset receipts

Use these commands only for an intentionally selected, already-converted Draw Things LoRA file:

```bash
uv run dtflow assets prepare --help
uv run dtflow assets list
uv run dtflow assets show ASSET_ID
uv run dtflow assets reconcile ASSET_ID
```

`assets prepare -f/--file FILE` explicitly prepares a converted LoRA and keeps a verified local copy under `<state>/lora_files/`. A confirmed receipt is reusable under its own DT+ API key without a routine BYOM check. First use under another key checks that key's bucket and restores only positively missing bytes; a relevant rejected generation may trigger one bounded recovery probe. Unknown or authentication failures never authorize uploads. The managed files persist until removed through Connection > Custom LoRAs or `dtflow assets files [--remove SHA256]` (removes only the retained local copy). `-n/--name` selects an identifier instead of the filename. List and Show inspect the local registry without network access; these receipts are not complete remote account inventory. Reconcile is a metadata-only check under the current credential, never an upload. Bind a confirmed receipt with `-z/--asset ASSET_ID` on cloud generation or queue creation, alongside the matching `-l/--lora ID:WEIGHT`. Binding itself never uploads; the selected key's confirmed reference is used directly after its first verification, with the above bounded restoration when needed. Receipt bindings are not exported in generation configurations. See the [cloud integration contract](../architecture/cloud-integration.md) for protocol and uncertainty details.

## Civitai downloads

```bash
uv run dtflow assets download VERSION_ID_OR_CIVITAI_LINK [--file-id FILE_ID]
```

Resolves a Civitai version id or a civitai.com model or download link to one concrete version and `.safetensors` file, downloads it and converts it to a managed Draw Things `.ckpt`, through the same service as the Downloads view. It never uploads to DT+: Prepare stays a separate explicit step. A model link without a version, or a version with several files, prints the choices instead of picking one; pass a link with `?modelVersionId=` or `--file-id`. A LoRA whose Civitai base model is not a listed supported family is refused before any bytes are fetched. A file the converter refuses is kept and reported as downloaded, not convertible. The token comes from the Download credentials card of the app when one is saved, else from the `CIVITAI_TOKEN` environment variable; the output names the source used (`saved token`, `CIVITAI_TOKEN` or `none`), never the token. Gated or early-access files need a token that has the account's permission; a token never bypasses those permissions.

## Script analysis and engine self-check

`dtflow script <file>` parses a Draw Things script (never runs it) and lists the facade calls it finds; syntax checking
uses the vendored Acorn parser. `dtflow script --check-engine [-o result.json]` runs the engine self-check, and the hidden `dtflow script --hold-child pids.json` is
the manual child-containment diagnostic (kill the printed parent pid and check the child disappears). See the
[scripting layer](../architecture/scripting.md).

### Headless script execution (Tier A)

`uv run dtflow script FILE --run -b fake|local|cloud` executes a local Draw Things `//@api-1.0` JavaScript file through the shared generation service. Unlike inspection, `--run` submits work: local Draw Things may forward requests to DT+ and cloud may consume credits. There is no implicit backend fallback. Supply an initial valid request with `-p/--prompt` or `--config` (and a model for local/cloud); the CLI validates its starting settings **before** evaluating JavaScript, even when the script supplies prompts in `pipeline.run`.

Offline owner smoke commands from the repository root (the first intentionally generates two **fake** outputs, the second intentionally fails):

```powershell
uv run dtflow script tests/fixtures/scripting/tier_a_fake.js --run -b fake -p "Offline scripting smoke test" --max-generations 4
uv run dtflow script tests/fixtures/scripting/empty_model_rejected.js --run -b fake -p "Offline model validation smoke test"
```

Execution has a per-run generation cap (`--max-generations`, default 4, valid range 1–32), per-generation `--timeout` (default 600 seconds), an optional `--answers` JSON file (a list of ordered answer lists, one per `requestFromUser` call), `--state` (default `.dtflow`), and `--outputs` (default `<state>/outputs`). Request flags `-m/--model`, `-s/--seed`, `-w/--width`, `-y/--height`, `-i/--steps`, `-g/--guidance`, `-a/--sampler`, `-n/--negative`, `-l/--lora` and `-z/--asset` provide starting settings. `--endpoint` selects the local server.

Script-set fields are currently limited to width, height, seed, steps, guidanceScale, model, sampler and LoRAs (mode `all`). Unchanged inherited values of other fields are tolerated; changed unsupported fields and masks are rejected before the affected call. Dimensions are rounded down to multiples of 64 with an upstream-compatible warning, and seed `-1` resolves to an explicit recorded seed. `createControl` is lookup only. `downloadBuiltins` never downloads. File hooks use `<state>/script_files/<run-id>/` rather than the real Pictures folder; canvas save operations use the last committed final image, without editing the recorded output. Headless `requestFromUser` uses supplied answers or defaults where possible; it is **not** a native interactive form and some answer types remain unverified. Canvas geometry, masks, image-operation dispatch and detection/guidance are separate issues #116/#117, and the desktop experience is #118.

Every run is a one-item script queue, as in Scripts and Queues (#243). With `--queue` it is saved paused for `dtflow queue start`; without it the command starts that queue itself (it must be able to own the state directory; otherwise it is left paused and the command says so) and waits for it to end. The JSON report then includes `queue_id`, `run_id`, the run status, the item state, ordered child attempt IDs, `outputs` (the latest committed attempt's output paths), `attempt_outputs` (an ordered `{attempt_id, outputs}` entry for every committed attempt) and the last 200 console messages kept with the run. The run also shows in Queues. Stop interrupts waiting, not remote generation; an unresolved outcome must not be replayed. Do not interpret a failed script as evidence that a submitted attempt was cancelled. The fixtures are for manual smoke evidence, not automatic test execution.

Immediate `--run` execution retains headless form handling even though its run is stored in a queue item. `--queue` retains interactive queued behavior when no answer file is supplied; routing through a queue does not silently turn an immediate headless command into a wait for external answers.

## Exit codes

`0` success; `2` invalid input, config, preflight or missing credentials (nothing was dispatched); `3` backend rejection or failure with a known outcome; `4` unknown remote outcome (never replayed); `5` local state, output or deadline failure; `130` local interruption. Nonzero results print one JSON error line on stderr.

## Logging and local files

One shared logging setup now writes rotating UTF-8/LF `dtflow-<session>.jsonl` files under repository `logs/` (or `%LOCALAPPDATA%\DTFlow\logs\` outside a checkout), with startup/session identity, typed fields and optional attempt correlation. `-q/--logs` overrides that directory. Admission is bounded/nonblocking and loss is explicit; a missing close seal or nonzero loss/failure counters is incomplete evidence, not zero stalls or proof of successful generation. No automatic text/gallery CSV is produced; historical files remain untouched. The shared reader, native Logs workspace and benchmark JSONL migration are implemented; full fault/native/policy acceptance remains [#107](https://github.com/djebaz/dtflow/issues/107). The Logs workspace honors the same directory override. Prompts, credentials, private paths, headers and payloads are excluded; failures never change generation or retry behavior.

State is stored in `.dtflow/attempts.sqlite3` and outputs under `.dtflow/outputs/<attempt-id>/`. Use a local filesystem and the patched SQLite runtime required by the [backend contract](../architecture/backend-contract.md#durable-state-and-layer-enforcement). See the [runtime layout](../architecture/runtime-layout.md) for ownership and cache paths.

## Offline fixtures

The fake backend returns the bundled illustrative lighthouse, cropped and resampled to the requested dimensions, for `fake-fixture`, and a calibration grid for `fake-grid`. No model inference occurs. The CLI keeps seed 0 as a real seed; the desktop resolves 0 randomly on submission.

## Experimental body pose (DT+ Cloud)

`dtflow pose` supports ControlNet on FLUX.1, SDXL and SD 1.x text-to-image checkpoints. It runs on DT+ Cloud only; there is no local or fallback backend. The desktop offers the same path as [Controlnet mode](desktop.md#controlnet-mode-pose-guidance) and shares its code (`prepare_pose`, `pose_request` and `generate_pose_once`).

| Command | Effect |
|---|---|
| `pose catalogue` | Offline: pinned detector, licence and control identifiers |
| `pose install -a/--accept` | Downloads and verifies the AGPL-3.0 YOLO26n-pose ONNX detector (about 11.6 MiB); no inference. Installs again for anyone who has the older YOLO11n-pose file |
| `pose extract -i/--image PHOTO -o/--out MAP.png [-pn/--person N]` | Local ONNX extraction only; saves the map and a matching `.pose.json`; never submits. `--person` picks one of several people (the 1-based number from the detector's list) |
| `pose generate` | Explicit DT+ submission; sends the pose map off-machine and may consume credits |
| `pose retry -c/--config METADATA.json` | New explicit DT+ submission from the saved metadata and retained map; not remote recovery, may consume credits |

`pose generate` takes `-b cloud` (required), the usual request options (`-p -n -m -s -w -y -i -g -a -l -z`), `-c/--config` (ordinary txt2img configuration), `-o/--out`, `-t/--timeout` and `-d/--state`, plus:

| Option | Meaning |
|---|---|
| `-im/--image MAP` or `-ph/--photo PHOTO` | One required: a prepared OpenPose RGB body map on a black background, or a photograph extracted locally before submission |
| `-co/--control NAME` | One pinned Union Pro control: `controlnet_union_pro_flux_1_dev_{1.0,2.0}_{q8p,q5p}.ckpt`, or the explicit Xinsir SDXL probe below |
| `-cs/--strength` | Control weight in (0, 2]. Optional for fast-model Pose (default 0.9) and official SD 1.x Pose (default 0.77); otherwise required. Not img2img denoising or text guidance |
| `-cm/--control-mode` | Draw Things control force: `Balanced` (default), `Prompt` or `Control` |
| `-st/--start`, `-en/--end` | Where in the steps the control applies, 0 to 1 (default 0 and 1); the start must be before the end |
| `-pn/--person N` | With `--photo`, the person to use when the photograph shows several |

For the existing FLUX.1 controls, `-m` must be a FLUX.1 family checkpoint (by the catalogue's family; a checkpoint the catalogue does not classify falls back to the `flux_1_` name prefix). Only FLUX.1 Dev q5p with Union Pro 1.0 q5p is verified; other FLUX.1 checkpoints are accepted for testing and are unverified. A map or photograph whose aspect differs from the target size is fitted with centred black padding, never cropped or stretched. Extraction needs exactly one body (or `--person N`) and shoulders and hips found with enough confidence (0.5); other uncertain joints are omitted and named in the extraction evidence (`omitted_joints`); the map is a body-only OpenPose skeleton with no hands, feet or detailed face. Submission is refused before anything is sent if the control is not in the DT+ inventory under its exact name. JSON goes to stdout and progress to stderr; output metadata retains the control, strength and input map so `pose retry` does not depend on the original file path.

Owner observation (single seed and prompt): with FLUX.1 Dev q5p and `controlnet_union_pro_flux_1_dev_1.0_q5p.ckpt`, strength 0.7 followed the pose very well, 0.5 did not, and 1.0 followed it but looked washed out. Union Pro 2.0 (q5p and q8p) did not follow the pose at 0.7 and is unverified. Pose adherence is never measured by dtflow; it needs owner observation.

### PowerShell smoke

```powershell
uv run dtflow pose catalogue
uv run dtflow pose install --accept
uv run dtflow pose extract -i .\photo.png -o .\tmp\pose.png
# baseline (same seed, no control), then two strengths
uv run dtflow generate -b cloud -m flux_1_dev_q5p.ckpt -p "a person in a park, full body shot, natural light" -s 401474584 -w 768 -y 1024 -i 28 -g 4.5 -o .\tmp\base
uv run dtflow pose generate -b cloud -m flux_1_dev_q5p.ckpt -p "a person in a park, full body shot, natural light" -s 401474584 -w 768 -y 1024 -i 28 -g 4.5 -im .\tmp\pose.png -co controlnet_union_pro_flux_1_dev_1.0_q5p.ckpt -cs 0.7 -o .\tmp\p07
uv run dtflow pose generate ... -cs 1.0 -o .\tmp\p10
uv run dtflow pose retry -b cloud -c <metadata.json of a pose run> -o .\tmp\retry
```

`tools/pose_smoke.py` runs the extraction, the baseline and several strengths in parallel and builds a side-by-side `sheet.png`; it submits nothing without `--submit`, which starts paid DT+ generations.

### SDXL pose feasibility probe (#632)

This uses the same SDXL family admission as the desktop and queues; quality is not guaranteed. The exact [pinned Xinsir Union ProMax metadata](https://github.com/drawthingsai/community-models/blob/ce4b2ae34a2ef7899fa014f019d04f2632dabe6f/controlnets/xinsir-union-promax-sdxl-1.0/metadata.json) is sent through the existing control override and semantic Pose hint path. The provider must advertise the control. All compatible SDXL text-to-image checkpoints are admitted; cloud availability must be checked.

After the owner confirmed SD 1.5 pose adherence with unit-range RGB, SDXL was retested with the same direct-pose normalization but returned an entirely white image. The current diagnostic sends the Xinsir pose map as a custom RGB hint in −1–1, retaining Pose as the control input override. The upstream Pose branch converts and fits custom RGB when a direct pose hint is absent. This is a diagnostic alternative, not a confirmed fix; the metadata and Union mode mapping match upstream. The official SDXL Canny custom-hint path was confirmed to produce visible guidance by the owner. Use a source photograph with `-ph` (or an extracted pose map with `-im`) and the normal CLI environment credential (`DRAWTHINGS_API_KEY`). From the repository root in PowerShell:

```powershell
uv run dtflow models -b cloud -r
$Model = "sd_xl_base_1.0_f16.ckpt" # use the quantized Base 1.0 variant instead if only that one is listed
$Control = "controlnet_xinsir_union_promax_sdxl_1.0_f16.ckpt"
$Prompt = "a person in a red coat in a park, full body shot, natural light"
# Owner-approved submission: sends the map to DT+ Cloud and may consume credits.
uv run dtflow pose generate -b cloud -m $Model -p $Prompt -s 12345 -w 1024 -y 1024 -i 25 -g 6 -a euler_a -ph .\tmp\pose.png -co $Control -cs 0.7 -o .\tmp\sdxl-pose
# Only if the controlled request works: optional second paid generation for same-seed comparison.
uv run dtflow generate -b cloud -m $Model -p $Prompt -s 12345 -w 1024 -y 1024 -i 25 -g 6 -a euler_a -o .\tmp\sdxl-baseline
```

Inspect the generated image, output metadata and the provider error if the request fails. An accepted request does not establish pose adherence; compare the controlled image against the supplied map and the uncontrolled baseline. No automatic retry, provider switch or UI integration is introduced. Do not proceed to desktop integration until owner observation establishes feasibility.

#### SDXL Turbo app-configuration comparison

The owner supplied an SDXL Turbo Xinsir configuration with 448 × 576, 24 steps, CFG 5, DPM++ 2M AYS (sampler 12), seed 444714817, weight 0.8, Balanced and control range 0–0.66. The probe retains the current custom RGB hint path (−1–1 on the wire, converted internally by Draw Things); the prior direct unit-range SDXL pose attempt returned white. The exact app preprocessing, seed mode, Clip Skip and SDXL size/negative-prompt fields are not necessarily reproduced by these commands.

```powershell
uv run dtflow pose generate -b cloud -m sd_xl_turbo_f16.ckpt -co controlnet_xinsir_union_promax_sdxl_1.0_f16.ckpt -ph .\tmp\pose.png -p $Prompt -s 444714817 -w 448 -y 576 -i 24 -g 5 -a dpmpp_2m_ays -cs 0.8 -cm Balanced -st 0 -en 0.66 -o .\tmp\sdxl-turbo-pose
uv run dtflow generate -b cloud -m sd_xl_turbo_f16.ckpt -p $Prompt -s 444714817 -w 448 -y 576 -i 24 -g 5 -a dpmpp_2m_ays -o .\tmp\sdxl-turbo-baseline
```

### Official SDXL Canny feasibility probe (#632)

This diagnostic uses the built-in `controlnet_canny_sdxl_v1.0_mid_f16.ckpt` rather than community Union metadata. It sends a Canny input override and a `custom` RGB hint in −1–1 using the existing durable generation path. The upstream Canny branch reads custom RGB when no initial image is present and converts it internally to 0–1; the previous `canny` hint was not consumed by that branch. The provider must advertise the exact control. The desktop and queue composer also offer this control for SDXL.

Use a source photo with `-ph`; OpenCV generates edges locally at the fitted target resolution (thresholds 100/200). OpenCV (`opencv-python-headless`) is a project dependency since #635. Alternatively use `-im` with an already prepared edge map.

```powershell
uv run dtflow models -b cloud -r
uv run dtflow pose generate -b cloud -m sd_xl_base_1.0_f16.ckpt -co controlnet_canny_sdxl_v1.0_mid_f16.ckpt -ph .\tmp\pose.png -p "a person in a red coat in a park, full body shot" -s 12345 -w 1024 -y 1024 -i 25 -g 6 -a euler_a -cs 0.7 -o .\tmp\sdxl-canny
```

This sends one real request and may consume credits. Compare the result to the existing baseline and the retained edge map. The Canny probe does not establish pose support or satisfy AC-1 by itself. No automatic retries or provider substitutions are added.

### Official SD 1.5 Pose feasibility probe

New CLI requests for catalogue SD 1.x text-to-image checkpoints with the official SD v1.x Pose checkpoint default to weight 0.77, Balanced, start 0 and end 1, matching the latest successful owner app export (384×512, seed 1819044058). Previous owner exports used weight 1; the later 640×960 CLI result was poor. Weight, resolution and seed varied, so causality is not isolated. Explicit overrides win; Retry retains the original settings. Recommended generation settings are 16 steps, CFG 5 and DPM++ 2M AYS, not automatic generation overrides. Other SD 1.x checkpoints are admitted for experimentation, not verified by this evidence.

The official [Draw Things ControlNetZoo specification](https://github.com/drawthingsai/draw-things-community/blob/cb2d4c61d1b58816ce1843481e74293a240e5bf4/Libraries/ModelZoo/Sources/ControlNetZoo.swift) identifies `controlnet_openpose_1.x_v1.1_f16.ckpt` as Pose (SD v1.x, ControlNet 1.1), modifier Pose, version v1, regular ControlNet. The CLI probe accepts catalogue SD 1.x text-to-image checkpoints. It leaves the input override Unspecified (the app exports an empty string) and sends the existing semantic `pose` float tensor hint, without community metadata. The official control declares its native Pose modifier. The owner reported good pose results in the official app and subsequently confirmed a normal image with visible pose adherence in DTFlow after the unit-range normalization change. Neither the production desktop nor queue composer is extended.

The owner supplied two equivalent successful app configuration exports: 640 × 1024, 16 steps, guidance 5, sampler 12 (DPM++ 2M AYS), seed 1580038928, Clip Skip 1, seed mode 2, strength 1, control weight 1, Balanced, range 0–1, no LoRAs. The commands match exposed settings; exact equivalence of the app's seed mode, preprocessing and remaining defaults is not established.

The owner reported noise from the initial DTFlow SD 1.5 probe. This revision changes only the SD 1.5 pose hint RGB normalization to 0–1, preserving float16 NHWC and the tensor reference. The [upstream generator](https://github.com/drawthingsai/draw-things-community/blob/cb2d4c61d1b58816ce1843481e74293a240e5bf4/Libraries/LocalImageGenerator/Sources/LocalImageGenerator.swift#L2720-L2985) converts custom RGB to 0–1 for SD 1.5 but passes a direct pose hint through unchanged. The owner confirmed that the normalization change removed the noise and followed the pose. FLUX.1 and ordinary image operations retain their existing encoding; the SDXL Xinsir probe now uses the upstream custom RGB conversion path instead.

From the repository root in PowerShell, check the inventory before submitting:

```powershell
uv run dtflow models -b cloud -r
$Model = "sd_v1.5_f16.ckpt"
$Control = "controlnet_openpose_1.x_v1.1_f16.ckpt"
$Prompt = "a person in a red coat in a park, full body shot"
# Source photo: local ONNX extraction; only the prepared pose map is submitted.
uv run dtflow pose generate -b cloud -m $Model -co $Control -ph .\tmp\pose.png -p $Prompt -s 1580038928 -w 640 -y 1024 -i 16 -g 5 -a dpmpp_2m_ays -cs 1.0 -cm Balanced -st 0 -en 1 -o .\tmp\sd15-pose
# Same seed/settings, without control, for visual comparison.
uv run dtflow generate -b cloud -m $Model -p $Prompt -s 1580038928 -w 640 -y 1024 -i 16 -g 5 -a dpmpp_2m_ays -o .\tmp\sd15-baseline
```

Use `-im` instead of `-ph` for an already extracted pose map. Each generation is an explicit cloud submission and may consume credits. Compare the actual pose against the retained map and baseline; a final image alone is not evidence of conditioning. No automatic retry or provider substitution is added. This separate diagnostic does not satisfy the SDXL acceptance criteria of #632.

### Additional SD 1.5 and SDXL Turbo spatial control probes

The CLI admits more combinations than the desktop, which offers only the owner-observed ones (#635). The official SD 1.5 Canny, Depth and Segmentation checkpoints and official SDXL Depth/Canny checkpoints are admitted; Xinsir Union also accepts explicit `-ct depth`, `-ct seg` or `-ct canny` with SDXL Turbo or the previously admitted Base checkpoints. Official controls infer their native input; do not select a mismatching mode. Depth uses a single-channel float16 hint in −1–1, which the upstream generator converts to RGB 0–1. Segmentation and Canny use custom RGB in −1–1. Segmentation accepts a prepared model-compatible semantic palette map or a photo using the existing configured Inpaint YOLO segmentation model. The photo path maps a small subset of overlapping COCO object classes to ADE20K RGB colours; all unrecognized pixels remain black. This is sparse instance-derived conditioning, not full-scene semantic segmentation. Palette colours are preserved with nearest-neighbour resizing. No new model is automatically downloaded. Add `-sm clothes` to a segmentation photo command to reuse the configured SegFormer clothes model: apparel and bag receive their ADE20K colours, skin and hair remain person, and unknown background remains black. The default `-sm yolo` retains the original silhouette mode. The existing research-only model licensing applies; this is not dense scene parsing.

```powershell
$Prompt = "a girl in a bikini at the beach"
$Model = "sd_xl_turbo_f16.ckpt"
$Union = "controlnet_xinsir_union_promax_sdxl_1.0_f16.ckpt"
# Verified Turbo pose settings (owner attempt 7080ba2002994f53a4510dca95da06ca).
uv run dtflow pose generate -b cloud -m $Model -co $Union -ph .\tmp\pose.png -p $Prompt -s 1234455 -w 640 -y 960 -i 8 -g 0.7 -a euler_a -cs 1 -o .\tmp\turbo-pose
# Prepared grayscale relative inverse depth (near = bright).
uv run dtflow pose generate -b cloud -m $Model -co controlnet_depth_sdxl_v1.0_mid_f16.ckpt -im .\tmp\depth.png -p $Prompt -s 1234455 -w 640 -y 960 -i 8 -g 0.7 -a euler_a -cs 1 -o .\tmp\turbo-depth
# Or extract locally from a photo using the pinned Depth Anything V2 Small ONNX already used by tools/images.
uv run dtflow pose generate -b cloud -m $Model -co $Union -ct depth -ph .\tmp\pose.png -dm .\tmp\models\depth-anything-v2-small.onnx -p $Prompt -s 1234455 -w 640 -y 960 -i 8 -g 0.7 -a euler_a -cs 1 -o .\tmp\turbo-union-depth
uv run dtflow pose generate -b cloud -m $Model -co $Union -ct canny -ph .\tmp\pose.png -p $Prompt -s 1234455 -w 640 -y 960 -i 8 -g 0.7 -a euler_a -cs 1 -o .\tmp\turbo-union-canny
uv run dtflow pose generate -b cloud -m $Model -co $Union -ct seg -ph .\tmp\pose.png -p $Prompt -s 1234455 -w 640 -y 960 -i 8 -g 0.7 -a euler_a -cs 1 -o .\tmp\turbo-union-seg
uv run dtflow pose generate -b cloud -m $Model -co $Union -ct seg -sm clothes -ph .\tmp\pose.png -p $Prompt -s 1234455 -w 640 -y 960 -i 8 -g 0.7 -a euler_a -cs 1 -o .\tmp\turbo-union-seg-clothes
uv run dtflow pose generate -b cloud -m sd_v1.5_f16.ckpt -co controlnet_seg_1.x_v1.1_f16.ckpt -ph .\tmp\pose.png -p $Prompt -s 722970881 -w 640 -y 1024 -i 16 -g 5 -a dpmpp_2m_ays -cs 1 -o .\tmp\sd15-seg
```

For official SD 1.5 Depth/Canny, use `controlnet_depth_1.x_v1.1_f16.ckpt` or `controlnet_canny_1.x_v1.1_f16.ckpt` with the SD 1.5 model and settings above. For official Turbo Canny use `controlnet_canny_sdxl_v1.0_mid_f16.ckpt`. The pinned depth model (https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/4472b7362082ad9968fee890ca0f1e5aca36b93d/onnx/model.onnx, SHA-256 `afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c`) is installed by the Download button in desktop Controlnet → Depth into `<state>/models/person/`, which a depth photo uses by default; `-dm` names another copy explicitly. There is no fallback to `tmp/models`. The owner has also visually confirmed Turbo Xinsir Canny, Depth and both sparse and clothes-refined Segmentation. The official SD 1.5 non-pose combinations remain untested. The owner confirmed SDXL Turbo Xinsir Pose, SD 1.5 Pose, and SDXL Base official Canny; SDXL Base Xinsir Pose returned noise even with the successful Turbo settings. Compare against an uncontrolled same-seed baseline before claiming acceptance.

### Realistic Vision SD 1.x comparison

The owner preferred Realistic Vision V5.1 Q6P/Q8P in an uncontrolled 960×960 generation with HiRes Fix (512×512 first pass, strength 0.7), 16 steps, CFG 5 and DPM++ 2M AYS (attempt `a82df8968b4d408689cbadc06fd902f9`, 22.30 s). The catalogue identifies it as SD 1.x text-to-image and its published description is version v1. All four official SD 1.x controls now admit catalogue SD 1.x text-to-image checkpoints; inpaint-only and other families remain excluded. Retest without HiRes Fix first; quality and conditioning are not established for Realistic Vision. The owner reported good SD 1.5 Canny, Depth and clothes segmentation at weight 1, Balanced, range 0–1.

```powershell
$Model = "realistic_vision_v5.1_q6p_q8p.ckpt"
$Prompt = "Lara is a beautiful woman in a park at night"
$Photo = ".\tmp\pose.png"
$Out = ".\tmp\rv-controls"
foreach ($Type in @("pose", "canny", "depth", "seg")) {
    $Control = switch ($Type) {
        "pose" { "controlnet_openpose_1.x_v1.1_f16.ckpt" }
        "canny" { "controlnet_canny_1.x_v1.1_f16.ckpt" }
        "depth" { "controlnet_depth_1.x_v1.1_f16.ckpt" }
        "seg" { "controlnet_seg_1.x_v1.1_f16.ckpt" }
    }
    $Extra = switch ($Type) {
        "depth" { "-dm"; ".\tmp\models\depth-anything-v2-small.onnx" }
        "seg" { "-sm"; "clothes" }
    }
    $Weight = if ($Type -eq "pose") { 0.77 } else { 1 }
    uv run dtflow pose generate -b cloud -m $Model -co $Control -ct $Type -ph $Photo -p $Prompt -s 1819044058 -w 384 -y 512 -i 16 -g 5 -a dpmpp_2m_ays -cs $Weight -o $Out @Extra
}
```

### Official SD 1.x Pose HiRes experiment

The owner observed good control adherence on Realistic Vision but weak faces at 384×512. The CLI now accepts `-hf/--hires-factor` and `-hs/--hires-strength` for all admitted ControlNet modes and checkpoints. These reuse existing advanced settings and retained metadata; The first two direct-Pose HiRes attempts (8d56a2fee49e4d85870ad89c21c35f91 and 41bdeb64a94b477095edf4ad29ace760) reached DT+ but ended with no_final_output and unknown remote outcomes. The pinned upstream generator passes hints into both sampling passes; direct Pose forwards the tensor as-is, whereas customRGB(true) resizes to the current pass and converts signed RGB to unit RGB (LocalImageGenerator.swift around 2739–2777 and 2948–2974). All HiRes-active Pose controls send the retained final-size map as a custom RGB hint in [-1,1], retaining the native modifier or explicit Pose override. Ordinary SD 1.x Pose remains a direct unit-range hint. The owner confirmed SD 1.x Pose HiRes success with this custom path. FLUX Pose + HiRes remains untested, and Depth + HiRes remains untested with its unchanged direct one-channel hint. All admitted image modes share advanced settings, including desktop Controlnet; availability does not establish provider compatibility. Compare final 768×1024 with factor 2 (384×512 first pass), strength 0.5, 0.7 and 0.85. Each invocation explicitly submits one cloud request; no automatic fallback.

### Fast-model Pose defaults (owner comparison)

New CLI Pose requests using SDXL Turbo + Xinsir, or FLUX.1 Schnell / PixelWave Schnell 04 + Union Pro 2.0, default to weight 0.9, Balanced, guidance start 0 and end 0.65. Explicit flags always win; official SD 1.x Pose instead defaults to weight 0.77 and end 1; other combinations retain their explicit-weight requirement and default end 1. Saved requests and Retry retain their recorded values. The desktop uses its own suggested settings for new selections.

The owner compared eight-step, 640×960, seed 1234455 generations with Turbo (CFG 0.7, Euler A) and official Schnell Q4P (CFG 1, Euler A Trailing). Weight 0.7 / Balanced / end 1 gave poor adherence; weight 1 / Balanced / end 1 and weight 0.9 / Balanced / end 0.65 gave very good adherence; weight 1 / Control / end 1 gave poor adherence. The final Schnell 0.9 result (attempt `6e7818d8886547c590402b126f304b1d`) completed in 30.90 s without warnings. PixelWave shares the proposed Schnell defaults but has not been validated at those weights. No detector or wire changes were needed. Single-seed visual evidence, not a controlled baseline or broad compatibility guarantee.

```powershell
uv run dtflow pose generate -b cloud -m flux_1_schnell_q4p.ckpt -co controlnet_union_pro_flux_1_dev_2.0_q5p.ckpt -ph .\tmp\pose.png -p "an adult woman in a bikini at the beach" -s 1234455 -w 640 -y 960 -i 8 -g 1 -a euler_a_trailing -o .\tmp\pose-fast
```

### FLUX.1 Union spatial control probes

The existing FLUX.1 Union Pro 1.0 and 2.0 controls now accept `-ct canny`, `-ct depth` and `-ct seg` through the CLI. Existing Pose remains the default. Upstream FLUX.1 Union 1.0 maps Canny to mode 0, Depth to 2 and Segmentation to 1 (the shared tile/custom mode, not a dedicated segmentation mode); Union 2.0 is a regular ControlNet without the mode embedding. Segmentation is therefore exploratory, not established checkpoint training compatibility. The existing signed RGB custom hint and signed single-channel depth hint match the upstream FLUX.1 paths, which do not apply SDXL's RGB range conversion. The desktop offers all implemented input types for compatible model families, including exploratory Segmentation; see the desktop guide for quality observations.

```powershell
$Model = "flux_1_dev_q5p.ckpt"
$Union = "controlnet_union_pro_flux_1_dev_1.0_q5p.ckpt"
$Prompt = "a girl in a bikini at the beach"
uv run dtflow pose generate -b cloud -m $Model -co $Union -ct canny -ph .\tmp\pose.png -p $Prompt -s 1234455 -w 640 -y 960 -i 24 -g 3.5 -a euler_a -cs 0.7 -o .\tmp\flux-canny
uv run dtflow pose generate -b cloud -m $Model -co $Union -ct depth -ph .\tmp\pose.png -dm .\tmp\models\depth-anything-v2-small.onnx -p $Prompt -s 1234455 -w 640 -y 960 -i 24 -g 3.5 -a euler_a -cs 0.7 -o .\tmp\flux-depth
uv run dtflow pose generate -b cloud -m $Model -co $Union -ct seg -sm clothes -ph .\tmp\pose.png -p $Prompt -s 1234455 -w 640 -y 960 -i 24 -g 3.5 -a euler_a -cs 0.7 -o .\tmp\flux-seg
```

For fast FLUX.1 Canny and Depth, the owner observed good results with `flux_1_schnell_q4p.ckpt` and `pixelwave_flux_1_schnell_04_q8p.ckpt`, Union Pro 2.0 q5p, 8 steps, CFG 1, Euler A Trailing, weight 0.7. Pose and Segmentation did not preserve the reference geometry in that batch; they remain experimental on Schnell. Turbo Pose previously worked at weight 1, not at 0.7 in the later batch. PixelWave now inherits the Schnell-specific profile instead of the Dev family default; this changes implicit guidance embed and resolution-dependent shift, and awaits retest. The explicit CFG 1 override is retained for these experiments. Photo Canny extracts edges within the fitted photograph before pasting onto a black canvas: owner-observed streaks from the previous boundary replication motivated this correction. Depth still extends boundary values; aspect ratio is preserved without cropping. Canny retest pending. Segmentation palette corrections affect five non-person COCO categories; the supplied single-person maps are unaffected. These settings are starting points, not verified post-fix configurations. Use `-sm yolo` for a silhouette-only map or `-im` for a prepared semantic map. Each command is an explicit cloud submission. The official SD 1.5 Canny, Depth and Segmentation commands in the preceding section are also available; neither the new FLUX.1 nor SD 1.5 combinations have been executed by the agent.

## Single-shot image-operation owner probe

`edit` is the explicit acceptance harness for PR #64, not general image-operation support. Without `-r/--probe` it refuses before backend selection, file preparation or authentication. Each invocation requests one real generation, with no queue, plan, batch or retry. Generic `generate`, configuration reuse and `queue add` remain txt2img-only.

```powershell
# Examples only: these submit real work when explicitly run by the owner.
uv run dtflow edit -b local -e HOST:PORT -m EXACT_MODEL -p 'edit prompt' --source source.png --evidence local-img2img-01 --probe
uv run dtflow edit -b cloud -m EXACT_MODEL -p 'edit prompt' --source source.png --mask mask.png --evidence cloud-inpaint-01 --probe
```

The command prints an off-machine/possible-cloud-credit warning on stderr. It builds an `owner_probe` permit scoped to the selected backend, endpoint, exact checkpoint and operation; the user-chosen evidence ID is not proof of acceptance. Cloud additionally requires the bundled exact published model description digest: missing descriptions refuse, with no family-derived fallback or user-supplied digest override. Inventory availability is checked independently by the shared session before dispatch.

| Canonical options | Meaning |
| --- | --- |
| `-src/--source` | Required source image (original unchanged) |
| `-mk/--mask` | Optional same-size EXIF-oriented mask; white editable, black preserved; chooses inpaint instead of img2img |
| `-st/--strength` | 0..1, default 0.75; checkpoint behavior still needs acceptance |
| `-gr/--grow` | Signed grow/shrink in source pixels, default 0; source-size work budget applies |
| `-f/--feather` | Inward feather in source pixels, default 0 |
| `-k/--keep on\|off` | Default on: preserve normalized prepared RGB pixels outside the mask, not original-file pixels; off returns generation-size raw output copy |
| `-u/--evidence` | Required path-free evidence ID, 1..128 ASCII letters/digits/`.`/`_`/`-` |
| `-r/--probe` | Explicit consent for this one run; never promotes evidence to owner-confirmed |
| `-o/--out` | Output root; default `<state>/outputs` |
| `-t/--timeout` | Backend deadline in seconds, default 600 |

The usual `-b/--backend` (local or cloud only), `-e/--endpoint`, `-d/--state`, `-p/--prompt`, `-n/--negative`, `-m/--model`, `-s/--seed`, `-w/--width`, `-y/--height`, `-i/--steps`, `-g/--guidance` and `-a/--sampler` build the ordinary single-output txt2img base request. Target size is explicit/default request size, not derived from the source. Multi-letter image aliases avoid collisions with these established spellings. No config, LoRA, asset, prefix, batch or fake-scenario options are exposed by this bounded harness.

Successful JSON contains `attempt_id`, `backend`, `outputs` (registered final paths), `metadata`, `permit_status` and `evidence_id` read from the effective request, and warnings. Metadata retains request permit evidence, prepared inputs/fit, raw output evidence and normalization policy. Inspect unknown outcomes; never automatically replay. No local/cloud image-operation acceptance is claimed by this command's presence or offline tests.

### Local-to-DT+ bridge probes

When the selected local Draw Things endpoint forwards generation to DT+, add `-c/--bridge` to `edit`. This is an explicit user assertion of bridge routing, not automatic detection. It sets the existing `allow_unlisted_model` request field and records `image_bridge=local_to_dtplus` resolution evidence, allowing an exact checkpoint not advertised in the bridge's local inventory. `--probe` remains required; `--bridge` with `-b cloud` refuses. Without this flag, local inventory gating is unchanged. The permit remains scoped to the local endpoint and `owner_probe`; the direct-cloud description gate is unchanged. Remote acceptance is not established by this opt-in. Bridge generation may consume DT+ credits and forward source/mask beyond the local machine.

For the owner's Fill command, keep all parameters and add `--bridge` alongside `-b local`. This changes only the explicit inventory opt-in; sampler/tensor/checkpoint handling still needs owner live acceptance.


### Parallel SD 1.x control checkpoint comparison

Use `pose generate -ms MODEL MODEL ...` instead of `-m` to compare official SD 1.x controls with one shared fitted map and fixed seed. This creates and starts one durable generation queue through the existing queue dispatcher. Concurrency follows the saved DT+ Connection setting (client cap 10, not a claimed provider limit). Queue items retain ordinary status, attempts, bounded retry policy and metadata; no new scheduler, process-level state locks or automatic retry policy. The CLI prints the queue ID before dispatch and writes a labelled `comparison-<queue-id>.jpg` directly under `--out` (or `STATE/comparisons` without `--out`) after settlement, with placeholders for missing results. Originals are unchanged. Existing queue Start/Resume/Retry are available; a resumed queue does not automatically rebuild the collage. Explicit asset receipts are not supported for this comparison. The owner observed seven independent CLI processes failing during session opening while one completed; exact exceptions were not supplied. Eight sequential SD 1.x Pose HiRes runs succeeded. The owner’s parallel comparison succeeded 8/8 on Windows with DT+ Cloud, producing the labelled collage; the subsequent memory optimization still needs retesting.


### Cloud ControlNet admission and timing

The owner’s eight-model Pose + HiRes comparison succeeded, but the normal diagnostic log (session 82cfca4ed33e) shows eight prepared attempts at 18:26:52–54 UTC, the first four request-sent events at 18:26:57–18:27:00 and the next four at 18:27:16–24, with concurrency set to ten. The previous image-edit reservation was about 131 MiB plus retained PNG bytes per 768×1024 control request against a 640 MiB queue allowance, admitting only four at a time. The shared generation service now reserves one output plus retained guidance PNGs and the existing pixel/tensor allowance (about 62 MiB at this size), without changing the budget or edit/composite allowances. This is an accounting estimate, not measured RSS. Ordinary single-pass txt2img, local image-operation reservations, video admission, ownership, persistence and retries are unchanged. Cloud HiRes requests also retain their provider slot until the provider stream closes (second-pass progress does not expose a reliable completion count), rather than releasing it at the end of the first pass. A DEBUG generation.buffer_acquired record exposes reservation, used/limit bytes and wait duration; existing -time timelines also show buffer wait and subsequent preparation. Provider-side scheduling after submission remains unmeasured. Repeat the owner comparison with -time and development diagnostics before asserting a speedup or peak-memory bound.

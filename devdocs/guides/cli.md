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

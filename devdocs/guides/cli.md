# CLI reference

The CLI is DTFlow's automation and development interface. For the desktop application, see the [desktop guide](desktop.md).

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
uv run dtflow settings -j 2                                       # persist the cloud concurrency maximum (1-4, default 4)
```

See [queues and batch plans](queues.md) for queue commands and JSON plans. These examples include real-backend operations: select them deliberately, not as setup checks.

## Options

Generation: `-p/--prompt`, `-n/--negative`, `-m/--model`, `-s/--seed`, `-w/--width`, `-y/--height`, `-i/--steps`, `-g/--guidance`, `-a/--sampler`, `-l/--lora ID:WEIGHT` (repeatable, explicit weight required), `-r/--prefix`, `-o/--out`, `-t/--timeout`.

Connection: `-b/--backend`, `-e/--endpoint HOST:PORT` (local), `-d/--state`, `-nc/--no-cache` (bypasses persistent disposable-cache reads and writes; also on `dtflow gui`). `dtflow gui` also accepts the gallery test limits `-ml/--load N` and `-mc/--cards N` described in the [desktop guide](desktop.md).

Top level: `-v/--version`, `-x/--dev` (more log detail only), `-q/--logs DIR`.

Fake-only: `-f/--scenario`.

## Saved-output gallery

`dtflow gallery list` and the desktop Images tab use the same read-only query
service. Gallery commands never generate or submit provider work and do not take
the dispatch ownership lock.

`gallery list` supports `-p/--prompt`, `-n/--negative`,
`-m/--model`, `-l/--lora`, `-s/--since`, `-u/--until`,
`-r/--ratio`, `-w/--width`, `-y/--height`, `-o/--sort`,
`-k/--limit` (1–200) and `-f/--offset`. Prompt matching is a
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

## Cloud asset receipts

Use these commands only for an intentionally selected, already-converted Draw Things LoRA file:

```bash
uv run dtflow assets prepare --help
uv run dtflow assets list
uv run dtflow assets show ASSET_ID
uv run dtflow assets reconcile ASSET_ID
```

Only `assets prepare -f/--file FILE` may upload bytes. `-n/--name` selects an identifier instead of the filename. List and Show inspect the local registry without network access; these receipts are not complete remote account inventory. Reconcile is a metadata-only check under the current credential, never an upload. Bind a confirmed receipt with `-z/--asset ASSET_ID` on cloud generation or queue creation, alongside the matching `-l/--lora ID:WEIGHT`. Binding never uploads; the reference is revalidated before dispatch. Receipt bindings are not exported in generation configurations. See the [cloud integration contract](../architecture/cloud-integration.md) for protocol and uncertainty details.

## Exit codes

`0` success; `2` invalid input, config, preflight or missing credentials (nothing was dispatched); `3` backend rejection or failure with a known outcome; `4` unknown remote outcome (never replayed); `5` local state, output or deadline failure; `130` local interruption. Nonzero results print one JSON error line on stderr.

## Logging and local files

One shared logging setup writes rotating UTF-8 `dtflow-<session>.log` files under repository `logs/` (or `%LOCALAPPDATA%\DTFlow\logs\` outside a checkout), with session/attempt correlation and bounded retention. Prompts, credentials, headers and payloads are not logged; a log failure is reported once and never changes the command.

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

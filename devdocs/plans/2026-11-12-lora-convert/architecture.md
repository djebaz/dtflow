# Architecture plan: pure-Python LoRA converter inside `src/dtflow/` (#357, #354)

Plan only. No code, no PR. Written against djebaz/dtflow main `23f1254`. Reads: `options.md`, `ranking.md`, `tools/drawthings/lora_to_ckpt.py` (1210 lines), `devdocs/guides/lora-ckpt-format.md`, `services/lora_store.py`, `services/lora_family.py`, `services/lora_naming.py`, `ui/lora_panel.py`, `ui/prepare_lora_editor.py`, `ui/runner.py`, `pyproject.toml`, `tools/packaging/build_exe.py`, AGENTS.md. Static reading only; nothing was executed.

## 1. Facts that shape the design

- The converter is one file, `tools/drawthings/lora_to_ckpt.py`, plus `lora_mappings.json` (215 KB, SD 1.x / SDXL tables extracted from Swift by `extract_swift_mappings.py`). It depends only on `numpy` and the standard library. `ConversionError` is the single error type. `convert(source, destination, family, scale, log, ignore_unmatched, allow_experimental)` is the single entry. Progress is only a `log(str)` callback. There is no cancel.
- `pyproject.toml` packages only `src/dtflow`. `tools/` is a source-only namespace, so the converter is not importable from the Windows build. Moving it is a hard prerequisite for every UI option (ranking.md, verified).
- JSON resources already live under `src/dtflow/resources/` and are read with `Path(__file__).parents[1] / "resources" / ...` (`core/compute_units.py`, `core/model_profiles.py`). PyInstaller already runs with `--collect-data dtflow`, so a JSON file under the package ships without build changes.
- Layering today: `core/` is backend-neutral and dependency-neutral, `services/` is headless (Qt-free), `ui/` owns Qt, `ui/runner.AsyncRunner` runs coroutines on one owned event-loop thread and returns results on the GUI thread, services use `asyncio.to_thread` for blocking work (`_inspect_prepare_files` already does this for `detect_lora_family`), and cooperative cancel is a `threading.Event` (`services/image_export.py`). numpy is already a runtime dependency (`numpy==2.3.5`).
- Prepare flow: `PrepareBatchEditor` (UI rows, readiness) → `LoraPanel._authorize_prepare` → `_prepare_next` → `session.prepare_asset(state, path, provenance, saved, metadata)` one file at a time under page-busy ownership. `LoraStore` retains converted `.ckpt` copies by sha256+size and records `origins` per name (path, size, mtime, edge, prepared_at). `LoraPrepareMetadata` carries short name, exact architecture, format (F16/Q8P), trigger words, declared version.
- Policy: Windows only; no new tests and no local test runs (CI runs them); ruff format and lint (`TID251` bans plain `QFileDialog`); docs after smoke; hard rule "do not assume LoRA compatibility without evidence".

## 2. Goals and non-goals

Goals: one importable, Qt-free converter service; one family-explicit call; cooperative cancel and progress; typed result with provenance; families plug in through a registry; SDXL stays experimental behind an explicit flag; the CLI keeps working; GUI (PR 2) and run-time restore (#326/#327, later) call the same function.

Non-goals: detector for safetensors (separate PR, only a hook now), Colab replacement for unsupported families, Q8P conversion, non-cloud backends, any new tests.

## 3. Package layout

A package, not one module, because the file already has six separable concerns and families will grow. Name: `src/dtflow/services/lora_convert/`.

```
src/dtflow/services/lora_convert/
  __init__.py        public API only: convert_lora, ConversionRequest, ConversionResult,
                     ConversionError (+ subclasses), FamilySpec, supported_families, CONVERTER_VERSION
  api.py             convert_lora(), request/result dataclasses, cancel + progress plumbing, atomic output
  errors.py          error hierarchy + user message / hint fields
  provenance.py      CONVERTER_VERSION, DT_REVISION, result provenance record (JSON-safe)
  safetensors_io.py  SafeTensors reader (today lines 73-150), tensor dtype helpers
  keys.py            split_key, LoraKey, role suffix and prefix tables (today 159-215)
  numeric.py         f32_to_f16_bits, to_f16, scale_up, scale_down, fused splitting helpers (449-578)
  ckpt_writer.py     write_ckpt, _encode_dims, DATATYPE/FORMAT constants (580-632)
  registry.py        FamilySpec, register(), lookup(), supported_families(); no family imports at top level
  families/
    __init__.py      imports each family module once so each registers itself (explicit list, no scan)
    krea2.py         resolve_krea2 + FamilySpec
    zimage.py        resolve_zimage + FamilySpec
    flux1.py         resolve_flux1 + FamilySpec
    tables.py        shared engine for table-driven families (convert_tables and helpers, today 635-1060)
    sd1.py           FamilySpec using tables.py, mappings key "sd1_unet"
    sdxl.py          FamilySpec using tables.py, experimental=True
  data/
    lora_mappings.json   moved from tools/drawthings (read with Path(__file__).parent / "data")
    README.md            how it is regenerated (extract_swift_mappings.py + pinned revision)
```

Notes:
- Data location: next to the code in `lora_convert/data/` rather than `resources/`, because it is generated for this module only and must move with it. `--collect-data dtflow` and hatch both include non-Python files inside the package, so no packaging change. The loader keeps the `functools.cache` pattern.
- `numpy` is imported only in `numeric.py`, `families/*`, `ckpt_writer.py`, `safetensors_io.py`. `__init__.py` must not import numpy at import time of `dtflow.services` (keeps startup lean, in line with the startup work in the repo): `convert_lora` imports `api` lazily, and `supported_families()` reads only the registry metadata. Concretely, family modules declare `FamilySpec` metadata (label, status) at import time and import numpy inside their resolver module-level code only when first used; if that proves awkward, accept the numpy import, since the GUI already loads it through Pillow/onnx paths. Decide in PR 1 by measuring import time statically with `python -X importtime` (not a test).
- This is a mostly mechanical split of one file. Function bodies and table values move unchanged in PR 1 so the verified output (0 differing tensors against the Swift references) cannot change. PR 1 must not "improve" arithmetic.

## 4. Public API

All in `dtflow.services.lora_convert`. Synchronous and blocking by design; callers decide the thread. That is what the existing services do (`detect_lora_family`, `LoraStore`), and it serves the GUI (`asyncio.to_thread`), the CLI (direct) and the run-time restore (already async, `to_thread`).

```python
@dataclass(frozen=True, slots=True)
class ConversionRequest:
    source: Path  # .safetensors
    destination: Path  # .ckpt; must not exist (never overwrite, same rule as the CLI today)
    family: str  # converter family key: "krea2" | "zimage" | "flux1" | "sd1" | "sdxl"
    scale: float = 1.0
    allow_experimental: bool = False
    ignore_unmatched: bool = False  # table families only, as today


@dataclass(frozen=True, slots=True)
class ConversionProgress:
    stage: str  # "reading" | "converting" | "writing" | "finishing"
    done: int  # tensors (modules) processed
    total: int  # known after key scan; 0 before
    message: str = ""  # human line for the log, same text the old log() printed


@dataclass(frozen=True, slots=True)
class ConversionResult:
    destination: Path
    family: str  # converter key
    exact_architecture: str  # Draw Things version string, e.g. "krea_2", "z_image", "flux1", "v1", "sdxl_base_v0.9"
    tensors_written: int
    destination_sha256: str  # computed while writing (no second pass) so LoraStore can skip a rehash
    destination_size: int
    source_sha256: str | None  # None unless request.hash_source (see below)
    source_size: int
    experimental: bool
    warnings: tuple[str, ...]  # e.g. skipped unmatched modules, experimental notice
    provenance: ConversionProvenance


def convert_lora(
    request: ConversionRequest,
    *,
    progress: Callable[[ConversionProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> ConversionResult: ...
```

Design decisions:
- **Family explicit.** `family` is required, no auto mode in this API. Detection is a separate function (section 7) whose output is fed to the user, never silently into `convert_lora`.
- **Result of exact architecture.** The registry maps each converter key to the Draw Things exact architecture string used by `LoraPrepareMetadata.exact_architecture` and `detect_lora_family` (`krea_2`, `z_image`, `flux1`, `v1`, `sdxl_base_v0.9`). That lets the Prepare editor pre-fill the architecture dropdown with no second mapping. Assumption to verify in PR 1: those five strings are exactly the ones `architecture_shortcode` accepts.
- **Progress.** The old `log(str)` becomes structured `ConversionProgress`; the CLI wrapper prints `message`. Callbacks run on the converting thread. The GUI wrapper marshals to the GUI thread through the existing runner/signal path; the API itself stays Qt-free. Calls are throttled by the converter (at most ~10 per second and always at stage changes) so a signal flood is impossible.
- **Cancel.** Cooperative `threading.Event` checked between modules and between tensor writes (same style as `image_export`). On cancel: stop, delete the partial output, raise `ConversionCancelled`. Cancel never leaves a half-written `.ckpt` at the final name.
- **Atomic output.** Write to `<destination>.part` in the destination folder (same volume), `fsync`, `os.replace` to the final name, refusing if the final name exists (checked before and again at replace, as `LoraStore.retain` does). Removes the "half converted file at final name" risk the Swift importer has, and matters because Windows keeps handles open: the reader already closes the source in a context manager, keep that.
- **Threading contract (documented in the module docstring).** Thread-safe for independent requests; a single request is not re-entrant; no global mutable state other than the `functools.cache` of the mapping JSON.
- **Memory.** Unchanged for now: tensors are read one at a time, outputs are accumulated in a dict then written. Peak memory is the whole converted LoRA (218 MB for Krea 2). Acceptable on the target machines; a streaming writer is a later optimisation, not in scope, and the API does not prevent it.
- **Source hash.** The source sha256 is needed for provenance and for Option 8 / #327. Hashing a multi-hundred MB file costs a second pass. Make it part of the request (`hash_source: bool = True`) so the CLI can opt out; the GUI wants it.

### Provenance

```python
@dataclass(frozen=True, slots=True)
class ConversionProvenance:
    converter: str  # "dtflow.lora_convert"
    converter_version: str  # CONVERTER_VERSION, an integer-like string bumped on any output change
    draw_things_revision: str  # "db37e65"
    family: str
    scale: float
    source_name: str  # base name only; full path is recorded by LoraStore.origins, not here
    source_sha256: str | None
    source_size: int
    created_at_utc: str
    verification: str  # "verified" | "unverified_on_dt_plus" | "experimental" (from the FamilySpec)
```

`CONVERTER_VERSION` is a constant in `provenance.py`, bumped by hand whenever output bytes could change. It is the thing #327/Option 8 compare to offer "reconvert". It is JSON-safe (`asdict`) and stored with the retained file (section 8). No prompts, no private paths, no endpoints (AGENTS privacy rule): only the file base name.

### Failure vs warning rule

Unchanged from today and kept deliberately strict: unknown keys, unmatched modules, shape mismatch, LoHa/LoKr/bias/diff keys, duplicate modules stop with an error. `ignore_unmatched` stays a table-family-only escape hatch and its skips are returned in `warnings`.

## 5. Families and registry

```python
@dataclass(frozen=True, slots=True)
class FamilySpec:
    key: str  # "krea2"
    label: str  # "Krea 2"
    exact_architecture: str  # "krea_2"
    status: Literal["verified", "unverified_on_dt_plus", "experimental"]
    experimental: bool  # True only for sdxl; requires allow_experimental
    kind: Literal["resolver", "tables"]
    convert: Callable[[ConversionContext], ConversionPlan]  # see below
    notes: str = ""
```

Registration is explicit, not directory scanning: `families/__init__.py` imports the five modules; each calls `register(FamilySpec(...))` at import time. Adding a family is one new module plus one import line plus one line in the docs table. No dynamic import of unknown code (PyInstaller-safe, matches the repo habit of explicit lists).

Engine split: the two existing engines (resolver families: `_convert_open`; table families: `convert_tables`) become two reusable engines in `api.py`/`families/tables.py`. A resolver family supplies `resolve(module) -> Target | None` plus expected block counts; a table family supplies its mapping key and text-encoder rules. `api.convert_lora` looks up the spec, opens the reader, runs the engine, writes the checkpoint, hashes, returns the result. No family code knows about files or threads.

Status values map to what the evidence in `lora-ckpt-format.md` and Djé's smoke already say:
- `krea2`, `zimage`: `verified` (Swift-compared and DT+ smoked on Windows).
- `flux1`, `sd1`: `unverified_on_dt_plus` (Swift-compared, not yet run on DT+). Badge removed by a one-line change after Djé's smoke.
- `sdxl`: `experimental`, `experimental=True`.
The status is data, so the GUI badge, CLI message and docs table all read the same source, and `devdocs/guides/lora-ckpt-format.md` is updated by hand to match (no generated docs).

`supported_families()` returns the specs in a stable display order, the only list the GUI dropdown and the CLI `--family` choices use.

## 6. Experimental flag for SDXL

Two layers, both required:
1. API: `FamilySpec.experimental` makes `convert_lora` raise `ExperimentalFamilyError` unless `request.allow_experimental` is true. Same behaviour as the current `--experimental` flag. Success adds an "experimental" warning and `result.experimental=True`.
2. GUI (PR 2): SDXL is not in the default dropdown. It appears behind an explicit "Show experimental families" choice, and selecting it shows the existing in-window inline confirmation (`ui/inline_feedback.py`, Confirm replaces the action button) naming the credit risk. The confirmation sets `allow_experimental=True` for that row only; it is not remembered. Metadata records `declared_version` as usual and the result's `verification` field is stored so a later review can see which LoRAs came from an experimental converter.
Open decision for Djé: hidden vs visible with confirmation (ranking.md suggested confirmation).

## 7. Family detection hook for safetensors (later PR)

Not implemented now, but the shape is fixed so PR 1 does not block it.

- Module: `services/lora_convert/detect.py` exposing `detect_source_family(path: Path) -> SourceFamilyGuess`. It reads only the safetensors header (no weights), reusing `safetensors_io` (header parse is already separate from tensor reads).
- Result: `SourceFamilyGuess(family: str | None, confidence: Literal["certain","ambiguous","none"], candidates: tuple[str, ...], evidence: tuple[str, ...])`. Same principle as `detect_lora_family`: only return a family when independent landmarks agree; ambiguity returns candidates and no pick; never use filename.
- Signals reuse what exists: each `FamilySpec` may provide `matches(keys, shapes) -> bool`-style landmark data (module-name patterns the resolvers already encode, e.g. Krea 2 `to_gate` and text fusion, Z Image `noise_refiner`, Flux 1 `double_blocks/single_blocks`, SD `input_blocks`). So detection is registry-driven: adding a family adds its landmarks. For PR 1 the `FamilySpec` has an optional `landmarks` field left `None`.
- Integration: the Prepare editor's inspection step (`_inspect_prepare_files`) already runs `detect_lora_family` off the GUI thread for each path. It will call a single dispatcher `inspect_lora_file(path)` that returns the existing `LoraFamily` for `.ckpt` and a safetensors guess for `.safetensors`. The result lands in the existing "suggested/detected/manual" column. Ambiguous → dropdown stays empty and required.
- Do not extend `lora_family.py` itself with safetensors logic; keep the SQLite detector untouched and put the new detector beside the converter, because its evidence is converter-shaped.

## 8. Integration with Prepare editor and LoraStore (PR 2)

Principle from ranking.md and #370: conversion is a step before the unchanged `AssetService.prepare`; the canonical record stays the converted `.ckpt` (sha of the converted bytes). The source safetensors is origin metadata. Prepare/preflight/#326 stay unchanged.

New orchestration service (Qt-free): `services/lora_convert_prepare.py` (name tbd) with one coroutine:

```
async def convert_for_prepare(state, source, family, *, allow_experimental, progress, cancel) -> PreparedConversion
```
Steps: `asyncio.to_thread(convert_lora, ...)` into a staging folder under `<state>/lora_files/.convert/` (same volume as the store, so the final `os.replace` into the store is a rename, not a copy); hash comes from the result; call a new `LoraStore.retain_converted(...)` (below); return the retained path and sha so `prepare_asset` can run on it. The UI never calls `convert_lora` directly; it calls this service through `AsyncRunner.submit`, which matches `_inspect_prepare_files`.

`LoraStore` additions (kept small, additive, index version stays 1 because readers ignore unknown keys; confirm in PR 2 by reading `_read`/`_write` migration handling):
- `retain_converted(staged: Path, sha256, size, *, managed_name, conversion: dict)` — like `retain`, but moves the staged file instead of copying, and stores a `conversion` record on the index item: `{converter_version, draw_things_revision, family, scale, verification, source: {sha256, size, name, path, mtime_ns, edge}, converted_at_utc}`. Reuses the existing origin stamp format for the source so `changed_origin` logic can later be reused for "the source safetensors changed since conversion".
- `find_converted(source_sha256, family, scale, converter_version)` — returns an existing retained conversion so re-preparing the same source does not reconvert and never creates a second name. Reconversion with a new `converter_version` makes a new managed name (never overwrite, as ranking.md requires).
- No change to `verify`, `remove`, `usage`. Converted files are ordinary retained files, so the retained-LoRA inventory, restore (#326) and download from saved source (#327) already treat them correctly.

Prepare editor changes (UI only, no conversion logic):
- Picker filter: `LoRA files (*.ckpt *.safetensors)`; `AGENTS.md` paragraph updated in the PR.
- Row model gains `kind` ("ckpt" | "safetensors") and `convert_state`. For safetensors rows: family dropdown is limited to `supported_families()` (verified and unverified-with-badge; SDXL per section 6); empty until the user picks or the later detector fills it; format locked to F16 and Q8P hidden (converted rows are f16 only); row status shows "Will convert before upload".
- Authorize: for safetensors rows `_authorize_prepare` first runs `convert_for_prepare`, then continues into `_prepare_next` with the converted path. Conversion progress goes to `activity_strip` as part of the same "Preparing cloud assets" activity (detail "Converting 3 of 5…"), cancel uses the existing stop affordance wired to the `threading.Event`. A conversion failure marks only that row "Failed" and shows `error.user_message` plus `error.hint` verbatim; other rows continue. No automatic retries, per the existing rule.
- Conversion happens under the same page-busy ownership and the same backend/credential revalidation as upload; a credential change after conversion but before upload only skips the upload, the retained converted file remains.
- All static controls go into the canonical Designer `.ui` forms (`prepare_lora_row.ui`), not built in Python.
- The cloud-only gate stays. Non-cloud backends are out of scope.

## 9. Errors and user-facing messages

`errors.py`:

```
ConversionError(Exception)            # base; keeps today's name so old callers still catch it
  .user_message: str                  # one plain sentence for the UI
  .hint: str | None                   # next step ("Use the Colab notebook ...")
  .code: str                          # stable machine code for logs
  UnsupportedFamilyError              # unknown family key, or family not ported
  ExperimentalFamilyError             # needs allow_experimental
  UnsupportedLoraFormatError          # LoHa, LoKr, bias, diff, DoRA, unknown keys
  UnmatchedModulesError               # modules not in the family's layers (wrong family / model size)
  ShapeMismatchError                  # fused tensor shapes do not match the family
  SourceReadError                     # not a safetensors file, truncated, unreadable
  DestinationError                    # exists, not writable, cannot replace
  ConversionCancelled                 # cancel requested; not a failure for the UI
  DependencyError                     # numpy too old
```

Rules:
- Messages keep today's content (they were written against real failures) but split into `user_message` and `hint`; `str(error)` returns both so the CLI output is unchanged.
- The Colab hint becomes a constant `COLAB_HINT` in `errors.py`; it points at the notebook path now and can later point to a doc URL in one place. The hint never claims a path the packaged build cannot offer: in the packaged app the text reads "the Colab notebook in the DTFlow docs" (decision: the notebook ships in the repo, not the wheel).
- Wrong-family errors (unmatched modules, shape mismatch) mention the chosen family by label and suggest checking the dropdown, since with a user-chosen family this is the most likely real failure, more so than a converter bug.
- Logging: use the shared logging setup, `log = logging.getLogger(__name__)` and `diagnostics.event` with stable codes (`lora_convert.started`, `.finished`, `.failed`, `.cancelled`), only counts and codes, no paths, in line with AGENTS privacy and diagnostics rules. The old `print`-based `log` goes away from the library; the CLI wrapper prints.

## 10. CLI and `tools/`

Keep `python tools/drawthings/lora_to_ckpt.py ...` working as a thin wrapper, because `devdocs/guides/lora-ckpt-format.md`, Djé's Windows smoke commands and the notes from #397 use that command line and `inspect_ckpt.py` is the comparison tool that proves the 0-differing-tensor result.

- `tools/drawthings/lora_to_ckpt.py` shrinks to about 40 lines: argparse, build `ConversionRequest`, call `convert_lora`, print progress, map errors to exit code 2 as today. Imports `dtflow.services.lora_convert`; running it needs `uv run` (project environment) or `PYTHONPATH=src`. That is a small behaviour change for the standalone-file usage ("only numpy needed"); document it.
- `extract_swift_mappings.py` stays in `tools/` (dev-only, needs Swift source) and its output path changes to `src/dtflow/services/lora_convert/data/lora_mappings.json`.
- `inspect_ckpt.py`, `inspect_lora_families.py` stay in `tools/`.
- No `dtflow` subcommand in the first PRs. A `dtflow lora convert` command is cheap by-product (Option 5) and can come later; ranking.md says CLI is not the deliverable. Revisit after PR 2.
- Retire nothing now. Retire the wrapper only if it stops being used by smoke instructions.

## 11. Packaging and pyproject

- No dependency change: numpy is already a runtime dependency. No `uv.lock` change.
- No `pyproject.toml` packaging change: hatch includes everything under `src/dtflow` (package data included), and `build_exe.py` already does `--collect-data dtflow`. Confirm in PR 1 by checking that hatch includes `.json` under `services/lora_convert/data/` (it does by default for files inside the package; do not add `force-include` unless that check says otherwise) and that PyInstaller does not need `--hidden-import` for the families (explicit imports in `families/__init__.py` make them static imports, which is the reason to avoid directory scanning).
- Ruff: `tools/**` already ignores TID251; the new package has no Qt, so nothing to ignore. Run `ruff format` and `ruff check` before commit, per convention. `ruff` `B` rules apply to the moved code, so expect a few fixes while splitting (for example `B905` style findings); fix them without changing behaviour.
- Docs touched across the PRs (AGENTS.md "documentation-sync" rule): `devdocs/guides/lora-ckpt-format.md` (new import path, status table from the registry), `devdocs/architecture/` new short page `lora-convert.md` (layering, API, provenance), `AGENTS.md` Architecture bullet for the Prepare paragraph, `devdocs/releases/unreleased.md`, `devdocs/current_dev_status.md`.

## 12. Run-time restore (#326/#327), later

Not built now, but the API supports it without change. The restore path never converts: it uploads the retained converted `.ckpt` (the conversion happened in Prepare and is recorded in the index). The only run-time converter use is "reconvert from saved source because `converter_version` changed", which is an explicit action in the metadata editor (Option 8), not automatic inside a paid run. If Djé later wants fully automatic conversion at run time, it uses `find_converted` first, then `convert_lora` with the family stored on the record; never auto-detected at run time.

## 13. Proposed PR split

All PRs: Windows only, no new tests, no local test runs, ruff format and lint, drafts first, PR body with `Refs #357`, squash subject ending with the PR number, Djé sets Development links.

**PR 1: Move and restructure the converter (no behaviour change, no UI).** Scope:
- Create `services/lora_convert/` (modules in section 3) by moving code, `lora_mappings.json` to `data/`.
- Add `ConversionRequest/Result/Progress/Provenance`, error hierarchy, registry and `FamilySpec` with the five families, `CONVERTER_VERSION=1`, `DT_REVISION="db37e65"`, atomic output, cancel, progress.
- Turn `tools/drawthings/lora_to_ckpt.py` into the thin wrapper; update `extract_swift_mappings.py` output path.
- Docs: `lora-ckpt-format.md` import path, new `architecture/lora-convert.md`, release note.
- Acceptance (by Djé / CI, not me): existing CI green; Djé re-runs one Krea 2 and one Z Image conversion on Windows with the wrapper and compares `inspect_ckpt.py` for 0 differing hashes (same bytes as before the move). This is the safety proof for a refactor that must not change output. Risk: refactor changing bytes; mitigation: move code verbatim, change nothing arithmetical.
- Size: mostly moved lines; reviewable by diffing against the old file.

**PR 2: Prepare editor accepts `.safetensors` (Option 1 first PR).** Scope: `lora_convert_prepare` service, `LoraStore.retain_converted/find_converted` with conversion record, picker filter, row kind and family dropdown (verified + unverified badge; SDXL behind confirmation), conversion step in `_authorize_prepare` with progress/cancel in the activity strip, per-row failure state with `user_message` + `hint`, F16 only for converted rows, `.ui` form edits, AGENTS.md and docs. Gate: Djé smoke on Windows with DT+ (Krea 2, Z Image already verified; Flux 1 / SD 1.x flagged until run).

**PR 3: Safetensors family detection.** `lora_convert/detect.py`, per-family `landmarks`, one dispatcher in the Prepare inspection step, prefill and "detected" evidence. Refuse ambiguity. Depends on Djé's confirmation that detection should not extend `lora_family.py`.

**PR 4: Metadata editor "convert from source" (Option 8).** Reconvert when `converter_version` changes or the source changed; uses `conversion.source` in the index; ties to #327 saved-source. Includes "source changed since conversion" using `changed_origin` logic.

**PR 5 (optional): drag and drop that opens the editor (Option 7), and a `dtflow lora convert` subcommand.** Small add-ons, independent.

**Later: restricted run-time conversion (Option 4)** only for LoRAs that went through PR 2 once; mostly "upload the retained converted ckpt", which #326/#327 already do.

Dependencies: PR 2 needs PR 1. PR 3, 4, 5 each need PR 2 and are independent of each other. A review thread is suggested for PR 1 (refactor safety, byte-equality reasoning) and PR 2 (concurrency, ownership revalidation).

## 14. Risks and mitigations

- Refactor changes output bytes → move verbatim; `CONVERTER_VERSION` stays 1; Djé byte-compare on Windows before PR 2.
- Conversion blocks the GUI → always `asyncio.to_thread` through `AsyncRunner`; progress marshalled by the existing queued signal; never convert in the callback.
- Large memory on weak machines (Pentium E6500 target) → dict-of-all-tensors peak is the converted LoRA size (hundreds of MB); documented; streaming writer is a later change behind the same API.
- Windows file locks → reader closes the source via context manager (kept), `.part` file closed before `os.replace`, never delete a file that is open.
- A user picks the wrong family and pays for a bad upload → strict converter errors, no ignore-unmatched in the GUI, verification badge, SDXL confirmation, detector later with ambiguity refusal.
- Index growth/compat → additive keys only; if `_read` validates unknown keys, handle that in PR 2.

## 15. Open questions for Djé

1. Package name and location: `services/lora_convert/` as proposed. OK, or do you prefer a flat `services/lora_converter.py` plus a `services/lora_families/` folder?
2. Is it acceptable that `python tools/drawthings/lora_to_ckpt.py` now needs the project environment (`uv run` or `PYTHONPATH=src`) instead of only numpy and the standard library?
3. Family list and badges in PR 2: Krea 2 and Z Image verified, Flux 1 and SD 1.x shown with "unverified on DT+" until you smoke them. OK?
4. SDXL: hidden behind "Show experimental families" plus confirmation (my recommendation), or not shown at all until a Mac reference exists?
5. Keep the source `.safetensors` path recorded as origin (needed for Option 8 and #327), and treat the converted `.ckpt` sha as the canonical record. Confirm?
6. Is hashing the source on every conversion fine (extra pass over a few hundred MB), or should the hash be optional in the GUI?
7. Which exact-architecture strings does the cloud inventory eligibility check accept for Flux 1 and SD 1.x (`flux1`, `v1`)? I assume the ones used by `detect_lora_family`; PR 1 will confirm against `architecture_shortcode`.
8. Should PR 1 add a `dtflow lora convert` subcommand now (cheap), or wait?

## 16. Decisions from Djé (2026-11-12)

1. Package `services/lora_convert/` accepted.
2. CLI needing the project environment (`uv run` or `PYTHONPATH=src`) accepted.
3. Flux 1 and SD 1.x ship with an "unverified on DT+" badge; final status decided during implementation smoke tests.
4. SDXL visibility (hidden plus confirmation vs not shown) is not decided: Djé will test it during implementation. Default for PR 2 until then: the registry flag stays `experimental`, UI exposure kept behind the explicit confirmation path so it can be switched off in one place.
5. Converted ckpt sha is the canonical record; source safetensors path is recorded as origin metadata. Confirmed.

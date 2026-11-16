# Testing

Run from the repository root after `uv sync`. Ordinary tests are offline and never submit real generation.

## Offline validation

Ruff lint and format checks exclude `.ipynb` notebooks and the markerized q8p notebook source. Notebook cell ordering is interactive; this exclusion does not validate notebook execution. Ordinary Python modules remain checked.

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Offline fake-backend tests generate a deterministic 96 x 64 RGB calibration PNG
in pytest's temporary directory and use it as the fake provider's image source.
Only children launched with `guarded_python(..., fake_image=True)` use that same
generated file through the shared test-only image-source helper. The default
`guarded_python` bootstrap installs the network/process guard without importing
the fake backend ahead of the supplied code. The substitution is process-local;
direct subprocesses and grandchildren do not inherit it, and child subprocess
commands still require explicit guard registration. Tests exercise the real
fitting, artifact validation and persistence paths without downloading bundled
artwork. The fitting contract checks RGB8/unfiltered source encoding and sampled
pixels for centered cover cropping. Desktop fake simulation keeps its bundled illustration.

The source checkout provides a pytest wrapper that preserves pytest output/exit status and requires development dependencies:

```bash
uv run dtflow test                 # Offline suite (default)
uv run dtflow test -all -live       # Offline suite plus live connectivity
uv run dtflow test -all -gen        # Offline suite plus live generation
uv run dtflow test -live            # Live connectivity only
uv run dtflow test -gen             # Live generation only
uv run dtflow test -live -local     # Local connectivity only
uv run dtflow test -live -cloud     # DT+ connectivity only
```

## Live opt-in and configuration

Option pairs are `-all/--all`, `-live/--live`, `-gen/--generate`, `-local/--local` and `-cloud/--cloud`. Groups can be combined. Local/cloud scope implies `-live` when used alone and requires only that side's settings. `-local` cannot be combined with `-gen`, which is DT+ only. A cloud-only run omits the local LoRA inventory comparison.

| Setting | Purpose |
| --- | --- |
| `DRAWTHINGS_API_KEY` | DT+ credential for live tests |
| `DTFLOW_LIVE_HOST`, `DTFLOW_LIVE_PORT` | Local Draw Things endpoint |
| `DTFLOW_LIVE_SHARED_SECRET` | Optional local shared secret |
| `DTFLOW_LIVE_CA_FILE`, `DTFLOW_LIVE_SERVER_NAME` | Explicit strict alternative trust; otherwise the bundled Draw Things CA is used |
| `DTFLOW_LIVE_MODEL` | Exact discovered model for the generation smoke; listing alone does not prove workload support |
| `DTFLOW_LIVE_LORA` | Optional known already-available cloud fixture when asserting reuse |

The flag supplies opt-in without a separate marker or opt-in environment variable. Requested live checks fail clearly if unconfigured. `-live` is connectivity/inventory only: it never calls `/authenticate` or submits generation. Ordinary `uv run pytest` remains offline with live tests gated.

**Each `-gen` invocation submits a new generation and may consume subscription credits:** one 256×256 image, four steps, seed 42, guidance 1, without LoRA or PAYG fields. Select a model that supports this txt2img workload. Unknown attempts are never automatically replayed.

Live inventory can enrich local LoRA availability with a bounded metadata-only BYOM lookup. Without `DTFLOW_LIVE_LORA`, upload-required/unknown availability is legitimate; an empty local LoRA inventory omits enrichment. When enrichment runs it retains `inventory/lora_inventory.json`.

## Inspecting retained evidence

```powershell
# Credentials and the intended live connection must already be configured.
uv run dtflow test -live
$run = Get-ChildItem test-runs -Directory | Sort-Object Name -Descending | Select-Object -First 1
Get-Content "$($run.FullName)\inventory\cloud_inventory.json"
$env:DTFLOW_LIVE_MODEL = "exact-model-identifier-from-inventory"
# The following deliberately request fresh generation (may consume credits).
uv run dtflow test -gen
uv run dtflow test -all -live -gen
```

Each invocation prints and retains `test-runs/<run-id>/`: flat `inventory/local_inventory.json` and `cloud_inventory.json` snapshots, `state/attempts.sqlite3` when generating, `outputs/<attempt-id>/`, and one `results.json` with build identity and check outcomes. Inventory JSON includes `cache_version`; these are retained evidence, not application cache. Failed/unknown attempts remain recorded.

## Continuous integration

[GitHub Actions CI](../../.github/workflows/ci.yml) has two **offline** test levels under one workflow:

- **Fast (ordinary PR ready for review):** runs once when an eligible PR whose **source branch name does not contain `release`** changes from Draft to **Ready for review** (`pull_request: ready_for_review`). Runs the explicit test targets in [`tools/ci/fast_tests.txt`](../../tools/ci/fast_tests.txt). This is a focused subset for Generate, shared queue/workflow foundations, retry/persistence safety, models, LoRAs and cloud assets. Scripts-specific tests and most Qt/gallery/tooling tests run in Full. There are not yet dedicated workflow-executor tests in this selection; shared queue and planning tests do **not** establish complete Workflows coverage.
- **Full (release PR ready for review):** runs the entire offline pytest suite on `ready_for_review` if the PR's **source branch name contains `release`**, regardless of its target branch. The match is case-insensitive.
- **Full (release PR merged into `main`):** runs again after a PR from a `release`-named source branch is **merged** into `main` (`pull_request: closed` with `merged == true` and base branch `main`). Ordinary merges, PRs closed without merging, and pushes to `main` do not trigger this workflow. The post-merge release run bypasses the PR change gate so an empty post-merge comparison cannot suppress Full CI.
- **Manual dispatch:** choose **Fast** or **Full** (Full is the default), and select the target using `sha`, `pr` (number or `latest`) or `branch`; resolution precedence stays `sha > pr > branch`. Manual runs bypass change filtering.

New commits (`pull_request: synchronize`) **do not automatically rerun CI**. After committing changes to a previously Ready PR, move it back to Draft and then Ready again to validate the latest code before merging. The event-based run is not a guarantee that an older green result still covers the current PR head. The workflow's `paths-ignore` patterns exclude documentation-only PR changes from automatic CI (including release PRs).

Both levels check the lockfile, run Ruff format/lint as non-blocking warnings, and run pytest with `-n auto`. Only lock and pytest failures fail the job; a Fast pass is explicitly **not** a Full pass. The shared change gate skips expensive setup for eligible Ready PRs without CI-relevant changes; manual dispatch and post-merge release validation bypass it. The Fast job also prints all pytest phase durations and emits a **non-blocking warning** when measured in-job elapsed time reaches 60 seconds. The timing excludes runner allocation and teardown and is a budget signal, not an interruption or a billing guarantee. Individual test durations cannot simply be added to predict parallel wall-clock time.

Normal CI has no live provider credentials and must not submit any real local/cloud generation. Live/backend and native Windows/packaged checks remain separately authorized and recorded.

See [development tools](development.md) for probes and [runtime layout](../architecture/runtime-layout.md) for state/evidence separation.

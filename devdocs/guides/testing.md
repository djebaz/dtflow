# Testing

Run from the repository root after `uv sync`. Ordinary tests are offline and never submit real generation.

## Offline validation

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

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

[GitHub Actions](../../.github/workflows/ci.yml) runs lock consistency, Ruff lint/format and offline pytest on Ubuntu for pushes to main, non-draft pull requests (including ready-for-review), and manual dispatch. Draft PRs are skipped. A newer push to the same PR cancels its previous run; manual runs are not cancelled.

Manual dispatch accepts `sha`, `pr` (number or `latest`) and `branch` (empty means the default branch). Precedence is sha > pr > branch; latest selects the highest-numbered open PR. No application credentials or live generation are used. CI does not establish Windows or packaged acceptance.

See [development tools](development.md) for probes and [runtime layout](../architecture/runtime-layout.md) for state/evidence separation.

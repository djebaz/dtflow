# Portable Krea2 mixer (#216 / PR #219)

Reimport `tools/scripting/krea2_lora_mixer_portable.js` (version 00017) as a new saved script version. The original Draw Things branch retains its preset, marker-PNG settings and sequential exports.

## One Setup form

Use **Inspect → Setup → Run Script**. Inspect preloads the form from cached inventory without creating a queue or connecting to a provider. Setup contains prompts, mixing, output, chunk settings and LoRA selections together. Run Script submits that form directly: no Preview button, review wizard or second confirmation.

LoRAs load from captured inventory, catalogue and confirmed local receipts. Refresh Generate inventory first if needed. Metadata mismatches and unconfirmed availability are skipped; unknown compatibility is advisory, not a required opt-in. Backend validation still applies. Missing files require explicit Prepare; the adapter does not download or upload them automatically.

“Static inspection limited” means dynamic calls can only be checked at run time, not a detected incompatibility. Parser resource exhaustion reports Analysis incomplete without inventing a source location or claiming invalid syntax.

## Settings and chunks

The adapter inherits Generate’s checkpoint/steps and changes supported dimensions, sampler, guidance, seed and LoRAs. It does not claim the original iOS preset’s shift, clipSkip, tile or guidance-embedding fidelity; those mappings remain #115.

Plans contain at most 10000 jobs. Each run uses an explicit global start and chunk size within the captured generation allowance (1–32). Random blends use deterministic global-job seeds; comparison enumerates singles/pairs for each prompt with a fixed image seed. Requested concurrency remains bounded by shared queue/backend capacity.

Source/settings plan IDs and global indices enter child request evidence. Changing only chunk start, size or concurrency retains plan identity. Matching identity does not prove earlier jobs ran or succeeded. Inspect durable attempts before manual continuation; no automatic resume, deduplication or replay of uncertain work is added.

## Remember settings

Remember saves authoring inputs for the exact source version after explicit form submission, selecting LoRAs by identifier rather than checkbox position. Turning Remember off leaves the previous record unchanged. Preferences are private authoring state, not execution history or consent.

`<state>/script_preferences.sqlite3` uses independent schema 1, at most 128 source versions and 256 KiB ASCII JSON per version. Bounds fail without eviction or truncation; unreadable/newer schemas are not reset. This database contains private prompts/settings; do not publish it. The DTFlow adapter writes no marker PNGs.

## DTFlow-only APIs

`__dtflowScriptHost.listLoRAs(offset = 0)` returns at most 100 entries per page from one captured projection. Follow `nextOffset` until `null`; replies also include `items`, `total`, `omitted`, `model`, `observedAt`, `cached` and a notice. At most 4096 definitions are projected. Entries contain `file`, `name`, `listed`, `prepared`, `compatibility`, `compatibilitySource`, `eligible` and `reason`, never endpoints, paths or remote receipt IDs. Unsafe identifiers are omitted with a count.

The form supports at most 400 candidates; set `LORA_NAME_FILTER` and reimport if needed. The API does not silently discard entries. Skipped text is bounded and discloses additional entries available through pagination.

Other helpers are `runLimits()`, `loadPreferences()`, `savePreferences(object)` and `planId(object)`. Preference writes require explicit form answers. These are feature-detected DTFlow overlays, not changes to the pinned Draw Things facade. Headless CLI can open its explicitly selected backend; the desktop’s preparation guarantee does not describe every CLI invocation.

## Owner acceptance

On Fake, refresh inventory, choose `fake-fixture` and import the updated script. Confirm Inspect preloads one Setup without queue activity, unknown metadata remains selectable, and Run Script directly runs one chosen job. Check identifier-based recall, chunk ranges, Stop/close and changed source/inventory separately. Exact backend/checkpoint/LoRA, Windows/DPI and packaged acceptance remain owner-controlled; #216 stays open pending evidence.

No tests, checks, renders, app launch, provider requests or generation were executed by the agent for this follow-up. See [Scripts](scripts.md), [architecture](../architecture/scripting.md) and the [plan](../plans/2026-11-03-krea2-mixer-adaptation.md).

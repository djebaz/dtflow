# Windows GUI development-build smoke — 2026-10-05

## Identity and evidence boundary

The owner built and interactively exercised `dtflow-0.1.0-g518ffbe-windows-x64.exe` on Windows. The stamped source commit is `518ffbe6693f3aefaf4fb10a518069f85ba3a48b`, with dirty PR #29 implementation changes: it is **not** the source of that plan-only commit by itself, a clean merged build, or a 0.3.0 release artifact.

- Artifact size: 77,949,322 bytes.
- SHA-256: `2d1926bdb16284c4ea13d214acc5b7da8c47167a54215bdcfb248da595ff1161`.
- Live process inspection matched the executable path to this file in repository `dist/`.
- Application sessions: `94078e96a438` and `29a5527c4d38`; both logs report version 0.1.0 and stamped commit `518ffbe6693f`.
- Original logs: `%LOCALAPPDATA%\DTFlow\logs\dtflow-<session>.log`.
- Retained local snapshot: `test-runs/20261005T184405Z-owner-gui-pr29/`, captured 2026-10-05 18:44:05 UTC; `logs/` contains both copies and `output-file-presence.json` contains the bounded file-presence check. This ignored evidence is not committed.

**Owner-run versus agent-checked:** the owner performed the build, UI interaction and live generation. The agent read the logs, identified/hash-checked the existing executable, and checked output-file presence without opening credentials/prompts, submitting work or rerunning inference. Logs are diagnostic evidence, not the durable state store or proof of LoRA application.

## Owner-reported observations

- GUI launched directly and no additional console windows appeared during use.
- API-key saving and persistence across restart worked.
- Form-field persistence worked separately for each provider.
- Local generation was exercised with a selected LoRA; the logs do not identify its weight or prove the LoRA's actual application.

## Log and file observations

| Surface | Observed scope |
| --- | --- |
| Connections/inventory | Fake connection; cloud authentication after key saving; local connection after endpoint entry. Inventory refresh/cache writes for cloud and local. |
| Restart | Local session restored, local inventory cache hit, cloud form restored with `remembered=True`. |
| Standalone generation | Four successful cloud attempts and one successful local attempt, each reporting one saved output. |
| Queues | Four successful cloud items and four successful local items. The local items are logged sequentially. No conclusion about universal concurrency performance follows. |
| Model | Logged successful generations use `z_image_turbo_1.0_q6p.ckpt`; no wider model compatibility is established. |
| Output-file presence | All 13 logged successful attempts have `metadata.json` and one nonempty PNG under the corresponding per-user output directory. This check does not decode images or validate all metadata contents. |
| Cloud assets | Receipt `f5231a97194740aaa8d1530be0c81695` prepared with `source=already_exists`. This establishes the already-present preparation path, not a new upload. |
| Validation | Two cloud LoRA preflight rejections, logged as `lora_preflight_failed`/validation without a prepared generation attempt for those submissions. Exact rejection reasons are not in normal-mode logs. |
| Dimension handling | Desktop adjustment from 420×512 to 448×512 was logged before successful local generation. |

Session `94078e96a438` records 11 successes (including eight queue items); session `29a5527c4d38` records two additional cloud successes and two preflight rejections. Counts describe this frozen evidence snapshot, not further owner tests after capture.

## Unresolved outcome and limits

One earlier cloud attempt, `bfcdd215a8b74b288c710a64a47649e2`, failed with `no_final_output`, protocol category and unknown remote outcome. Subsequent submissions used different attempt IDs and succeeded; they do not resolve that first attempt. The snapshot does not establish why final output was absent, whether remote generation completed or what credits were consumed. Do not automatically replay it or claim confirmed remote cancellation/refund behavior.

This is successful **bounded development-build** evidence for GUI startup, secure-key/form persistence and local/cloud generation/queue composition, with the failures above retained. It does not establish a fresh custom cloud upload, universal LoRA application, clean-PATH/second-machine deployment, native startup-error dialogs, simulated log-write failures or a clean final release build.

## Release handoff

PR #29 may merge based on its scoped implementation checks, CI and owner smoke evidence. Prepare 0.3.0 version/release metadata separately, then build the clean accepted source revision and identify that exact artifact for final release acceptance. Reuse the bounded live evidence where the implementation path is unchanged; do not repeat expensive inference solely because the version or documentation changed.

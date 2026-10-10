# First image-operation owner acceptance

Date: 2026-10-10. PR #64, step 4. Owner explicitly confirmed: "ok all acceptance ok" and subsequently stated that step 4 is passed and editor/smart-selection work can start.

This records owner acceptance, not agent-run inference or universal checkpoint compatibility. The agent read retained metadata and verified final/raw/metadata file presence offline. Visual judgments are owner-reported. Inputs and outputs remain ignored under the runtime/evidence layout; no photos, prompts or credentials are committed here.

| Attempt | Route | Checkpoint | Target | Steps / guidance / strength |
| --- | --- | --- | --- | --- |
| `3a509fa61db04dd387efe75ed5b0097e` | local endpoint forwarding to DT+ | `z_image_turbo_1.0_q6p.ckpt` | 832x1216 | 8 / 1 / 1.0 |
| `929f31b2810341f997118ff427fe6c2d` | local endpoint forwarding to DT+ | `flux_1_fill_dev_q8p.ckpt` | 256x512 | 8 / 10 / 0.7 |
| `6aee5d812df949099f402021fb1d2a66` | local endpoint forwarding to DT+ | `flux_1_fill_dev_q5p.ckpt` | 384x512 | 28 / 50 / 1.0 |
| `b1f520008aa5428bbf59dcf41daf1251` | local endpoint forwarding to DT+ | `flux_1_fill_dev_q5p.ckpt` | 512x768 | 12 / 50 / 0.7 |
| `211197ee6e9c4dd29ec37e4579d16315` | direct DT+ (`drawthings-cloud`) | `flux_1_fill_dev_q5p.ckpt` | 512x768 | 12 / 50 / 0.7 |

All listed records are mask-file inpaint, seed 123456, Euler A trailing, grow 10, feather 4, preserve-outside on. They returned terminal OK, registered finals and no warnings. The owner explicitly reported a clean/no-noise Z Image result and accepted the gate after the direct-cloud run. The final cloud evidence ID contains "local" because the owner reused it; backend/attempt identity, not the label, identifies the route. Stored permits remain `owner_probe`; acceptance does not rewrite historical requests or create a bundled verified permit.

Earlier empty-output runs remain unknown, not retroactively successful. Z Image guidance 50 failed whereas 1 succeeded; Fill comparisons changed multiple settings and do not establish a root cause. No description-forwarding change was made. Bridge acceptance does not establish local-device inference; direct-cloud evidence is separate.

The shared commands/records provided here contain no separate no-mask img2img trace; do not invent one from the owner's overall acceptance. Step 4 is passed by owner decision with the bounded evidence above. Qt, smart-selection accuracy, old-CPU ONNX, export/recycle and packaged-executable acceptance remain step 8 or separate gates.

The owner chose 0.7 as the manual editor default after observing identity loss at 1.0. This is an adjustable starting value, not a guarantee of identity preservation; the CLI's explicit/default settings remain separately documented. Low-step model guidance must be advisory, not a new compatibility or minimum-step claim.

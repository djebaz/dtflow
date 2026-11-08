# Unreleased

Changes through 6.1.0 are consolidated in [DTFlow 6.1.0](v6.1.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- The GUI writes a Python stack to `logs/crash-<pid>.txt` if the process dies natively (empty files are removed on clean exit), so a crash like [#308](https://github.com/djebaz/dtflow/issues/308) shows which Python code was running.
- Dev-mode (`-x` / `--dev`) workflow/detector RAM checkpoints and up to one minute of post-execution observations in the existing structured log, with process/system memory availability and detector session/tensor byte counts. See [interpretation limits](../guides/development.md#workflow-and-detector-memory-diagnostics) (#306).

- Workflows: ComfyUI-standard **Load Image**, **Load Image (as Mask)** and **Upscale Image** nodes (`LoadImage`, `LoadImageMask`, `ImageScale`) start a workflow from a local image or mask file and resize it (`MaskToImage` and `ImageToMask` let Upscale image resize a mask too). Image steps accept any size: a source that is not a multiple of 64 is padded for generation and the result is cropped back to its frame. Choosing a file copies it into `<state>/workflows/inputs/` under a content-hash name and the workflow stores only that name, so no absolute paths are saved. See [workflows](../guides/workflows.md) ([#316](https://github.com/djebaz/dtflow/issues/316)).
- Workflows: wildcard prompts. Generate, Inpaint and Img2img nodes can use `{a|b}` choices and `__name__` lists from the `wildcards` folder, as a random sample or every combination (up to 16 per node), with the varied parts highlighted. The draw is seeded per image and the lists are stored with the run, so Retry repeats the same prompts. Optional node properties `DTFlow prompt mode` and `DTFlow prompt count` (contract still v1; an app version before this one rejects a workflow file that uses them, because it rejects unknown properties); a new wide branch numbering covers more than 4 outputs per node without changing any existing run ([#317](https://github.com/djebaz/dtflow/issues/317)).

## Changed

- Every file and folder picker now opens at the folder it was last used in, remembered separately per picker and per runtime (Windows/WSL), in `settings.json`. Import/export pairs (presets, workflows, generation configuration, masks) and script input pickers share one folder each. A new picker needs no code beyond importing `QFileDialog` from `dtflow.ui.file_dialogs`. Connection has a Remembered folders list with Forget buttons. The Prepare picker's folder moves into the same memory ([#312](https://github.com/djebaz/dtflow/issues/312)).

- DT+ keys: the key selected in the app (saved in Windows Credential Manager) now wins over `DRAWTHINGS_API_KEY`; several keys can be saved, named and switched, and the environment key can be selected explicitly. The Connection card shows which key is active and whether the environment variable is also set. The CLI still reads the environment only.
- Daily generation stats list every available key (even at zero) under one consistent name; no key is shown as unidentified.

- Ordinary queues, workflows and scripts skip unused preview image decoding, PNG encoding, service buffering and forwarding. Direct Generate keeps previews; progress and saved finals are unchanged. Preview bytes still arrive because the inspected protocol has no verified client download opt-out. Dev logs summarize received/skipped previews (#306).

## Fixed

- The left and right side panels no longer show a vertical scrollbar when their content fits: the rail stacks are now `CurrentPageStack`, which sizes from the shown page only instead of the tallest page of any tab (documented in the design system and Qt design tokens) ([#314](https://github.com/djebaz/dtflow/issues/314)).
- Workflows: node graphics items and their embedded widgets are now destroyed at a known point with the app-wide wheel and Escape filters paused, targeting the suspected cause of the native Windows heap-corruption crash in [#308](https://github.com/djebaz/dtflow/issues/308). Unchanged run status is no longer re-applied to the node widgets on every poll (less proxy-widget churn during a run). The cause is not proven. The owner's load A, run, load B, run smoke passed once at 296a453 (an earlier head crashed); #308 stays open until it survives repeated runs.

## Upgrade and compatibility

- No dependency, durable-state/request schema, model-cache, generation-setting or retry/billing-policy change. The pre-existing native Windows graphics-proxy heap-corruption crash is tracked separately in [#308](https://github.com/djebaz/dtflow/issues/308); these diagnostics and preview changes do not claim to fix it.

## Validations

- [ ] pytest Full passed
- [ ] render_ui passed
- [ ] SmokeTests passed

No local tests or renders were created/run for #306. Source/runtime observations and automatic final-head CI are recorded on its PR; existing owner captures are not exact-head or packaged acceptance.

## Release audit

- PRs: #306
- Scope: Dev-only memory observations and suppression of unused queued-execution preview processing; pre-existing native crash remains #308.

The `## CI build` section (source, artifact, SHA256) is appended by the build-and-release workflow to the draft; keep it in the published body.

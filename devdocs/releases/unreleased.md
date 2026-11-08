# Unreleased

Changes through 6.1.0 are consolidated in [DTFlow 6.1.0](v6.1.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- The GUI writes a Python stack to `logs/crash-<pid>.txt` if the process dies natively (empty files are removed on clean exit), so a crash like [#308](https://github.com/djebaz/dtflow/issues/308) shows which Python code was running.
- Dev-mode (`-x` / `--dev`) workflow/detector RAM checkpoints and up to one minute of post-execution observations in the existing structured log, with process/system memory availability and detector session/tensor byte counts. See [interpretation limits](../guides/development.md#workflow-and-detector-memory-diagnostics) (#306).

## Changed

- DT+ keys: the key selected in the app (saved in Windows Credential Manager) now wins over `DRAWTHINGS_API_KEY`; several keys can be saved, named and switched, and the environment key can be selected explicitly. The Connection card shows which key is active and whether the environment variable is also set. The CLI still reads the environment only.
- Daily generation stats list every available key (even at zero) under one consistent name; no key is shown as unidentified.

- Ordinary queues, workflows and scripts skip unused preview image decoding, PNG encoding, service buffering and forwarding. Direct Generate keeps previews; progress and saved finals are unchanged. Preview bytes still arrive because the inspected protocol has no verified client download opt-out. Dev logs summarize received/skipped previews (#306).

## Fixed

- Workflows: node graphics items and their embedded widgets are now destroyed at a known point with the app-wide wheel and Escape filters paused, targeting the suspected cause of the native Windows heap-corruption crash in [#308](https://github.com/djebaz/dtflow/issues/308). This is an unconfirmed hypothesis; the issue stays open until the owner's Windows reproduction passes.

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

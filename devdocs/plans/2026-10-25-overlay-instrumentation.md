# Plan: isolate overlay startup cost (#169, PR #173)

## Scope
- In: instrument ActivityStrip initialization and expose startup-report phase populations/schema differences.
- Out: lazy ActivityStrip, speculative optimization, UI changes, generation, packaging and merge.

## Action items
- [x] Inspect the active clean PR #173 branch and current constructor/report implementation.
- [x] Add marks around Designer loading, scrim construction, status-bar acquisition, painted ActivityBar construction and final wiring/timers.
- [x] Preserve existing median fields while adding per-phase sample counts, schema count and a mixed-schema warning.
- [x] Synchronize status/release documentation.
- [x] Inspect three owner captures with identical phase schemas; correct the earlier broad attribution to ActivityStrip.
- [x] Split preceding Logs placeholder, stack-hide and root-layout insertion work, and isolate QObject construction from host acquisition.
- [x] Split the development-only QLineEdit probe into construction, polishing and explicit native destruction timings, without changing normal non-development startup.
- [x] Add an isolated native-versus-DTFlow-styled QLineEdit diagnostic with prelude timings and post-measurement metadata; never change production styling or open application state.
- [x] Run three alternating fresh-process pairs and record results/limitations; follow with three pairs comparing the unchanged theme against a diagnostic-only exact-font ablation.
- [ ] Collect matched native Windows logs and select an optimization only after identifying the expensive operation.
- [ ] Final tests and smoke tests deferred to user.

## Decisions and Design Changes
- 2026-10-25: the checkout already advanced beyond merged #172 to PR #173; keep this refinement on its existing clean branch.
- 2026-10-25: retain eager shared status presentation. The reported 96% share identifies a candidate, not a proven internal cause or speedup.
- 2026-10-25: phase schemas compare ordered phase names. Missing phases are not zero-duration samples; retain the existing per-phase medians with explicit populations.
- 2026-10-25: owner captures `2ec1e03dcfb6`, `2947e04de618` and `90ea09debf67` share one phase schema (n=3 per phase). Recorded startup totals are 11.33 s, 11.10 s and 15.74 s (median 11.33 s); all report successful connection checks and no warnings/errors in the inspected snapshots. No launches or tests were needed to inspect this evidence.
- 2026-10-25: ActivityStrip work measured after its internal entry mark is approximately 17 ms. The preceding 0.87–1.07 s interval also includes Logs placeholders, hiding the old stack and inserting the shell into the root layout, plus initial QObject/host acquisition. This supersedes the earlier 96%-overlay attribution: do not make ActivityStrip lazy or identify root-layout insertion as the culprit until the new boundaries isolate it.

## Open questions
- None blocking implementation; matched native measurements remain necessary.

## QLineEdit probe refinement
- The old `first QLineEdit polished` phase combined construction, polishing and temporary disposal. It is replaced by `QLineEdit probe: constructed`, `QLineEdit probe: polished` and `QLineEdit probe: destroyed`; do not compare the old phase directly to the new polishing-only phase.
- The probe remains hidden, parentless and development-only. Explicit Shiboken deletion measures native destruction before releasing its Python wrapper; no real input control is deleted or moved to a worker.
- This adds measurement boundaries only. It does not disable the warm-up, establish a speedup or perform the proposed with/without-warm-up comparison. New matched owner logs are required; mixing old/new logs must trigger the report's schema warning.
- Follow-up: owner authorized the isolated styled/native comparison. `tools/ui/probe_line_edit.py --style native|dtflow` measures application/theme/loader/basic-widget/font-metrics/probe costs separately, excluding Python/Qt import time and the full application. Run each case in its own process, alternating order; native Qt styling also differs in font/theme policy, so results cannot attribute cost to a specific QSS selector.

## Validation
- Tests were not added or run; smoke tests were not run. Final validation remains with the user.
- The owner authorized diagnostic commands. Twelve isolated native-Windows Qt probe processes were run (six native/styled comparisons, six styled/exact-font comparisons); the full desktop was not launched, no application state/credentials were opened and startup_report.py was not rerun for this experiment.
- No renders, packaging, provider calls, uploads or inference were performed.

## Isolated native Windows font/style comparison (2026-10-25)
- Tool: `tools/ui/probe_line_edit.py --style native|dtflow|dtflow-exact-font`. Each invocation creates its own QApplication and hidden probe, applies only the selected process-local style, then exits. Production theme loading and QSS remain unchanged.
- Environment: native Windows Qt platform, Qt 6.12.0, Python 3.13.13, application font reported as Segoe UI. Each mode has n=3 fresh processes with varied ordering; these are not controlled cold-boot or full-app trials.
- Native versus unchanged DTFlow theme: median QLineEdit polishing **0.046 ms versus 1,130.701 ms**. Prelude-inclusive medians **740.375 ms versus 1,918.901 ms**; native font-metric time varied substantially (one run approximately 1,923 ms), so do not rank full startup from these totals.
- Follow-up ablation retains the stylesheet except for replacing its single global `font-family: "Segoe UI", sans-serif;` declaration with `font-family: "Segoe UI";` in the diagnostic process. Median polishing: unchanged theme **1,127.559 ms** (range 1,053.466–1,134.394), exact-font variant **1.252 ms** (range 1.062–1.748). Prelude-inclusive medians: **1,621.849 ms versus 462.280 ms**.
- Evidence supports the generic fallback declaration as the source of the isolated polishing penalty on this machine; it does not prove a specific Qt internal mechanism, visual equivalence, other-platform behavior or a measured full-desktop speedup. A production fix still needs deliberate platform/fallback handling and owner visual/startup checks.
- Ignored raw evidence: `tmp/line-edit-style-comparison.json` and `tmp/line-edit-font-ablation.json`. These contain diagnostic metadata/timings only, not prompts, credentials or application-state paths.

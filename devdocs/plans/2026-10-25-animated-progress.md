# Plan: animated progress and observable stages (#170)

## Scope
- In: shared visible-only stripe animation, truthful existing Generate/Prepare/Queues stage presentation, pre-generation queue indeterminate progress and offline preview definitions.
- Out: new backend lifecycle emissions, inferred server internals, synthetic percentages, scheduler/retry/poll changes, tests, smoke tests, renders, packaging and merge.

## Source-to-stage mapping (before UI wiring)
- `backends/drawthings/backend.py`: `preparing(stage=wire_validated)` means request validation completed; `submission_possible(stage=grpc_request)` means submission may occur, not that generation was acknowledged. Present submission/waiting together without inventing an acknowledgement.
- `_signpost`: `sampling` carries backend `step` and request `steps`; other signposts carry only their actual phase name, with no measurable percentage. Preserve last reported sampling counts across count-less events.
- Backend `terminal` means the RPC completed; Generate's existing final callback follows validation/durable saving in `services/generation.py`. Do not claim durable completion from the terminal event alone.
- Prepare `ui/lora_panel.py::_prepare_next`: per-file admission and settlement are observable. The service currently exposes no UI upload-byte or negotiation-stage callback. Keep per-file preparation/possible-upload/wait-for-receipt wording indeterminate; do not invent upload percentages or stage completion.
- Queues: durable running/admission and wrapped backend events are observable. Undispatched work stays queued, without active animation; admitted work without progress is waiting/preparing, not proven generation. Queue settled-item counts are not sampler-step percentages.
- Input preparation, model/settings resolution, upload substeps and server internals are not uniformly observable through these UI event streams. Expose only supported milestones, not a speculative universal stage ladder.

## Action items
- [x] Inspect issue, branch state and existing event/presentation paths.
- [ ] Implement shared animation and existing-source stage mapping.
- [ ] Extend offline render definitions; synchronize user/developer docs and release audit.
- [ ] Commit, push and open PR to main; keep this ledger current.

## Decisions and Design Changes
- 2026-10-25: user explicitly selected fast-implement and requested an early PR. Do not add or run tests/smoke tests; do not merge. Native animation acceptance belongs to the owner.
- 2026-10-25: use one shared animation clock, not per-queue-row timers; stop ticking when no visible indeterminate subscriber remains.

## Open questions
- None blocking. Unsupported milestones remain explicitly unobservable rather than guessed.

## Validation
- [ ] Final tests and smoke tests deferred to user.
- Tests and smoke tests were not run. No renders, packaging, inference, uploads or billable calls were run. Packaging is not applicable to this source UI fix.

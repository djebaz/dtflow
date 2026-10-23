# Desktop UI consistency and independent queue authoring — PR #162

## Agreed interaction model

- Window fullscreen is controlled by F11. Optimized Images layout is controlled by its icon or Escape outside text editors/popups/inline feedback. The modes are independent: optimizing must not call native window-state/show methods or cause the reported window flash.
- Shared brand, navigation, Queue and Run controls retain their positions and styling on Logs. Logs-specific action styling must not leak onto remounted shared chrome.
- Generate and the queue composer have independent drafts. The composer has an editable base/source prompt. Use Generate settings explicitly replaces the composer base; Copy to Generate explicitly copies it back without dispatch. Selecting/inspecting/editing a queue must not overwrite Generate.
- Saved queue authoring input (base request, source prompt, original PlanForm, count and local receipt identities) is distinct from resolved execution items. Save it atomically in a bounded, versioned queue-only event, not image request evidence, exported metadata, diagnostic logs or a new database schema.
- Edit as new restores saved authoring input. Legacy queues can expose an evidenced prompt template when available, but otherwise must disclose reconstruction from resolved items; never invent lost wildcard contents, randomness settings or source prompts.
- A small icon beside the selected queue's prompt copies its full first-item prompt to the clipboard without changing either draft.

## Delivery checkpoints

- `7672117`: Logs Designer layout/style isolation, shared-header/window-mode targeted check, initial F11/Escape handling and offline Logs-header preview definition. Pushed to PR #162.
- Owner confirmed window-mode behavior, then reported flashing on leaving optimized mode. The newer independent-modes requirement above supersedes the initial restore-window-state implementation.
- Owner's small-window Logs screenshot exposed navigation sharing the filters' horizontal row. Navigation now has its own Designer row below the brand, with filters below it; the focused shell check covers non-overlap at requested widths 900, 1040 and 1600 px. Narrow offline preview states are defined, not rendered; native Windows/DPI acceptance remains separate.
- `899438e`: separate Logs navigation/filter rows, narrow geometry checks and offline preview definitions. Owner confirmed the smaller-window layout in an interactive smoke test.
- `f4755ad`: independent viewing modes, composer base prompt/draft transfers, prompt clipboard button and atomic queue-only authoring-source persistence. Internal source events are hidden from the timeline. Focused review also corrected quick Plan's backend disclosure and aligned the new-queue concurrency control with its captured backend. Unsupported source versions/draws/variation ranges fail rather than being silently clamped.
- Code commits are pushed to PR #162. Documentation and the PR body are synchronized in the separate docs follow-up; no merge or release is implied.

## Evidence and remaining limits

- At `f4755ad`, three targeted checks passed in 10.86 s: shared-header/window modes; independent composer source/copy round trip; atomic source persistence, shared-owner insertion and oversize rollback. Repository-wide Ruff lint and format checks passed (407 Python files formatted); diff checks passed.
- Owner reported window modes worked, then flagged the old exit flash; the independent-mode implementation removes the native restore calls. Owner confirmed the small-window Logs correction and reported queue Save paused seemed okay during smoke testing. No exhaustive interaction or DPI/packaged acceptance is claimed; further owner testing was explicitly deferred to real use.
- Offline definitions cover narrow Logs, independent queue source prompts and optimized Images in windowed/fullscreen states. Definitions were updated, not rendered or accepted visually by the agent.
- Retained authoring input is separate from resolved execution items and does not copy external wildcard file contents. Edit as new uses current wildcard files. Legacy queues lack complete source and disclose reconstruction; Inpaint Edit as new remains unsupported. Relaunch continues to reuse resolved items.
- No additional tests were run for documentation synchronization. No full-suite rerun, provider traffic or billable work was performed. The earlier truncated LoRA test-failure text is insufficient to diagnose or claim resolved by this PR.

## Validation boundaries

Run only relevant targeted tests and static checks. Do not rerun the full suite. Do not render screenshots unless requested. No provider calls, real generation, training, model downloads, native Windows/DPI or packaged acceptance are authorized by this plan. The owner’s interactive observations are separate from offline test evidence. Preserve the previous PRs' queue Stop and uncertainty safeguards.

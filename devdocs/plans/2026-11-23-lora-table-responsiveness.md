# Retained-LoRA table responsiveness (#565)

## Cause and scope

[Issue #565](https://github.com/djebaz/dtflow/issues/565) reports Windows GUI gaps of 2098, 1778 and 584 ms with 96 retained files and three 1.1–1.2 s stall samples in `_render_lora_table` → `_fill_lora_table` → `_lora_row_actions`. These are owner-supplied historical measurements, not a new local profile or a measurement of this fix.

Source inspection identifies repeated work: entering Connection requests retained entries, family/format inspection, metadata/receipt reads and registry-only rows. The retained and family callbacks previously rebuilt unconditionally. The initial retained read also discarded family/format observations, forcing a second presentation change at scan completion. Independently, `GenerationPage.sync_backend` → `LoraPanel.sync_backend` → `set_enabled` requests a managed-table render even when its presentation has not changed. Every rebuild loaded `managed_lora_actions.ui`, connected two actions, polished widgets and recalculated geometry for every retained row. With 96 retained rows, that means 96 form loads per rebuild, including unchanged rows; this work matches the issue's sampled stack, but its individual timing has not been measured separately.

The change is presentation-only. It does not alter scans, family detection, generation, queues, providers, credentials, upload consent or persistent schemas. In particular, the separately reported 4.3–7.1 s worker-side `families_ms` is not addressed or claimed fixed.

## Implementation

- Retain previous family/format/q8p observations for unchanged content identities while a new worker inspection is pending; its delivered result still replaces those observations, including unreadable/missing-file outcomes.
- Compare a copied presentation snapshot, including entries, metadata, no-copy records, formats, q8p availability, selected-key confirmations, backend and current busy state. An identical render request leaves the table and selection untouched.
- Reuse each existing row's canonical Designer action host. Update its current identity and metadata before enabling its controls; click handlers resolve those current values rather than an old row's captured identity. Existing eligibility, status text, tooltips and service calls remain authoritative.
- Mount at most one missing action form per timer turn, yielding between retained rows instead of synchronously loading the whole library. A replacement projection cancels the old pending list; each mount checks the row's current content identity and no-copy status. Busy state is read again at mount time. The timer belongs to the table/window and is stopped by the existing shutdown guard; callbacks also refuse work after runner shutdown starts.
- Keep the existing GUI-gap, stall-sample and retained-read diagnostics unchanged. No competing diagnostic writer or new sink is introduced.

## Evidence and remaining acceptance

Only source review and non-test/non-render static checks are permitted during implementation. No new test, fixture, render or automated navigation workload is created or run. No inference, conversion, upload or billable action is needed for these checks.

AC-1's original measurements support the suspected hot path and source inspection establishes the repeated form-load count; isolated before/after timing remains unverified. AC-2 (no table-attributable gap above 500 ms) and AC-3 (preserved behavior) require owner Windows evidence before merge unless explicitly waived. AC-4's unchanged-state guard and AC-5's unchanged diagnostic producers are source-inspectable structural evidence, not native performance acceptance.

For owner acceptance, use the normal Windows launch and existing diagnostics with approximately 96 retained files: switch Connection ↔ Generate while generation is already running, allow reads to finish, and inspect existing `ui.gui_gap`, `ui.gui_stall_sample` and `connection.retained_loras_slow_read` records. Check retained and no-copy rows, status pills, family filtering, selection, Prepare/Prepared and Quantize q8p eligibility, metadata edits/removal, key/backend changes and closing while actions are still appearing. No extra generation or upload is authorized by this document. Record the exact source/build identity and any table-attributable gap above 500 ms; successful source checks alone do not tick behavioral ACs.

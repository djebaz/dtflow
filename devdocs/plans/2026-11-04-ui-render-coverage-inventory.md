# UI render coverage inventory: components and representatives

**Status:** First inventory and path-level selection slice; shared pack approved, page packs pending owner review, visual equivalence not established. **Inventory source pin:** PR #226 head `a684257cae6f4d0dba125024f42019632827a8fb` (2026-11-04); implementation continues in #227. **Parent:** [render orchestration plan](2026-11-04-ui-render-orchestration.md).

## Owner direction and the distinction this inventory makes

Select specific affected presentations, not every screenshot on every page using a shared component. Reuse one representative when rendering really is identical; retain additional screenshots only for a different visual variant, mounting/layout contract or interaction. Category-only selection keeps the fast default unless a level is specified. `render-all` is the explicit exhaustive override and triggers independently. The first implementation slice below preserves these controls.

Two coverage questions must stay separate:

1. **Component appearance:** does the shared widget render its controls, text and state correctly? One suitable representative can cover identical reuse.
2. **Consumer integration:** does a particular caller mount, size, configure or interact with that widget differently? Only a changed or distinct integration needs another representative.

Example: changing an export-card checkbox style should not render every Connection and Images export scenario. Changing the card's reparenting or available width should cover its Connection and Images hosts. Changing error presentation should add the relevant error variant, not every operation that can produce an error.

## Evidence and limits

This slice reads the tracked catalogue, render fixtures, relevant UI source/forms and their direct consumers. It does not import or execute the renderer, add/run unit tests, render screenshots, access owner application state or call providers. All candidate state IDs below exist in the pinned catalogue. Their names and source setup are not proof of screenshot quality, pixel equivalence or native interaction acceptance.

The catalogue contains **456 states in 34 subcategories**:

| Category | States |
| --- | ---: |
| Shell | 27 |
| Connection | 60 |
| Generate | 64 |
| Inpaint | 20 |
| Queues | 92 |
| Scripts | 57 |
| Images | 117 |
| Logs | 19 |

Those counts describe available fixtures, not the number a change should select. Sources: [catalogue](../../tools/ui/render_catalog.json), [common fixtures](../../tools/ui/render_states.py), [Scripts fixtures](../../tools/ui/render_scripts_states.py). Subcategory names currently describe authoring/navigation groups; they are not component-equivalence classes.

## First shared-component inventory

The following are candidate coverage families. A row lists distinct possible requirements; it does **not** mean every listed state must run whenever any source in that row changes. A visual-only change selects the affected variant; an integration change adds the relevant consumer representative.

| Family | Owner and consumers verified in source | Candidate representatives | When another screenshot is justified |
| --- | --- | --- | --- |
| Shared searchable selector | `ui/search_select.py::choose_searchable`; Generate's `SearchableComboBox`; Queue composer model selector; Connection timezone selector | `generate-21-checkpoint-editor` for editable selection; `generate-22-checkpoint-no-matches` for empty results; `generate-23-custom-checkpoint-editor` for custom-identifier entry; `queues-39-composer-model-search` for the Queue caller | Optional custom identifier, read-only selection, caller sizing or validation differs. Do not select both Generate and Queue solely because they call the same function. Timezone's open editor is a gap below. |
| LoRA selection editor | `ui/lora_selection_editor.py::edit_lora_selection`, called by `lora_panel.py` and `queue_composer.py`; this is not the generic searchable-selector implementation | `generate-24-lora-selection-editor`; `generate-25-lora-selection-single`; `queues-43a-composer-lora-weights-editor` | Multi-selection, existing selections, per-LoRA weights or Queue set/multi-weight configuration changes. Do not assume the two editor implementations are interchangeable coverage. |
| Shared inline confirmation | `ui/inline_feedback.py`, canonical `inline_feedback.ui`; consumers include queues/templates, removal, close and cache actions | `images-08-permanent-confirmation` for irreversible action; `connection-22-cache-clear-confirmation` for a different action context; `shell-09-overlay-notice-during-confirmation` for protection against another notice | Danger styling versus ordinary confirmation, action-button replacement/width, conflicting notice behavior or a caller-specific count/message changes. Different action wording alone need not select every caller. |
| Shared notice/overlay rows | `ui/activity_strip.py`, `activity_status_row.ui`, `activity_progress_row.ui`; producers include Generate, Queues, export, removal, Logs and diagnostics | `shell-08-overlay-several-rows` for concurrent progress/result/warning rows; `shell-10-overlay-info-result` for persistent information; `generate-14-failed` for an error path | Severity styling, wrapping, row stacking, progress versus lifetime presentation, or cancellation controls change. One producer's equivalent warning should not force all other producer screenshots. |
| Docked activity bar and narrow fallback | `ui/activity_bar.py`, `ui/activity_strip.py`, `activity_strip.ui`, `ui/live_control_fx.py`; same host across workspaces | `shell-22-activity-queued-width-239` and `shell-22-activity-queued-width-240` for the presentation boundary; `shell-23-activity-unknown-stripes` for indeterminate activity | Boundary behavior, retained upcoming chip, striped progress, Stop controls or a different overlay fallback changes. Add `queues-81-stopping-caveat-400` for actual Queue stopping wording/ownership presentation, not for an unrelated bar-color edit. |
| Activity result lifetime/fade | Same activity implementation, but distinct presentation phases | `shell-25-activity-success-lifetime`, `shell-26-activity-success-fade`; stopped variants exist separately | Select the changed lifetime/fade phase or state color; do not multiply it across all tabs. Captures do not prove elapsed-time or input behavior. |
| Gallery snapshot condition | `ui/gallery_page.py` projects a special persistent warning through the shared overlay | `images-21-snapshot-grid`; `images-24-live-recovery-stabilizing`; `images-23-snapshot-warning-dismissed` | Persistence/recovery/dismissal or content placement changes. This special row is not equivalent to ordinary expiring amber notices. Grid versus Detail is only separate coverage when the host/context is affected. |
| Shared export card | `ui/image_export_panel.py`, `forms/main_window.ui::imageExportBox`; `MainWindow._place_export_panel()` moves the same card between Connection and Images | `connection-28-export-idle` for the normal card; `images-54-export-panel-checked` for Images mounting; `images-53-export-panel-empty-scope` for scope gating | Mounting/scroll width, hidden scope controls, checked-image requirements or navigation changes. A shared control-style edit normally needs one card instance, not every export outcome. |
| Export progress/results | Shared export controller feeds the activity strip; card and status presentation are separate coverage | `connection-30-export-progress`; `connection-33-export-partial`; `images-55-export-panel-running` | Select a changed result/progress variant. Add Images only for its mounting/selection interaction, not because the same export worker runs there. |
| Usage table and shared bottom count | `ui/cloud_usage_panel.py`; table in Connection and a count pill in the main window's bottom bar | `connection-46-usage-per-key-thresholds` for table levels; `connection-54-bottom-bar-count-amber` for the pill; `connection-55-bottom-bar-count-generate` for cross-page mounting | Table row layout versus pill projection, color thresholds, timezone text or unidentified key state changes. A table-only edit should not select every page with the pill. |
| Main-window shell, rails and resizing | `ui/main_window.py`, `forms/main_window.ui`, `forms/workspace_shell.ui`; multiple page layouts | Candidate cross-layout samples: `generate-20-local-controls`, `queues-50-inspector-minimum-width`, `scripts_form_default`, `images-105-pager-default-20-first-batch`, `logs-15-shared-header-aligned` | Broad shared files use the small shared pack, not all states. Do not infer changed object subtrees automatically; add a specific file mapping only when repeated unnecessary renders justify it. |
| Inspector integration | Generate/Queue inspector seams and Scripts' dedicated inspector/drawer | `generate-50-inspector-after-queues`; `queues-50-inspector-minimum-width`; `scripts_narrow_details` | Hidden-page width leakage, rail minimum, long content or drawer behavior changes. Scripts' drawer is not equivalent to the ordinary rail. |
| Shared preview/canvas base | `ui/image_preview.py::ImagePreview`; Generate result and `ui/inpaint_canvas.py::InpaintCanvas` | `generate-02-portrait` for image placement; `generate-05-zoom` for zoom presentation; `inpaint-03-mask-painted` for the subclass overlay | Base viewport change may need the mask subclass once. Inpaint-only brush/mask behavior should not force gallery or Generate examples. Gallery has its own presentation code, so it is not automatically covered by this base. |
| Queue thumbnail preview | `ui/queue_thumbnails.py`, including `HoverPreview`; used by the Queue item view | `queues-64-item-table-thumbnail-preview`; `queues-70-thumbnail-preview-revalidation-pending` | Popup placement, stale/absent image state or origin-row integration changes. A gallery thumbnail's similar pixels do not prove identical widget ownership or mounting. |
| Gallery image navigation and density | `gallery_page.py`, `gallery_pager.py`, `gallery_loading.py`, `gallery_selection_check.py` | `images-105-pager-default-20-first-batch`; `images-71-pager-loading`; `images-170-grid-independent-zoom` | Grid/List/Detail geometry, loading, selection hit targets or explicit density variants change. Restrict to the changed view and a boundary state, not all 36 navigation fixtures. |
| Gallery detail hover card | `ui/gallery_hover_card.py` and Gallery detail caller | `images-99-detail-hover-card-long-prompt`; `images-99a-detail-hover-card-empty` | Long wrapping versus empty content, anchor placement or viewport constraints changes. Do not include every gallery state. |
| Script dynamic forms | `ui/scripts_form.py` mounted by `scripts_page.py` in the canonical Scripts workspace | `scripts_form_default`; `scripts_form_long_values`; `scripts_setup_narrow`; `scripts_setup_crop_multiple` | Different field component, nesting, picker/crop controls or wrapping changes. This form is not equivalent to the generic selection editor merely because both contain inputs. |
| Script library and code editor | `scripts_page.py`, Scripts editor/library canonical forms and handwritten widgets | `scripts_library_remove_armed`; `scripts_editor_syntax_error`; `scripts_editor_narrow` | Changed library membership control, code diagnostics or editor-specific layout. Select the matching surface, not every Scripts state. |
| Logs table/header/detail | `ui/logs_workspace.py`, `forms/logs_workspace.ui`; filter controls have their own popup behavior | `logs-02-mixed-severity`; `logs-15-component-popup-readable`; `logs-03-details`; `logs-16-narrow-header-900` | Sort/header/table/detail or native-popup changes. Shared theme changes need representative table/text/popup shapes, not all Logs scenarios. |
| Queue recipe library | `ui/queue_templates.py`, `forms/queue_template_library.ui`; uses the common feedback layer | `queues-92-template-library`; `queues-95-template-delete-consent` | Library-specific selection/revision/recipe disclosures versus shared confirmation appearance. A common confirmation edit can reuse another danger representative unless this mounting changes. |
| Inpaint editor | `ui/inpaint_editor.py`, `inpaint_canvas.py`, canonical `inpaint_editor.ui`; local selection controls | `inpaint-03-mask-painted`; `inpaint-13-select-people-found`; `inpaint-07-clear-confirmation` | Canvas versus local-selection controls versus shared confirmation changes. Do not require model inference for a presentation edit. |
| Startup and diagnostic notices | `ui/splash.py`, `ui/diagnostic_notice.py`, main-window notice receiver | `shell-30-startup-splash`; `shell-21-diagnostic-warning` | Splash is a distinct window. A diagnostic notice uses the shared warning presentation, but receiver/mounting changes need its own producer path once. |

### Source anchors for the non-obvious shared relationships

- [Search selector](../../src/dtflow/ui/search_select.py), `choose_searchable` at line 21 and `SearchableComboBox.showPopup` at line 162; [Queue composer](../../src/dtflow/ui/queue_composer.py), imports at lines 26-27 and chooser/editor calls around lines 339-350; [LoRA panel](../../src/dtflow/ui/lora_panel.py), editor import/call at lines 31/215.
- [Main-window form](../../src/dtflow/ui/forms/main_window.ui) has `usageTimezoneCombo` at line 676 and `imageExportBox` at line 742. [Usage controller](../../src/dtflow/ui/cloud_usage_panel.py), lines 64-66, disables custom identifier entry for the timezone selector.
- [Main-window controller](../../src/dtflow/ui/main_window.py), `_place_export_panel` around lines 1715-1736, selects the host, reparents the one card and hides scope controls in Images. [Export controller](../../src/dtflow/ui/image_export_panel.py) owns the shared operation.
- [Activity strip](../../src/dtflow/ui/activity_strip.py), imports `ActivityBar` and loads the shared strip/row forms; [inline feedback](../../src/dtflow/ui/inline_feedback.py), lines 31-33, mounts its canonical form into that same strip. These are different presentations under one coordinator, not separate page-owned implementations.
- [Preview base](../../src/dtflow/ui/image_preview.py) and [Inpaint subclass](../../src/dtflow/ui/inpaint_canvas.py); [Gallery detail presentation](../../src/dtflow/ui/gallery_detail_preview.py) is a separate implementation. Do not deduplicate these by appearance alone.

## Gaps and non-equivalence discovered

1. **Timezone search is not visibly exercised open.** It uses the shared `SearchableComboBox` but with `custom_label=None` and timezone-specific content. Existing timezone screenshots show usage projections, not the open read-only search editor. Candidate coverage remains incomplete for that configuration; adding a fixture later requires owner scope, not an invented state ID here.
2. **Plain searchable selection and weighted LoRA selection are separate implementations.** `queues-43-composer-lora-set-search` must not be assumed to exercise `search_select.py` from its name alone; the Queue code can open `edit_lora_selection` for weighted sets.
3. **Global styling does not have one universal representative.** Inputs, table rows, custom-painted previews, code/form content and overlays have different rendering paths. Build a small pack covering those shapes; do not select every state sharing a color token.
4. **Identical text or colors are insufficient evidence of reuse.** Treat viewport mounting, scroll containers, custom painting, read-only/custom inputs and narrow drawers as explicit variant dimensions.
5. **Fixture dependency groups are not coverage families.** One selected state can require setup/transitions in a larger fixture. That setup may execute without saving all intermediate states; selection counts and cost must describe saved representatives separately from prerequisite work.
6. **Capture completion is not equivalence.** The reported Generate differences, including `generate-20-local-controls` and `generate-21-checkpoint-editor`, still need owner visual review. Do not declare equivalence merely because both images were saved or a full run completed.

## Approved shared fallback pack

The owner-approved shared fallback pack contains: `generate-21-checkpoint-editor` (inputs/list/editor), `queues-60-item-table` (table/rows), `scripts_form_default` (dynamic form), `images-105-pager-default-20-first-batch` (cards/pager), `inpaint-03-mask-painted` (canvas overlay), `logs-02-mixed-severity` (dense diagnostic text), `shell-08-overlay-several-rows` (shared rows) and `images-08-permanent-confirmation` (destructive intent). Add a narrow or dark/light/state-color variant only where the changed selector or token affects a distinct branch. This is broad appearance sampling, not a claim that it covers every QSS rule or every host.

Keep fallback packs as named state lists, not a second speed level. `-f` remains general fast review and provides the starting pool for smaller page-specific packs; it is not automatically the shared fallback pack. Reliably mapped changes may select any required state outside `-f`. Page-specific membership remains to be inventoried and reviewed.

## Proposed inventory record shape for the next implementation step

Each component needs: stable component ID; owning source paths; direct consumers; visual variant requirements; representative state IDs; optional mounting representatives; prerequisite fixture IDs; confidence/evidence; and known gaps. Function/object names may document source ownership, but are not automatic selection rules. Keep component appearance and consumer mounting separate so the planner can deduplicate the former without discarding the latter.

**Owner-approved simplification:** keep file-to-component packs for focused files, file-to-page packs for page controllers/forms, and the eight-state shared pack for broad shared files such as `main_window.py`, `main_window.ui` and the QSS. Do not build automatic function/object/QSS-selector diff analysis. Add or refine a specific file mapping only when repeated unnecessary renders justify it. Preserve deduplication, coverage-incomplete warnings and the exhaustive `render-all` override.

## Owner-approved selection policy

- **Unmapped impact:** render representatives for the affected page. If the page is unknown, use the small shared pack. Prominently warn that coverage is incomplete and explain which changes could not be mapped; do not silently expand to exhaustive rendering.
- **Shared styles/tokens:** select affected components first when reliably identifiable; use the small shared pack for broad or uncertain impact. Retain distinct mount/layout/interaction variants, not every identical consumer.
- **Labels:** `render` requests mapped selection. Adding `render-all` immediately requests every state, independently of whether `render` is present. Do not enable rendering on unlabelled PRs by default.

These decisions supersede the earlier full-render fallback design. The path-level implementation follows them; representative completeness is not established, and automatic sub-file scope analysis is out of scope.

## Follow-up implementation checklist

### Completion and acceptance

**Complete for the agreed file-level scope.** At implementation head `828cc4767f5f7f1ff2e87ed5c0c3f1ef450532e9`, [CI passed](https://github.com/djebaz/dtflow/actions/runs/41923457176) and the [UI render Action passed](https://github.com/djebaz/dtflow/actions/runs/41923303302). The owner then confirmed that the screenshots are good. This closes the pending Action-run and visual-review gates recorded below for this scope; no new local tests or renders were run to record acceptance. It does not claim exhaustive representative equivalence, independent exercise of every label/failure branch, native Windows/DPI or packaged acceptance. Subsequent documentation-only commits do not change the accepted implementation.

PR #226 is squash-merged as `00d49dd`; its working renderer is the baseline for this separate follow-up. PR #227 implements file-level selection: 37 named packs, 30 component rules, catalogue-ordered exact-state deduplication, page/shared sampling with coverage warnings, and independent `render-all` triggering. All inventory families above have file-level representatives or the documented shared/page fallback. The eight page packs use two to four states each, including populated content and relevant layout variants. Source/fixture review is complete for this inventory slice; owner visual review and Actions acceptance of the final head remain separate. Automatic function/UI-object/QSS scope mapping is explicitly out of scope.

- [x] Complete the source-level component/variant inventory and representative boundaries for the listed families; visual equivalence remains owner review.
- [x] Encode file-to-component/page coverage families: 30 file-level rules and 37 packs are tracked separately from fixture prerequisites in `render_coverage.json`.
- [x] Resolve changed paths to deduplicated state selections with mapping reasons; retain distinct mounting/layout representatives where mapped (source implemented; Actions acceptance pending).
- [x] Use the eight-state shared pack for broad shared files rather than every consumer page, including theme helpers and the shared workspace form.
- [x] Preserve category fast defaults, explicit levels/manual selectors and the exhaustive `render-all` override; ensure adding `render-all` triggers rendering immediately (source implemented; Actions acceptance pending).
- [x] Resolve the unknown-change fallback with the owner: affected-page representatives, or the small shared pack when the page is unknown, with a prominent coverage-incomplete warning.
- [x] Update Action summaries and selection documentation with pack names, distinct screenshot counts, fixture setup counts and exact state lists. No unit tests added/run; renders remain owner-requested only.

## Acceptance record and future maintenance

- Owner screenshot review: accepted after successful renders at `828cc47`; no remaining visual correction requested.
- Review whether repeated unnecessary renders justify any additional specific file mappings; keep broad shared files on the small shared pack without sub-file diff analysis.
- CI and UI render execution at `828cc47`: passed in the linked runs. Independent exhaustive label/fault-path exercises remain unclaimed, not required implementation work for this completed scope.

**Delivery boundary:** the first selection slice changes developer renderer selection metadata/planning and the Action trigger, not application runtime behavior. No unit tests added/run and no screenshots produced. Static checks do not establish Action execution, screenshot equivalence or native acceptance.

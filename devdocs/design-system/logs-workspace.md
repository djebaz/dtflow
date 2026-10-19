# Logs workspace — compact event and characteristic explorer

**Status (2026-10-19):** native PySide6 source was introduced by PR #105 using the canonical Designer form, shared runtime tokens/QSS and Qt model/delegate; PR #111 implements issue #108's remaining header, Refresh, empty-state and inspector interactions. The [delivery audit](../plans/2026-10-19-structured-log-viewer.md) records CI and scoped Windows render-startup feedback. [#106](https://github.com/djebaz/dtflow/issues/106) owns remaining memory/lazy-creation work (export cancel is implemented in PR #111); [#107](https://github.com/djebaz/dtflow/issues/107) owns native/fault/performance/policy acceptance. [Offline HTML mockup](../mockups/dtflow_logs_workspace.html), [app-shell study](../mockups/dtflow_app_shell_with_logs.html), color card and component catalog are synthetic design references, not production UI or acceptance evidence. Runtime appearance authority remains Designer, `tokens.json`, shared QSS and the existing theme loader.

## Final compact row geometry

**Severity stripe | Date / Time | Event | Characteristics | Row actions**. Three content columns, a very narrow non-sortable severity strip and a narrow (about 80 px) row-actions column. No separate Source, Level, Value or Summary columns.

- **Severity:** 3–4 px indicator showing the **recorded** level only. DEBUG muted gray, INFO quiet neutral, WARNING amber, ERROR/CRITICAL red. Do not make INFO green or promote a DEBUG stall to WARNING. Tooltips, accessible row names, details and severity filter spell out the level; color is never the sole encoding.
- **Date / Time:** independent fixed-width column, date on first line, source time `HH:mm:ss.SSS` on second, tabular digits. Show full UTC `ts` and selected display timezone on hover/details. Date may be compressed by a day separator only if the full date stays accessible and exact source-time sorting is preserved.
- **Event:** separate fixed-width column (about 250 px), set in the regular UI font (not monospace), **one muted colored pill for the entire dotted event identifier** (`gallery.query_summary`, `gallery.gui_gap`, etc.). No split prefix/suffix, underline, wrapping, or per-segment styling. Truncate only visually with ellipsis and full-name tooltip/details; clicks/filters always use the original identifier.
- **Characteristics:** all remaining width for **multiple** compact colored metric-name chips followed by neutral, unit-bearing values. Semantically order keys, show the first six, let them wrap onto as many lines as needed (never clipped), and show `+N more` for the rest; expose all in the selectable details inspector. Example names `wall_elapsed_ms`, `sql_wall_ms`, `sql_cpu_ms`, `wall_total_ms`, `mounted`, `mode`, `query_busy`. Do not synthesize unmeasured p95 or choose a single privileged metric. Sanitized `msg` may appear in details, as tooltip, or as a labeled characteristic only when there is room and it is not duplicated.

The event name is on **exactly one line** and stays aligned vertically across rows. Rows have a compact minimum height (roughly 53 logical px, final Qt sizing through Designer/QSS) and grow only to fit wrapped characteristic chips; no per-cell widgets or HTML-in-Qt (row action icons are painted and hit-tested in the delegate). A simple painted delegate or model presentation role can draw the strip, full-event pill and key chips efficiently. Keep selected row under DTFlow's existing muted-blue selection colors without losing chip contrast.

**Native sizing guard:** wrap at most six projected characteristics, with byte-bounded labels/value previews and all content available in details. Cache height calculations by record identity, projection version and column width; invalidate only affected visible/page rows on resize or update. Never run `ResizeToContents` or hydrate full records to size the whole session. The HTML's natural browser layout is not evidence that Qt variable-height rows meet responsiveness or memory budgets; native narrow-width/DPI and anchor-preservation acceptance remains required.

### Row actions and header sorting

- **Row actions:** two quiet icon buttons at the end of each row, dim at rest and full brightness on row hover/selection: **Details** (DTFlow `detail` icon, opens the inspector) and **Copy raw** (`json` icon, copies the raw JSONL record). Both have tooltips and accessible names. **Clicking a row does not open the inspector** (opening it is rare); the row only highlights.
- **Sortable headers:** `DATE / TIME`, `COMPONENT` and `EVENT` sort on click; the active header is brighter and shows ↑/↓ (↕ when inactive), and a second click reverses. `COMPONENT . EVENT` presents two separate sort buttons in the event-column header: component sorts the recorded `component`, while event sorts the full recorded `event`. Component is not inferred from an event prefix; the event family is a separate predicate. `CHARACTERISTICS` opens a menu of the **numeric** characteristics present in the session; picking one sorts highest first, picking it again reverses, and the header shows the choice (for example `CHARACTERISTICS · wall elapsed ↓`). Rows without that characteristic sort last. Header sorting and the Sort dropdown always stay in sync.

### Stable semantic key order — not input JSON order

The **same key always retains its relative order** against other keys within all records, irrespective of field insertion order, other visible metrics, sorting or session filters. Use a versioned **display-only** ranking applied in the shared projection path for compact rows, inspector and human-oriented CSV projections (raw JSONL exports/copy retain recorded order and bytes unless shared privacy revalidation requires an explicitly labeled transformation). Within each category, the named sequence is explicit; unknown keys sort last by normalized lowercase key with raw-key tie-break.

| Category | Preferred order (left to right) |
| --- | --- |
| Primary wall | `wall_elapsed_ms`, `elapsed_ms`, `wall_total_ms`, `build_wall_ms`, `sql_wall_ms` |
| CPU | `sql_cpu_ms`, `cpu_total_ms`, then other `*_cpu_ms` |
| Other durations/statistics | `mean_ms`, `p95_ms`, `max_ms`, `max_slice_ms`, then other `*_ms` |
| Volume | `samples`, `elements`, `returned`, `mounted`, `added`, `removed`, `outputs`, then other counts |
| Execution context | `mode`, `scale`, `source`, `direction`, `offset`, then other known safe dimensions |
| Activity / flags | `query_busy`, `preparation_busy`, `retryable`, then other booleans |
| Extensions | Other safe scalar keys in stable alphabetical order; nested objects/arrays represented conservatively and fully inspected in details |

Omit missing characteristics without reordering remaining ones. Show `false`, `0` and `null` honestly rather than dropping them as falsey. For aggregates show **real** source-window sample counts and real recorded metrics; source `_PerfBatchStats` has no p95. Unknown ordinary events require **no new registry entry or JSON Schema modification**.

### Three independent visual channels

1. **Severity stripe:** stable semantic level tokens from `tokens.json`; not used to indicate measured duration.
2. **Full event pill:** deterministic palette index from normalized **complete** dotted event name (for example a stable specified FNV-1a 32-bit UTF-8 hash modulo a small ordered theme-derived muted palette). Do not depend on list position, filtering, session, Python's salted `hash()`, or alphabetical index of present names. Collisions are allowed: color aids recognition, never unique identity.
3. **Characteristic-name chips:** same stable specified algorithm but keyed by canonical metric name; neutral numeric/text values. No metric-color mapping by position in the row. Normalize names/units consistently and cache bounded unique lookups when implemented. Reuse shared DTFlow design tokens and scoped Qt styling, not a separate runtime stylesheet.

## Search, filtering and ordering

All controls apply to the **entire retained selected session** through the shared Qt-independent reader *before* 500-row page selection. No proxy-only search of currently loaded rows, and no UI regex parsing of `msg`. The offline HTML mockup demonstrates interaction semantics against just synthetic in-memory data; it does not prove reader throughput or disk identity handling.

The [shared query semantics](../architecture/diagnostic-event-schema.md#shared-query-semantics--phase-2-contract) are authoritative for presence/null, numeric/string typing, case sensitivity, literal nested-key paths, collection handling, event-prefix boundaries and deterministic sort placement. This control table maps onto that contract; it does not introduce a second query language. All consumers reuse the same validated values and versioned projections; palette assignment never affects filtering or sort keys. Labels and units must have recorded or explicitly contracted provenance, not guessed magnitude, invented duration suffixes, nonexistent percentiles or promoted severity.

| Control | Semantics |
| --- | --- |
| **Session** | Current/newest by default; select a retained session. Missing/incomplete status is explicit. |
| **Search** | Case-insensitive text over safe `msg`, full dotted `event`, `component`, and permitted structured scalar values/keys; combined with all other filters by AND. |
| **Severity** | All / DEBUG / INFO / WARNING / ERROR / CRITICAL, based on stored level, not metric threshold. |
| **Component / category** | Exact `component` and optional application/gallery category. Event family is the first dotted segment, a separate prefix predicate. The "All" option is labelled just `All`. |
| **Event** | One dropdown replacing separate family and type controls: `All events`, then a group **Event families** (`gallery.* (all)`, discovered first segments, never a hardcoded registry) and a group **Exact events** (full `event`, discovered from the selected session). The query still carries family and exact event as separate predicates. Event pill context action offers Filter exact event / Filter event family / Copy full name. |
                                                                                                                                                                                                                    
| **Time range** | Inclusive source `ts` bounds with explicit timezone; empty means unbounded. Never compare formatted display strings. |
| **Structured characteristic** | Choose discovered safe `data.*` key, operator `=`, `!=`, `<`, `<=`, `>`, `>=` (and `contains` for strings), typed value. Require key presence; typed comparisons only; incompatible types do not match, rather than coercing a string into a number. Optionally allow additional predicates later. |
| **Sort** | One plain-English dropdown inside **More filters**, mirrored by the column headers: `Datetime · newest first / oldest first`, `Severity · most severe first / least severe first`, `Event · A → Z / Z → A`, `Component · A → Z / Z → A`, `Selected metric · highest first / lowest first`. No separate Order by and Direction controls. Missing metric last with deterministic `(session_id, seq)` tie-break. Sorting affects full-session results, not just the current page. |
| **Value** (structured characteristic) | The value field is labelled just `Value`; the placeholder (`100, grid, false`) shows accepted forms. |
| **Clear** | Reset query/predicates/sort to newest-first defaults. |

The primary filter row stays on **one line** (Session, Search, Severity, Component, Event, then **More filters** at the line end; it scrolls horizontally when narrower than its minimum rather than wrapping). **More filters** holds Sort, From/To, Characteristic, Operator, Value and the preview state selector.

Visible controls may live in a compact primary toolbar plus an expandable **More filters** row rather than all competing with the table at once. Clearly indicate active filters and the **snapshot** match count; differentiate empty session, no matches and partial evidence. Preserve the full `event` in filter values even if the pill is visually truncated.

**Follow and Refresh (live behaviour, demonstrated with synthetic arrivals):**

- **Follow on:** newest-first, the table stays pinned to the top and new records are inserted above. Scrolling down by more than a few pixels, opening details, selecting rows or using a nondefault sort switches Follow off; filter changes retain the current Follow choice; it never pulls the viewport back. The Follow button turns accent blue while on.
- **Follow off:** new records are held back and the list does not move. **Refresh** shows the count (`Refresh (3 new)`, accent border). Clicking Refresh adds the held records above and adjusts the scroll offset so the row the user was reading stays at the same position.
- Turning Follow on flushes held records and jumps to the top. Never steal selection.
- The static HTML demo emits a synthetic record about every 3 seconds only to show this behaviour; it does not claim real new messages.

For native implementation, keep only bounded pending metadata/cursors while Follow is off, not every arriving record. Refresh count must describe matching arrivals against an explicit query snapshot, with incomplete evidence disclosed; refreshing under a non-time sort inserts results at their sorted positions rather than promising they always appear above. Restore the inspected `(session_id, seq)` anchor and its pixel offset where still present. The reader/projection budgets and cancellation rules remain binding. Natively the count is one cancellable background reader scan at most every three seconds: a record is new when it lies beyond the shown snapshot's size for its file identity, or sits in a file that is new or has a lower rotation age than recorded (renames keep the identity and only raise the age, so a lower age means a reused inode), and it matches the shown query; only the count is kept. Each count rereads the retained session; restricting it to arrival byte ranges belongs with the #106 byte budgets. An unavailable count falls back to `Refresh (new)`; incomplete evidence adds `· incomplete` even when zero arrivals match (`Refresh (0 new · incomplete)` with a visible accented pending treatment); only a complete zero-match count leaves plain `Refresh`. Outside-click dismissal is a non-consuming filter on the Logs widgets outside the pane, active only while the pane is shown; it collapses after the release is handled, so the table does not move under an in-progress click, and ignores presses on the pane, popups, the splitter handle, scroll bars and the row-action column.

## Inspector, actions and states

The **Details** row action opens a **read-only details pane** (not a row click). Layout is a vertical split: the selected row is scrolled to the top of the table (under the sticky header, with about two rows visible) and the pane fills the remaining window height below it, scrolling on its own. The pane header has the event name, one meta line (`LEVEL · component · seq · UTC ts`) and a **collapse chevron** (there is no second Close, because the panel already has one). Clicking anywhere outside the pane collapses it; Details/Copy raw on another row switches to that record. It shows actual level text, full UTC time/timezone, exact full event/component, `seq`/verified session, sanitized `msg`, *all* semantic-order characteristics, nested JSON, `ctx`, `error`/traceback and `agg` where genuinely present. JSON/raw copy retains record identity; raw JSONL copy is not reordered to match presentation. A `+N more` marker is never an indication of lost diagnostics.

**Header (top right):** Refresh, Follow and a red **Close panel** icon button (×, DTFlow danger tokens, tooltip and accessible name "Close logs panel"); there is no Back button. **Footer:** `Copy visible rows`, then the file actions in a **Save / export** menu (Copy selected / raw JSONL, Export matching CSV / JSONL, Open raw file, Open folder), then `Reset filters and sort`. Save and export actions never sit in the header.

Retain Copy selected / visible / raw JSONL, Export matching CSV / JSONL, Open raw file and Open folder. Exports apply matching filters to the whole retained snapshot, are bounded/cancellable, preserve shared privacy rules, and never silently claim complete evidence. No delete-log control.

JSONL exports follow the [derived excerpt/manifest contract](../architecture/diagnostic-event-schema.md#canonical-sessions-versus-derived-jsonl-excerpts), not canonical session discovery. Describe source evidence status separately from export completion; preserve original sequence numbers without treating intentional selection gaps as lost source records. Raw clipboard excerpts are not independently certified sessions.

## App shell integration

Logs is a panel inside the normal DTFlow shell: brand and navigation stay in the header, the status bar stays at the bottom. It opens from a small **square (20×20, 3 px radius) list icon button at the bottom right of the status bar, immediately to the right of the Help button**; the button shows the selection-navy state while Logs is open. The panel covers the workspace area. It closes with its red × button, the status bar icon again or Esc. The mockup mounts the Logs page in the shell (`DTFlow App Shell with Logs`); Help is drawn as a plain `?` because the design system has no help icon.

Native keyboard behavior must preserve shared overlay/editor ownership: Escape dismisses an active Logs menu/inspector before closing the workspace, and must not bypass an active shared confirmation or editor. Closing Logs restores the previous page/shell/rail and Images export-card placement without cancelling ongoing work. Painted row actions need keyboard equivalents and accessible names; outside-click dismissal must not consume text-selection/copy gestures or activate another row action twice.

## Tokens and design-system alignment

The Logs content study reuses DTFlow tokens (`colors`, `typography`, `spacing`) plus the proposed additions below in [tokens/logs.css](tokens/logs.css). The accepted native palette is present in runtime `tokens.json`, scoped shared QSS and `DESIGN.md`; the CSS remains a documentation reference and is never loaded as a second native theme. Generated bundle wrappers embed styling, so they must be refreshed deliberately and may drift from production.

| New token | Use |
| --- | --- |
| `--color-log-info` (`#8a99a6`) | INFO severity stripe (DEBUG, WARNING and ERROR reuse `--color-disabled`, `--color-warning`, `--color-error`) |
| `--color-log-row-hover` | Table row hover |
| `--color-log-code-surface` | Raw JSON block |
| `--color-log-popup-surface` | Event context menu |
| `--color-log-notice-surface` | Incomplete-session notice (border reuses `--color-status-warning-border`) |
| `--color-log-pill-1-bg` … `-8-fg` | Eight muted background/text pairs for event pills and characteristic chips, chosen by the stable hash |
| `--size-log-row-action`, `--size-log-table-min` | 28 px row action button, 700 px minimum table width |

Other choices: the native header has no separate Logs title (the shared navigation already sits there and a title read as a fifth tab); row text, labels and chips use `--font-size-small`; monospace (`--font-family-mono`) is only for timestamps and raw JSON; the header bar uses `--color-section-header`, separators `--color-section-border`, controls the button/input tokens, selected rows `--color-selection` with `--color-selection-border`, Follow on `--color-accent`, and the Close panel button the danger tokens. Dropdown lists use the input surface, `--color-selection-hover` for the hovered option and `--color-selection` for the selected one (web mockup uses customizable select; Qt draws its own popup, so verify contrast there). Open question: the eight pill pairs include amber/red/green tints, which the design system otherwise reserves for real status; a neutral or single-blue treatment may be preferable.

The real `tools/ui/render_states.py` must define synthetic **empty**, **mixed-severity/populated**, **selected error/details**, and **incomplete/reader error** states when Phase 3 builds the native Designer UI. The HTML mockup has matching preview states but is neither a Qt render nor a substitute for Windows/DPI smoke checks. Follow [DESIGN.md](../../DESIGN.md), [design system](README.md), [Qt model/view guide](../guides/qt-model-view-patterns.md), [schema documentation](../architecture/diagnostic-event-schema.md), and the [binding PR plan/G1–G6](../plans/2026-10-19-structured-log-viewer.md).

## Source-review limits and implementation handoff

- The standalone bundle's `predicate` helper does not implement the authoritative typed-query contract: mixed-type `!=` can match, string/bool relational comparisons can run, and `contains` coerces its operand. Native filters must use the shared Phase 2 query implementation, not copy this demo helper. Search in the demo scans serialized records rather than only the agreed safe scalar fields; its aggregate display projection is also illustrative, not the shared schema/projection authority.
- The demo stores synthetic arrivals in growing `entries`/`pending` arrays and renders/sorts its whole synthetic dataset. That is acceptable only as an interaction illustration, not a scalable reader/model implementation; native keyset pagination, bounded pending metadata and identity-based reconciliation are implemented; full byte budgets remain #106 and native anchor/performance acceptance remains #107.
- The standalone, shell and component HTML files are generated self-contained bundles with embedded resources. Their copies of tokens/icons can drift from the source files and from each other; refresh the affected bundles deliberately after design-source changes. The shell and component catalog are not canonical Designer forms or production dependencies.
- The modified shared `chevron.svg`, `detail.svg` and `json.svg` retain the same drawing paths; their added C2PA metadata is provenance, not a visual icon redesign. No provenance removal or asset normalization is part of this documentation review.

**Verification:** this update used source/diff inspection only; no browser launch, render, test, lint, format, runtime, benchmark, storage-size comparison or live session validation was performed. The studies do not establish native PySide6, keyboard, accessibility, Windows/DPI or performance acceptance.

## PR #105 paging and Follow source checkpoint (2026-10-19)

The native footer has Previous page / Next page controls. Each request reads the whole retained session for filtering/sorting, but displays at most 500 projected rows; the match count is session-wide. Paging pauses Follow. Refresh keeps the current query/page cursor; explicit Follow returns to the newest first page. No page-history or full-record cache grows with navigation. Appends/pruning can change later page results; this is live retained-session browsing, not an export snapshot.

Same-query updates reconcile resident rows by `(session_id, seq)` with batched model notifications and preserve surviving selected/current identities and the paused top-row anchor. Full details remain lazy; previews come from the shared reader scan. Export completion is shown separately from query status. Offline `logs-05-paging` and `logs-06-follow-selection` definitions extend the canonical preview script; they have not been rendered or accepted on Windows.

## Whole-millisecond timing display

Numeric `*_ms` characteristics display as rounded integers in the table and readable detail chips: `117.4` displays as `117`, `118.6` as `119`, and `0.4` as `0`. Other units/types keep their existing display rules. Projection v2 changes display text only: raw JSON details/copy/exports, recorded source timestamps and numeric sorting/filtering retain full precision. `logs-07-whole-milliseconds` defines the corresponding offline table/detail state; it is not a native Windows acceptance claim.

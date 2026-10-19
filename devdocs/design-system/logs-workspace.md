# Logs workspace — compact event and characteristic explorer

**Status:** reviewed design direction for [draft PR #105](../plans/2026-10-19-structured-log-viewer.md), not yet a PySide6 workspace. [Open the standalone offline HTML mockup](../mockups/dtflow_logs_workspace.html). The HTML is a **synthetic interaction study**, never a substitute for Qt Designer or the canonical `tools/ui/render_states.py` previews. The [compact JSONL schema](../architecture/diagnostic-event-schema.md) is storage authority; this is only a view/query contract.

## Final compact row geometry

**Severity stripe | Date / Time | Event | Characteristics**. Three content columns, plus a very narrow non-sortable severity strip. No separate Source, Level, Value or Summary columns.

- **Severity:** 3–4 px indicator showing the **recorded** level only. DEBUG muted gray, INFO quiet neutral, WARNING amber, ERROR/CRITICAL red. Do not make INFO green or promote a DEBUG stall to WARNING. Tooltips, accessible row names, details and severity filter spell out the level; color is never the sole encoding.
- **Date / Time:** independent fixed-width column, date on first line, source time `HH:mm:ss.SSS` on second, tabular digits. Show full UTC `ts` and selected display timezone on hover/details. Date may be compressed by a day separator only if the full date stays accessible and exact source-time sorting is preserved.
- **Event:** separate fixed-width column, **one muted colored pill for the entire dotted event identifier** (`gallery.query_summary`, `gallery.gui_gap`, etc.). No split prefix/suffix, underline, wrapping, or per-segment styling. Truncate only visually with ellipsis and full-name tooltip/details; clicks/filters always use the original identifier.
- **Characteristics:** all remaining width for **multiple** compact colored metric-name chips followed by neutral, unit-bearing values. Semantically order keys, allow no more than two visual lines in each row, and show `+N more` for undisplayed characteristics; expose all in the selectable details inspector. Example names `wall_elapsed_ms`, `sql_wall_ms`, `sql_cpu_ms`, `wall_total_ms`, `mounted`, `mode`, `query_busy`. Do not synthesize unmeasured p95 or choose a single privileged metric. Sanitized `msg` may appear in details, as tooltip, or as a labeled characteristic only when there is room and it is not duplicated.

The event name is on **exactly one line** and stays aligned vertically across rows. Use fixed-height compact rows (roughly 46–54 logical px, final Qt sizing through Designer/QSS), no arbitrary content-driven expansion, per-cell widgets or HTML-in-Qt. A simple painted delegate or model presentation role can draw the strip, full-event pill and key chips efficiently. Keep selected row under DTFlow's existing muted-blue selection colors without losing chip contrast.

### Stable semantic key order — not input JSON order

The **same key always retains its relative order** against other keys within all records, irrespective of field insertion order, other visible metrics, sorting or session filters. Use a versioned **display-only** ranking applied in the shared projection path for compact rows, inspector and human-oriented CSV projections (raw JSONL exports/copy remain byte-faithful). Within each category, the named sequence is explicit; unknown keys sort last by normalized lowercase key with raw-key tie-break.

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
2. **Full event pill:** deterministic palette index from normalized **complete** dotted event name (e.g. stable BLAKE2 digest modulo a small ordered theme-derived muted palette). Do not depend on list position, filtering, session, Python's salted `hash()`, or alphabetical index of present names. Collisions are allowed: color aids recognition, never unique identity.
3. **Characteristic-name chips:** same algorithm but keyed by canonical metric name; neutral numeric/text values. No metric-color mapping by position in the row. Normalize names/units consistently and cache bounded unique lookups when implemented. Reuse shared DTFlow design tokens and scoped Qt styling, not a separate runtime stylesheet.

## Search, filtering and ordering

All controls apply to the **entire retained selected session** through the shared Qt-independent reader *before* 500-row page selection. No proxy-only search of currently loaded rows, and no UI regex parsing of `msg`. The offline HTML mockup demonstrates interaction semantics against just synthetic in-memory data; it does not prove reader throughput or disk identity handling.

| Control | Semantics |
| --- | --- |
| **Session** | Current/newest by default; select a retained session. Missing/incomplete status is explicit. |
| **Search** | Case-insensitive text over safe `msg`, full dotted `event`, `component`, and permitted structured scalar values/keys; combined with all other filters by AND. |
| **Severity** | All / DEBUG / INFO / WARNING / ERROR / CRITICAL, based on stored level, not metric threshold. |
| **Component / category** | Exact `component` and optional application/gallery category. Event family is the first dotted segment, a separate prefix predicate. |
| **Event family** | All or discovered first-segment prefix, e.g. `gallery.*`; never a hardcoded registry. |
| **Event type** | All or exact full `event`; options are discovered from the selected session and constrained by family. Event pill context action may offer Filter exact event / Filter family / Copy full name. |
| **Time range** | Inclusive source `ts` bounds with explicit timezone; empty means unbounded. Never compare formatted display strings. |
| **Structured characteristic** | Choose discovered safe `data.*` key, operator `=`, `!=`, `<`, `<=`, `>`, `>=` (and `contains` for strings), typed value. Require key presence; typed comparisons only; incompatible types do not match, rather than coercing a string into a number. Optionally allow additional predicates later. |
| **Sort** | Source time (newest/oldest), Event, Component, Severity, and chosen numeric metric when available; ascending/descending toggle. Missing metric last with deterministic `(session_id, seq)` tie-break. Sorting affects full-session results, not just the current page. |
| **Clear** | Reset query/predicates/sort to newest-first defaults. |

Visible controls may live in a compact primary toolbar plus an expandable **More filters** row rather than all competing with the table at once. Clearly indicate active filters and the **snapshot** match count; differentiate empty session, no matches and partial evidence. Preserve the full `event` in filter values even if the pill is visually truncated.

**Follow:** defaults on for current session, polls only while visible. Selecting/inspecting older evidence, changing filters or using a nondefault sort pauses viewport jumping. Explicit Follow restores newest-first behavior; never steal selection, and never claim new messages exist in the static demo.

## Inspector, actions and states

Selecting a record opens a **read-only details pane**, showing actual level text, full UTC time/timezone, exact full event/component, `seq`/verified session, sanitized `msg`, *all* semantic-order characteristics, nested JSON, `ctx`, `error`/traceback and `agg` where genuinely present. JSON/raw copy retains record identity; raw JSONL copy is not reordered to match presentation. A `+N more` marker is never an indication of lost diagnostics.

Retain Refresh, Back, Follow, Copy selected / visible / raw JSONL, Export matching CSV / JSONL, Open raw file and Open folder. Exports apply matching filters to the whole retained snapshot, are bounded/cancellable, preserve shared privacy rules, and never silently claim complete evidence. No delete-log control.

The real `tools/ui/render_states.py` must define synthetic **empty**, **mixed-severity/populated**, **selected error/details**, and **incomplete/reader error** states when Phase 3 builds the native Designer UI. The HTML mockup has matching preview states but is neither a Qt render nor a substitute for Windows/DPI smoke checks. Follow [DESIGN.md](../../DESIGN.md), [design system](README.md), [Qt model/view guide](../guides/qt-model-view-patterns.md), [schema documentation](../architecture/diagnostic-event-schema.md), and the [binding PR plan/G1–G6](../plans/2026-10-19-structured-log-viewer.md).

**Verification:** UX/documentation proposal only. No native PySide6 implementation, Qt screenshot, benchmark, storage-size comparison, or live session validation.

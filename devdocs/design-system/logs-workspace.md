# Logs workspace — compact table and inspection design

**Status:** approved design direction for [draft PR #105](../plans/2026-10-19-structured-log-viewer.md), **not yet a native Qt implementation**. [Open the standalone offline concept](../mockups/dtflow_logs_workspace.html). The HTML is review material, not a replacement for the canonical Qt Designer form or `tools/ui/render_states.py`. The [compact JSONL v1 contract](../architecture/diagnostic-event-schema.md) remains storage authority; this document defines only **presentation**.

## Design goal

Make log browsing **dense, readable and useful for diagnostics**. The compact JSONL shape is intentionally not a table schema: each row displays the most useful projection and the selected record retains full sanitized evidence.

- One `QTableView` with a custom `QAbstractTableModel`, no per-row widgets or HTML inside Qt. Fixed-height, single-line rows; long text is elided rather than enlarging rows. A persistent selection/detail region may expand independently of row height.
- **Visual columns:** narrow severity indicator | **Time** | **Source** | **Event / Details** | **Value**. The level indicator is **not** a full text column. `Event / Details` is one flexible Qt table cell with a prominent shortened event name and, where available, a one-line subdued summary; implement with efficient painting/delegate or an equivalent model role, not child widgets.
- Use the existing design tokens and global table styles. No new theme, global stylesheet overrides, hardcoded palette in Qt code or ornamental colored rows. Selection stays the normal muted-blue selection treatment.

### Column mapping

| Column | Compact display | Full meaning / edge cases |
| --- | --- | --- |
| Severity edge | 3–4 logical-pixel colored stripe inside a narrow indicator column | DEBUG neutral muted, INFO neutral/low-emphasis, WARNING amber, ERROR/CRITICAL red. INFO **must not** imply success/green. Severity level remains accessible via tooltip, model accessibility/accessible description and the Details pane; color alone cannot carry status. No badge is required per row. |
| Time | `HH:mm:ss.SSS`, tabular digits | From source `ts` (UTC, displayed in chosen session/view timezone consistently); date, timezone and complete original UTC source timestamp accessible in details/tooltip. Header sorting uses full timestamp and stable `(session_id,seq)` tie-break, not display string. |
| Source | Short, readable `component`, e.g. `gallery`, `backend` | The short label must be an unambiguous projection of the stored component, not an invented provider or event category. Expose full original component if abbreviated; keep component filtering independent of event. |
| Event / Details | Last `event` segment emphasized, plus one ellipsized summary underneath if meaningful | Tooltip/details show complete dotted event. Summary prefers sanitized `msg`; if absent, derive a concise display-only explanation from a **small, explicitly safe projection** of `data`. Never parse old log strings or reinterpret the data contract. For unknown events with no `msg`, display the event identifier alone; never pretend an unknown field is a successful outcome. |
| Value | Right-aligned primary metric with unit, e.g. `117.4 ms`, `24 items` | Only from typed numeric `data` or `agg` fields using a **reviewed small projection for known events**; unknown event -> em dash (—), not an arbitrary number. Display `0` only if explicitly recorded; never invent a metric or aggregate percentiles. Full measurements remain in Details. |

**Responsive behavior:** optimize for a Windows desktop workspace, not a mobile table. Summary/Details receives remaining width; Source, Time and Value remain readable. At narrower supported widths, reduce flexible summary truncation before discarding the event identity or horizontal metric unit. Avoid a secondary full Level column merely for accessibility.

## Search and filters

The compact chrome contains current/newest **Session**, search, severity and component/event category filters, follow state, a small match count, and commands within the existing window. The initial UI need not expose a full query-language editor.

- Search matches sanitized messages and safe structured values; filters act across the **whole retained session**, before paging. Underlying reader supports typed predicates (`data.elapsed_ms >= 100`, `data.mode = grid`), event-prefix matching (`gallery.*`), source `ts` ranges and severity; these are reader contracts, not necessarily all visible controls on day one.
- A concise count is a **snapshot match count**, not a claim that every record is in Qt memory. Distinguish no session, empty session, no matches, loading, pending live session, incomplete/corrupt/rotated session and failed reader.
- Level/status counts may be shown with tiny colored markers **and text numbers**, but must not become a second column or make ordinary INFO rows look like successes.
- Default order newest first. Sorting header/query changes pause Follow; so does selecting and inspecting an older row or manual scrolling. Explicit Follow resumes newest. Do not steal selection/scroll position with incoming records.

## Details inspector and commands

Selecting a row opens or updates a **read-only expandable details pane** inside the Logs workspace, not a modal window. Show complete `event`, severity **as text**, full source timestamp/timezone, `component`, `seq`/session, sanitized `msg`, typed `data`, optional `ctx`, error traceback and `agg` metadata. Use selectable monospaced formatting for JSON/traceback. Retain full safe error content only if the accepted JSONL record contains it; show missing/oversized/rotated evidence explicitly.

Keep Refresh, Back, Follow, Copy selected, Copy visible, Copy raw JSONL, Export matching CSV/JSONL, Open raw file and Open folder in the available action chrome/menu; do not remove documented capabilities just to reduce table width. Raw file opening is an explicit local action; clipboard/export uses the shared sanitizer. No delete/clear-log command.

## Offline review states (canonical Phase 3 render definitions)

The eventual real PySide6 Designer workspace must add the following synthetic scenarios to `tools/ui/render_states.py` in the same implementation phase:

1. **Empty session:** complete header/filter chrome and informative empty state.
2. **Populated mixed severity:** ordinary INFO, DEBUG, WARNING and ERROR, short and long event names, missing `msg`, varied metrics, selected/hover/focus.
3. **Selected exception:** details expanded, long sanitized traceback and nested typed data, Copy actions; no per-row expansion.
4. **Reader error/incomplete:** missing startup or segment, malformed/unsupported/oversized record, visible nonmodal incomplete banner; readable rows remain browsable if possible.

A standalone browser mockup may switch among states for conceptual review; these are **not actual native renders**. Per PR owner instructions, Phase 3 defines but does not execute render states. Owner/native Windows screenshots, interaction testing, DPI and large-log responsiveness remain G6 acceptance gates.

## Example mapping

| Stored example | Row projection |
| --- | --- |
| `{"level":"DEBUG","component":"gallery","event":"gallery.query_done","data":{"elapsed_ms":38.2,"returned":20,"source":"live","error":null}}` | Muted indicator / gallery / **query_done** / “20 images returned · live” / **38.2 ms** |
| `{"level":"DEBUG","component":"gallery","event":"gallery.gui_gap","data":{"elapsed_ms":117.4,"mounted":100,"mode":"grid"}}` | Muted DEBUG indicator, **not automatic WARNING** / gallery / **gui_gap** / “Grid · 100 mounted” / **117.4 ms** |
| `{"level":"ERROR","component":"backend","event":"backend.connection_failed","msg":"Connection unavailable"}` | Red edge / backend / **connection_failed** / “Connection unavailable” / — |
| New `gallery.thumbnail.some_new_sub_event` without known metric | Appropriate level edge / gallery / **some_new_sub_event** / safe `msg` if supplied / — |

**Important:** the earlier conceptual mockup used WARNING for a 117 ms `gallery.gui_gap`. The real observed producer logs GUI-gap outliers at DEBUG; presentation must preserve the **recorded severity** rather than promote it for aesthetics. Threshold-specific interest belongs in filters/metrics, not rewritten stored levels.

## Relationship to existing authority

- [Root DESIGN.md](../../DESIGN.md) and [design system](../design-system/README.md): canonical typography, QSS/tokens, state colors, and Qt workspace layout.
- [Qt model/view guide](../guides/qt-model-view-patterns.md): background queries, bounded projections, safe row notifications, selection identity and cancellation.
- [Structured diagnostics architecture](../architecture/structured-diagnostics.md): writer/reader ownership, privacy, incomplete evidence semantics.
- [PR #105 implementation plan](../plans/2026-10-19-structured-log-viewer.md): remaining features, phase boundaries, exact acceptance gates.

**Verification status:** document and offline concept only; no Python/Qt implementation, rendered Qt screenshots, native smoke tests, schema validator run, or performance benchmarks.

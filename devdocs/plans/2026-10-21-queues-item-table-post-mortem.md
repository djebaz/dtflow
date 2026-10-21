# Queues #127: item table and inspector, implementation post-mortem

**Date:** 2026-10-21 · **Scope:** Qt translation of the owner HTML mockup, plus the data, thumbnail and navigation work it needed.
**Status of evidence:** the owner ran `render_states.py --category queues` to completion (59 states, 46.9 s, including `queues-60` to `queues-64`) and a real app run, and reported the layout good. The author reviewed the `queues-60`, `61`, `63` and `64` images and found no defects. The author could not run Qt or Python. Everything below marked *unverified* has not been executed by the author. No tests were added or run (plan gate, M3).

## 1. What was built

| Area | File | Change |
| --- | --- | --- |
| Data | `persistence/queues.py` | `_read` items gain `prompt`, `negative_prompt`, `model`, `loras`, `steps`, `guidance`, `sampler`, `width`, `height`, `duration`. Request fields come from `request_json`; `duration` from `item_settled` events (`backend_elapsed_seconds`). Absent values stay `None`/empty. |
| Presenter | `ui/queue_presenter.py` | `ItemRow` gains those fields plus `duration_text`, `steps_text`, `guidance_text` (one decimal, `5.0`), `sampler_text`, `dimensions_text`, `operation_text`, `outcome_line`, `gallery_identity`. Adds `gauge_scale()`, `lora_fraction()`, `display_name()`. `queue_subtitle` uses `display_name`. |
| Model | `ui/queue_model.py` | Seven columns (Preview, Prompt, Model, LoRAs, Parameters, Status, duration). `ROW_ROLE`, read-only tooltips, `gauge_scale`, `thumbnails` hook. |
| Paint | `ui/queue_item_delegate.py` (new) | Painted cells, `wrapped_lines()` (ellipsis only on an overflowing last line; soft breaks after `_ - .`), LoRA rows with fixed gauge and weight columns, wrapped sampler, status text kept in colour on selection, monospace right-aligned duration, `ItemTableHeader` (painted stopwatch, right-aligned), `ItemTableLayout` (Prompt about 30%). |
| Thumbnails | `ui/queue_thumbnails.py` (new) | `QueueThumbnails`: asynchronous, LRU, one decode per output through the gallery's `_image` at 600 px with the gallery's disk cache (record looked up from `attempt:index`). Tile is a centred square crop at 128 px. `HoverPreview`: frameless, mouse-transparent window, 350 ms delay, up to 440 px, screen-clamped. |
| Page | `ui/queue_page.py`, `forms/queue_page.ui` | Installs delegate, header, 76 px rows. New inspector card **Additional information** (Seed, Image dimensions, Operation, Outcome) and **Open in Images** button with `set_images_hook()`. |
| Navigation | `ui/gallery_page.py`, `ui/main_window.py` | `GalleryPage.open_output(identity)` loads the output's page through the anchored query and opens Detail. `MainWindow._open_output_in_images` switches to Images and calls it. |
| Style | `dtflow_dark.qss` | `QTableView#queueItemsView` rules and `QLabel#queueThumbPreview`. No new tokens. |
| Renders | `tools/ui/render_states.py` | `render_item_table_states` (`queues-60` to `queues-64`; `-64` captures the hover preview window), `header_end_visible`, fixture fields for the older `present()` states, asserts for columns, tooltips (extension-less names), scale, header class, hook identity, plus the real-`submit` handoff and `thumbnails.clear()` the async fixture needs. |
| Mockup | `Queues Item Table.dc.html` (design project) | Shell, table, inspector and hover overlays used as the visual reference; standalone HTML export. |

## 2. Decisions made during review (owner-directed)

1. Real status words and colours: succeeded green; running, no result, cancelled amber; failed and save failed red; pending neutral gray. Colour stays on the selected row.
2. At most three LoRA lines; the whole LoRAs cell is the hover target; no `…` chip and no "+N" cue.
3. Gauge scale follows recorded weights (largest weight rounded up to 0.5, never below 1.0), shared by all rows.
4. Hover preview is the gallery's 600 px thumbnail, not a smaller copy; tile is a centred square crop of it.
5. Weight-file extensions are hidden in the table, the queue list and the LoRA hover; full names remain in the inspector and header.
6. Duration header is an icon only (painted, since the glyph U+23F1 is missing from some fonts).
7. The "Selected item" heading ("Generation A") was dropped from the inspector.

## 3. Problems found and how they were fixed

| Symptom | Cause | Fix |
| --- | --- | --- |
| Duration column off-screen | Column widths exceeded the viewport | Narrower fixed columns; render asserts the last column is visible |
| Blank prompt/model in older renders | Old fixtures lacked new fields | Fixtures updated |
| `item-000` truncated IDs | 8-character shortening of fixture IDs | Hex-style fixture IDs |
| Stopwatch left-aligned | Qt draws header decoration at the left | `ItemTableHeader` paints it; model supplies no decoration |
| Dot instead of stopwatch | Font lacks U+23F1 | Painted icon |
| Mid-word model wrap | Wrap-anywhere | Soft breaks after `_ - .` |
| Tiny LoRA gauges | Fixed 3.0 scale | Adaptive shared scale |
| Preview decoded the full original | Rich-text `<img>` tooltip, synchronous | Bounded asynchronous overlay |
| Table thumbnails decoded on the GUI thread | Synchronous paint-time decode | Asynchronous loader with LRU |
| Hover preview too small | Copied the 220 px mockup box | Up to 440 px, frame hugs the image |
| Render timeout waiting for the first thumbnail | The fixture function patches `runner.submit` to hold every job; held jobs never finish, so they kept all 3 decode slots occupied | Fixture hands the loader the real `submit` and calls `clear()`; `QueueThumbnails.clear()` now also releases decode slots (late results are still ignored by epoch) |
| Render assert failed on the LoRA hover | The assert looked for `.safetensors` names after extensions were hidden | Assert checks the extension-less names and that no extension appears |
| Render assert failed once in `queues-52` (Items caption overlapped the LoRA label) | Label heights are fitted on a deferred pass and the first check measured too early; passed on re-run | Check now refits and waits 150 ms before measuring, as the second check already did. Treat as a timing flake unless it recurs |

## 4. Unverified, risks and open items

- `queue_thumbnails.py` imports the private `_image` and `THUMBNAIL_SIDE` from `gallery_page.py`; an import cycle was not checked.
- Disk-cache reuse assumes `persistence.gallery.get_record` plus `services.gallery.item_from_record` yield the item `_image` needs. Not exercised against real data.
- `gallery_identity` is `attempt_id:index` in the recorded artifact list (including `metadata.json`), derived from `persistence/gallery.py`; not verified on real data. A filtered-out output leaves the Images view unchanged with no message.
- `request_json` key `negative_prompt` is assumed; the other keys were read from `queues.py`.
- Cosmetic: with extensions hidden, a short model name such as `Z-Image-Turbo_v2` still wraps after the underscore in the Model column (about 15 px too wide). Widening the column would take space from Prompt or Parameters; left as is.
- Tooltips are Qt tooltips: no gauge in the LoRA hover, and the prompt tooltip is not scrollable (the plan's known gap).
- Below about 1000 px the table scrolls horizontally; Parameters, Status and duration need scrolling. Not changed.
- Import order in `queue_page.py` (new `queue_thumbnails` import before `queue_presenter`) will fail isort.
- No Windows/DPI check, no performance measurement with 100 to 200 rows, no tests (`test_qt_queue.py` and `test_queue_presenter.py` likely assert the old six columns).
- Inspector grouping differs from the HTML: Outcome sits in Additional information, and the three actions sit in Selected item. The HTML has a separate Technical details card.

## 5. Changes to make to the repo docs

### `devdocs/plans/2026-10-21-queues-item-table-render-first.md`
1. **Differences table, LoRA row:** replace "every configured LoRA inline" with "first three inline; whole-cell hover lists all; no `…`, no +N cue".
2. **LoRA weight scale row:** replace `weight / 3` with the adaptive shared scale (section 2, item 3).
3. **Prompt/LoRA width row:** state the Qt target (Prompt about 30%, LoRAs the largest remaining share) or revert the code to equal 2fr tracks. Pick one.
4. **Thumbnail row:** 64 × 64 centred crop of the gallery's 600 px cached thumbnail; hover preview is the same 600 px file, up to 440 px; remove the 220 × 220 figure.
5. **Hover row:** hover overlays are a Qt tooltip (prompt, LoRAs) and a mouse-transparent window (preview); note the scroll gap for long prompts.
6. **M1 checklist:** tick the Qt translation items and the stress fixtures; name `queues-60` to `queues-64`. Keep "owner visual approval" open until the owner records it.
7. **M2 audit items:** record the confirmed field sources (section 1, Data), the `attempt:index` identity rule, the duration source, the `open_output` wiring, and the thumbnail path through the gallery cache. Keep the unverified items from section 4.
8. **Evidence log:** add a row for the render runs (58 states) and the real-app check, stating Linux/offscreen and a single real run, not Windows/DPI acceptance.
9. **Decisions:** add section 2 as an owner-decisions list, including hidden extensions and the status colour on selection.
10. **Scope note:** the tests-before-approval gate still applies; list the two test files likely to need edits.

### `devdocs/mockups/2026-10-21-queues-item-table-text-preview.md`
1. **Table:** show the first three LoRAs only; row B shows three plus a note that all four appear on hover.
2. **Names:** display model and LoRA names without `.ckpt` / `.safetensors`; mention the full name in hover.
3. **Geometry table:** LoRAs row (three lines, 40 px gauge and 30 px weight in Qt), Preview row (crop), Status row (semibold, colour kept on selection), Duration row (icon header, monospace values).
4. **Hover table:** LoRA hover lists all, without gauges in Qt; preview up to 440 px from the 600 px thumbnail; prompt tooltip not scrollable.
5. **Inspector table:** match the built grouping (Selected item with actions; Additional information with Seed, Image dimensions, Operation, Outcome), or move Outcome to its own card; `Open in Images` goes to Images Detail for that output.
6. **Superseded section:** add that "all LoRAs inline" is superseded again by the 2026-10-21 owner decision.

### Docs deferred by the plan until functional approval (M3)
Not read by the author; verify before editing.
- `DESIGN.md`: items-table anatomy, painted-cell rules, status colours, gauge scale, hover preview, hidden extensions.
- `devdocs/guides/queues.md`: columns, inspector fields, Open in Images behaviour and its filtered-out case.
- Architecture / source-of-truth docs: queue item projection fields; the thumbnail path through the gallery cache; `GalleryPage.open_output`.
- `devdocs/current_dev_status.md`, `devdocs/releases/unreleased.md`, and the stale checklist in #130.
- `tokens.json`: unchanged; no snapshot update needed.

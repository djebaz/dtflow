# Responsive gallery page transitions

Date: 2026-10-18
Status: implementation experiment; native Windows acceptance pending
Base: main at 7cf49c4ca942e84556c3db3fad4b35adbfdb564a

## User scope

Prioritise ordinary 100-image page switches and appending 100 to 200 images. Keep the existing 100-image batches, 300-record committed window, 200-record query cap, six thumbnail decoders, selection identities, anchors, snapshot authority and current visuals. Large-library stress is secondary.

## Measured baseline

A provider-free Qt 6.12 Linux/X11 presentation probe, with already prepared 256x192 synthetic images, reproduced approximately 108 ms median synchronous mutation for append 100 to 200 and 110 ms for a simple 100-image page replacement. This excludes database reads and image decoding. GUI timer gaps were approximately 144-147 ms median. An isolated model/view prototype avoided per-image widgets and kept observed timer gaps near 10 ms, but is not feature/visual parity and is not production acceptance. PR #91 records related research; issue #88 still owns intermittent Detail-stall diagnosis.

## Bounded preparation experiment

1. Keep the committed page visible while preparing new card/filmstrip/cell objects on the GUI thread in short, timed slices, using a parented timer. Do not build Qt widgets in a worker thread.
2. Stage objects separately from committed identity maps. Reuse existing widgets for retained identities. Prepare data and styles before committing the page so the final switch avoids synchronous creation of 100 cards and hidden filmstrip entries.
3. Bound staging to the requested window. Cancel stale preparation by generation, clean staged objects on replacement and shutdown, and preserve normal error, pending refresh and source-authority handling.
4. Keep synchronous fixture rendering as an explicit fallback, while real query completion enters the bounded preparation path. Add public-seam coverage for old-page retention, completion, cancellation and stable IDs, plus a named offline presentation state.
5. Measure actual ordinary 100-to-100 and 100-to-200 transitions before/after, including queued input and GUI heartbeat gaps, not only database or Python proxy timings. If final layout/paint still stalls, do not claim success; use the measured model/view alternative for the next scoped change.

## Verification

Target 4 ms preparation slices; record actual slice maxima and final commit/layout delays. Provisional local objectives are final mutation below one nominal 16 ms frame and no repeated ordinary-transition GUI gaps above 33 ms. These are development targets for this environment, not Windows guarantees or a product FPS claim. Run focused lifecycle/navigation tests, Ruff and relevant offline UI renders. No provider calls, inference, training, uploads, original-file mutation or billable operations. Keep Windows/packaged/physical-trackpad acceptance separate.

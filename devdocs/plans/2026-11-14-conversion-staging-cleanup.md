# Conversion staging ownership and cleanup (#451)

## Design before implementation

- Keep this Qt-free and worker-owned; no provider calls, changes to conversion arithmetic, retained index or upload consent.
- Each new `job-<uuid>` directory has a stable owner file under `.owners/`. Acquire its nonblocking OS lock before creating the directory and retain it through conversion, retention and final cleanup.
- Never remove owner files or reuse job IDs. Cleanup acquires the same stable owner lock; age and the store lock are not authority. No global gate or new scheduler is needed.
- Owner files sit outside job directories so Windows can remove job contents while ownership remains locked. Tiny ownership metadata is kept deliberately to avoid lock-inode replacement races; sweeps enumerate job directories, not the owner-file directory.
- Check staging ancestors, root, owner and job entries for links/reparse points. Only flat, regular, single-link files in the exact owned job are eligible; unknown, nested and legacy unowned jobs stay untouched.
- Bound each sweep by directory entries, candidate jobs, files per job, bytes and elapsed-time checks. Run it at the beginning of explicit Prepare conversion work, including conversion reuse; no startup scan, recursive tree walk or content reads.
- Emit shared structured diagnostics with cleanup counts, bytes, bounded/skipped/error outcomes and no names, paths or contents. A failed best-effort sweep does not prevent conversion; unsafe staging cannot receive a new job.

## Work

- [x] Implement ownership, conservative bounded reclamation and final cleanup.
- [x] Integrate with the existing Prepare worker.
- [x] Review locking, interruption windows, path guards and Windows handle lifetime by source inspection.
- [x] Synchronize architecture, runtime layout, diagnostics and release/status docs.
- [x] Run only permitted static checks; prepare the draft PR with unchanged issue ACs.

## Evidence boundaries

No tests, fixtures, renders or provider operations are authorized. AC-1 active-job preservation and AC-2 interruption/restart reclamation require owner smoke; source review cannot establish those runtime outcomes. Legacy jobs without this ownership protocol are intentionally not reclaimed automatically.

Readiness and focused Ruff lint/format checks passed. The design was simplified at the owner's request: stable per-job ownership metadata replaces a proposed global gate; no new scheduler, startup service, age heuristic or recursive sweep was added. Sweep budgets are 128 root entries, 16 candidates, 32 files/job, 8 GiB and a soft 0.5-second check between jobs. Unknown/oversized entries can remain indefinitely; this is bounded opportunistic reclamation, not a promise to drain every backlog. Stable owner markers deliberately accumulate one byte each plus filesystem overhead.

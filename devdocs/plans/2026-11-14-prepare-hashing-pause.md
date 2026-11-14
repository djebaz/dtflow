# Prepare hashing-pause callback (#437)

## Scope and implementation

- Base: current main `f5dde4fd`. Fix only the missing callback argument and editor dismissal wording; #436 negotiation remains separate.
- Pass the owning editor and the existing `done_bytes=None` inspection-end signal when hashing pauses during Prepare. Leave the pause tuple, resume path and Stop admission rules unchanged.
- Label Prepare's shared editor footer `Close editor`, with a tooltip distinguishing dismissal from `Stop after current file`. Reuse the canonical shared button and reset text, accessibility name and tooltip on every editor mount so later editors keep their default Cancel label.

## Evidence

- Source inspection confirms the paused branch supplies all five arguments and retains editor ownership validation. Runtime AC-1/AC-2 require CI or owner evidence; they are not accepted from this inspection alone.
- AC-3 wording is source-reviewed only, not visually verified. No tests, fixtures, renders, conversion, uploads, generation or inference are added or run by the development agent.
- Static environment readiness, imports, lint/format and diff checks are recorded in the PR after execution.

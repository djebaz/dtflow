# Images grid checkbox hit target

## Scope

Keep each grid selection checkbox’s existing 28 × 28 px housing, 14 px indicator, styling and (9, 9) image offset. Expand only its invisible click target to 56 × 56 px: twice the housing width and height. List, Detail and image-open behavior outside that target stay unchanged.

## Delivery

- [x] Share a presentation-only QCheckBox subclass between the production grid and offline sample grid. Draw the existing styled control inside the larger native widget and accept clicks throughout that widget, preserving checked-state signals, keyboard behavior and accessibility.
- [x] Add an offline focused-state capture definition and document the change. No test, render, app launch or validation command run.
- [ ] Owner: verify selecting/deselecting inside the extra area, image-open outside it, checked/hover/focus visuals, and List/Detail unchanged on native Windows/DPI.

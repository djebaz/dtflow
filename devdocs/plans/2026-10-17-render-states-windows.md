# Render-state script fixes for Windows (2026-10-17)

Owner report: `uv run python tools/ui/render_states.py --out tmp/ui-renders` on
Windows stopped with an `AssertionError` after `39-images-trash-mode.png`, and
several renders showed square glyphs.

## 1. Removal-menu position assertion

`render_mapped_gallery_states` checks the Trash/Delete popup position after
`39-images-trash-mode.png`. The offscreen screen is 800×800 while the
rendered window is 1600×1000, so the split control lies outside every screen.
`_RemovalModeMenu.showEvent` clamps only when `QGuiApplication.screenAt()` finds
the anchor on a screen and otherwise keeps the control's left edge; the script
clamped unconditionally against `menu.screen()`, so the expectation and the
production rule disagreed (reproduced on Linux offscreen as well: menu at
x=1441, expected 658).

Fix: make the script compute the expectation with the same screen lookup as
the production menu. Production positioning is unchanged.

## 2. Missing fonts on Windows offscreen

Qt's offscreen plugin on Windows looks for fonts in PySide6's empty `lib/fonts`
directory unless `QT_QPA_FONTDIR` is set, so text renders as squares. The guide
already documents the variable, but the script did not set it.

Fix: when the script runs on Windows with the offscreen platform and
`QT_QPA_FONTDIR` is unset, default it to `%WINDIR%\Fonts` before Qt starts. An
explicit value still wins. Update the desktop guide accordingly.

## Verification

No new tests, per owner convention; CI runs the suite. Ruff format/lint before
committing. Renders remain layout evidence only, not Windows or DPI acceptance.

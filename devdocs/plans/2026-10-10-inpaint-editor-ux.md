# Inpaint editor UX (Edit image)

Date: 2026-10-10. Part of [the smart-mask inpainting plan](2026-10-10-smart-mask-inpainting.md), slice 6 (inline editor). Draft PR: [#64](https://github.com/djebaz/dtflow/pull/64).

This is the design target for the Generate editor, agreed with the owner while iterating on the interactive mockup [`devdocs/mockups/dtflow_inpaint.html`](../mockups/dtflow_inpaint.html) (open it in a browser; the toolbar at the top switches demo states). It describes intended behavior, not shipped behavior. The mockup simulates detection, generation and the scene image; it is not DTFlow code and proves nothing about runtime, models or provider support. Follow `DESIGN.md` tokens and the AGENTS.md overlay/confirmation rules when implementing.

## Principles

- Reuse the current Generate layout: left rail (Source image, Backend, Parameters), centre (prompt and canvas), right rail. Add no new top-level tab or window.
- One mode control: the header switch **Text to image | Inpaint** next to the prompt label. The button in the prompt header reads **Edit image** in Inpaint mode, to remind the user this is an image-to-image operation.
- The mask is a single shared mask. Select and Paint are two additive ways to edit it, not two masks.
- Feedback is live and honest: tint, thumbnail and coverage update on every edit before anything is generated. Progress, results and notices use the shared bottom overlay, never inline banners.
- Overlay colour and opacity are fixed (accent blue at 45%, with a legend); they are not user settings.

## Layout

### Left rail

- **Source image**: file name, size/orientation/colour-space line (for example "2048 × 1536 · EXIF orientation applied · RGB"), **Choose image…** and **From Images**.
- **Backend**: Provider, Generation (model-type filter), Base model, Checkpoint, and a capability line. The line is green "Inpainting confirmed for this checkpoint." or an amber explanation when exact evidence is missing (see Edit image states).
- **Parameters**: Seed, Steps, Guidance, **Strength** (default 0.75), Sampler, and the backend control **Keep everything outside the mask** (default on; off returns the raw provider result). Width and height follow the source image, fitted to 64 px.

### Centre

- Prompt header: label, mode switch, **Edit image** (primary). Prompt keeps its 52 px resting height; negative prompt below.
- Canvas stage on the dark canvas colour:
  - top-left toolbar: **Undo**, **Redo** (icon-only turning arrows with tooltip and accessible name);
  - top-right: view switch **Source | Mask | Result** (independent of zoom);
  - bottom-left: **Fit**, **100%**, **−**, **+**;
  - bottom-right: a context hint that follows the active tool.
- A one-line status below the canvas explains why Edit image is disabled, or shows the live-preview note, or the saved-result summary.

### Right rail

- **Mask** card: a **Select | Paint** toggle, the active mode's controls, and a collapsed **Advanced** row (Grow, Edge feather; both default 0).
  - Select mode: **Find people**, the people list, Body / Clothes / Person layers, the body filter (All pixels / Skin only / Clothing only) and **Expand to nearby region** (off by default).
  - Paint mode replaces the Select controls with: **Brush**, **Erase**, **Rectangle**, **Circle**, and a brush-size slider with its value.
- **Live mask** card, top to bottom: mask thumbnail, "Covers N% of the image" (plus the delta of the last edit), **Clear mask**, **Load mask…** / **Save mask…**, then the **Edits** history list.

## Select and Paint modes

- Exactly one mode is active; the other mode's controls are not shown. Switching modes never discards the mask. Edits from both modes accumulate.
- Select: hovering previews the person under the pointer; click adds that person, Alt-click removes, Shift-click also adds. The searchable people list does the same by keyboard. Layers and the body filter apply to the next selection action and never rewrite earlier edits. Suggestions can be wrong; refine in Paint.
- Paint: drag to paint with Brush, Erase, Rectangle or Circle. Rectangle and Circle drag out a box (Circle is the ellipse inscribed in it) with a dashed preview; a drag of 3 px or less is ignored. Alt inverts the action (paint becomes erase, add becomes remove). Strokes may extend beyond people.
- Undo/Redo step through edits from both modes; each edit has a line in the Edits list (for example "Add circle", "Remove rectangle", "Clear mask").
- Pan with Space + drag; zoom with Fit, 100%, − and + (Ctrl-wheel as in the plan).

## Views

- **Source**: the prepared source with the blue tint over the mask.
- **Mask**: black and white, exactly what the provider receives (white = changes, black = stays). A caption states this.
- **Result**: the saved result with the same tint over the changed area. Before a result exists, the view shows the source.

## Edit image states

Edit image is disabled with the reason in the status line (and as its tooltip) when:

- no source image: "Edit image is unavailable: choose a source image first.";
- empty mask: "…the mask is empty. Click a person or paint the area to change.";
- the checkpoint has no confirmed inpainting evidence for the selected backend. Inventory presence is not evidence; refusal happens before authentication or submission, per the plan.

With no source, the canvas shows the empty state "Choose a source image — then click a person or paint the area to change." Find people is also disabled.

## Shared bottom overlay

All feedback uses the one thin bottom overlay with a 6-second draining lifetime line for success and amber rows, pausing on hover/focus:

- **Find people**: running rows "Step n of 3" (loading person model, segmenting people, separating clothes) with real progress and **Stop after current**, which reports "Stopped after the current step. The mask is unchanged." Success: "Found 2 people. Click one to select."
- **Edit image**: running with **Stop waiting**; cancelling must say honestly that the provider may still finish. Success: "Saved. 1 image with reproducibility metadata. Original pixels kept outside the mask."
- **Clear mask**: the action button becomes a red **Confirm clear** in place and keeps its width; **Cancel** lives in the overlay ("Removes the whole mask. Undo stays available."). Escape or any scope change discards the pending confirmation. No expiry timer.
- Hiding the overlay or changing tabs never stops running work.

## Open decisions and not in the mockup

- "Use result as source" on the Result view is not designed.
- Live preview during editing is an approximation (low-resolution thumbnail at editing speed); the exact mask is built from the source at generation time.
- Grow and Feather are shown but not simulated.
- Mask file import/export polarity and size handling follow the plan's mask-file contract; the buttons here only fix their position.
- DT+ off-machine and credit disclosure uses the existing asset-note/tooltip pattern; the mockup adds no separate warning.

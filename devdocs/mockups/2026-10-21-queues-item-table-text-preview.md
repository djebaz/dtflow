# Queues #127 — text-only table pre-mockup

**Fictional sample for design review, 2026-10-21.** Not a Qt screenshot, real queue, actual inference result, or implemented UI. Every value below is illustrative.

## Central table: seven columns

Model and LoRAs repeat on **every** row, even when identical. The first three LoRAs appear one per line, with name, weight gauge, and numeric value. Bars are schematic only; the final valid gauge scale must follow actual recorded weights.

| Preview | Prompt | Model | LoRAs | Parameters | Status | Backend duration |
| :--- | :--- | :--- | :--- | :--- | :--- | ---: |
| `[thumb A]` | Portrait of a traveler in a blue coat at dawn…<br>Soft light, mountains in the distance… | **Example Model A** | **PortraitStyle** `███░░░░░░░` 0.90<br>**FilmLight** `████░░░░░░` 1.20 | **Steps 28**<br>CFG 4.5 · Sampler Euler | **Succeeded** | 31.2 s |
| `[thumb B]` | Portrait of a traveler in a blue coat at dawn…<br>Soft light, mountains in the distance… | **Example Model A** | **PortraitStyle** `████░░░░░░` 1.20<br>**FilmLight** `██░░░░░░░░` 0.60<br>**SkinDetail** `█████░░░░░` 1.50<br>**…** (1 more) | **Steps 32**<br>CFG 5.0 · Sampler Euler | **Succeeded** | 36.8 s |
| `[no image]` | A red house beside a mountain lake…<br>Cloud reflections on the water… | **Example Model B** | **Landscape** `███░░░░░░░` 0.90<br>**CloudMood** `████░░░░░░` 1.20<br>**Texture** `██░░░░░░░░` 0.60 | **Steps 24**<br>CFG 4.0 · Sampler DPM++ | **Running** | — |
| `[no image]` | Studio photo of a ceramic cup…<br>Warm light against a pale background… | **Example Model B** | — | **Steps 24**<br>CFG 4.0 · Sampler DPM++ | **Failed** | — |
| `[no image]` | An illustrated town square after rainfall…<br>Street lamps reflected in cobblestones… | **Example Model A** | **PortraitStyle** `███░░░░░░░` 0.90 | **Steps 28**<br>CFG 4.5 · Sampler Euler | **Queued** | — |

**Hierarchy:** model and steps are visually emphasized; each LoRA has a readable name and weight value; CFG and sampler are secondary-sized. Status is a **single text label**, with DTFlow semantic colors added later in Qt. No per-item step counter, progress bar, filename, seed or image dimensions in the table. Duration is **observed backend duration only**; `—` means unavailable, not zero.

**Prompt width:** initially around one third of the table; display **positive prompt only** and clip after two lines. This Markdown's line breaks are illustrative, not a pixel-accurate wrapping test.

## Hover overlays (read-only and nonblocking)

- **Thumbnail:** on hover, show a larger aspect-fit image when there is a real saved output; otherwise retain a neutral placeholder.
- **Prompt:** on hover, display **complete positive and negative prompts**, with scrolling if needed. Example for row B:
  - **Positive:** Portrait of a traveler in a blue coat at dawn, soft light, mountains in the distance, realistic fabric details, shallow depth of field.
  - **Negative:** Blurry, distorted hands, duplicate faces, text, watermark.
- **LoRA ellipsis `…`:** if more than three are configured, hovering the icon shows **all** LoRAs, including those already visible:

| LoRA (row B) | Weight | Illustrative gauge |
| --- | ---: | --- |
| PortraitStyle | 1.20 | `████░░░░░░` |
| FilmLight | 0.60 | `██░░░░░░░░` |
| SkinDetail | 1.50 | `█████░░░░░` |
| BackgroundBokeh | 0.90 | `███░░░░░░░` |

Gauges represent configured strengths, **not task progress**. The overlay and ellipsis are hover-only, with no Copy button or click action. They must **never intercept a row click**: selecting a row always updates the inspector when it is visible.

## Right inspector — complementary only

Keep the existing **creation mini-log**. The selected-item section may show **seed, dimensions, backend/operation, stable technical identifiers, outcome/uncertainty evidence**, and eligible actions such as **Open in Images**, **Open folder**, and **Retry item**. **No large image preview, no filename field.**

At narrow window widths the shared right inspector hides normally. **Do not move its secondary information into a fallback panel.** The central table remains the primary information surface.

## Scope and approval

This is a Markdown-only hierarchy exercise for [draft PR #150](https://github.com/djebaz/dtflow/pull/150) and [issue #127](https://github.com/djebaz/dtflow/issues/127). It does not authorize implementation, tests, renders, general documentation changes, generation, or billable work. A real offline Qt render-state mockup and explicit owner visual approval come next.

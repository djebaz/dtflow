# Queues #127 — text-only table pre-mockup

**Fictional sample for design review, 2026-10-21.** Not a Qt screenshot, real queue, actual inference result, or implemented UI. Every value below is illustrative.

## Central table: seven columns

Model and LoRAs repeat on **every** row, even when identical. The synthetic `ultra_realistic_characters_style_detail_v12.safetensors` models the owner's reported **55-character LoRA filename, including its extension**; it is not the user's real filename. The first three LoRAs appear one per line, with name, weight gauge, and numeric value. Bars are schematic only; the final valid gauge scale must follow actual recorded weights.

| Preview | Prompt | Model | LoRAs | Parameters | Status | Backend duration |
| :--- | :--- | :--- | :--- | :--- | :--- | ---: |
| `[thumb A]` | Portrait of a traveler in a blue coat at dawn…<br>Soft light, mountains in the distance… | **Example Model A** | **PortraitStyle** `███░░░░░░░` 0.90<br>**FilmLight** `████░░░░░░` 1.20 | **Steps 28**<br>CFG 4.5 · Sampler Euler | **Succeeded** | 31.2 s |
| `[thumb B]` | Portrait of a traveler in a blue coat at dawn…<br>Soft light, mountains in the distance… | **Example Model A** | **PortraitStyle** `████░░░░░░` 1.20<br>**FilmLight** `██░░░░░░░░` 0.60<br>**ultra_realistic_characters_style_detail_v12.safetensors** `█████░░░░░` 1.50<br>**…** (1 more) | **Steps 32**<br>CFG 5.0 · Sampler Euler | **Succeeded** | 36.8 s |
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
| ultra_realistic_characters_style_detail_v12.safetensors | 1.50 | `█████░░░░░` |
| BackgroundBokeh | 0.90 | `███░░░░░░░` |

**Long-name layout requirement:** the 55-character name must not force the LoRAs column wider or displace its weight value/gauge. In the Qt mockup, compare single-line elision versus wrapping; preserve the full name via a read-only hover tooltip when the inline label is shortened. This is a design test case, not a preapproved truncation style.

Gauges represent configured strengths, **not task progress**. The overlay and ellipsis are hover-only, with no Copy button or click action. They must **never intercept a row click**: selecting a row always updates the inspector when it is visible.

## Right inspector — fictional selected-item example (row B)

This is a second **Markdown example table** for the right inspector. The inspector shows complementary evidence; the central table already gives the thumbnail, prompt, model, LoRAs, steps, CFG, sampler, status and backend duration. Values below are entirely fictional.

| Section | Field / action | Example display | Treatment |
| --- | --- | --- | --- |
| Selected item | Selected generation | **Generation B** | Small selected-item heading; no duplicate image |
| Additional information | Seed | `295625551` | Monospace, selectable |
| Additional information | Image dimensions | **1024 × 1024** | Normal metadata |
| Additional information | Backend | Draw Things (example) | Normal metadata |
| Additional information | Operation | Text to image | Normal metadata |
| Evidence | Saved result | **1 saved image** | Only when actual output is verified |
| Technical details | Item ID | `demo-item-b-002` | Secondary, selectable |
| Technical details | Attempt ID | `demo-attempt-b-01` | Secondary, selectable |
| Technical details | Outcome / uncertainty | No error recorded | Explicit when evidence supports it |
| Actions | Open in Images | **Open in Images** | Enabled only for a verified gallery image |
| Actions | Open folder | **Open folder** | Only if a valid accessible folder exists |
| Actions | Retry item | **Retry item** | Only when allowed by existing retry/credit safeguards |

### Creation mini-log — retain existing placement

| Time (example) | Event |
| --- | --- |
| 19:42:01 | Item accepted into queue |
| 19:42:02 | Attempt started on example backend |
| 19:42:39 | Attempt completed; result saved |

The log uses **made-up timestamps**, not observed timing evidence. Preserve the real existing creation/activity mini-log in the inspector rather than replacing it with a preview. For failed or uncertain items, show the actual recorded outcome and only actions that are eligible; do not imply every item has a saved image.

**No large image preview and no filename field** are proposed in this inspector.

At narrow window widths the shared right inspector hides normally. **Do not move its secondary information into a fallback panel.** The central table remains the primary information surface.

## Scope and approval

This is a Markdown-only hierarchy exercise for [draft PR #150](https://github.com/djebaz/dtflow/pull/150) and [issue #127](https://github.com/djebaz/dtflow/issues/127). It does not authorize implementation, tests, renders, general documentation changes, generation, or billable work. A real offline Qt render-state mockup and explicit owner visual approval come next.

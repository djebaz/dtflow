# Queues #127 — text-only table pre-mockup

**Fictional sample for design review, 2026-10-21.** Not a Qt screenshot, real queue, actual inference result, or implemented UI. Every value below is illustrative.

## Central table: seven columns

Model and LoRAs repeat on **every** row, even when identical. The synthetic `ultra_realistic_characters_style_detail_v12.safetensors` models the owner's reported **55-character LoRA filename, including its extension**; it is not the user's real filename. The first three LoRAs appear one per line, with name, weight gauge, and numeric value. Bars are schematic only; the final valid gauge scale must follow actual recorded weights. **Model and LoRA names are illustrative checkpoint/adapter filenames, not friendly display aliases or verified provider inventory.**

| Preview | Prompt | Model | LoRAs | Parameters | Status | ⏱ |
| :--- | :--- | :--- | :--- | :--- | :--- | ---: |
| `[thumb A]` | Portrait of a traveler in a blue coat at dawn,<br>with soft rim lighting and distant mountains… | **Z-Image-Turbo_v2.ckpt** | **portrait_style_v1.safetensors** `███░░░░░░░` 0.90<br>**cinematic_rim_light_v3.safetensors** `████░░░░░░` 1.20 | **Steps 28**<br>CFG 4.5<br>Sampler DPM++ 2M SDE Karras | **Succeeded** | 31.2 s |
| `[thumb B]` | Portrait of a traveler in a blue coat at dawn,<br>with soft rim lighting and distant mountains… | **Z-Image-Turbo_v2.ckpt** | **portrait_style_v1.safetensors** `████░░░░░░` 1.20<br>**cinematic_rim_light_v3.safetensors** `██░░░░░░░░` 0.60<br>**ultra_realistic_characters_style_detail_v12.safetensors** `█████░░░░░` 1.50<br>**…** (1 more) | **Steps 32**<br>CFG 5.0<br>Sampler DPM++ 3M SDE Exponential | **Succeeded** | 36.8 s |
| `[no image]` | A red house beside a mountain lake, with<br>cloud reflections spreading across the water… | **PhotoReal_XL-v2.ckpt** | **landscape_style_v2.safetensors** `███░░░░░░░` 0.90<br>**cloud_mood_v4.safetensors** `████░░░░░░` 1.20<br>**texture_detail_v1.safetensors** `██░░░░░░░░` 0.60 | **Steps 24**<br>CFG 4.0<br>Sampler DPM++ 2M SDE Karras | **Running** | — |
| `[no image]` | Studio photo of a ceramic cup on a table,<br>with warm light against a pale background… | **PhotoReal_XL-v2.ckpt** | — | **Steps 24**<br>CFG 4.0<br>Sampler DPM++ 2M SDE Karras | **Failed** | — |
| `[no image]` | An illustrated old town square after rainfall,<br>with lamps reflected in wet cobblestones… | **Z-Image-Turbo_v2.ckpt** | **portrait_style_v1.safetensors** `███░░░░░░░` 0.90 | **Steps 28**<br>CFG 4.5<br>Sampler DPM++ 3M SDE Exponential | **Queued** | — |

**Hierarchy:** model checkpoint filenames and steps are visually emphasized; each LoRA uses its actual adapter filename (extension, hyphens and underscores retained) with readable strength gauge and numeric value; CFG and sampler are secondary-sized. Long sampler names may need their own wrapped line within Parameters. Status is a **single text label**, with DTFlow semantic colors added later in Qt. No per-item step counter, progress bar, filename, seed or image dimensions in the table. Duration is **observed backend duration only**; `—` means unavailable, not zero.

**Prompt wrapping and width:** target about **30–33%** of the total central table width in the actual Qt layout. Show the **beginning of one continuous positive prompt** with natural wrapping across **up to two visual lines**. **No ellipsis on the first line.** Use a single final ellipsis **only if the text overflows the second line**; do not truncate by a fixed number of characters. The explicit `<br>` tags in this text specimen approximate wrapping, but Markdown cannot enforce width, font metrics or real two-line truncation.

**Duration header:** `⏱` is a **stopwatch-icon placeholder**, not a shipped icon. In Qt use a matching design-system icon, header tooltip/accessibility label **“Backend duration”**, and the narrowest practical width that still fits the recorded values (for example `31.2 s`, `125.8 s`) with adequate padding and right alignment. **Do not reserve width for the text label**.

## Hover overlays (read-only and nonblocking)

- **Thumbnail:** on hover, show a larger aspect-fit image when there is a real saved output; otherwise retain a neutral placeholder.
- **Prompt:** on hover, display **complete positive and negative prompts**, with scrolling if needed. Example for row B:
  - **Positive:** Portrait of a traveler in a blue coat at dawn, soft light, mountains in the distance, realistic fabric details, shallow depth of field.
  - **Negative:** Blurry, distorted hands, duplicate faces, text, watermark.
- **LoRA ellipsis `…`:** if more than three are configured, hovering the icon shows **all** LoRAs, including those already visible:

| LoRA (row B) | Weight | Illustrative gauge |
| --- | ---: | --- |
| portrait_style_v1.safetensors | 1.20 | `████░░░░░░` |
| cinematic_rim_light_v3.safetensors | 0.60 | `██░░░░░░░░` |
| ultra_realistic_characters_style_detail_v12.safetensors | 1.50 | `█████░░░░░` |
| background_bokeh_v2.safetensors | 0.90 | `███░░░░░░░` |

**Long-name layout requirement:** the 55-character filename and other underscore/hyphen-heavy filenames must not force the LoRAs column wider or displace the numeric weight/gauge. In the Qt mockup, compare single-line elision versus wrapping; preserve the full name via a read-only hover tooltip when the inline label is shortened. This is a design test case, not a preapproved truncation style.

Gauges represent configured strengths, **not task progress**. The overlay and ellipsis are hover-only, with no Copy button or click action. They must **never intercept a row click**: selecting a row always updates the inspector when it is visible.

## Right inspector — fictional selected-item example (row B)

**Owner review:** the inspector information is acceptable for this Markdown checkpoint; finer spacing and arrangement remain for the actual Qt mockup.

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

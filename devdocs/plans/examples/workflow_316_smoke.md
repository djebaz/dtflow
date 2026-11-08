# Smoke test: Load image / Load image (as mask) / Upscale image (#316)

Two example workflows next to this file. Both import without errors; their checkpoint and file names are placeholders
that you replace (Run is blocked until you do).

- `workflow_316_load_image_mask_inpaint_smoke.json`: Load image + Load image (as mask, channel `red`) → Inpaint → Save.
- `workflow_316_load_image_scale_img2img_smoke.json`: Load image → Upscale image (lanczos, 1024 × auto) → Img2img → Save.

Use a real backend (local or DT+). Pick a photo whose sides are **not** multiples of 64 (for example 1000 × 750).

## Inpaint with a mask file

1. Make a grayscale mask the **same size** as the photo: white where it should be repainted, black elsewhere (a PNG
   with a white blob is enough).
2. Open the first JSON in Workflows. Choose a checkpoint on the Inpaint node.
3. Select **Load image**, **Choose file…**, pick the photo. Select **Load image (as mask)**, pick the mask.
4. Expect: the file name in each node changes to `<name>-<16 hex>.<ext>`, and the file now exists in
   `.dtflow/workflows/inputs/`. Picking the same file again keeps the same name and adds no second copy.
5. Save the workflow and open the JSON: `widgets_values` of the Load nodes hold the file name only, no path.
6. Run. Expect: both Load steps succeed first with a thumbnail on Load image, then Inpaint runs with the stage
   "Fitting 1000x750 into 1024x768" (your sizes), then Save succeeds. The result keeps your photo's size (1000 × 750,
   no pixel trimmed) and the repainted area matches the white area of your mask.
7. Run with a mask of a different size: Inpaint should fail with "Mask dimensions must match the EXIF-oriented source image."

## Resize with Upscale image

1. Open the second JSON, choose a checkpoint on Img2img, choose the photo in Load image.
2. Run. Expect: Upscale image output is 1024 wide, height from the aspect ratio (1000 × 750 → 1024 × 768), so Img2img
   needs no extra resize. Change Width to 900 and Height to 0, run again: Img2img now shows "Fitting" (900 × 675 is not
   a multiple of 64).
3. Try Crop `center` with Width 1024 and Height 1024: the photo is filled and trimmed evenly to a square.

## Failure paths

- Leave a Load node on its `PLACEHOLDER_…` name and Run: the run ends at once with "… is not in this computer's DTFlow
  inputs folder; choose the file again in the node." and nothing is submitted.
- Choose a non-image or a `.gif`: the picker rejects it with a notice and the node keeps its old value.

## Mask resized with the photo

In the first workflow add Upscale image after Load image (1024 × auto) and, for the mask, Convert mask to image →
Upscale image (same settings) → Convert image to mask (red) between Load image (as mask) and Inpaint. Expect: no size
mismatch, and the mask lines up with the scaled photo. Without the mask chain the Inpaint fails with
"The mask is WxH but its image is W'xH'; they must match…".

Also try: a Load image of an opaque photo with its MASK wired to Inpaint ends the run at once ("its MASK is empty…").

Report anything that differs.

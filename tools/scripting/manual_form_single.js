//@api-1.0
// Simple MANUAL form smoke: one request, all parameters together, no generation.
// Import into Generate > Scripts, select fake, choose Setup, then Run Script.
// Image/folder selections are optional. No follow-up form, uploads or file writes.

const blue = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAD0lEQVR4nGNgyNgBQhAKAByuBIF5tDm0AAAAAElFTkSuQmCC";
const green = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAD0lEQVR4nGNguLEBhCAUACguBiH9jnnoAAAAAElFTkSuQmCC";

const params = requestFromUser("Simple smoke · all parameters", "Run Script", function () {
  return [
    this.plainText("All settings are below, in one form. Nothing will be generated."),
    this.section("Text", "Edit either field and pick or type a style.", [
      this.textField("A lighthouse at sunset", "Prompt", false),
      this.textField("First line\nSecond line", "Notes", true, 80),
      this.comboBox("Photo", ["Photo", "Illustration", "Sketch"]),
      this.markdown("**One form only.** File selections are optional; this script makes **zero generation calls**.")
    ]),
    this.section("Sampling", "Try the sliders, both range thumbs and the size controls.", [
      this.slider(0.75, this.slider.percent, 0, 1, "Strength"),
      this.slider(5.5, this.slider.fractional(1), 1, 20, "Guidance"),
      this.slider(2, this.slider.scale, 1, 4, "Scale"),
      this.doubleSlider([8, 28], this.slider.fractional(0), 1, 40, "Step range"),
      this.size(512, 768, 64, 2048)
    ]),
    this.section("Choices", "These answers are a boolean and two selection indices.", [
      this.switch(false, "Enable option"),
      this.segmented(0, ["Square", "Portrait", "Landscape"]),
      this.menu(0, ["Sequential", "Shuffled", "Random"])
    ]),
    this.section("Files", "Optional. Use small image files; a folder becomes an owned image copy.", [
      this.imageField("One image", false),
      this.imageField("Multiple images", true),
      this.directory()
    ]),
    this.section("Images", "Blue is display-only. Choose blue (0) or green (1) in the grid.", [
      this.image(blue, 64, false),
      this.image([blue, green], 64, true)
    ])
  ];
});

// Only inspect counts/types: never log entered text, image contents or folder paths.
if (!Array.isArray(params) || params.length !== 5) {
  throw new Error("Simple form smoke: expected five section answer arrays.");
}
const counts = [3, 5, 3, 3, 1];
for (let i = 0; i < counts.length; i++) {
  if (!Array.isArray(params[i]) || params[i].length !== counts[i]) {
    throw new Error("Simple form smoke: unexpected answer shape in section " + (i + 1));
  }
}
console.log("Simple form smoke complete: one form answered; no generation calls.");

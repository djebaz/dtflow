//@api-1.0
// DTFlow #196: MANUAL form smoke only. Never calls pipeline.run(), uploads, or writes files.
// Import this file into Generate > Scripts, select fake, and press Start.
// Forms: all components -> dynamic follow-up -> differently shaped request -> summary.
// Use small image inputs; directory selection imports a bounded owned image copy.
// Repeat with the advanced switch off, restart for answer recall, and try Stop/close
// while a form or native picker is open. Also inspect at 800x680. Nothing auto-submits.
// This is a custom owner-invoked script, not an automated suite or upstream conformance proof.

const BLUE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAD0lEQVR4nGNgyNgBQhAKAByuBIF5tDm0AAAAAElFTkSuQmCC";
const GREEN = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAD0lEQVR4nGNguLEBhCAUACguBiH9jnnoAAAAAElFTkSuQmCC";
const LONG_TEXT = Array(12).fill(
  "Long editable text: blue hour, emerald forest, amber light. Unicode: café — 日本語."
).join("\n");

function expect(ok, message) {
  if (!ok) throw new Error("FORM SMOKE: " + message);
}

function array(value, length, label) {
  expect(Array.isArray(value) && value.length === length,
    label + " should be an array of " + length + " values");
}

function numeric(value, low, high, label) {
  expect(typeof value === "number" && Number.isFinite(value) && value >= low && value <= high,
    label + " should be a finite number within its bounds");
}

function index(value, count, label) {
  expect(Number.isInteger(value) && value >= 0 && value < count,
    label + " should be an integer selection index, not a caption or source string");
}

const first = requestFromUser("1/4 · All 14 form components", "Check these answers", function () {
  return [
    this.plainText("Manual smoke only: zero generation calls. Edit values before continuing. File selections are optional."),
    this.section("Text, combo and safe Markdown", "Long captions should wrap; inputs should remain usable at 800 × 680.", [
      this.markdown(
        "## Safe Markdown\n\n**Bold**, *italic*, `code` and a list:\n\n- First item\n- Second item\n\n" +
        "| Column A | Column B |\n| --- | --- |\n| One | Two |\n\n" +
        "Literal HTML must not execute: <b>not injected HTML</b><script>throw 'unsafe';</script>\n\n" +
        "[Blocked navigation](https://example.invalid/do-not-open)\n\n" +
        "![Blocked external image](https://example.invalid/do-not-fetch.png)"
      ),
      this.textField("Edit me — café", "Single-line text: replace this value with your own text", false),
      this.textField(LONG_TEXT, "Multiline text: check selection, scrolling, line breaks and Unicode", true, 180),
      this.comboBox("Photographic, natural light, 35mm film grain, shallow depth of field", [
        "Photographic, natural light, 35mm film grain, shallow depth of field", "Illustration", "Line art"
      ])
    ]),
    this.section("Sliders, double slider and size", "Percent values return their underlying number, not a formatted percent string.", [
      this.slider(0.75, this.slider.percent, 0, 1, "Percent slider: 0.75 displays as 75%"),
      this.slider(12, this.slider.fractional(0), 1, 40, "Integer-precision slider"),
      this.slider(1.125, this.slider.fractional(3), -2, 4, "Fractional slider: three decimal places"),
      this.slider(2, this.slider.scale, 1, 4, "Scale slider: × value"),
      this.doubleSlider([0.2, 0.8], this.slider.percent, 0, 1, "Dual-thumb range: Space changes thumb; arrows move it"),
      this.size(512, 768, 64, 2048)
    ]),
    this.section("Switch, segmented and menu", "The switch changes the NEXT form after Continue; the script builder is not live-reexecuted.", [
      this.switch(true, "Show advanced controls in the next request"),
      this.segmented(1, ["Square", "Portrait", "Landscape"]),
      this.menu(0, ["Sequential", "Shuffled", "Random"])
    ]),
    this.section("Native image and directory pickers", "Try Choose, cancel the native picker, reselect and Clear. The original folder is never exposed to JS.", [
      this.imageField("One image: select a small PNG, JPEG or WebP", false),
      this.imageField("Multiple images: select two small files together", true),
      this.directory()
    ]),
    this.section("Image display and selectable image grid", "The display-only image adds no answer. The grid adds one integer index: blue 0, green 1.", [
      this.image(BLUE, 80, false),
      this.image([BLUE, GREEN], 80, true)
    ]),
    this.section("Nested and empty sections", "Section answers must stay nested, including an empty section's empty array.", [
      this.section("Inner section", "Change both controls, then check that the values survive polling.", [
        this.switch(false, "Nested switch"),
        this.textField("Nested text", "Nested text field", false)
      ]),
      this.section("Empty section", "This section intentionally has no fields.", [])
    ])
  ];
});

array(first, 6, "Top-level section answers");
array(first[0], 3, "Text section (Markdown does not add an answer)");
expect(typeof first[0][0] === "string", "Single-line text should be a string");
expect(typeof first[0][1] === "string", "Multiline text should be a string");
expect(typeof first[0][2] === "string", "Combo box should return its text");
array(first[1], 6, "Numeric section");
numeric(first[1][0], 0, 1, "Percent slider");
numeric(first[1][1], 1, 40, "Integer-precision slider");
numeric(first[1][2], -2, 4, "Fractional slider");
numeric(first[1][3], 1, 4, "Scale slider");
array(first[1][4], 2, "Double slider");
numeric(first[1][4][0], 0, 1, "Range minimum");
numeric(first[1][4][1], 0, 1, "Range maximum");
expect(first[1][4][0] <= first[1][4][1], "Range minimum must not exceed maximum");
array(first[1][5], 2, "Size [width, height]");
first[1][5].forEach(function (value) {
  expect(Number.isInteger(value) && value >= 64 && value <= 2048, "Size dimensions should be bounded integers");
});
array(first[2], 3, "Choice section");
expect(typeof first[2][0] === "boolean", "Switch should return a boolean");
index(first[2][1], 3, "Segmented control");
index(first[2][2], 3, "Menu");
array(first[3], 3, "Picker section");
expect(Array.isArray(first[3][0]) && first[3][0].length <= 1, "Single image field should return zero or one source");
expect(Array.isArray(first[3][1]) && first[3][1].length <= 16, "Multi-image field should return an ordered source array");
first[3].slice(0, 2).forEach(function (images) {
  images.forEach(function (source) {
    expect(typeof source === "string" && source.indexOf("data:image/") === 0,
      "Picked images must become embedded raster sources, not external paths or internal capability tokens");
  });
});
expect(typeof first[3][2] === "string", "Directory should return a string");
if (first[3][2]) {
  expect(first[3][2].replace(/\\/g, "/").indexOf("/script_files/") >= 0,
    "Directory should be the owned per-run copy, not the external selected folder");
}
array(first[4], 1, "Image section (display-only image does not add an answer)");
index(first[4][0], 2, "Selectable image grid");
array(first[5], 2, "Outer nested section");
array(first[5][0], 2, "Inner section");
expect(typeof first[5][0][0] === "boolean" && typeof first[5][0][1] === "string", "Nested values lost their types");
array(first[5][1], 0, "Empty section");

const advanced = first[2][0];
const second = requestFromUser("2/4 · Dynamic follow-up: " + (advanced ? "advanced" : "simple"), "Continue", function () {
  const fields = [
    this.plainText("This is a different request with a different field count. Your previous values must not leak into it."),
    this.textField("Fresh second-form value", "Second request text", false)
  ];
  if (advanced) {
    fields.push(this.section("Advanced controls chosen by the previous switch", "These exist only on the advanced path.", [
      this.switch(false, "New switch, initially Off"),
      this.doubleSlider([-0.5, 0.5], this.slider.fractional(2), -1, 1, "New signed range"),
      this.menu(1, ["New first option", "New second option"])
    ]));
  } else {
    fields.push(this.switch(true, "Simple path switch, initially On"));
  }
  return fields;
});
array(second, 2, "Dynamic follow-up");
expect(typeof second[0] === "string", "Second request text should be independent");
if (advanced) {
  array(second[1], 3, "Advanced section");
  expect(typeof second[1][0] === "boolean", "Advanced switch should be boolean");
  array(second[1][1], 2, "Advanced range");
  numeric(second[1][1][0], -1, 1, "Advanced minimum");
  numeric(second[1][1][1], -1, 1, "Advanced maximum");
  index(second[1][2], 2, "Advanced menu");
} else {
  expect(typeof second[1] === "boolean", "Simple-path answer should not be an old section array");
}

const third = requestFromUser("3/4 · Repeated request with top-level inputs", "Finish control checks", function () {
  return [
    this.markdown("### Repeated form\nThis request starts with top-level controls rather than a section. Try keyboard-only navigation."),
    this.menu(2, ["Zero", "One", "Two"]),
    this.switch(false, "Final independent switch"),
    this.section("One nested field", "The answer shape is [index, boolean, [text]].", [
      this.textField("Last request", "Final nested text", false)
    ])
  ];
});
array(third, 3, "Third request");
index(third[0], 3, "Top-level menu");
expect(typeof third[1] === "boolean", "Top-level switch should be boolean");
array(third[2], 1, "Final nested section");
expect(typeof third[2][0] === "string", "Final nested text should be a string");

// Deliberately show only counts and harmless selections, not text, file data or paths.
const summary = [
  "All 14 component types were presented and the returned answer shapes passed this script's checks.",
  "First form: six nested sections. Advanced follow-up: " + advanced + ".",
  "Text lengths: " + first[0][0].length + " / " + first[0][1].length + " characters.",
  "Image-field counts: " + first[3][0].length + " single / " + first[3][1].length + " multiple.",
  "Owned folder selected: " + Boolean(first[3][2]) + ". Selectable image index: " + first[4][0] + ".",
  "Zero generation/provider calls were made by this script.",
  "Visual layout, native picker behavior, Stop/close, recall and upstream conformance still require your observations."
].join("\n\n");
const last = requestFromUser("4/4 · Manual smoke summary", "Complete smoke", function () {
  return [this.plainText(summary)];
});
array(last, 0, "Display-only summary");
console.log("FORM SMOKE COMPLETE: ordered/nested answers checked; no generation calls.");

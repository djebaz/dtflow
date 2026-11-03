//@api-1.0
// DTFlow PR #217 — interactive DT+ cloud generation studio.
// Select DT+ Cloud and a compatible checkpoint in Generate before opening Scripts.
// Setup is non-billable; Run Script can submit real, potentially billable generations.
// This script performs at most two sequential text-to-image generations.
// No image-to-image, ControlNet, or arbitrary uploaded-image generation is claimed.

const base = pipeline.configuration;
const inherited = pipeline.prompts;
const defaults = inherited.prompt || "A cinematic alpine lake at blue hour, realistic photography, detailed reflections";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function safeName(value) {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-").slice(0, 45) || "image";
}
function validSize(pair) {
  assert(Array.isArray(pair) && pair.length === 2, "Expected [width, height].");
  assert(pair.every(x => Number.isInteger(x) && x >= 128 && x <= 2048), "Invalid dimensions.");
}

const setup = requestFromUser("Cloud Studio · Configure generation", "Run Script", function () {
  return [
    this.markdown("## Real cloud generation\nThis script uses the **currently selected DT+ Cloud backend and checkpoint**. Pressing **Run Script** may consume credits. Setup alone does not submit a generation. Maximum: **2 images**."),
    this.section("Creative direction", "Choose a prompt and rendering style.", [
      this.textField(defaults, "Positive prompt", true, 110),
      this.textField(inherited.negativePrompt || "blurry, low quality, artifacts", "Negative prompt", true, 70),
      this.comboBox("Photorealistic", ["Photorealistic", "Cinematic", "Illustration", "Watercolor", "3D render"]),
      this.switch(true, "Append selected style to the prompt")
    ]),
    this.section("Sampling", "Uses only configuration fields supported by DTFlow's script host.", [
      this.size(base.width, base.height, 128, 2048),
      this.slider(base.steps, this.slider.fractional(0), 1, 60, "Sampling steps"),
      this.slider(base.guidanceScale, this.slider.fractional(1), 1, 15, "Guidance scale"),
      this.textField(String(base.seed < 0 ? 123456 : base.seed), "Fixed seed (integer)", false),
      this.segmented(0, ["Keep selected sampler", "Euler A", "DPM++ 2M Karras"])
    ]),
    this.section("Run options", "A follow-up form is offered only after the first image completes.", [
      this.switch(true, "Offer an optional second generation"),
      this.menu(0, ["Keep original dimensions", "Square 512 × 512", "Portrait 512 × 768"]),
      this.doubleSlider([0.3, 0.7], this.slider.percent, 0, 1, "Style variation range (used only to guide the follow-up)")
    ])
  ];
});

assert(Array.isArray(setup) && setup.length === 3, "Unexpected Setup form shape.");
const creative = setup[0], sampling = setup[1], options = setup[2];
assert(creative.length === 4 && sampling.length === 5 && options.length === 3, "Unexpected section answers.");
validSize(sampling[0]);
const seed = Number(sampling[3].trim());
assert(Number.isSafeInteger(seed) && seed >= 0 && seed <= 2147483647, "Seed must be a nonnegative integer <= 2147483647.");
const style = creative[2].trim();
const prompt = creative[0].trim() + (creative[3] ? ", " + style.toLowerCase() + " style" : "");
assert(prompt.length > 0, "A positive prompt is required.");
const config = pipeline.configuration;
config.width = sampling[0][0];
config.height = sampling[0][1];
config.steps = sampling[1];
config.guidanceScale = sampling[2];
config.seed = seed;
if (sampling[4] === 1) config.sampler = 1;
if (sampling[4] === 2) config.sampler = 0;
if (options[1] === 1) { config.width = 512; config.height = 512; }
if (options[1] === 2) { config.width = 512; config.height = 768; }

console.log("Cloud Studio: submitting first generation; inspect the linked attempt for provider outcome.");
pipeline.run({configuration: config, prompt: prompt, negativePrompt: creative[1]});
canvas.saveImage("cloud-studio-01-" + safeName(style) + ".png", true);
console.log("Cloud Studio: first generation returned and save requested.");

if (options[0]) {
  const followup = requestFromUser("Cloud Studio · Optional second image", "Continue", function () {
    return [
      this.markdown("The first generation has completed. **Continue** submits another potentially billable cloud generation. Disable the switch to finish without another submission."),
      this.section("Second image", "Adjust the prompt and reuse the controlled seed for comparison.", [
        this.switch(false, "Generate a second image"),
        this.textField(prompt + ", alternate composition", "Second prompt", true, 90),
        this.switch(true, "Reuse the same seed"),
        this.slider(0.5, this.slider.percent, options[2][0], options[2][1], "Variation reference (informational only)")
      ])
    ];
  });
  assert(Array.isArray(followup) && followup.length === 1 && followup[0].length === 4, "Invalid follow-up answers.");
  if (followup[0][0]) {
    const next = Object.assign({}, config);
    next.seed = followup[0][2] ? seed : (seed + 1) % 2147483647;
    const nextPrompt = followup[0][1].trim();
    assert(nextPrompt.length > 0, "Second prompt cannot be empty.");
    console.log("Cloud Studio: submitting optional second generation.");
    pipeline.run({configuration: next, prompt: nextPrompt, negativePrompt: creative[1]});
    canvas.saveImage("cloud-studio-02.png", true);
    console.log("Cloud Studio: second generation returned and save requested.");
  }
}
console.log("Cloud Studio completed. Review saved outputs and durable child attempts in DTFlow.");

//@api-1.0
/**
 * Krea 2 LoRA Mixer for DTFlow PR #217.
 *
 * One native Setup form, then real generations with the currently selected
 * DTFlow backend. Set the Krea 2 checkpoint, sampler, steps, guidance and
 * prepared cloud LoRAs in Generate before starting this script.
 *
 * Only DTFlow-supported fields are changed: dimensions, seed and LoRAs.
 * DTFlow saves successful generation outputs and attempt metadata itself.
 * This script does not download models, upload LoRAs, or retry failures.
 *
 * Each generation may use DT+ Compute Units. The default four-generation
 * ceiling is intentional; the Scripts execution cap must be at least the
 * requested number of images.
 */

const LORAS = [
  { name: "YARN_Hegre", file: "yarn_hegre_lora_f16.ckpt" },
  { name: "YARN_ShowyBeauty", file: "yarn_showybeauty_lora_f16.ckpt" },
  { name: "YARN_BlackedRaw", file: "yarn_blackedraw_lora_f16.ckpt" },
  { name: "YARN_GFRevenge", file: "yarn_gfrevenge_lora_f16.ckpt" },
  { name: "YARN_Zishy", file: "yarn_zishy_lora_f16.ckpt" },
  { name: "YARN_FTV", file: "yarn_ftv_lora_f16.ckpt" },
  { name: "YARN_MetArt", file: "yarn_metart_lora_f16.ckpt" },
  { name: "YARN_SexArt", file: "yarn_sexart_lora_f16.ckpt" }
];

const MAX_IMAGES = 4;
const MAX_SEED = 4294967295;
const base = pipeline.configuration;
const initialPrompts = pipeline.prompts || {};
const activeFiles = (base.loras || []).map(function (item) { return item.file; });
const matchingFiles = LORAS.filter(function (item) { return activeFiles.indexOf(item.file) >= 0; });

function integerInput(text, name) {
  const value = String(text).trim();
  if (!/^\d+$/.test(value) || Number(value) > MAX_SEED) {
    throw new Error(name + " must be an integer between 0 and " + MAX_SEED + ".");
  }
  return Number(value);
}

function seededRandom(seed, index) {
  let state = (seed + Math.imul(index + 1, 0x9e3779b9)) >>> 0;
  return function () {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), state | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function randomMix(pool, count, totalWeight, seed, index) {
  const random = seededRandom(seed, index);
  const choices = pool.slice();
  for (let i = choices.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    const temp = choices[i];
    choices[i] = choices[j];
    choices[j] = temp;
  }
  const selected = choices.slice(0, count);
  const parts = selected.map(function () { return 0.25 + random() * 0.75; });
  const sum = parts.reduce(function (a, b) { return a + b; }, 0);
  const units = Math.round(totalWeight * 10000);
  let assigned = 0;
  return selected.map(function (item, i) {
    const weightUnits = i === selected.length - 1
      ? units - assigned
      : Math.floor(parts[i] / sum * units);
    assigned += weightUnits;
    return { file: item.file, weight: weightUnits / 10000, mode: "all" };
  });
}

// PR #217 recalls ordinary form values for this exact script version. No
// PNG markers, filesystem settings hacks or additional confirmation forms.
const answers = requestFromUser("Krea 2 · LoRA Mixer", "Run Script", function () {
  return [
    this.plainText(
      "Real generations only. DTFlow uses the checkpoint, sampler, steps and " +
      "guidance currently selected in Generate. Select and prepare the chosen " +
      "LoRAs for DT+ Cloud before running. Up to four images per run."
    ),
    this.section("Prompts", "Separate multiple prompts with |. All prompts are used in random mode; comparisons use the first.", [
      this.textField(
        String(initialPrompts.prompt || "A cinematic portrait in soft natural light"),
        "Prompt one | Prompt two", true, 120
      ),
      this.textField(String(initialPrompts.negativePrompt || ""), "Negative prompt", true, 60)
    ]),
    this.section("Mix and output", "Use Random, compare individual LoRAs, or compare unique pairs. No dry-run option.", [
      this.menu(0, ["Random mix", "Compare singles", "Compare pairs"]),
      this.slider(1, this.slider.fractional(0), 1, MAX_IMAGES, "Number of images (maximum 4)"),
      this.slider(2, this.slider.fractional(0), 1, 4, "LoRAs per image (random mode)"),
      this.slider(0.99, this.slider.fractional(2), 0.01, 2, "Total LoRA strength"),
      this.textField("42", "LoRA blend seed", false, 45),
      this.textField("422", "Image seed", false, 45),
      this.switch(false, "Increment image seed in random mode"),
      this.size(768, 1152, 128, 2048)
    ]),
    this.section("LoRA pool", "Enable only LoRAs available for this model and backend.",
      LORAS.map(function (item, i) {
        const selectedByGenerate = matchingFiles.length > 0
          ? activeFiles.indexOf(item.file) >= 0
          : i < 2;
        return this.switch(selectedByGenerate, item.name);
      }, this)
    )
  ];
});

if (!Array.isArray(answers) || answers.length !== 3 ||
    !Array.isArray(answers[0]) || answers[0].length !== 2 ||
    !Array.isArray(answers[1]) || answers[1].length !== 8 ||
    !Array.isArray(answers[2]) || answers[2].length !== LORAS.length) {
  throw new Error("Unexpected native form response. No generation started.");
}

const prompts = answers[0][0].split("|").map(function (text) { return text.trim(); })
  .filter(function (text) { return text.length > 0; });
if (prompts.length === 0) throw new Error("Enter at least one prompt.");
const negativePrompt = answers[0][1];
const mode = answers[1][0];
const requested = answers[1][1];
const perImage = answers[1][2];
const strength = answers[1][3];
const blendSeed = integerInput(answers[1][4], "LoRA blend seed");
const imageSeed = integerInput(answers[1][5], "Image seed");
const incrementSeed = answers[1][6];
const dimensions = answers[1][7];
const pool = LORAS.filter(function (_, i) { return answers[2][i] === true; });

if (!base.model) throw new Error("Choose a checkpoint in Generate before running.");
if (!Number.isInteger(mode) || mode < 0 || mode > 2 ||
    !Number.isInteger(requested) || requested < 1 || requested > MAX_IMAGES ||
    !Number.isInteger(perImage) || perImage < 1 || perImage > 4 ||
    typeof strength !== "number" || !Number.isFinite(strength) || strength < 0.01 || strength > 2 ||
    typeof incrementSeed !== "boolean") {
  throw new Error("Invalid generation settings. No generation started.");
}
if (!Array.isArray(dimensions) || dimensions.length !== 2 ||
    dimensions.some(function (value) {
      return !Number.isInteger(value) || value < 128 || value > 2048 || value % 64 !== 0;
    })) {
  throw new Error("Width and height must be multiples of 64, from 128 to 2048.");
}
if (pool.length === 0) throw new Error("Select at least one LoRA.");
if (mode === 0 && pool.length < perImage) {
  throw new Error("Select at least " + perImage + " LoRAs for random mixing.");
}
if (mode === 2 && pool.length < 2) {
  throw new Error("Select at least two LoRAs to compare pairs.");
}
if (mode === 0 && incrementSeed && imageSeed + requested - 1 > MAX_SEED) {
  throw new Error("The incremented image seed exceeds the supported limit.");
}

const combinations = [];
if (mode === 1) {
  pool.forEach(function (item) { combinations.push([item]); });
} else if (mode === 2) {
  for (let a = 0; a < pool.length - 1; a++) {
    for (let b = a + 1; b < pool.length; b++) {
      combinations.push([pool[a], pool[b]]);
    }
  }
}
const jobCount = mode === 0 ? requested : Math.min(requested, combinations.length);
console.log("Krea 2 LoRA mixer: " + ["random", "singles", "pairs"][mode] +
  "; model=" + base.model + "; jobs=" + jobCount +
  "; dimensions=" + dimensions[0] + "x" + dimensions[1] +
  "; initial image seed=" + imageSeed + "; blend seed=" + blendSeed + ".");
if (mode !== 0 && combinations.length > jobCount) {
  console.log("Comparison limited to the first " + jobCount + " of " +
    combinations.length + " combinations. No combinations are run implicitly.");
}

for (let index = 0; index < jobCount; index++) {
  const prompt = mode === 0 ? prompts[index % prompts.length] : prompts[0];
  const seed = imageSeed + (mode === 0 && incrementSeed ? index : 0);
  let loras;
  if (mode === 0) {
    loras = randomMix(pool, perImage, strength, blendSeed, index);
  } else {
    const selected = combinations[index];
    loras = selected.map(function (item) {
      return { file: item.file, weight: strength / selected.length, mode: "all" };
    });
  }

  const configuration = Object.assign({}, base, {
    width: dimensions[0],
    height: dimensions[1],
    seed: seed,
    loras: loras
  });
  console.log("Generation " + (index + 1) + "/" + jobCount +
    "; seed=" + seed + "; LoRAs=" + loras.map(function (item) {
      return item.file + "@" + item.weight.toFixed(4);
    }).join(", "));

  // Real backend call. DTFlow persists each committed output automatically.
  // On error, stop without resubmitting the same potentially billable request.
  pipeline.run({
    configuration: configuration,
    prompt: prompt,
    negativePrompt: negativePrompt
  });
  console.log("Generation " + (index + 1) + " returned successfully.");
}
console.log("Mixer complete: " + jobCount + " generation call(s) returned.");

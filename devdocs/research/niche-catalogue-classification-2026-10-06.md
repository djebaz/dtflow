# Niche catalogue classification research — 2026-10-06

## Scope

This audit started from the 514 entries in `Models_niche.json` whose `base_model` was `unknown`.
The goal is to replace unknown metadata only when an external source establishes the model family or operation.
Filename wording alone is not treated as compatibility evidence.

## Evidence order

1. Exact filename metadata in the upstream Draw Things community model registry:
   `drawthingsai/draw-things-community/Libraries/MediaGenerationKit/Resources/models.json`.
2. Hugging Face model cards/model trees from the model author or an identifiable conversion of the same model.
3. Published model pages or archives that identify the checkpoint family/base model and task.

The upstream Draw Things registry is especially useful because it associates exact DT checkpoint filenames with a model
`version`, modifier and upstream note/link. Web research is used for recurring community families that are not represented
there by exact filename.

## Result

- Original Niche entries: 681
- Originally unknown base-model entries: 514
- Newly classified in this audit: 264
- Remaining unknown base-model entries: 250

The remaining 250 are intentionally not guessed. A filename containing words such as `flux`, `xl`, `video`, or a model
brand is not sufficient by itself.

A rough review of the remaining names gives 40 auxiliary/language/vision components, 11 upscaler/restoration artifacts,
12 specialized editing pipelines, 23 video-family artifacts not yet mapped, 6 SD 2.x/legacy candidates, and 158 other
unresolved model candidates. These buckets are triage labels only; they are not written into the runtime catalogue.

## Exact upstream classifications

Exact Draw Things registry matches establish classifications for entries including:

- FLUX.1: Chroma, Shuttle, devMODE/schnellMODE and RayFLUX variants.
- SDXL: Kwai Kolors, Playground v2, SSD-1B, Juggernaut XL, DreamShaper XL, PixelWave, iCatcher, ColorfulXL and Proteus.
- SD 1.x: Realistic Vision 5.1, MajicMIX Realistic, Cyberpunk Anime, Dungeons and Diffusion, Openjourney, Arcane,
  Van Gogh Style, Elden Ring, F222, Super Mario Nation and Inkpunk.
- WAN 2.1: exact SkyReels v2 T2V/I2V variants.
- HiDream-I1: HiDream E1-1 is identified upstream as an image-editing model built on HiDream-I1.
- Hunyuan Video and Stable Video Diffusion: exact video variants are retained as their own families rather than being
  forced into an unrelated image family.
- SD 2.x: the exact upstream `v2` entry is classified as `sd_2`.

Source:
https://github.com/drawthingsai/draw-things-community/blob/main/Libraries/MediaGenerationKit/Resources/models.json

## Hugging Face and published-family evidence

The following sources were used to resolve recurring families not covered by an exact current Draw Things registry entry:

- Jib Mix Flux — FLUX.1 Dev / text-to-image:
  https://civitai.green/models/686814?modelVersionId=1755367
  https://huggingface.co/John6666/jib-mix-flux-v6realpix-bf16-flux
- Colossus Project Flux — base model `black-forest-labs/FLUX.1-dev`:
  https://huggingface.co/little-lake-studios/colossus-project-flux
- Moxie Fusion Flux — Flux.1 D:
  https://www.diffus.me/models/moxie-fusion-flux-v1-5d
- Acorn Is Spinning FLUX — FLUX.1:
  https://huggingface.co/John6666/acorn-is-spinning-flux-v11-fp8-flux
- Fluxmania — Flux.1 D:
  https://civarchive.com/seaart/models/fa53902dccd7582ef763c53183be21d3/versions/f3a9e9c74df931f5c74d3348a0a0e43d
- AGFlux — Flux.1 D:
  https://www.seaart.ai/models/detail/ba01156a1c2ec091dba4c8ef6441fb8c
- Artsy Dream / Artsy Vibe — Flux checkpoints:
  https://tungsten.run/model/HWyTnowVts
  https://www.diffus.me/models/artsy-vibe-v2-fp8
- Genova Apex — Flux.1:
  https://www.runninghub.ai/model/public/1901187120470597633
- Project0 — FLUX.1:
  https://tensor.art/models/829313332784727291
- C4PACITOR — FLUX variants, text-to-image:
  https://prompthero.com/ai-models/c4pacitor-download/a_v2_beta
  https://prompthero.com/ai-models/c4pacitor-download/s_v2_alpha
  https://prompthero.com/ai-models/c4pacitor-download/d_v2
- Demon Core FLUX 1.3 / later FLUX lineage:
  https://prompthero.com/ai-models/demon-core-sfw-nsfw-download/flux-v1-3
- Ratatoskr FLUX V5.94:
  https://modelslab.com/models/community-model/ratatoskr-animal-creature-and-furry-flux-v5-94-gguf-dev1-hybrid
- Centerfold Flux — Flux.1 D:
  https://civitai.green/models/686215?modelVersionId=783949
- UltraReal Fine-Tune — FLUX.1:
  https://openlaboratory.com/models/ultrareal-fine-tune/
- Copax TimeLess FLUX/XPlus family:
  https://www.diffus.me/models/copax-timeless-fluxreal-fast
- Fluxed Up — Flux.1 D:
  https://civarchive.com/models/847101?modelVersionId=1305680
- RayFLUX — Flux.1 D:
  https://www.diffus.me/models/rayflux-photoplus
- Nepotism — FLUX.1 checkpoint family:
  https://robertgalasso.com/work/nepotism/
- Illustration Juaner Ghibli — published as a FLUX checkpoint:
  https://github.com/orgs/civitai/discussions/1564
- RedCraft — FLUX.1; the exact `1445047...` entry is explicitly FLUX.Fill inpainting:
  https://civarchive.com/models/958009?modelVersionId=1445047
- Animagine XL 3.1 — StableDiffusionXLPipeline:
  https://huggingface.co/tonera/animagine-xl-3.1
- RealVisXL 4.0 — SDXL:
  https://huggingface.co/imagepipeline/RealVisXL-V4.0
- NoobAI XL — StableDiffusionXLPipeline:
  https://huggingface.co/Laxhar/noobai-XL-1.0
- Playground v2.5 — SDXL architecture:
  https://huggingface.co/sd-research/playground-v2.5-1024px-aesthetic
- Anything V3 — SD 1.5 lineage:
  https://github.com/lllyasviel/ControlNet/discussions/12
- Juggernaut Reborn — SD 1.5:
  https://www.sogni.ai/models
- DreamShaper v8 — SD 1.5 teacher/base evidence:
  https://huggingface.co/ChenHe727/EdgeDiffusion_distilled_final
- Counterfeit V3 — SD 1.5:
  https://www.stablediffusion.online/sd-1-5
- WAN 2.2 TI2V — WAN 2.2 video:
  https://huggingface.co/snowytsai/Wan2.2-TI2V-5B
- HiDream-I1 Dev — HiDream image generation:
  https://huggingface.co/HiDream-ai/HiDream-I1-Dev

## Conservative decisions

- A family is not assigned merely because its filename contains a family-looking token.
- A mode is left `unknown` when the source establishes the base family but not the operation represented by the exact
  checkpoint. This is why some newly classified FLUX entries still have unknown modes.
- FLUX.Fill RedCraft is recorded as `i2i`; ordinary RedCraft FLUX checkpoints remain `t2i`.
- Exact WAN 2.2 T2V/I2V/TI2V names are mapped to the corresponding video modes.
- Auxiliary encoders, LLM/VLM weights, preprocessors, upscalers and specialized pipelines are not forced into an image
  checkpoint family merely to reduce the unknown count.
- No runtime compatibility, LoRA compatibility or provider availability is inferred from this research.

## Follow-up

The remaining 250 unknown entries need either an exact authoritative registry record or model-specific upstream evidence.
A future catalogue cleanup should also decide whether auxiliary components and specialized pipelines belong in
`Models_niche.json` at all; this audit deliberately does not move/delete them.

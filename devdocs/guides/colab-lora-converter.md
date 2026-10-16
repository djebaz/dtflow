# Colab LoRA conversion (experimental)

The standalone workflow lives in [tools/colab/lora-converter](../../tools/colab/lora-converter/README.md). Open its generated `notebooks/production_colab_minimal.ipynb` in Google Colab, run Configure, then Run conversion. The form defaults to the owner's selected Civitai version/file and a safe CKPT output name; optional empty model_version delegates to the upstream converter rather than guessing an architecture.

This downloads a Civitai safetensors LoRA, builds the pinned official Draw Things LoRAConverter on CPU Linux, attempts native SQLite CKPT conversion, and saves outputs and provenance to Drive. It is standalone tooling, not an integrated DTFlow Prepare action, URL ingestion endpoint, or automatic DT+ upload.

## Source and maintenance

Edit `src/project_app/` and `src/cells/` inside the tool directory, then regenerate both notebooks with `python3 tools/colab/lora-converter/tools/build_notebook.py --profile both`. Never edit notebook JSON directly. The builder requires nbformat; its dedicated Colab-tool validation dependency is recorded in requirements-test.txt. These compatibility/validation files belong to the isolated notebook project, not DTFlow runtime dependencies. Canonical configuration is JobConfig in config.py; run_complete_job in orchestrator.py owns execution. Both profiles currently retain the same recovered compact documentation and executable code.

The initial source project was reconstructed from the supplied notebook's embedded Python package and cells because its original standalone builder was not supplied. The new deterministic builder preserves that executable workflow. No model weights, Swift toolchain, vendored upstream checkout, credentials or Colab outputs are committed.

## Fixes included

- Swift 6.0.3 URLs distinguish ubuntu2204/ubuntu2404 directory names from ubuntu22.04/ubuntu24.04 archive and extracted-folder names.
- Civitai aliases include .red. Explicit fileId is validated and selects that file rather than a different primary variant.
- User defaults include the selected URL, output name, Drive/runtime roots, scale_factor=1.0 and build_jobs=1; all generated defaults come from JobConfig.
- OpenMP setup installs libomp-dev and explicitly selects system Clang for C/C++ Bazel actions. A compile/link/run preflight checks omp.h and the runtime before the full build.
- ZIPFoundation's optional FILE pointer is bound before fseeko. The patch checks both original and patched source SHA-256, is idempotent, and refuses unexpected source. Its revision participates in build receipts, conversion-cache identity and output metadata.
- Existing Swift/Bazel tool paths and SHA-256-addressed input downloads remain available for reuse. No Python/CUDA version replacement is introduced.

## Evidence and remaining acceptance

Observed in the owner's supplied Colab logs: Civitai download and SHA-256 verification succeeded; after the OpenMP repair the small compile/link/run check passed and the full build progressed to ZIPFoundation, where Swift rejected the optional FILE pointer. The final ZIPFoundation repair has not been observed through a complete Colab build.

Agent checks completed before repository import: Python syntax, notebook schema, embedded-source parity, full/minimal code parity, folding and deterministic regeneration; valid default configuration and mocked explicit fileId selection; mocked OpenMP/compiler-selection/failure behavior; ZIPFoundation patch application against the retrieved pinned source, idempotence and rejection of unexpected source. Both corrected Swift URLs returned HTTP 200 to HEAD requests.

Not verified: full final Swift/Bazel compilation, actual conversion, output loading/application in Draw Things or DT+, final Drive/browser delivery, broad architecture compatibility, or the DTFlow application suite against this imported tool. SQLite quick_check is structural evidence only. Treat this PR as a review checkpoint, not a working release or a successful conversion claim.

No agent training, inference, model checkpoint download, DT+ upload or billable job was performed. No additional Colab run is required merely to review this draft.

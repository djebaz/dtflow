# Civitai LoRA converter

The Python package and extracted cell sources are authoritative. Both notebooks are generated. The original builder was absent from the supplied notebook; tools/build_notebook.py restores deterministic generation from the extracted sources. Full and minimal currently retain the same recovered compact documentation and executable code.

Swift 6.0.3 uses ubuntu2204/ubuntu2404 download directories and ubuntu22.04/ubuntu24.04 archive and extracted-directory suffixes. Build revision cpu-linux-v2 invalidates prior converter builds. Input downloads remain SHA-256 addressed and reusable. The canonical configuration and job authority remain src/project_app/config.py and src/project_app/orchestrator.py.

Development: install requirements-test.txt, then run bash tools/check.sh. Regenerate with python3 tools/build_notebook.py --profile both.

No Python or CUDA version changes are made by this repair. The CPU Linux Swift/Bazel build remains experimental; a successful toolchain download does not establish converter build or output compatibility.

OpenMP repair: install libomp-dev, select /usr/bin/clang and /usr/bin/clang++ explicitly for Bazel, and compile/link/run an OpenMP smoke program before building. The build receipt includes c_toolchain=system-clang-openmp-v1. Existing toolchain and input-cache paths are retained. No successful real build or conversion has been verified.

ZIPFoundation repair: apply checksum-guarded zipfoundation-optional-file-v1 to Archive+MemoryFile.swift, binding the optional FILE pointer before fseeko. Source patch identity is included in build receipts, conversion cache keys and conversion metadata. Existing Swift and Bazel cache paths are retained. Verified patch application against retrieved pinned source, idempotence and rejection of unexpected source; full Swift build remains unverified.

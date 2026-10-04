# Decision 0001 — drawthings-py (GPL-3.0-only) and DTFlow distribution

**Status:** Accepted — DTFlow is GPL-3.0-only before its first public release.  
**Decision date:** 2026-10-04.  
**Scope:** Project licensing and distribution posture. This records the project decision; it is not legal advice.  
**Related:** [Phase 2D](../plans/phase-2-backends.md#phase-2d--packaging-licensing-and-feature-inventory), [replacement estimate](../research/drawthings-py-replacement.md), [backend contract](../architecture/backend-contract.md), [Windows packaging](../research/windows-packaging.md).

## Context

DTFlow had not been publicly released under MIT. Before the first release, the owner chose **GPL-3.0-only** for DTFlow.
The Draw Things Python client DTFlow depends on, `drawthings-py` (pinned `>=0.4.0,<0.5.0`), also declares
**GPL-3.0-only** and ships the full GPL-3.0 text.

MIT code is generally GPL-compatible; the earlier concern was not that MIT and GPL could never coexist. The practical
distribution question was whether a combined build containing GPL-covered code could remain presented as an MIT-only
application. Adopting GPL-3.0-only for DTFlow removes that top-level mismatch before any public DTFlow release.

## Facts and evidence

Source labels: **observed** = read from installed package metadata or files in this repository; **source-read** = read from a pinned upstream checkout; **unverified** = not established.

| Item | Fact | Evidence |
| --- | --- | --- |
| DTFlow license | GPL-3.0-only | observed after this decision: `LICENSE`, `pyproject.toml` |
| Prior public DTFlow MIT release | None | owner-confirmed before this decision |
| drawthings-py 0.4.0 | `License-Expression: GPL-3.0-only`; `licenses/LICENSE` is the GPL-3.0 text (34,892 bytes) | observed: installed `drawthings_py-0.4.0.dist-info` |
| Official Draw Things source (`drawthingsai/draw-things-community`) | GPL-3.0 (`LICENSE.md`); contributions require a CLA; it contains `imageService.proto` (190 lines) and `config.fbs` (191 lines) among its sources | source-read at commit `e51089d5229b3147e13a21cfe0c379deff851715` (2026-10-04) |
| Where DTFlow uses drawthings-py | Diagnostic probe and Phase 2B/2C adapters; provider use remains inside one replaceable integration package (see backend contract) | observed: repository sources and architecture docs |
| Full legal/compliance effect of a future packaged distribution | Not determined here | unverified; release packaging still needs notice/source review |

### Locked runtime dependency licenses

Read from installed metadata after `uv sync --no-dev` on Linux CPython 3.13 against `uv.lock` at repository commit `fb1615ee70bdb5c9767320db7f04973364df314c`. The runtime closure (`uv export --no-dev --no-emit-project`) was compared again at `a357f1f` after Phase 2A merged and is identical; only development dependencies changed. The list is the `uv export --no-dev --no-emit-project` closure. Re-read it whenever the lock changes. Phase 2C adds HTTPX and its six-package runtime closure listed below, re-read from installed metadata and compared with `uv export --no-dev --no-emit-project` after the uv lock update. These values are metadata declarations, not legal conclusions; a package's own bundled third-party code may carry additional licenses.

| Package | Version | Declared license (metadata) | Notes |
| --- | --- | --- | --- |
| httpx | 0.28.1 | BSD-3-Clause | Phase 2C HTTP client |
| httpcore | 1.0.9 | BSD-3-Clause | HTTPX dependency |
| anyio | 4.15.1 | MIT | HTTPX dependency; services remain asyncio-native |
| certifi | 2026.7.22 | MPL-2.0 | HTTPX CA bundle |
| h11 | 0.16.0 | MIT | HTTPX dependency |
| idna | 3.20 | BSD-3-Clause | HTTPX dependency |
| drawthings-py | 0.4.0 | GPL-3.0-only | The decision subject |
| betterproto | 2.0.0b7 | MIT | |
| grpclib | 0.4.9 | BSD-3-Clause | |
| h2 | 4.4.1 | MIT | |
| hpack | 4.2.0 | MIT | |
| hyperframe | 6.1.0 | MIT | |
| multidict | 7.0.0 | Apache-2.0 | |
| flatbuffers | 25.12.19 | Apache-2.0 | |
| fpzip | 1.2.5 | metadata field empty; bundled `LICENSE` is BSD-3-Clause (Copyright 2022 William Silversmith) | Python wrapper; the wrapped upstream C++ fpzip library (LLNL) is BSD-3 per TanqueStudio's vendored header; whether the wheel carries that notice is unverified |
| numpy | 2.3.5 (uv override) | metadata lists a BSD-style copyright statement and the BSD classifier | Wheels bundle further third-party notices not enumerated here |
| pillow | 12.3.0 | MIT-CMU | |
| python-dateutil | 2.9.0.post0 | Dual license (BSD / Apache-2.0 per classifiers) | |
| six | 1.17.0 | MIT | |
| strictyaml | 1.7.3 | MIT | |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | |
| typing-extensions | 4.16.0 | PSF-2.0 | |
| colorama | 0.4.6 | not read | Windows-only transitive dependency of tqdm; not installed on Linux, so unverified here. Read it on the target Windows environment |

Qt/PySide6 is planned but is not a declared runtime dependency yet; its licensing (LGPL/GPL/commercial options) is a separate future entry.

## Decision

1. License DTFlow under **GPL-3.0-only** before its first public release.
2. Replace the repository MIT license text with the GPL version 3 text and declare `GPL-3.0-only` in project metadata.
3. Do not maintain a legacy MIT release line because no DTFlow release was published under MIT.
4. Keep `drawthings-py` isolated behind the backend integration boundary for maintainability, testing and future replacement flexibility; isolation is no longer being treated as a way to avoid the accepted project license.
5. Continue tracking third-party licenses independently. A GPL project license does not erase dependency-specific notice, source or redistribution obligations.

## Distribution implications

- **Private use:** running and modifying DTFlow privately does not by itself create a public-distribution workflow.
- **Source distribution:** publish DTFlow source under GPL-3.0-only with the license text and required notices.
- **Executable/installer distribution:** provide the GPL-required corresponding source and preserve applicable third-party notices and license material for what is actually shipped.
- **Commercial distribution:** GPL-3.0-only does not prohibit charging for DTFlow or services around it, but distributed covered source remains subject to GPL terms.
- **Future proprietary relicensing:** contributor ownership and permissions would matter if the project later wanted a different licensing model.

## Follow-up

- Re-read the runtime license table whenever `uv.lock` changes the runtime closure.
- Review PySide6/Qt and installer/runtime licenses when those dependencies are introduced.
- Keep packaged-release checklists explicit about corresponding source, notices and bundled dependency licenses.
- Obtain legal review before relying on a specific packaging/compliance interpretation for a public commercial distribution.

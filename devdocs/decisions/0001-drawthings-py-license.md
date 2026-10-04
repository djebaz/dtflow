# Decision 0001 — drawthings-py (GPL-3.0-only) and DTFlow distribution

**Status:** Proposed — decision deferred until a public build is planned.  
**Trigger:** Decide before any public distribution of a DTFlow build that bundles or links `drawthings-py`.  
**Scope:** Facts, options and a recommendation. This is not a legal determination; obtain legal review before relying on any option below.  
**Related:** [Phase 2D](../plans/phase-2-backends.md#phase-2d--packaging-licensing-and-feature-inventory), [replacement estimate](../research/drawthings-py-replacement.md), [backend contract](../architecture/backend-contract.md), [Windows packaging](../research/windows-packaging.md).

## Context

DTFlow currently targets personal use, with possible public distribution later. DTFlow's own license is MIT (`LICENSE`, `pyproject.toml`). The Draw Things Python client DTFlow depends on, `drawthings-py` (pinned `>=0.4.0,<0.5.0`), declares **GPL-3.0-only** and ships the full GPL-3.0 text. The two are not obviously compatible for a bundled public distribution.

## Facts and evidence

Source labels: **observed** = read from installed package metadata or files in this repository; **source-read** = read from a pinned upstream checkout; **unverified** = not established.

| Item | Fact | Evidence |
| --- | --- | --- |
| DTFlow license | MIT | observed: `LICENSE`, `pyproject.toml` |
| drawthings-py 0.4.0 | `License-Expression: GPL-3.0-only`; `licenses/LICENSE` is the GPL-3.0 text (34,892 bytes) | observed: installed `drawthings_py-0.4.0.dist-info` |
| Official Draw Things source (`drawthingsai/draw-things-community`) | GPL-3.0 (`LICENSE.md`); contributions require a CLA; it contains `imageService.proto` (190 lines) and `config.fbs` (191 lines) among its sources | source-read at commit `e51089d5229b3147e13a21cfe0c379deff851715` (2026-10-04) |
| Where DTFlow uses drawthings-py | Only the diagnostic probe today; production use is planned behind one replaceable integration module (see backend contract) | observed: repository sources and architecture docs |
| Legal effect of the above on distributing a combined work | Not determined here | unverified |

### Locked runtime dependency licenses

Read from installed metadata after `uv sync --no-dev` on Linux CPython 3.13 against `uv.lock` at repository commit `fb1615ee70bdb5c9767320db7f04973364df314c`. The list is the `uv export --no-dev --no-emit-project` closure. Re-read it whenever the lock changes. These values are metadata declarations, not legal conclusions; a package's own bundled third-party code may carry additional licenses.

| Package | Version | Declared license (metadata) | Notes |
| --- | --- | --- | --- |
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

## Trigger analysis

- **Private, personal use:** the obligations that attach to distribution are not triggered by running DTFlow privately. This is a statement of the planning assumption, not legal advice.
- **Any public or third-party distribution** of a build that includes `drawthings-py` (or a work derived from it) triggers the decision. The same applies to publishing an executable, installer or container that bundles it.
- Source-only publication is a separate case that also needs review; it is not settled here.

## Options

| Option | Effect | Cost / risk |
| --- | --- | --- |
| A. Stay private | No distribution, no decision needed now | Blocks the planned public path; keeps the dependency |
| B. Relicense DTFlow to a GPL-compatible license when distributing with the dependency | Dependency stays; obligations satisfied by licensing choice | Changes DTFlow's stated MIT licensing; affects any contributors; needs a deliberate owner decision and legal check |
| C. Replace `drawthings-py` in the distributed artifact | Removes the GPL dependency from the bundle | Replacement scope in the [estimate](../research/drawthings-py-replacement.md); note the protocol and configuration schema sources are themselves GPL-3.0 in the official repository, so reimplementation needs clean-room and legal review, not just new code |
| D. Process boundary (separate helper process or tool) | Might keep DTFlow itself out of a combined work | Not assumed to resolve the question; needs legal review; adds packaging and IPC complexity |

## Recommendation

1. **Defer the license decision** to the point a public build is planned; nothing in Phase 2 or 3 needs it resolved.
2. **Keep the dependency isolated** behind the single integration module already required by the [backend contract](../architecture/backend-contract.md), with its workaround ledger, so options B–D stay open and cheap.
3. **Do not copy `drawthings-py` or official Draw Things source into DTFlow** without a recorded decision. Reading them as protocol/design references is already the project's practice.
4. **Re-run the license table** whenever `uv.lock` changes the runtime closure, and record the lock revision used.
5. **Obtain legal advice** before choosing B, C or D, and before any public release.

## Revisit when

- A public or third-party build is scheduled.
- `drawthings-py` changes license, or the project moves to another client or implementation.
- The runtime dependency closure changes (new native codecs, Qt, an installer toolchain).
